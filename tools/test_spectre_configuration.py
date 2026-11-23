"""Static safety guards for Spectre mitigations across all shipped build paths."""
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NS = {'m': 'http://schemas.microsoft.com/developer/msbuild/2003'}

class SpectreConfig(unittest.TestCase):
    def test_first_party_projects(self):
        for name in ('foo_doujinstyle', 'publish_references'):
            with self.subTest(project=name):
                tree = ET.parse(ROOT / 'native' / (name + '.vcxproj'))
                compilation = tree.findall('.//m:ItemDefinitionGroup/m:ClCompile', NS)
                self.assertTrue(compilation)
                values = [e for c in compilation for e in c]
                self.assertTrue(any(e.tag.endswith('SpectreMitigation') and e.text == 'Spectre'
                                    and 'ClangCL' in e.attrib.get('Condition', '') for e in values))
                self.assertTrue(any(e.tag.endswith('AdditionalOptions') and e.text and
                                    '-mspeculative-load-hardening' in e.text and
                                    'ClangCL' in e.attrib.get('Condition', '') for e in values))
                link = tree.findall('.//m:ItemDefinitionGroup/m:Link', NS)
                opts = [e for c in link for e in c if e.tag.endswith('AdditionalLibraryDirectories')]
                self.assertTrue(any('lib\\spectre\\x86' in (e.text or '') for e in opts))
                self.assertTrue(any('lib\\spectre\\x64' in (e.text or '') for e in opts))

    def test_sdk_mitigations_not_omitted(self):
        sdk = (ROOT / 'tools' / 'configure_foobar_sdk.ps1').read_text(encoding='utf-8')
        self.assertIn('SpectreMitigation', sdk)
        self.assertIn('`$(PlatformToolset)', sdk)
        self.assertIn('-mspeculative-load-hardening', sdk)
        self.assertIn('foobar2000_SDK.vcxproj', sdk)
        self.assertIn('pfc.vcxproj', sdk)

    def test_explicit_win32_msvc_compiler_exception(self):
        build = (ROOT / 'native' / 'build.ps1').read_text(encoding='utf-8')
        workflow = (ROOT / '.github' / 'workflows' / 'windows-build.yml').read_text(encoding='utf-8')
        self.assertIn('Win32WithMsvc', build)
        self.assertIn('-Win32WithMsvc', workflow)
        self.assertIn('-X64WithMsvc', workflow)
        self.assertIn('X64WithMsvc', build)
        self.assertIn('-Arm64ecWithMsvc', workflow)

    def test_ba2024_stays_blocking(self):
        import json
        policy = json.loads((ROOT / 'security' / 'bin-policy.json').read_text())
        self.assertEqual(policy['rules']['BA2024']['enforcement'], 'blocking')
        baseline = json.loads((ROOT / 'tools' / 'binskim-baseline.json').read_text())
        self.assertFalse(any(r.get('rule') == 'BA2024' for r in baseline['accepted_findings']))

if __name__ == '__main__':
    unittest.main()
