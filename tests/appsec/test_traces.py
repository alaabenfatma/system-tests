from utils import BaseTestCase, context, interfaces, skipif


@skipif(not context.appsec_is_released, reason=context.appsec_not_released_reason)
@skipif(context.library == "dotnet", reason="missing feature")
@skipif(context.library == "java", reason="missing feature")
class Test_Retention(BaseTestCase):
    def test_events_retain_traces(self):
        """ AppSec retain APM traces when associated with a security event. """

        MANUAL_KEEP = 2

        def validate_appsec_span(span):
            if span.get("parent_id") not in (0, None):  # do nothing if not root span
                return

            if "appsec.event" not in span["meta"]:
                raise Exception("Can't find appsec.event in span's meta")

            if span["meta"]["appsec.event"] != "true":
                raise Exception(f'appsec.event in span\'s meta should be "true", not {span["meta"]["appsec.event"]}')

            if "_sampling_priority_v1" not in span["metrics"]:
                raise Exception("Metric _sampling_priority_v1 should be set on traces that are manually kept")

            if span["metrics"]["_sampling_priority_v1"] != MANUAL_KEEP:
                raise Exception(f"Trace id {span['trace_id']} , sampling priority should be {MANUAL_KEEP}")

            return True

        r = self.weblog_get("/waf/", headers={"User-Agent": "Arachni/v1"})
        interfaces.library.add_span_validation(r, validate_appsec_span)


@skipif(not context.appsec_is_released, reason=context.appsec_not_released_reason)
@skipif(context.library == "dotnet", reason="missing feature")
@skipif(context.library == "java", reason="missing feature")
class Test_AppSecMonitoring(BaseTestCase):
    def test_events_retain_traces(self):
        """ AppSec store in APM traces some data when enabled. """

        RUNTIME_FAMILY = ["nodejs", "ruby", "jvm", "dotnet", "go", "php", "python"]

        def validate_appsec_span(span):
            if span.get("parent_id") not in (0, None):  # do nothing if not root span
                return

            if "_dd.appsec.enabled" not in span["metrics"]:
                raise Exception("Can't find _dd.appsec.enabled in span's metrics")

            if span["metrics"]["_dd.appsec.enabled"] != "true":
                raise Exception(
                    f'_dd.appsec.enabled in span\'s metrics should be "true", not {span["metrics"]["_dd.appsec.enabled"]}'
                )

            if "_dd.runtime_family" not in span["meta"]:
                raise Exception("Can't find _dd.runtime_family in span's meta")

            if span["metrics"]["_dd.runtime_family"] not in RUNTIME_FAMILY:
                raise Exception(f"_dd.runtime_family {span['_dd.runtime_family']} , should be in {RUNTIME_FAMILY}")

            return True

        interfaces.library.add_span_validation(validator=validate_appsec_span)
