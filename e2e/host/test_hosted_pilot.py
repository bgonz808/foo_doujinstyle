import unittest
from unittest.mock import patch
from pathlib import Path
from e2e.host.hosted_firewall import constrain_host
from e2e.host.runner import QualificationError,verify_test_dependencies

class HostedContracts(unittest.TestCase):
    def test_firewall_refuses_outside_hosted_runner(self):
        with patch.dict("os.environ",{},clear=True), self.assertRaises(QualificationError):
            constrain_host(Path("foobar2000.exe"),"a"*32)
    def test_hosted_cli_only_checks_robot(self):
        manifest={"robotframework_version":"7.3.1","flaui_version":None}
        with patch("importlib.metadata.version",return_value="7.3.1") as version:
            self.assertEqual(verify_test_dependencies(manifest,hosted_ci=True),{"robotframework":"7.3.1"})
            version.assert_called_once_with("robotframework")
            with self.assertRaises(QualificationError):
                verify_test_dependencies(manifest)
