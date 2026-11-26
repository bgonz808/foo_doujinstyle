"""Sanitized, observational reproducibility pilot; never a release approval."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import re
import struct
import subprocess
import sys
from zipfile import ZipFile
import zlib

from tools.build_identity import identify
from tools.windows_matrix import validate_runtime
from tools.symbol_identity import verify_pair

FILES = ("foo_doujinstyle.dll", "publish_references.exe",
         "foo_doujinstyle.pdb", "publish_references.pdb", "component")
HEX = re.compile(r"[0-9a-f]{64}")
SDK = "d4c55077336fae81bf8df0259b5b2748fa45ea84132c656ead93eb123cbcdc26"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    h = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return {"sha256": h.hexdigest(), "size": path.stat().st_size}


def pe_structure(data):
    """Bounded PE header and section fingerprints; no PDB paths in evidence."""
    require(len(data) >= 64 and data[:2] == b"MZ", "Invalid DOS header")
    off = struct.unpack_from("<I", data, 60)[0]
    require(off <= len(data) - 24 and data[off:off+4] == b"PE\0\0", "Invalid PE header")
    machine, count, stamp, _, _, optional_size, _ = struct.unpack_from("<HHIIIHH", data, off+4)
    opt = off + 24
    require(0 < count <= 96 and optional_size >= 68 and opt+optional_size+40*count <= len(data),
            "Invalid optional/section headers")
    magic = struct.unpack_from("<H", data, opt)[0]
    require(magic in (0x10b, 0x20b), "Invalid optional header magic")
    sections = []
    for n in range(count):
        at = opt + optional_size + n*40
        name = data[at:at+8].rstrip(b"\0").decode("ascii", errors="replace")
        size, ptr = struct.unpack_from("<II", data, at+16)
        require(ptr <= len(data) and size <= len(data)-ptr, "Section outside file")
        sections.append({"name": name, "size": size,
                         "sha256": sha256(data[ptr:ptr+size]).hexdigest()})
    return {"machine": machine, "coff_timestamp": stamp,
            "checksum": struct.unpack_from("<I", data, opt+64)[0], "sections": sections}


def run_context():
    return {key: os.environ[key] for key in
            ("GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")}


def source_receipt(root):
    source = identify(root)
    ctx = run_context()
    require(source["source_kind"] == "git" and not source["dirty"]
            and source["git_commit"] == ctx["GITHUB_SHA"], "Unexpected source")
    tree = subprocess.check_output(["git", "-C", str(root), "rev-parse",
                                    "--verify", "HEAD^{tree}"], text=True).strip()
    require(re.fullmatch(r"[0-9a-f]{40}", tree), "Invalid Git tree")
    return {"context": ctx, "source": source, "git_tree": tree}


def verify_source_receipt(before, after):
    require(before == after, "Source identity changed during build")
    return after["source"]


def verify_embedded_identity(dll, worker, source):
    # Check the DLL outside the embedded worker bytes, so a correct worker
    # cannot accidentally satisfy the DLL's attribution check.
    require(dll.count(worker) == 1, "Embedded worker missing or ambiguous")
    independent_dll = dll.replace(worker, b"", 1)
    for key in ("git_commit", "source_sha256", "version"):
        marker = source[key].encode("ascii")
        require(marker in worker and marker in independent_dll,
                "Missing independent binary source marker: " + key)


def capture(root, build, runtime, replica, before, inventory, lock_verdict):
    receipt = source_receipt(root)
    source = verify_source_receipt(before, receipt)
    ctx = receipt["context"]
    require(lock_verdict.get("allowed") is True and lock_verdict.get("match") is True,
            "Unverified toolchain lock")
    rt = json.loads(runtime.read_text(encoding="utf-8-sig"))
    validate_runtime(rt)
    directory = build / "x64"
    records = {}
    for name in FILES[:-1]:
        path = directory / name
        records[name] = digest(path)
        require(records[name]["size"] > 0, "Empty artifact: " + name)
        if name.endswith((".dll", ".exe")):
            records[name]["pe"] = pe_structure(path.read_bytes())
    dll = (directory / FILES[0]).read_bytes()
    worker = (directory / FILES[1]).read_bytes()
    verify_embedded_identity(dll, worker, source)
    for binary, pdb in ((FILES[0], FILES[2]), (FILES[1], FILES[3])):
        records[binary]["symbol_identity"] = verify_pair((directory / binary).read_bytes(),
                                                          (directory / pdb).read_bytes(), pdb)
    packages = list(build.glob("*.fb2k-component"))
    require(len(packages) == 1, "Expected one architecture package")
    with ZipFile(packages[0]) as archive:
        dlls = [name for name in archive.namelist() if name.endswith(".dll")]
        require(dlls == ["x64/foo_doujinstyle.dll"] and archive.read(dlls[0]) == dll,
                "Component package differs")
    records["component"] = digest(packages[0])
    return {"schema_version": 1, "context": ctx, "replica": replica, "arch": "x64",
            "source": source, "git_tree": receipt["git_tree"], "clean_before_after": True,
            "embedded_identity_verified": True, "known_inputs": {
                "toolchain_inventory": inventory,
                "toolchain_lock": lock_verdict,
                "checkout_path_sha256": sha256(str(root.resolve()).casefold().encode("utf-8")).hexdigest(),
                "sdk_archive_sha256": SDK, "windows_sdk_version": "10.0.26100.0",
                "msvc_runtime": rt, "python_version": platform.python_version(),
                "zlib_version": zlib.ZLIB_RUNTIME_VERSION,
                "image_os": os.environ.get("ImageOS"), "image_version": os.environ.get("ImageVersion"),
            }, "derived_outputs": {"sqlite_lib": digest(build / "shared-lib" / "x64" / "doujinstyle_sqlite.lib")},
            "files": records}


def compare(a, b, context):
    for record, replica in ((a, "a"), (b, "b")):
        require(record["schema_version"] == 1 and record["context"] == context and
                record["replica"] == replica and record["arch"] == "x64", "Wrong experiment identity")
        source = record["source"]
        require(source["git_commit"] == context["GITHUB_SHA"] and not source["dirty"] and
                HEX.fullmatch(source["source_sha256"]), "Wrong source identity")
        require(set(record["files"]) == set(FILES), "Missing or extra output")
        for row in record["files"].values():
            require(HEX.fullmatch(row["sha256"]) and type(row["size"]) is int and row["size"] > 0,
                    "Invalid artifact record")
    require(a["source"] == b["source"] and a["git_tree"] == b["git_tree"], "Different source coordinates")
    for record in (a, b):
        require(re.fullmatch(r"[0-9a-f]{40}", record["git_tree"]) and
                record["clean_before_after"] is True and record["embedded_identity_verified"] is True,
                "Missing source provenance evidence")
    differences = sorted(key for key in set(a["known_inputs"]) | set(b["known_inputs"])
                         if a["known_inputs"].get(key) != b["known_inputs"].get(key))
    results = {}
    for name in FILES:
        left, right = a["files"][name], b["files"][name]
        results[name] = {"equal": (left["sha256"], left["size"]) == (right["sha256"], right["size"]),
                         "a": left, "b": right}
    status = ("different_known_inputs" if differences else
              "identical_observed_outputs" if all(row["equal"] for row in results.values())
              else "different_observed_outputs")
    return {"schema_version": 1, "status": status, "context": context,
            "source": a["source"], "git_tree": a["git_tree"], "clean_before_after": True,
            "embedded_identity_verified": True, "known_input_differences": differences,
            "known_inputs_a": a["known_inputs"], "known_inputs_b": b["known_inputs"],
            "derived_outputs_a": a.get("derived_outputs", {}), "derived_outputs_b": b.get("derived_outputs", {}),
            "artifacts": results, "scope": "x64 two clean independent hosted jobs; fixed checkout layout",
            "limitations": ["partial toolchain/input closure", "reported hashes, no independent artifact rehash",
                            "PDB GUID/age matched in capture; privacy audit not performed", "not release qualification"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    pre = subs.add_parser("source")
    cap = subs.add_parser("capture")
    cap.add_argument("--before", type=Path, required=True)
    cap.add_argument("--inventory", type=Path, required=True)
    cap.add_argument("--lock-verdict", type=Path, required=True)
    cap.add_argument("--build", type=Path, required=True)
    cap.add_argument("--runtime", type=Path, required=True)
    cap.add_argument("--replica", choices=("a", "b"), required=True)
    cmp = subs.add_parser("compare")
    cmp.add_argument("--a", type=Path, required=True)
    cmp.add_argument("--b", type=Path, required=True)
    for sub in (pre, cap, cmp):
        sub.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "source":
        result = source_receipt(Path(__file__).resolve().parents[1])
    elif args.command == "capture":
        result = capture(Path(__file__).resolve().parents[1], args.build, args.runtime, args.replica,
                         json.loads(args.before.read_text(encoding="utf-8")),
                         json.loads(args.inventory.read_text(encoding="utf-8")),
                         json.loads(args.lock_verdict.read_text(encoding="utf-8")))
    else:
        result = compare(json.loads(args.a.read_text()), json.loads(args.b.read_text()), run_context())
        print("Reproducibility observation:", result["status"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
