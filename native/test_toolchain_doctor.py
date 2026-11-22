"""Preflight should fail closed, identify missing pins and be reproducible."""
import tempfile
from pathlib import Path
import unittest
from native.toolchain_doctor import audit


class ToolchainDoctorTests(unittest.TestCase):
    def test_missing_external_sdks_do_not_claim_windows_build(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = audit(Path(temporary), Path(temporary), Path(temporary), ('x86', 'x64'))
        self.assertFalse(report['ready_to_attempt_build'])
        self.assertFalse(report['windows_host_verified'])
        self.assertTrue(any('foobar SDK' in check['item'] and not check['ok']
                            for check in report['checks']))
        self.assertTrue(any(check['item'] == 'LLVM clang-tidy' and not check['ok']
                            for check in report['checks']))
        self.assertTrue(any(check['item'] == 'source third_party/json.hpp' and check['ok']
                            for check in report['checks']))

    def test_windows_native_mode_requires_real_host_and_msbuild_not_xwin(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = audit(Path(temporary), llvm_bin=Path(temporary),
                           architectures=('x64',), mode='windows', windows_kit=Path(temporary))
        self.assertEqual(report['mode'], 'windows')
        self.assertFalse(report['ready_to_attempt_build'])
        self.assertFalse(any(check['item'].startswith('xwin ') for check in report['checks']))
        self.assertTrue(any(check['item'] == 'Windows host' and not check['ok']
                            for check in report['checks']))
        self.assertTrue(any('MSBuild.exe' in check['item'] for check in report['checks']))

    def test_architecture_scoping_and_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ('pfc/pfc.vcxproj', 'foobar2000/SDK/foobar2000.h',
                         'foobar2000/SDK/foobar2000_SDK.vcxproj',
                         'foobar2000/foobar2000_component_client/foobar2000_component_client.vcxproj',
                         'foobar2000/shared/shared-x64.lib'):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'fixture')
            result = audit(root, root, root, ('x64',))
            self.assertEqual(result['targets'], ['x64'])
            self.assertEqual(len(result['fingerprints_sha256']['foobar SDK shared x64']), 64)
            self.assertFalse(any('shared Win32' in check['item'] for check in result['checks']))


if __name__ == '__main__':
    unittest.main()
