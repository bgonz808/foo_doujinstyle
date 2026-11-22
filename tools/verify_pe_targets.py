"""Verify PE machine types for every claimed architecture (component and worker)."""
from __future__ import annotations
import argparse
from pathlib import Path
import struct
import sys

EXPECTED = {"Win32": 0x014C, "x64": 0x8664, "ARM64EC": 0xA641}


def machine(path: Path) -> int:
    data = path.read_bytes()
    if len(data) < 0x40 or data[:2] != b"MZ":
        raise ValueError(f"{path}: invalid DOS header")
    offset = struct.unpack_from("<I", data, 0x3C)[0]
    if offset < 0x40 or offset + 6 > len(data) or data[offset:offset+4] != b"PE" + bytes(2):
        raise ValueError(f"{path}: invalid PE header")
    return struct.unpack_from("<H", data, offset + 4)[0]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--require", nargs="+", choices=tuple(EXPECTED), required=True)
    args = parser.parse_args()
    for arch in args.require:
        for name in ("foo_doujinstyle.dll", "publish_references.exe"):
            path = args.build_dir / arch / name
            if not path.is_file():
                raise RuntimeError(f"Missing {arch} shipped-PE build output: {path}")
            actual = machine(path)
            if actual != EXPECTED[arch]:
                raise RuntimeError(f"{path}: expected PE machine 0x{EXPECTED[arch]:04X}, got 0x{actual:04X}")
            print(f"PASS: {path} machine=0x{actual:04X}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as exc:
        sys.exit(str(exc))
