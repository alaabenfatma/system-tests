# Unless explicitly stated otherwise all files in this repository are licensed under the the Apache License Version 2.0.
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2021 Datadog, Inc.

from utils import context, BaseTestCase, interfaces, not_relevant


class Test_NoExceptions(BaseTestCase):
    @not_relevant(context.library != "dotnet", reason="only for .NET")
    def test_dotnet(self):
        """There is not exception in dotnet-tracer-managed log files"""
        interfaces.library_dotnet_managed.assert_absence(r"[A-Za-z]+\.[A-Za-z]*Exception")
