"""Select a bounded analyzer-only closure from an already digest-verified LLVM TAR.

The caller verifies the pinned official archive BEFORE listing or extracting.
No tool executes until the selected file inventory and known executable hashes
have been checked. No intermediate uncompressed TAR is written.
"""
import argparse
from pathlib import Path, PurePosixPath
import re

TOOLS = {"clang-cl.exe", "clang-tidy.exe", "lld-link.exe", "llvm-readobj.exe"}
MAX_FILES = 10000


def require(ok, message):
    if not ok:
        raise ValueError(message)


def safe_name(raw):
    name = raw.rstrip("/")
    p = PurePosixPath(name)
    require(name and not p.is_absolute() and p.as_posix() == name
            and "\\" not in name and ":" not in name
            and all(x not in {"", ".", ".."} and not x.endswith((" ", "."))
                    and not any(ord(c) < 32 for c in x) for x in p.parts),
            "Unsafe archive path: " + raw)
    return p


def select(lines):
    require(0 < len(lines) <= MAX_FILES, "Unexpected archive inventory size")
    seen, roots, chosen, tools = set(), set(), [], set()
    resource_headers = 0
    for raw in lines:
        p = safe_name(raw)
        name = p.as_posix()
        require(name.casefold() not in seen, "Duplicate archive member: " + name)
        seen.add(name.casefold())
        roots.add(p.parts[0])
        if raw.endswith("/") or len(p.parts) < 3:
            continue
        rel = p.parts[1:]
        if len(rel) == 2 and rel[0] == "bin" and (rel[1] in TOOLS or rel[1].lower().endswith(".dll")):
            chosen.append(name)
            if rel[1] in TOOLS:
                tools.add(rel[1])
        elif len(rel) >= 5 and rel[:2] == ("lib", "clang") and rel[3] == "include":
            require(re.fullmatch(r"23(?:[.]1(?:[.]6)?)?", rel[2]), "Unexpected resource directory")
            chosen.append(name)
            resource_headers += 1
    require(len(roots) == 1 and tools == TOOLS and resource_headers > 0,
            "Incomplete or ambiguous LLVM analyzer closure")
    return sorted(chosen)


def verify(root, expected):
    actual, size = set(), 0
    for p in root.rglob("*"):
        require(not p.is_symlink() and not getattr(p, "is_junction", lambda: False)(),
                "Unexpected extracted link")
        if p.is_dir():
            continue
        require(p.is_file(), "Unexpected extracted member type")
        name = p.relative_to(root).as_posix()
        actual.add(name)
        size += p.stat().st_size
    require(actual == set(expected), "Extracted file inventory mismatch")
    print(f"LLVM-METRIC selected_files={len(actual)} extracted_bytes={size} intermediate_tar_bytes=0")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--list-output", type=Path, required=True)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--inventory", type=Path)
    mode.add_argument("--verify-root", type=Path)
    args = p.parse_args()
    if args.inventory:
        chosen = select(args.inventory.read_text(encoding="utf-8").splitlines())
        with args.list_output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write("\n".join(chosen) + "\n")
        print(f"LLVM-METRIC selected_archive_members={len(chosen)}")
    else:
        verify(args.verify_root, args.list_output.read_text(encoding="utf-8").splitlines())


if __name__ == "__main__":
    main()
