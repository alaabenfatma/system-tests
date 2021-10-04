import asyncio
import signal
import os
import json
import hashlib
import logging
from logging.handlers import RotatingFileHandler
import aiohttp
from yarl import URL
import time
from datetime import datetime, timedelta

from utils import context, data_collector

from scenarios.fuzzer.corpus import get_corpus
from scenarios.fuzzer.request_mutator import get_mutator
from scenarios.fuzzer.request_generators import RequestGenerator
from scenarios.fuzzer.tools.metrics import (
    RateMetric,
    AccumulatedMetric,
    PerformanceMetric,
    Metric,
    NumericalMetric,
    ResetedAccumulatedMetric,
    AccumulatedMetricWithPercent,
    SelfAccumulatedMetricWithPercent,
    Report,
)


class _RequestDumper:
    def __init__(self, name=None, enabled=True):
        self.enabled = enabled
        self.logger = None
        if name:
            self.filename = f"logs/dump_{name}_{datetime.now().isoformat()}.dump"
        else:
            self.filename = f"logs/dump_{datetime.now().isoformat()}.dump"

    def __call__(self, payload):
        if not self.enabled:
            return

        if self.logger == None:
            self.logger = logging.Logger(__name__)
            self.logger.addHandler(RotatingFileHandler(self.filename))

        self.logger.info(json.dumps(payload))


