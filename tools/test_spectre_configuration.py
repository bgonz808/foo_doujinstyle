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
                self.assertTrue(any(e.text == 'Spectre' and 'ClangCL' in e.attrib.get('Condition', '') for e in tree.findall('.//m:PropertyGroup/m:SpectreMitigation', NS)))
                self.assertTrue(any(c.find('m:SDLCheck', NS) is not None and c.find('m:SDLCheck', NS).text == 'true' for c in compilation))
                values = [e for c in compilation for e in c]
                self.assertTrue(any(e.tag.endswith('SpectreMitigation') and e.text == 'Spectre'
                                    and 'ClangCL' in e.attrib.get('Condition', '') for e in values))
                self.assertTrue(any(e.tag.endswith('AdditionalOptions') and e.text and '/Qspectre' in e.text for e in values))
                self.assertTrue(any(e.tag.endswith('AdditionalOptions') and '/Qspectre' in (e.text or '') and "ClangCL'" in e.attrib.get('Condition', '') for e in values))
                self.assertTrue(any(e.tag.endswith('AdditionalOptions') and e.text and
                                    '-mspeculative-load-hardening' in e.text and
                                    'ClangCL' in e.attrib.get('Condition', '') for e in values))
                link = tree.findall('.//m:ItemDefinitionGroup/m:Link', NS)
                self.assertTrue(any('SOURCELINK:' in (e.text or '') and e.attrib.get('Condition', '') == "'$(DoujinSourceLink)' == 'true'" and 'ARM64EC' not in e.attrib.get('Condition', '') for c in link for e in c))
                self.assertTrue(any(e.attrib.get('Name') == 'EmitSourceLink' and e.attrib.get('Condition', '') == "'$(DoujinSourceLink)' == 'true'" and 'ARM64EC' not in e.attrib.get('Condition', '') for e in tree.findall('.//m:Target', NS)))
                opts = [e for c in link for e in c if e.tag.endswith('AdditionalLibraryDirectories')]
                self.assertTrue(any('lib\\spectre\\x86' in (e.text or '') for e in opts))
                self.assertTrue(any('lib\\spectre\\x64' in (e.text or '') for e in opts))
                self.assertFalse(any('lib\\spectre\\arm64ec' in (e.text or '') for e in opts))
                self.assertTrue(any('lib\\spectre\\arm64' in (e.text or '') and "'$(Platform)' == 'ARM64EC'" in e.attrib.get('Condition','') for e in opts))

    def test_sdk_mitigations_not_omitted(self):
        sdk = (ROOT / 'tools' / 'configure_foobar_sdk.ps1').read_text(encoding='utf-8')
        self.assertIn('SpectreMitigation', sdk)
        self.assertIn('InsertAfter($properties, $cppProps[0])', sdk)
        self.assertIn("Name = 'SDLCheck'; Value = 'true'", sdk)
        # Match the actual MSVC assignment, not an accidental substring in
        # an unrelated comment or Clang branch. Preserve both option tokens.
        import re
        assignment = re.findall(r"(?m)^\s*\$qspectre\.InnerText\s*=\s*'([^']+)'\s*$", sdk)
        self.assertEqual(len(assignment), 1)
        self.assertIn('/Qspectre', assignment[0].split())
        self.assertIn('/Brepro', assignment[0].split())
        self.assertTrue(assignment[0].endswith('%(AdditionalOptions)'))
        self.assertIn('`$(PlatformToolset)', sdk)
        self.assertIn('-mspeculative-load-hardening', sdk)
        self.assertIn('foobar2000_SDK.vcxproj', sdk)
        self.assertIn('pfc.vcxproj', sdk)

    def test_explicit_win32_msvc_compiler_exception(self):
        build = (ROOT / 'native' / 'build.ps1').read_text(encoding='utf-8')
        workflow = (ROOT / '.github' / 'workflows' / 'windows-build.yml').read_text(encoding='utf-8')
        self.assertIn('Win32WithMsvc', build)
        self.assertIn('X64WithMsvc', build)
        self.assertIn('Arm64ecWithMsvc', build)
        self.assertIn("./native/build.ps1 -SdkRoot $env:FOOBAR_SDK_ROOT -Platform $env:TARGET_ARCH -VerifySharedSqliteCompilation", workflow)
        self.assertIn("./tools/test_build_profile.ps1", workflow)
        self.assertIn("$PinnedMsvcOnly = [bool]$profile.pinned", build)
        self.assertIn("if ($PinnedMsvcOnly)", build)
        self.assertNotIn("-UnqualifiedDevelopment", workflow)
        self.assertNotIn("-AllowDirtySource", workflow)
        self.assertIn("$sourceLink = if ($identity.source_kind -eq 'git' -and !$identity.dirty)", build)
        self.assertIn('/p:DoujinSourceLink=$sourceLink', build)
        profile = (ROOT / 'tools' / 'build_profile.ps1').read_text(encoding='utf-8')
        self.assertIn("$canonical = !$UnqualifiedDevelopment -and !$HasLlvm -and !$HasExceptions", profile)
        self.assertIn("name='canonical-msvc'; pinned=$true", profile)
        self.assertIn('arch: [Win32, x64, ARM64EC]', workflow)
        self.assertIn("'/p:PlatformToolset=v143'", build)

    def test_spectre_installer_is_in_release_workflow_before_preflight(self):
        workflow = (ROOT / '.github' / 'workflows' / 'windows-build.yml').read_text(encoding='utf-8')
        install = 'run: ./tools/install_msvc_spectre.ps1'
        guard = 'run: ./tools/verify_msvc_runtime.ps1'
        compile_step = '- name: Compile one architecture using exact MSVC pins'
        self.assertEqual(workflow.count(install), 1)
        self.assertTrue(0 <= workflow.index(install) < workflow.index(guard) < workflow.index(compile_step))
        script = (ROOT / 'tools' / 'install_msvc_spectre.ps1').read_text(encoding='utf-8')
        self.assertIn('Microsoft.VisualStudio.Component.VC.Runtimes.ARM64.Spectre', script)
        self.assertNotIn('Microsoft.VisualStudio.Component.VC.Runtimes.ARM64EC.Spectre', script)
        self.assertIn('Get-AuthenticodeSignature', script)
        self.assertIn("@('msvcrt.lib','vcruntime.lib','msvcprt.lib')", script)
        runtime = (ROOT / 'tools' / 'verify_msvc_runtime.ps1').read_text(encoding='utf-8')
        self.assertIn("if ($arch -eq 'arm64ec') { 'arm64' }", runtime)
        self.assertIn("@('msvcrt.lib', 'vcruntime.lib', 'msvcprt.lib')", runtime)
        self.assertNotIn("runtime_library_sha256['libvcruntime.lib']", runtime)

    def test_ba2024_stays_blocking(self):
        import json
        policy = json.loads((ROOT / 'security' / 'bin-policy.json').read_text())
        self.assertEqual(policy['rules']['BA2024']['enforcement'], 'blocking')
        baseline = json.loads((ROOT / 'tools' / 'binskim-baseline.json').read_text())
        self.assertFalse(any(r.get('rule') == 'BA2024' for r in baseline['accepted_findings']))

if __name__ == '__main__':
    unittest.main()
