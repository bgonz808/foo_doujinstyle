"""Select a bounded analyzer-only closure from an already digest-verified LLVM TAR.

The caller verifies the pinned official archive BEFORE listing or extracting.
No tool executes until the selected file inventory and known executable hashes
have been checked. No intermediate uncompressed TAR is written.
"""
import argparse
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import time

TOOLS = {"clang-cl.exe", "clang-tidy.exe", "lld-link.exe", "llvm-readobj.exe"}
FORENSIC_TOOLS = TOOLS | {"llvm-pdbutil.exe", "llvm-objdump.exe", "llvm-lib.exe", "llvm-ar.exe"}
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


def wanted(p, tools=TOOLS):
    rel = p.parts[1:]
    if len(rel) == 2 and rel[0] == "bin":
        return rel[1] in tools or rel[1].lower().endswith(".dll")
    if len(rel) >= 4 and rel[:2] == ("lib", "clang") and rel[3] == "include":
        require(re.fullmatch(r"23(?:[.]1(?:[.]6)?)?", rel[2]), "Unexpected resource directory")
        return len(rel) >= 5
    return False


def select(lines, required=TOOLS):
    require(0 < len(lines) <= MAX_FILES, "Unexpected archive inventory size")
    seen, roots, chosen, tools = set(), set(), [], set()
    resource_headers = 0
    for raw in lines:
        p = safe_name(raw)
        name = p.as_posix()
        require(name.casefold() not in seen, "Duplicate archive member: " + name)
        seen.add(name.casefold())
        roots.add(p.parts[0])
        if raw.endswith("/") or not wanted(p, required):
            continue
        chosen.append(name)
        if len(p.parts) == 3 and p.parts[1] == "bin" and p.name in required:
            tools.add(p.name)
        if p.parts[1:3] == ("lib", "clang"):
            resource_headers += 1
    require(len(roots) == 1 and tools == required and resource_headers > 0,
            "Incomplete or ambiguous LLVM analyzer closure")
    return sorted(chosen)


def materialize(stream, root, required=TOOLS):
    """Single forward TAR pass; never materialize unselected bytes or archive links."""
    require(root.is_dir() and not root.is_symlink() and not any(root.iterdir()),
            "Extraction requires an empty destination")
    lines, seen, chosen = [], set(), []
    total = selected = 0
    with tarfile.open(fileobj=stream, mode="r|", bufsize=1024 * 1024) as archive:
        for member in archive:
            p = safe_name(member.name)
            name = p.as_posix()
            require(name.casefold() not in seen, "Duplicate archive member: " + name)
            seen.add(name.casefold())
            lines.append(name + ("/" if member.isdir() else ""))
            total += member.size
            require(len(lines) <= MAX_FILES and 0 <= member.size <= 5 * 1024**3
                    and total <= 5 * 1024**3, "Archive bounds exceeded")
            if member.isdir() or not wanted(p, required):
                continue
            require(member.isreg() and not member.issparse(), "Selected member is not a regular file")
            selected += member.size
            require(selected <= 2 * 1024**3, "Selected closure exceeds bound")
            target = root.joinpath(*p.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            require(target.stat().st_size == member.size, "Truncated selected file")
            chosen.append(name)
    require(sorted(chosen) == select(lines, required), "Selected closure mismatch")
    print(f"LLVM-METRIC archive_members={len(lines)} archive_payload_bytes={total}")
    return sorted(chosen)


def stream_archive(decoder, archive, root, required=TOOLS):
    start = time.monotonic()
    # Binary pipe, not PowerShell text pipeline. Keep stderr visible.
    with subprocess.Popen([str(decoder), "x", "-so", "-bsp0", str(archive)],
                          stdout=subprocess.PIPE) as proc:
        try:
            chosen = materialize(proc.stdout, root, required)
            # tarfile stops at end marker; consume remaining TAR padding and
            # require decoder completion rather than ignoring a broken pipe.
            while proc.stdout.read(1024 * 1024):
                pass
            require(proc.wait() == 0, "Zstandard decoder failed")
        except BaseException:
            proc.kill()
            proc.wait()
            raise
    print(f"LLVM-METRIC stage=stream_selective_extract seconds={time.monotonic()-start:.3f}")
    verify(root, chosen)


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
    p.add_argument("--list-output", type=Path)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--inventory", type=Path)
    mode.add_argument("--verify-root", type=Path)
    mode.add_argument("--archive", type=Path)
    p.add_argument("--decoder", type=Path)
    p.add_argument("--destination", type=Path)
    p.add_argument("--forensics", action="store_true", help="Include explicit forensic and COFF librarian tools")
    args = p.parse_args()
    required = FORENSIC_TOOLS if args.forensics else TOOLS
    if args.archive:
        require(args.decoder and args.destination, "Streaming requires decoder and destination")
        stream_archive(args.decoder, args.archive, args.destination, required)
    elif args.inventory:
        require(args.list_output, "Missing selection output")
        chosen = select(args.inventory.read_text(encoding="utf-8").splitlines(), required)
        with args.list_output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write("\n".join(chosen) + "\n")
        print(f"LLVM-METRIC selected_archive_members={len(chosen)}")
    else:
        require(args.list_output, "Missing selection input")
        verify(args.verify_root, args.list_output.read_text(encoding="utf-8").splitlines())


if __name__ == "__main__":
    main()
