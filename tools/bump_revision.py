"""Explicit source mutation; never invoked by compilation or CI."""
import argparse
from pathlib import Path
from tools.build_identity import write_if_changed
from tools.verify_revision import resolve, revision_at
from tools.version_policy import MAX_REVISION, read_policy


def bump(root, base):
    root = Path(root)
    current = read_policy(root).revision
    floor = revision_at(root, resolve(root, base))
    next_revision = max(current, floor) + 1
    if next_revision > MAX_REVISION:
        raise ValueError("Revision limit reached")
    write_if_changed(root / "REVISION", f"{next_revision}\n".encode("ascii"))
    return next_revision


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--base", default="origin/main",
                        help="Locally known integration ref; fetch explicitly first")
    args = parser.parse_args()
    print(f"Allocated locally: {bump(args.root, args.base)}")


if __name__ == "__main__":
    main()