class Fuzzer(object):
    def __init__(
        self,
        corpus,
        no_mutation,
        base_url,
        port,
        seed,
        max_tasks,
        report_frequency,
        logger,
        request_count=None,
        max_time=None,
        dump_on_status=("500",),
        debug=False,
        systematic_export=False,
    ):
        self.loop = asyncio.get_event_loop()
        self.loop.set_debug(debug)
        # asyncio.set_event_loop(self.loop)

        self.logger = logger

        self.base_url = base_url
        self.report = Report(report_frequency=report_frequency, logger=self.logger)

        self.corpus = corpus
        self.seed = seed
        self.requests = RequestGenerator(get_mutator(no_mutation), get_corpus(corpus))
        self.request_count = request_count

        self.max_tasks = max_tasks
        self.max_time = max_time
        self.max_datetime = None  # will be set later
        self.sem = asyncio.Semaphore(max_tasks, loop=self.loop)

        self.dump_on_status = dump_on_status
        self.enable_response_dump = False
        self.systematic_exporter = _RequestDumper() if systematic_export else lambda x: 0

        self.total_metric = AccumulatedMetric("#", format_string="#{value}", display_length=7, has_raw_value=False)
        self.memory_metric = NumericalMetric("Mem")

        self.performances = PerformanceMetric()

        self.count_metric = ResetedAccumulatedMetric("Count")
        self.bytes_metric = ResetedAccumulatedMetric("Bytes")
        self.with_sqreen = SelfAccumulatedMetricWithPercent("Sqreen", display_length=6)

        self.status_metrics = {}
        self.backend_requests = {}
        self.backend_requests_size = ResetedAccumulatedMetric("Bytes", raw_name="backend_bytes")
        self.backend_signals = {}
        self.backend_commands = {}

        self._add_status_metric("200", "200")
        self._add_status_metric("400", "400")
        self._add_status_metric("403", "403")
        self._add_status_metric("404", "404")

        self._add_backend_request("/ping", "Ping")
        self._add_backend_request("/batches", "Batch")

        self.backend_requests_stack = []

        self.finished = False

    def _add_status_metric(self, key, name):
        self.status_metrics[key] = AccumulatedMetricWithPercent(
            name, self.count_metric, display_length=4, raw_name="R_" + key
        )

    def _add_backend_request(self, key, name):
        self.backend_requests[key] = ResetedAccumulatedMetric(name, raw_name="B_" + key)

    def _add_backend_signal(self, key, name):
        self.backend_signals[key] = ResetedAccumulatedMetric(name, raw_name="S_" + key)

    async def wait_for_first_response(self):
        session = aiohttp.ClientSession(loop=self.loop)

        self.logger.info("Wait for a successful server response...")
        try:
            for i in range(30):
                try:
                    if self.finished:
                        self.logger.info("User interruption")
                        break

                    resp = await session.request(url=self.base_url, method="GET")
                except aiohttp.client.ClientConnectionError as exc:
                    answer = str(exc)
                else:
                    answer = str(resp.status)
                    if resp.status in (200, 404, 403):
                        await session.close()
                        self.logger.info(f"First response received after {i} attempts")
                        return

                time.sleep(1)

            raise Exception("Server does not respond")
        finally:
            await session.close()

    def run_forever(self):
        # not the best place ...
        data_collector.proxy_callbacks["agent"].append(self.backend_requests_stack.append)

        self.logger.info("")
        self.logger.info("=" * 80)

        asyncio.ensure_future(self._run(), loop=self.loop)
        self.loop.add_signal_handler(signal.SIGINT, self.perform_armageddon)
        self.logger.info("Starting event loop")
        self.loop.run_forever()

    def perform_armageddon(self):
        self.finished = True

    async def watch_docker_target(self):
        while not self.finished:
            try:
                session = aiohttp.ClientSession(
                    loop=self.loop, connector=aiohttp.UnixConnector(path="/var/run/docker.sock"),
                )

                async with session.request(
                    url="http://localhost/containers/systemtests_weblog_1/stats", method="GET",
                ) as resp:
                    async for line in resp.content:
                        if self.finished:
                            break
                        data = json.loads(line)
                        self.memory_metric.update(data["memory_stats"]["usage"])

            except FileNotFoundError:
                self.logger.info("Docker socket not found")
            except aiohttp.client_exceptions.ClientConnectorError:
                self.logger.info("Can't connect to Docker socket")
            except Exception as e:
                self.logger.info(f"Unexpected exception when connecting to Docker socket: {e}")

            finally:
                if session:
                    await session.close()

    async def _run(self):
        try:
            await self.wait_for_first_response()
        except Exception as e:
            self.logger.error(str(e))
            self.loop.stop()
            return

        self.report.start()
        self.max_datetime = None if self.max_time is None else datetime.now() + timedelta(seconds=self.max_time)

        tasks = set()

        task = self.loop.create_task(self.watch_docker_target())
        tasks.add(task)
        task.add_done_callback(tasks.remove)

        self.report.value("Target library", str(context.library))
        self.report.value("Weblog variant", context.weblog_variant)
        self.report.value("Corpus", self.corpus)
        self.report.value("Corpus size", len(self.requests.buffer))
        self.report.value("Target", f"{self.base_url}")
        self.report.value("Seed", self.seed)
        self.report.value("Dump on", str(self.dump_on_status))

        if self.max_time:
            self.report.value(f"Time", self.max_time)

        if self.request_count:
            self.report.value(f"Count", self.request_count)

        request_id = 0

        jar = aiohttp.CookieJar(unsafe=True)
        timeout = aiohttp.ClientTimeout(total=1)
        session = aiohttp.ClientSession(loop=self.loop, cookie_jar=jar, timeout=timeout)

        try:
            for request in self.requests:
                while len(self.backend_requests_stack) != 0:
                    self.update_backend_metrics(self.backend_requests_stack.pop(0))

                if self.max_datetime is not None and datetime.now() > self.max_datetime:
                    self.finished = True

                if self.finished:
                    break

                await asyncio.sleep(0, loop=self.loop)
                await self.sem.acquire()

                task = self.loop.create_task(self._process(session, request, request_id))
                tasks.add(task)
                task.add_done_callback(tasks.remove)
                task.add_done_callback(lambda t: self.sem.release())

                request_id += 1

                if self.request_count is not None and self.request_count == request_id:
                    self.finished = True

        finally:
            self.finished = True  # TODO revamp remove
            await asyncio.gather(*tasks)
            await session.close()
            self.report.pulse(self.get_metrics, force=True)
            self.report.done(self.get_metrics)

            self.loop.stop()

    async def _process(self, session, request, request_id):

        resp = None
        request_timestamp = datetime.now()
        self.systematic_exporter(request)

        try:
            args = {k: v for k, v in request.items()}
            args["url"] = URL(self.base_url + args.pop("path"), encoded=True)
            async with session.request(**args) as resp:

                # if str(resp.status) == "500":
                #     open("logs/500.html", "w").write(await resp.text())
                #     self.finished = True

                with_sqreen = resp.status == 403 or "x-protected-by" in resp.headers
                await self.update_metrics(
                    request_id, str(resp.status), request, request_timestamp, with_sqreen, response=resp,
                )

                try:
                    await self.requests.feedback(request, resp, self.base_url)
                except Exception as exc:
                    await self.logger.signal("Feedback exception", type(exc).__name__)

        except Exception as exc:
            await self.update_metrics(request_id, type(exc).__name__, request, request_timestamp)

        finally:
            if resp:
                resp.close()
            self.report.pulse(self.get_metrics)

    def get_metrics(self):
        task_metric = Metric("Tasks")
        task_metric.update(self.max_tasks - self.sem._value)

        separator = Metric(name="|", value="|", display_length=1, has_raw_value=False)

        result = [
            self.total_metric,
            self.count_metric,
            self.memory_metric,
            self.bytes_metric,
            task_metric,
            self.with_sqreen,
            separator,
            self.performances,
            separator,
        ]

        result += self.status_metrics.values()
        result.append(separator)
        result.append(self.backend_requests_size)
        result += self.backend_requests.values()
        result.append(separator)
        result += self.backend_signals.values()

        return result

    async def update_metrics(
        self, request_id, status, request, request_timestamp, with_sqreen=None, response=None,
    ):

        ellapsed = (datetime.now() - request_timestamp).total_seconds()

        byte_count = len(request["path"])

        if with_sqreen is not None:
            self.with_sqreen.update(1 if with_sqreen else 0)

        self.performances.update(ellapsed)

        def get_len(obj):
            if isinstance(obj, (int, float, bool)):
                return 4
            elif isinstance(obj, (str, bytearray)):
                return len(obj)
            elif isinstance(obj, list):
                return sum([get_len(item) for item in obj])
            elif isinstance(obj, dict):
                return sum([len(k) + get_len(v) for k, v in obj.items()])
            elif obj is None:
                return 0
            else:
                print(f"Unknown type {type(obj)}")
                return 0

        for key in ("data", "json", "headers", "cookies"):
            obj = request.get(key, {})
            byte_count += get_len(obj)

        self.total_metric.update()
        self.count_metric.update()
        self.bytes_metric.update(byte_count)

        if status not in self.status_metrics:
            self._add_status_metric(status, status)

        self.status_metrics[status].update()

        if status in self.dump_on_status:
            request_as_json = json.dumps(request, indent=4)
            hashed = hashlib.md5(request_as_json.encode()).hexdigest()

            with open(os.path.join("logs", f"{status}-{hashed}.json"), "w") as f:
                f.write(request_as_json)

            if response and self.enable_response_dump:
                text = await response.text()
                with open(os.path.join("logs", f"{status}-response-{hashed}.html"), "w") as f:
                    f.write(text)

    def update_backend_metrics(self, data):
        path, request = data["path"], data["request"]

        self.backend_requests_size.update(request["length"])

        if path not in self.backend_requests:
            self.report.signal("New backend requests", path)
            self._add_backend_request(path, path.split("/")[-1])

        self.backend_requests[path].update()

        # for exception in get_agent_exceptions(data):
        #     payload = exception.get("payload", {})
        #     name = payload.get("klass", "**class?**") + ":" + payload.get("message", "")
        #     if name not in self.backend_signals:
        #         self.report.signal("New agent exception", name)
        #         self._add_backend_signal(name, name[:5])

        #     self.backend_signals[name].update()

        if path in ("/sqreen/v1/app-login", "/sqreen/v1/app-beat"):
            for item in data["response"]["content"].get("commands", []):
                self.backend_commands[item["uuid"]] = item
                self.report.signal("Command", item["name"])

        if path == "/sqreen/v1/app-beat":
            content = data["request"]["content"]

            for key, result in content.get("command_results", {}).items():
                if key in self.backend_commands:
                    command = self.backend_commands.pop(key)
                else:
                    command = {"name": key}

                self.report.signal("Command result", command["name"] + " => " + str(result["status"]))
