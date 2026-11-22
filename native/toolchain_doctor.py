"""Preflight a reproducible Windows foobar2000 SDK / LLVM build environment.

This tool is developer-only. It never downloads SDKs, invokes untrusted build
artifacts, or qualifies an installable component. It inspects required files and
captures versions/hashes so that Windows runs can be compared with CI runners.
"""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
SDK_FILES = (
    'pfc/pfc.vcxproj',
    'foobar2000/SDK/foobar2000.h',
    'foobar2000/SDK/foobar2000_SDK.vcxproj',
    'foobar2000/foobar2000_component_client/foobar2000_component_client.vcxproj',
)
XWIN_FILES = (
    'crt/include/vcruntime.h',
    'sdk/include/um/windows.h',
    'sdk/include/shared/guiddef.h',
    'sdk/include/ucrt/stdio.h',
)
ARCH = {'x86': ('x86', 'Win32'), 'x64': ('x86_64', 'x64')}


def audit(sdk=None, xwin=None, llvm_bin=None, architectures=('x86', 'x64'),
          *, mode='cross', windows_kit=None):
    """Return factual preflight results; do not label an unbuilt DLL qualified."""
    if mode not in ('cross', 'windows'):
        raise ValueError('Unknown build mode: ' + mode)
    checks = []
    fingerprints = {}

    def check(label, path, *, fingerprint=False):
        path = Path(path)
        exists = path.is_file()
        checks.append({'item': label, 'path': str(path), 'ok': exists})
        if fingerprint and exists:
            fingerprints[label] = sha256(path.read_bytes()).hexdigest()
        return exists

    sdk = Path(sdk).resolve() if sdk else None
    xwin = Path(xwin).resolve() if xwin else None
    llvm_bin = Path(llvm_bin).resolve() if llvm_bin else None
    windows_kit = Path(windows_kit).resolve() if windows_kit else None
    for relative in SDK_FILES:
        check('foobar SDK ' + relative, sdk / relative if sdk else Path('/missing-fb2k-sdk') / relative,
              fingerprint=True)
    for arch in architectures:
        if arch not in ARCH:
            raise ValueError('Unknown architecture: ' + arch)
        platform = ARCH[arch][1]
        check('foobar SDK shared ' + platform,
              sdk / f'foobar2000/shared/shared-{platform}.lib' if sdk else
              Path('/missing-fb2k-sdk') / f'shared-{platform}.lib', fingerprint=True)
    if mode == 'cross':
        for relative in XWIN_FILES:
            check('xwin ' + relative, xwin / relative if xwin else Path('/missing-xwin') / relative)
        for arch in architectures:
            target = ARCH[arch][0]
            for prefix, filename in (('crt/lib', 'vcruntime.lib'), ('sdk/lib/um', 'winhttp.lib'),
                                     ('sdk/lib/um', 'bcrypt.lib'), ('sdk/lib/ucrt', 'ucrt.lib')):
                check(f'xwin {arch} {filename}',
                      xwin / prefix / target / filename if xwin else Path('/missing-xwin') / target / filename)
    else:
        checks.append({'item': 'Windows host', 'path': sys.platform, 'ok': sys.platform == 'win32'})
        msbuild = shutil.which('MSBuild.exe') or shutil.which('msbuild.exe')
        checks.append({'item': 'MSBuild.exe in developer environment', 'path': msbuild or 'MSBuild.exe',
                       'ok': bool(msbuild)})
        include = windows_kit / 'Include' if windows_kit else None
        versions = sorted((path.name for path in include.iterdir() if path.is_dir()), reverse=True) \
            if include is not None and include.is_dir() else []
        version = next((name for name in versions if name.startswith('10.0.')), None)
        for relative in ('um/windows.h', 'shared/guiddef.h', 'ucrt/stdio.h'):
            check('Windows kit include ' + relative,
                  include / version / relative if version else Path('/missing-windows-kit') / relative)
        for arch in architectures:
            for family, filename in (('um', 'winhttp.lib'), ('um', 'bcrypt.lib'), ('ucrt', 'ucrt.lib')):
                check(f'Windows kit {arch} {filename}',
                      windows_kit / 'Lib' / version / family / ARCH[arch][1] / filename if version else
                      Path('/missing-windows-kit') / arch / filename)
    tools = {}
    for executable in ('clang-cl', 'lld-link', 'clang-tidy', 'clang'):
        path = llvm_bin / (executable + ('.exe' if os.name == 'nt' else '')) if llvm_bin else None
        found = path if path and path.is_file() else shutil.which(executable) if not llvm_bin else None
        if not found:
            checks.append({'item': 'LLVM ' + executable, 'path': str(path) if path else executable, 'ok': False})
            continue
        try:
            banner = subprocess.run([str(found), '--version'], capture_output=True, text=True,
                                    timeout=6, check=True).stdout.splitlines()[0]
        except (OSError, IndexError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            checks.append({'item': 'LLVM ' + executable, 'path': str(found), 'ok': False})
            continue
        tools[executable] = {'path': str(found), 'banner': banner}
        checks.append({'item': 'LLVM ' + executable, 'path': str(found), 'ok': True})
    # clang-tidy must be from the same LLVM major family as clang-cl.
    def major(name):
        found = re.search(r'(?:clang|LLVM) version (\d+)', tools.get(name, {}).get('banner', ''), re.I)
        return int(found.group(1)) if found else None
    if 'clang-tidy' in tools and 'clang-cl' in tools:
        checks.append({'item': 'clang-tidy / clang-cl major match', 'path': 'tool versions',
                       'ok': major('clang-tidy') is not None and major('clang-tidy') == major('clang-cl')})
    for relative in ('sqlite-amalgamation-3540100.zip', 'third_party/json.hpp',
                     'third_party/utf8proc/utf8proc.c', 'foo_doujinstyle.vcxproj'):
        check('source ' + relative, HERE / relative, fingerprint=True)
    base_version = (HERE.parent / 'VERSION').read_text(encoding='utf-8').strip()
    report = {'source_version': base_version,
              'mode': mode, 'host': sys.platform, 'targets': list(architectures), 'tools': tools,
              'checks': checks, 'fingerprints_sha256': fingerprints}
    report['ready_to_attempt_build'] = all(item['ok'] for item in checks)
    report['windows_host_verified'] = False  # Preflight is not a foobar2000 smoke test.
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sdk', type=Path, help='Extracted foobar2000 SDK root')
    p.add_argument('--mode', choices=('cross', 'windows'), default='cross',
                   help='cross needs xwin; windows uses Visual Studio/MSBuild and Windows Kits')
    p.add_argument('--windows-kit', type=Path, help='Windows Kits/10 root for native Windows mode')
    p.add_argument('--xwin', type=Path, help='xwin CRT and Windows SDK sysroot')
    p.add_argument('--llvm-bin', type=Path, help='Matching clang-cl/clang-tidy/lld-link bin')
    p.add_argument('--arch', nargs='+', choices=ARCH, default=['x86', 'x64'])
    p.add_argument('--output', type=Path, help='Save machine-readable preflight report')
    args = p.parse_args()
    report = audit(args.sdk, args.xwin, args.llvm_bin, args.arch,
                   mode=args.mode, windows_kit=args.windows_kit)
    print(json.dumps(report, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return 0 if report['ready_to_attempt_build'] else 1


if __name__ == '__main__':
    sys.exit(main())
