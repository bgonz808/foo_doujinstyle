"""Read-only integration check against exact base and first-parent candidates."""
import argparse
from pathlib import Path
import subprocess
from tools.version_policy import parse_revision

LEGACY = "4ec14e1c609ebe20df64bb7beed3e2a588dd07d1"


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True).stdout.strip()


def resolve(root, ref):
    return git(root, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}")


def revision_at(root, commit):
    if commit == LEGACY:
        if git(root, "show", commit + ":VERSION") != "0.0.302":
            raise ValueError("Unexpected migration baseline")
        return 302
    try:
        return parse_revision(git(root, "show", commit + ":REVISION"))
    except subprocess.CalledProcessError as exc:
        raise ValueError("Missing REVISION at " + commit) from exc


def verify(root, base, head):
    base, head = resolve(root, base), resolve(root, head)
    floor = revision_at(root, base)
    value = revision_at(root, head)
    if value <= floor:
        raise ValueError(f"Head revision {value} must exceed base revision {floor}")
    commits = git(root, "rev-list", "--first-parent", head, "^" + base).splitlines()
    if not commits:
        raise ValueError("No new source candidate")
    for commit in commits:
        parent = resolve(root, commit + "^1")
        if revision_at(root, commit) <= revision_at(root, parent):
            raise ValueError("Non-increasing first-parent revision at " + commit)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args()
    print(f"Revision verified: {verify(args.root, args.base, args.head)}")


if __name__ == "__main__":
    main()
