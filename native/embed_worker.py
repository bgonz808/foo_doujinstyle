"""Build-time native worker embedding and evidence; no runtime interpreter."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import sys
import xml.etree.ElementTree as ET

MAX_WORKER = 64 * 1024 * 1024
LEGACY_NAMES = (b"native/search_worker.py", b"native/runtime_contract.py",
                b"native/reference_publisher.py", b"catalog_cache.py", b"__main__.py")


def source_inventory(root: Path) -> dict:
    project = ET.parse(root / "native/foo_doujinstyle.vcxproj")
    commands = [n.get("Command", "") for n in project.iter()
                if n.tag.rsplit("}", 1)[-1] == "Exec"]
    source = (root / "native/search.cpp").read_text(encoding="utf-8")
    embedding = [cmd for cmd in commands if "embed_worker.py" in cmd]
    if (len(embedding) != 1 or "native_worker_payload.inc" not in embedding[0]
            or "publish_references.exe" not in embedding[0]
            or any("pack_search_worker.py" in cmd or "search_worker_payload.inc" in cmd
                   for cmd in commands)
            or '#include "native_worker_payload.inc"' not in source
            or "WriteFile(file, native_worker_payload" not in source
            or any(marker in source for marker in
                   ("python_launcher", "py.exe", "search_worker_payload", "native_worker_offset"))
            or "launch_process(worker, profile, std::move(command)" not in source):
        raise ValueError("Native-only source packaging contract is not satisfied")
    return {"schema_version": 1, "scope": "production-source-packaging",
            "embedding": "raw-native-executable", "python_lines": 0, "python_files": 0,
            "fallback_python_lines": 0, "deprecated_active_lines": 0,
            "unmarked_python_lines": 0, "modules": {},
            "binary_embedding_verified": False}


def read_worker(path: Path) -> bytes:
    if not path.is_file() or not 256 <= path.stat().st_size <= MAX_WORKER:
        raise ValueError("Worker file missing or outside size bounds")
    data = path.read_bytes()
    if not 256 <= len(data) <= MAX_WORKER or data[:2] != b"MZ":
        raise ValueError("Invalid PE worker")
    offset = struct.unpack_from("<I", data, 0x3C)[0]
    if not 64 <= offset <= len(data) - 24 or data[offset:offset+4] != b"PE\x00\x00":
        raise ValueError("Invalid PE signature")
    return data


def render(data: bytes) -> str:
    lines = ["    " + ", ".join(f"0x{byte:02x}" for byte in data[offset:offset+16]) + ","
             for offset in range(0, len(data), 16)]
    return "constexpr unsigned char native_worker_payload[] = {\n" + "\n".join(lines) + "\n};\n"


def embed(header: Path, worker: Path) -> None:
    generated = render(read_worker(worker))
    header.parent.mkdir(parents=True, exist_ok=True)
    if not header.is_file() or header.read_text(encoding="ascii") != generated:
        header.write_text(generated, encoding="ascii")


def verify(header: Path, worker: Path, dll: Path) -> dict:
    data = read_worker(worker)
    if header.read_text(encoding="ascii") != render(data):
        raise ValueError("Generated worker bytes differ from the built executable")
    image = dll.read_bytes()
    if data not in image:
        raise ValueError("Exact worker executable missing from compiled component")
    if any(name in image for name in LEGACY_NAMES):
        raise ValueError("Legacy Python zipapp member name found in compiled component")
    return {"schema_version": 1, "scope": "built-component-embedding",
            "embedding": "raw-native-executable", "binary_embedding_verified": True,
            "known_legacy_member_names_absent": True,
            "worker_bytes": len(data), "worker_sha256": hashlib.sha256(data).hexdigest(),
            "dll_sha256": hashlib.sha256(image).hexdigest(),
            "header_sha256": hashlib.sha256(header.read_bytes()).hexdigest()}


def main() -> None:
    args = sys.argv[1:]
    root = Path(__file__).resolve().parents[1]
    if args == ["--inventory"]:
        print(json.dumps(source_inventory(root), indent=2))
    elif len(args) == 4 and args[0] == "--verify":
        source_inventory(root)
        print(json.dumps(verify(*map(Path, args[1:])), sort_keys=True))
    elif len(args) == 2:
        source_inventory(root)
        embed(*map(Path, args))
    else:
        raise SystemExit("Usage: embed_worker.py HEADER WORKER | --verify HEADER WORKER DLL | --inventory")


if __name__ == "__main__":
    main()
