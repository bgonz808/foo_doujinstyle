"""Write native linker SourceLink mappings for tracked project sources."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess

COMMIT = re.compile(r"[a-fA-F0-9]{40}")


def mapping(root: Path, commit: str, canonical_native: bool = False) -> dict:
    if not COMMIT.fullmatch(commit):
        raise ValueError("SourceLink requires a full Git commit SHA")
    prefix = root.resolve().as_posix().rstrip("/")
    if not prefix or "*" in prefix:
        raise ValueError("Invalid source root for SourceLink")
    url = f"https://raw.githubusercontent.com/bgonz808/foo_doujinstyle/{commit.lower()}"
    if canonical_native:
        # Experimental mapped native paths only; generated headers are not
        # claimed to be retrievable, and actual PDB document resolution is a separate gate.
        return {"documents": {"X:/doujin-repro/native/*": url + "/native/*"}}
    return {"documents": {prefix + "/*": url + "/*"}}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--canonical-native", action="store_true")
    args = parser.parse_args()
    root = args.repo_root.resolve(strict=True)
    commit = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"], text=True).strip()
    payload = json.dumps(mapping(root, commit, args.canonical_native), sort_keys=True, separators=(",", ":")) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not args.output.exists() or args.output.read_text(encoding="utf-8") != payload:
        args.output.write_text(payload, encoding="utf-8")


if __name__ == "__main__":
    main()
