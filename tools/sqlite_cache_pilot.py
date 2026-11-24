"""Same-run SQLite-only archive experiment; NOT a persistent trusted cache.

Preserve MSBuild tracking timestamps and exact absolute paths. No executable
payloads, SDK trees, PDBs from proprietary projects, or arbitrary extraction.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import time
from zipfile import ZipFile, ZIP_DEFLATED
from tools.binskim_evidence import digest, write_json
from tools.build_identity import identify
from tools.windows_matrix import validate_runtime

ROOTS = ("native/build/shared-lib/x64", "native/build/shared-obj/sqlite/x64",
         "native/build/generated/x64/sqlite")
INPUTS = ("native/sqlite_shared.vcxproj", "native/sqlite_amalgamation.c", "native/sqlite_config.h",
          "native/sqlite_source.py", "native/sqlite-amalgamation-3540100.zip",
          "native/catalog_schema.sql", "native/third_party/json.hpp")
LIMIT = 64 * 1024 * 1024
SUFFIXES = {".lib", ".obj", ".pdb", ".tlog", ".idb", ".lastbuildstate", ".recipe", ".log", ".c", ".h", ".hpp", ".txt", ".iobj", ".ipdb"}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def context(root, runtime):
    identity = identify(root)
    require(identity["dirty"] is False and identity["git_commit"] == os.environ["GITHUB_SHA"], "Dirty/foreign checkout")
    doc = json.loads(runtime.read_text(encoding="utf-8-sig"))
    validate_runtime(doc)
    return {"schema": 1, "source_commit": identity["git_commit"], "source_sha256": identity["source_sha256"],
            "run": os.environ["GITHUB_RUN_ID"], "attempt": os.environ["GITHUB_RUN_ATTEMPT"],
            "workspace": str(root.resolve()), "image": os.environ.get("ImageVersion", ""),
            "image_os": os.environ.get("ImageOS", ""), "arch": "x64",
            "configuration": "Release", "toolset": "v143", "sdk": "10.0.26100.0",
            "runtime": doc, "inputs": {p: digest(root / p) for p in INPUTS}}


def allowed(name):
    p = PurePosixPath(name)
    require(not p.is_absolute() and p.as_posix() == name and "\\" not in name
            and ":" not in name and all(x not in ("", ".", "..") for x in p.parts)
            and any(name.startswith(prefix + "/") for prefix in ROOTS)
            and p.suffix.lower() in SUFFIXES, "Unexpected archive path: " + name)


def snapshot(root, output, ctx):
    start = time.monotonic()
    require(not output.exists(), "Existing archive")
    rows = {}
    for prefix in ROOTS:
        folder = root / prefix
        require(folder.is_dir(), "Missing cache tree " + prefix)
        for path in sorted(folder.rglob("*")):
            require(not path.is_symlink() and not getattr(path, "is_junction", lambda: False)(), "Unexpected link")
            if path.is_dir():
                continue
            name = path.relative_to(root).as_posix()
            allowed(name)
            require(path.is_file(), "Unexpected file type")
            info = path.stat()
            rows[name] = {"sha256": digest(path), "size": info.st_size, "mtime_ns": info.st_mtime_ns}
    require(sum(row["size"] for row in rows.values()) <= LIMIT and len(rows) <= 128, "Cache bounds")
    require(ROOTS[0] + "/doujinstyle_sqlite.lib" in rows, "Missing SQLite library")
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "x", compression=ZIP_DEFLATED, compresslevel=1) as z:
        for name in sorted(rows):
            z.write(root / name, name)
        z.writestr("manifest.json", json.dumps({"context": ctx, "files": rows}, sort_keys=True))
    print(f"CACHE-METRIC stage=snapshot seconds={time.monotonic()-start:.3f} files={len(rows)} raw_bytes={sum(row['size'] for row in rows.values())} archive_bytes={output.stat().st_size} sha256={digest(output)}")
    return digest(output)


def restore(root, archive, expected_digest, ctx):
    start = time.monotonic()
    require(re.fullmatch("[a-f0-9]{64}", expected_digest) and digest(archive) == expected_digest, "Archive digest mismatch")
    require(all(not (root / p).exists() for p in ROOTS), "Restore requires empty destination")
    with ZipFile(archive) as z:
        names = z.namelist()
        require(len(names) == len(set(names)) and len(names) == len({n.casefold() for n in names})
                and len(names) <= 129 and "manifest.json" in names, "Archive member set")
        require(sum(i.file_size for i in z.infolist()) <= LIMIT + 1024 * 1024, "Archive size limit")
        doc = json.loads(z.read("manifest.json"))
        require(doc["context"] == ctx, "Cache context mismatch")
        rows = doc["files"]
        require(set(names) == set(rows) | {"manifest.json"}, "Manifest member mismatch")
        # Validate every member before materializing any build state.
        data = {}
        for name, row in rows.items():
            allowed(name)
            info = z.getinfo(name)
            require(not stat.S_ISLNK(info.external_attr >> 16), "Archive link")
            value = z.read(name)
            require(len(value) == row["size"] and hashlib.sha256(value).hexdigest() == row["sha256"], "Member digest mismatch")
            require(isinstance(row["mtime_ns"], int) and row["mtime_ns"] > 0, "Invalid timestamp")
            data[name] = value
        for name, value in data.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(value)
            os.utime(path, ns=(rows[name]["mtime_ns"], rows[name]["mtime_ns"]))
            require(digest(path) == rows[name]["sha256"], "Restore verification failed")
    print(f"CACHE-METRIC stage=restore_verify seconds={time.monotonic()-start:.3f} bytes={archive.stat().st_size}")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=("snapshot", "restore"))
    p.add_argument("--archive", type=Path, required=True)
    p.add_argument("--runtime", type=Path, required=True)
    p.add_argument("--sha256")
    a = p.parse_args()
    require(os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("SQLITE_CACHE_PILOT") == "1", "Pilot only")
    root = Path.cwd()
    ctx = context(root, a.runtime)
    if a.mode == "snapshot":
        result = snapshot(root, a.archive, ctx)
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
            stream.write("sha256=" + result + "\n")
    else:
        restore(root, a.archive, a.sha256, ctx)


if __name__ == "__main__":
    main()
