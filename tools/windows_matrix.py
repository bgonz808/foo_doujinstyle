"""Exact-run Windows architecture artifact assembly. No execution of downloaded PE files.

The local lane validates raw PDB-backed SARIF before publication; aggregate rechecks
the sanitized projection, byte identities and complete policy. Same-workflow artifact
transport is the trust boundary, not an independent signature of PR-produced data.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys
from zipfile import ZipFile

from tools.binskim_evidence import collect, digest, file_record, write_json, SCANNER_VERSION, SCANNER_SHA512
from tools.binskim_policy import ARCHES, BINARIES, check, findings_from_report, load_enforcement
from tools.build_identity import identify
from tools.package_component import pack
from tools.verify_pe_targets import machine, EXPECTED
from tools.binskim_diagnostics import inspect_reports

MEMBERS = {"Win32": "foo_doujinstyle.dll", "x64": "x64/foo_doujinstyle.dll",
           "ARM64EC": "arm64ec/foo_doujinstyle.dll"}
LICENSES = {"LICENSE.nlohmann-json": "native/third_party/LICENSE.nlohmann-json",
            "LICENSE.utf8proc": "native/third_party/utf8proc/LICENSE.md"}
HEX = re.compile(r"[a-f0-9]{64}\Z")
ANALYZER_HASHES = {"clang-cl.exe": "0d1fb4523a41c8ec722cdb72909a0a8fff1343700125b3f11d5cf83063a4d7e3",
                   "clang-tidy.exe": "5d6310f76e6f7055a3bafe9bac2d36b4d7cfc58a8c5cad913cc0ec30090b9529"}
ANALYSIS_UNITS = {"foo_doujinstyle.vcxproj": {"component.cpp", "descriptor.cpp", "search.cpp"},
                  "publish_references.vcxproj": {"publish_references.cpp", "descriptor.cpp", "worker_build_identity.cpp"}}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load(path):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 32 * 1024 * 1024,
            "Missing or invalid evidence: " + path.name)
    return json.loads(path.read_text(encoding="utf-8-sig"))


def context(root, commit, run_id, attempt, identity=None):
    identity = identify(root) if identity is None else identity
    require(re.fullmatch(r"[a-f0-9]{40}", commit) and re.fullmatch(r"[0-9]+", run_id)
            and re.fullmatch(r"[1-9][0-9]*", attempt), "Invalid workflow identity")
    require(identity["dirty"] is False and identity["git_commit"] == commit
            and HEX.fullmatch(identity["source_sha256"]), "Source identity mismatch")
    return {"source_commit": commit, "source_sha256": identity["source_sha256"],
            "ci_run_id": run_id, "run_attempt": attempt, "version": identity["base_version"],
            "component_version": identity["version"],
            "policy_sha256": digest(root / "security/bin-policy.json"),
            "baseline_sha256": digest(root / "tools/binskim-baseline.json")}


def validate_runtime(doc):
    require(doc["schema_version"] == 1 and doc["tools_version"] == "14.44.35207", "MSVC version mismatch")
    for arch in ("x86", "x64", "arm64ec"):
        row = doc["architectures"][arch]
        require(row["spectre_dir_present"] is True
                and row["selected_spectre_lib_arch"] == ("arm64" if arch == "arm64ec" else arch)
                and HEX.fullmatch(row["cl_sha256"] or "") and HEX.fullmatch(row["link_sha256"] or "")
                and set(row["runtime_library_sha256"]) == {"msvcrt.lib", "vcruntime.lib", "msvcprt.lib"}
                and all(HEX.fullmatch(v or "") for v in row["runtime_library_sha256"].values()),
                "Incomplete pinned MSVC runtime inventory")


def validate_analysis(doc, commit):
    require(doc["schema_version"] == 1 and doc["kind"] == "windows-frontend-static-analysis"
            and doc["source_commit"] == commit and doc["platform"] == "x64"
            and doc["status"] == "passed" and doc["tool_sha256"] == ANALYZER_HASHES,
            "Windows analysis identity or status mismatch")
    projects = doc["projects"]
    require(len(projects) == len(ANALYSIS_UNITS), "Missing/duplicate analysis project")
    require({row["project"] for row in projects} == set(ANALYSIS_UNITS), "Wrong analysis projects")
    for row in projects:
        expected = ANALYSIS_UNITS[row["project"]]
        require(row["platform"] == "x64" and row["target"] == "x86_64-pc-windows-msvc"
                and row["status"] == "passed" and row["count"] == len(expected)
                and len(row["units"]) == len(expected) and set(row["units"]) == expected
                and row["tracking_log_sha256"] and all(HEX.fullmatch(x) for x in row["tracking_log_sha256"]),
                "Incomplete actual compiler-command analysis")


def expected_files(arch):
    return {"foo_doujinstyle.dll", "publish_references.exe", "msvc-runtime.json",
            "evidence/binary-manifest.json", "evidence/binskim-verdict.json",
            "evidence/failure-summary.md", "mitigation-diagnostics.json",
            *(f"evidence/sarif/{arch}-{name}.sarif" for name in BINARIES)} | (
            {"windows-analysis.json"} if arch == "x64" else set())


def tree_files(folder):
    require(folder.is_dir() and not folder.is_symlink(), "Missing lane directory")
    paths = list(folder.rglob("*"))
    require(not any(p.is_symlink() for p in paths), "Unexpected lane symlink")
    return {p.relative_to(folder).as_posix() for p in paths if p.is_file()}


def seal(root, build, reports, runtime, analysis, output, arch, ctx):
    require(arch in ARCHES and not output.exists(), "Invalid or existing lane output")
    require(load(root / "tools/binskim-baseline.json")["accepted_findings"] == [], "Exceptions forbidden")
    output.mkdir(parents=True)
    verdict = collect(build, reports, root / "tools/binskim-baseline.json", root / "security/bin-policy.json",
                      output / "evidence", version_file=root / "VERSION", source_commit=ctx["source_commit"],
                      run_id=ctx["ci_run_id"], architecture=arch)
    require(verdict["status"] == "pass" and verdict["complete_scans"] == 2, "Local raw two-PE gate failed")
    for name in BINARIES:
        shutil.copyfile(build / arch / name, output / name)
    validate_runtime(load(runtime))
    shutil.copyfile(runtime, output / "msvc-runtime.json")
    if arch == "x64":
        validate_analysis(load(analysis), ctx["source_commit"])
        shutil.copyfile(analysis, output / "windows-analysis.json")
    diagnostics = inspect_reports(reports)
    diagnostics["targets"] = {k: v for k, v in diagnostics["targets"].items() if k.startswith(arch + "/")}
    diagnostics["complete_reports"] = sum(v["present"] and not v.get("parse_error") for v in diagnostics["targets"].values())
    require(diagnostics["complete_reports"] == 2, "Missing local mitigation diagnostics")
    write_json(output / "mitigation-diagnostics.json", diagnostics)
    files = expected_files(arch)
    require(tree_files(output) == files, "Unexpected lane contents")
    write_json(output / "lane.json", {"schema_version": 1, **ctx, "architecture": arch,
                                      "files": {name: file_record(output / name) for name in sorted(files)}})


def aggregate(root, lanes, build, evidence, ctx):
    require(not build.exists() and not evidence.exists(), "Output must be fresh")
    require({p.name for p in lanes.iterdir()} == set(ARCHES), "Missing or foreign architecture lane")
    policy = root / "security/bin-policy.json"
    baseline = root / "tools/binskim-baseline.json"
    require(load(baseline)["accepted_findings"] == [] and load_enforcement(policy).get("BA2024") == "blocking",
            "Blocking/no-exceptions invariant failed")
    reports = build / "sanitized-reports"
    reports.mkdir(parents=True)
    original_rows = {}
    lane_hashes = {}
    diagnostic_rows = {}
    for arch in ARCHES:
        folder = lanes / arch
        manifest = load(folder / "lane.json")
        require(manifest["schema_version"] == 1 and manifest["architecture"] == arch
                and all(manifest.get(k) == v for k, v in ctx.items()), "Lane source/run/attempt/arch mismatch")
        files = expected_files(arch)
        require(set(manifest["files"]) == files and tree_files(folder) == files | {"lane.json"}, "Lane file set mismatch")
        for name in files:
            require(manifest["files"][name] == file_record(folder / name), "Lane file digest mismatch: " + name)
        lane_hashes[arch] = digest(folder / "lane.json")
        local = load(folder / "evidence/binary-manifest.json")
        verdict = load(folder / "evidence/binskim-verdict.json")
        require(local["schema_version"] == 1 and local["source_commit"] == ctx["source_commit"]
                and local["ci_run_id"] == ctx["ci_run_id"] and local["version"] == ctx["version"]
                and local["policy_sha256"] == ctx["policy_sha256"] and local["baseline_sha256"] == ctx["baseline_sha256"]
                and local["scanner"] == {"name": "BinSkim", "version": SCANNER_VERSION, "package_sha512": SCANNER_SHA512}
                and local["package"]["content_verified"] is True
                and verdict["manifest_sha256"] == digest(folder / "evidence/binary-manifest.json")
                and verdict["policy_sha256"] == ctx["policy_sha256"]
                and verdict["status"] == "pass" and verdict["reasons"] == []
                and verdict["complete_scans"] == verdict["expected_scans"] == 2
                and verdict["findings"]["blocking"] == verdict["findings"]["accepted_exceptions"] == 0,
                "Local raw scan gate not qualified")
        require(set(local["targets"]) == {f"{arch}/{n}" for n in BINARIES}, "Local target set mismatch")
        validate_runtime(load(folder / "msvc-runtime.json"))
        if arch == "x64":
            validate_analysis(load(folder / "windows-analysis.json"), ctx["source_commit"])
        diag = load(folder / "mitigation-diagnostics.json")
        require(diag["complete_reports"] == 2 and set(diag["targets"]) == set(local["targets"]),
                "Mitigation diagnostics incomplete")
        diagnostic_rows.update(diag["targets"])
        dest = build / arch
        dest.mkdir()
        for name in BINARIES:
            target = f"{arch}/{name}"
            row = local["targets"][target]
            report = folder / "evidence/sarif" / f"{arch}-{name}.sarif"
            require(row["present"] is True and row["sha256"] == digest(folder / name)
                    and row["size"] == (folder / name).stat().st_size and machine(folder / name) == EXPECTED[arch]
                    and row["sarif"]["status"] == "complete" and HEX.fullmatch(row["sarif"]["raw_sha256"])
                    and row["sarif"]["sanitized_sha256"] == digest(report)
                    and row["sarif"]["output"] == f"sarif/{arch}-{name}.sarif", "PE/report identity mismatch")
            findings_from_report(load(report), target)
            shutil.copyfile(folder / name, dest / name)
            shutil.copyfile(report, reports / report.name)
            original_rows[target] = row
        require(local["targets"][f"{arch}/foo_doujinstyle.dll"]["embedded_worker_match"] is True
                and (dest / BINARIES[1]).stat().st_size > 256
                and (dest / BINARIES[1]).read_bytes() in (dest / BINARIES[0]).read_bytes(), "Embedded worker mismatch")
    package = build / ("foo_doujinstyle-v" + ctx["component_version"] + ".fb2k-component")
    pack(package, [(member, build / arch / BINARIES[0]) for arch, member in MEMBERS.items()]
         + [(name, root / path) for name, path in LICENSES.items()])
    # Re-evaluate the unchanged complete policy from the sanitized projection.
    # The raw-report hashes are restored from the verified local lane records,
    # not misrepresented as hashes of the transported sanitized projection.
    check(build, reports, baseline, policy)
    verdict = collect(build, reports, baseline, policy, evidence, version_file=root / "VERSION",
                      source_commit=ctx["source_commit"], run_id=ctx["ci_run_id"])
    require(verdict["status"] == "pass" and verdict["complete_scans"] == 6, "Aggregate six-PE policy failed")
    manifest_path = evidence / "binary-manifest.json"
    manifest = load(manifest_path)
    for target, row in manifest["targets"].items():
        original = original_rows[target]
        require(row["sha256"] == original["sha256"]
                and row["sarif"]["sanitized_sha256"] == original["sarif"]["sanitized_sha256"],
                "Sanitized scan projection changed")
        row["sarif"]["raw_sha256"] = original["sarif"]["raw_sha256"]
    manifest["matrix"] = {"run_attempt": ctx["run_attempt"], "source_sha256": ctx["source_sha256"],
                          "lane_manifest_sha256": lane_hashes}
    write_json(manifest_path, manifest)
    verdict["manifest_sha256"] = digest(manifest_path)
    write_json(evidence / "binskim-verdict.json", verdict)
    write_json(evidence / "mitigation-diagnostics.json", {"schema_version": 1, "rule": "BA2024",
               "complete_reports": 6, "targets": diagnostic_rows})
    shutil.copyfile(lanes / "x64/windows-analysis.json", evidence / "windows-analysis.json")
    for arch in ARCHES:
        shutil.copyfile(lanes / arch / "msvc-runtime.json", evidence / f"msvc-runtime-{arch}.json")
        shutil.copyfile(lanes / arch / "lane.json", evidence / f"lane-{arch}.json")
    print("MATRIX-METRIC qualified_scans=6 package_sha256=" + digest(package))
    return package


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=("seal", "aggregate"))
    p.add_argument("--arch", choices=ARCHES)
    p.add_argument("--build", type=Path, required=True)
    p.add_argument("--reports", type=Path)
    p.add_argument("--runtime", type=Path)
    p.add_argument("--analysis", type=Path)
    p.add_argument("--lanes", type=Path)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    try:
        root = Path.cwd()
        ctx = context(root, os.environ["GITHUB_SHA"], os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"])
        if a.mode == "seal":
            seal(root, a.build, a.reports, a.runtime, a.analysis, a.output, a.arch, ctx)
        else:
            aggregate(root, a.lanes, a.build, a.output, ctx)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        print("FAIL: matrix evidence: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
