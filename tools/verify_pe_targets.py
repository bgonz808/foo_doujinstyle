"""Verify each final PE image's architecture, including linked ARM64EC identity."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import struct
import subprocess
import sys

# Per Microsoft, ARM64EC *object files* use A641, but final EXE/DLL images
# use 8664 (x64) with the ARM64X marker in LINK /DUMP /HEADERS.
EXPECTED = {"Win32": 0x014C, "x64": 0x8664, "ARM64EC": 0x8664}
ARM64X_HEADER = re.compile(r"(?im)^\s*8664\s+machine\s+\(x64\)\s+\(ARM64X\)\s*$")


def machine(path: Path) -> int:
    data = path.read_bytes()
    if len(data) < 0x40 or data[:2] != b"MZ":
        raise ValueError(f"{path}: invalid DOS header")
    offset = struct.unpack_from("<I", data, 0x3C)[0]
    if offset < 0x40 or offset + 6 > len(data) or data[offset:offset+4] != b"PE" + bytes(2):
        raise ValueError(f"{path}: invalid PE header")
    return struct.unpack_from("<H", data, offset + 4)[0]


def verify(path: Path, arch: str, *, link_exe: Path | None = None) -> None:
    actual = machine(path)
    if actual != EXPECTED[arch]:
        raise RuntimeError(f"{path}: expected final PE machine 0x{EXPECTED[arch]:04X}, got 0x{actual:04X}")
    if arch == "ARM64EC":
        if link_exe is None or not link_exe.is_file():
            raise RuntimeError("ARM64EC verification requires a real Microsoft LINK.exe /DUMP")
        result = subprocess.run([str(link_exe), "/dump", "/headers", str(path)],
                                check=True, text=True, encoding="utf-8", errors="replace",
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if not ARM64X_HEADER.search(result.stdout):
            raise RuntimeError(f"{path}: final PE lacks ARM64X marker; 0x8664 alone is ordinary x64")
    print(f"PASS: {path} machine=0x{actual:04X}" + (" ARM64EC/ARM64X" if arch == "ARM64EC" else ""))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--require", nargs="+", choices=tuple(EXPECTED), required=True)
    parser.add_argument("--link-exe", type=Path, help="MSVC LINK.exe to verify the linked ARM64X image identity")
    args = parser.parse_args()
    if "ARM64EC" in args.require and (args.link_exe is None or not args.link_exe.is_file()):
        parser.error("--link-exe is required when ARM64EC is requested")
    for arch in args.require:
        for name in ("foo_doujinstyle.dll", "publish_references.exe"):
            path = args.build_dir / arch / name
            if not path.is_file():
                raise RuntimeError(f"Missing {arch} shipped-PE build output: {path}")
            verify(path, arch, link_exe=args.link_exe)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        sys.exit(str(exc))
