# Unless explicitly stated otherwise all files in this repository are licensed under the the Apache License Version 2.0.
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2021 Datadog, Inc.

from utils import BaseTestCase, context, interfaces, released, bug, missing_feature
import pytest


if context.weblog_variant == "echo-poc":
    pytestmark = pytest.mark.skip("not relevant: echo is not instrumented")
elif context.library == "cpp":
    pytestmark = pytest.mark.skip("not relevant")


@released(golang="?", dotnet="1.29.0", java="?", nodejs="?", php="?", python="?")
@missing_feature(library="ruby", reason="can't report user agent with dd-trace-rb")
class Test_Retention(BaseTestCase):
    def test_events_retain_traces(self):
        """On traces with appsec event, meta.appsec-event and sampling prio are set"""

        APPSEC_KEEP = 4

        def validate_appsec_span(span):
            if span.get("parent_id") not in (0, None):  # do nothing if not root span
                return

            if "appsec.event" not in span["meta"]:
                raise Exception("Can't find appsec.event in span's meta")

            if span["meta"]["appsec.event"] != "true":
                raise Exception(f'appsec.event in span\'s meta should be "true", not {span["meta"]["appsec.event"]}')

            if "_sampling_priority_v1" not in span["metrics"]:
                raise Exception("Metric _sampling_priority_v1 should be set on traces that are manually kept")

            if span["metrics"]["_sampling_priority_v1"] != APPSEC_KEEP:
                raise Exception(f"Trace id {span['trace_id']} , sampling priority should be {APPSEC_KEEP}")

            return True

        r = self.weblog_get("/waf/", headers={"User-Agent": "Arachni/v1"})
        interfaces.library.add_span_validation(r, validate_appsec_span)


@released(golang="?", dotnet="1.29.0", java="?", nodejs="2.0.0-appsec-alpha.1", php="?", python="?", ruby="0.51.0")
class Test_AppSecMonitoring(BaseTestCase):
    @bug(library="dotnet", reason="_dd.appsec.enabled is meta instead of metrics")
    @bug(library="ruby", reason="_dd.appsec.enabled is missing")
    def test_events_retain_traces(self):
        """ AppSec store in APM traces some data when enabled. """

        RUNTIME_FAMILY = ["nodejs", "ruby", "jvm", "dotnet", "go", "php", "python"]

        def validate_appsec_span(span):
            if span.get("parent_id") not in (0, None):  # do nothing if not root span
                return

            if "_dd.appsec.enabled" not in span["metrics"]:
                raise Exception("Can't find _dd.appsec.enabled in span's metrics")

            if span["metrics"]["_dd.appsec.enabled"] != 1:
                raise Exception(
                    f'_dd.appsec.enabled in span\'s metrics should be 1 or 1.0, not {span["metrics"]["_dd.appsec.enabled"]}'
                )

            if "_dd.runtime_family" not in span["meta"]:
                raise Exception("Can't find _dd.runtime_family in span's meta")

            if span["meta"]["_dd.runtime_family"] not in RUNTIME_FAMILY:
                raise Exception(f"_dd.runtime_family {span['_dd.runtime_family']} , should be in {RUNTIME_FAMILY}")

            return True

        interfaces.library.add_span_validation(validator=validate_appsec_span)
