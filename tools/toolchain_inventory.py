"""Content inventory of selected Windows build roots, not full process closure."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
from time import monotonic


def tree_digest(root):
    root = Path(root)
    if not root.is_dir():
        raise ValueError("Required toolchain root missing")
    entries = sorted((p for p in root.rglob("*") if p.is_file()),
                     key=lambda p: p.relative_to(root).as_posix().encode("utf-8"))
    if not entries:
        raise ValueError("Empty toolchain root")
    result = sha256(b"DS-TOOLCHAIN-TREE-V1\0")
    seen = set()
    total = 0
    for path in entries:
        relative = path.relative_to(root).as_posix()
        if relative.casefold() in seen or path.is_symlink():
            raise ValueError("Ambiguous toolchain file")
        seen.add(relative.casefold())
        name = relative.encode("utf-8")
        content = sha256()
        size = 0
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(chunk)
                content.update(chunk)
        result.update(len(name).to_bytes(4, "big") + name)
        result.update(size.to_bytes(8, "big") + content.digest())
        total += size
    return {"sha256": result.hexdigest(), "files": len(entries), "bytes": total}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    roots = {}
    started = monotonic()
    for arg in args.root:
        name, sep, path = arg.partition("=")
        if not sep or not name or name in roots:
            raise SystemExit("Invalid inventory root")
        roots[name] = tree_digest(path)
    args.output.write_text(json.dumps(roots, sort_keys=True, indent=2) + "\n",
                           encoding="utf-8")
    print("TOOLCHAIN-INVENTORY roots=" + str(len(roots)) +
          " files=" + str(sum(r["files"] for r in roots.values())) +
          " bytes=" + str(sum(r["bytes"] for r in roots.values())) +
          " seconds=" + str(round(monotonic()-started, 3)))


if __name__ == "__main__":
    main()
