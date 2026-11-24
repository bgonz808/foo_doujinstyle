"""Deterministic tree inventory for a same-job cache experiment, not attestation."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path

def inventory(root):
    root = Path(root).resolve(strict=True)
    paths = sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix())
    def record(p):
        if p.is_symlink() or p.is_junction():
            raise ValueError(f"Unexpected link in cache tree: {p}")
        if p.is_dir():
            return None
        if not p.is_file():
            raise ValueError(f"Unexpected file type: {p}")
        with p.open("rb") as stream:
            from hashlib import file_digest
            digest = file_digest(stream, "sha256").hexdigest()
        return {"path": p.relative_to(root).as_posix(), "size": p.stat().st_size, "sha256": digest}
    with ThreadPoolExecutor(max_workers=4) as pool:
        files = [r for r in pool.map(record, paths) if r is not None]
    if not files:
        raise ValueError("Empty invariant tree")
    return {"schema": 1, "files": files}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    value = inventory(args.root)
    args.output.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"CACHE-METRIC files={len(value['files'])} bytes={sum(r['size'] for r in value['files'])}")

if __name__ == "__main__":
    main()
