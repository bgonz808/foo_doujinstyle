"""Fail-closed foobar2000 host E2E launcher; no runtime component dependency.

Requires an already provisioned, offline Windows desktop guest and a pinned
portable foobar2000 template. Does NOT provision network isolation or measure
code coverage. See docs/HOST_E2E.md.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

HEX64 = re.compile(r"[0-9a-fA-F]{64}\Z")
HEX40 = re.compile(r"[0-9a-fA-F]{40}\Z")
ID = re.compile(r"[a-z][a-z0-9_-]{2,63}\Z")


class QualificationError(ValueError):
    """Missing or invalid host qualification prerequisite."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative_path(value: object) -> Path:
    if not isinstance(value, str) or "\\" in value or "\x00" in value:
        raise QualificationError("Use a POSIX-style relative manifest path")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or any(p in ("", ".", "..") for p in value.split("/")) or ":" in value:
        raise QualificationError("Unsafe component or host relative path")
    return Path(*path.parts)


def checked_digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not HEX64.fullmatch(value) or set(value.lower()) == {"0"}:
        raise QualificationError(f"{label}: supply a real SHA-256 digest")
    return value.lower()


def read_manifest(path: Path) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or doc.get("schema_version") != 1:
        raise QualificationError("Unsupported host manifest schema")
    if not isinstance(doc.get("project"), str) or not ID.fullmatch(doc["project"]):
        raise QualificationError("Invalid project id")
    if not isinstance(doc.get("source_commit"), str) or not HEX40.fullmatch(doc["source_commit"]):
        raise QualificationError("Full source commit SHA required")
    if not isinstance(doc.get("foobar_version"), str) or not doc["foobar_version"].strip():
        raise QualificationError("Exact foobar2000 version required")
    for field in ("template_sha256", "host_sha256", "component_sha256"):
        doc[field] = checked_digest(doc.get(field), field)
    for field in ("host_path", "component_path"):
        doc[field] = relative_path(doc.get(field))
    if doc["host_path"].name.lower() != "foobar2000.exe":
        raise QualificationError("Host path must identify foobar2000.exe")
    for field in ("robotframework_version", "flaui_version"):
        if not isinstance(doc.get(field), str) or not re.fullmatch(r"\d+\.\d+(?:\.\d+)*", doc[field]):
            raise QualificationError(f"Pin {field} to an installed exact version")
    return doc


def template_digest(root: Path) -> tuple[str, int]:
    """Canonical inventory hash: path plus content SHA-256 for every file.

    Reject links/junctions and special files instead of silently traversing an
    attacker-controlled or mutable location outside the template.
    """
    if not root.is_dir() or root.is_symlink() or (hasattr(root, "is_junction") and root.is_junction()):
        raise QualificationError("Portable template must be a real directory")
    digest = hashlib.sha256()
    count = 0

    def walk(folder: Path, rel: str = "") -> None:
        nonlocal count
        for entry in sorted(os.scandir(folder), key=lambda ent: ent.name):
            entry_path = Path(entry.path)
            if entry.is_symlink() or (hasattr(entry_path, "is_junction") and entry_path.is_junction()):
                raise QualificationError("Portable template contains a symlink or junction")
            name = f"{rel}/{entry.name}" if rel else entry.name
            if entry.is_dir(follow_symlinks=False):
                digest.update(b"D\0" + name.encode("utf-8") + b"\0")
                walk(entry_path, name)
            elif entry.is_file(follow_symlinks=False):
                digest.update(b"F\0" + name.encode("utf-8") + b"\0" + sha256(entry_path).encode("ascii") + b"\0")
                count += 1
            else:
                raise QualificationError("Special files forbidden in portable template")

    walk(root)
    return digest.hexdigest(), count


def check_template(root: Path, manifest: dict) -> dict:
    marker = root / "portable_mode_enabled"
    if not marker.is_file():
        raise QualificationError("Missing portable_mode_enabled marker; would risk personal profile")
    for prefix in ("host", "component"):
        target = root / manifest[f"{prefix}_path"]
        if not target.is_file() or sha256(target) != manifest[f"{prefix}_sha256"]:
            raise QualificationError(f"{prefix} binary absent or digest mismatch")
    digest, count = template_digest(root)
    if digest != manifest["template_sha256"]:
        raise QualificationError("Portable template full-tree digest mismatch")
    return {"tree_sha256": digest, "file_count": count,
            "host_sha256": manifest["host_sha256"], "component_sha256": manifest["component_sha256"]}


def verify_source_identity(manifest: dict) -> None:
    root = Path(__file__).resolve().parents[2]
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                            capture_output=True, text=True, timeout=10, check=False)
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root,
                           capture_output=True, text=True, timeout=10, check=False)
    if (commit.returncode != 0 or dirty.returncode != 0 or dirty.stdout.strip() or
            commit.stdout.strip().lower() != manifest["source_commit"].lower()):
        raise QualificationError("Runner checkout must be clean and equal manifest source_commit")


def verify_desktop_and_egress() -> dict:
    if sys.platform != "win32":
        raise QualificationError("Host UI tests require a real Windows interactive guest")
    # Session zero / detached desktops cannot provide meaningful GUI evidence.
    session_id = wintypes.DWORD()
    kernel32 = ctypes.windll.kernel32
    if not kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session_id)) or session_id.value == 0:
        raise QualificationError("Host tests may not run in Session 0")
    user32 = ctypes.windll.user32
    user32.GetSystemMetrics.restype = ctypes.c_int
    width, height = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    if width < 1024 or height < 720 or not user32.GetForegroundWindow():
        raise QualificationError("Interactive desktop absent or unsuitable")
    # Strict offline guest: ALL guest network adapters must be disconnected.
    # Loopback still works; the connected CI orchestration runner lives outside.
    ps = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
         "$ErrorActionPreference='Stop'; @(Get-NetAdapter | Where-Object { $_.Status -eq 'Up' } | Select-Object -ExpandProperty Name) | ConvertTo-Json -Compress"],
        capture_output=True, text=True, timeout=25, check=False,
    )
    if ps.returncode != 0:
        raise QualificationError("Cannot inspect guest network adapters (fail closed)")
    try:
        adapters = json.loads(ps.stdout.strip() or "[]")
    except json.JSONDecodeError as exc:
        raise QualificationError("Unexpected network adapter probe output") from exc
    if isinstance(adapters, str):
        adapters = [adapters]
    if adapters is None:
        adapters = []
    if not isinstance(adapters, list) or adapters:
        raise QualificationError("Network-connected guest; offline host run denied")
    return {"session_id": session_id.value, "screen": [width, height],
            "guest_network_adapters_up": 0}


def verify_test_dependencies(manifest: dict) -> dict:
    packages = {"robotframework": manifest["robotframework_version"],
                "robotframework-flaui": manifest["flaui_version"]}
    for name, expected in packages.items():
        try:
            found = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise QualificationError(f"Missing offline-provisioned test package: {name}") from exc
        if found != expected:
            raise QualificationError(f"Test dependency mismatch for {name}: expected {expected}, found {found}")
    return packages


def run(manifest_path: Path, template: Path, destination: Path) -> int:
    manifest = read_manifest(manifest_path)
    if destination.exists():
        raise QualificationError("Destination must be new and empty")
    template = template.resolve(strict=True)
    dest_abs = destination.resolve(strict=False)
    if template == dest_abs or template in dest_abs.parents or dest_abs in template.parents:
        raise QualificationError("Results directory overlaps immutable template")
    verify_source_identity(manifest)
    desktop = verify_desktop_and_egress()
    packages = verify_test_dependencies(manifest)
    provenance = check_template(template, manifest)
    destination.mkdir(parents=True, exist_ok=False)
    portable = destination / "portable"
    result_dir = destination / "robot"
    result_dir.mkdir()
    # Ignore no files: copying the full pin prevents implicit user-profile fallback.
    shutil.copytree(template, portable)
    if check_template(portable, manifest) != provenance:
        raise QualificationError("Portable copy differed from pinned source")
    exe = portable / manifest["host_path"]
    env = os.environ.copy()
    for key in list(env):
        if key.lower() in ("http_proxy", "https_proxy", "all_proxy", "no_proxy"):
            env.pop(key)
    cmd = [sys.executable, "-m", "robot", "--name", "doujinstyle.host",
           "--outputdir", str(result_dir), "--output", "output.xml",
           "--xunit", "junit.xml", "--variable", f"HOST_EXE:{exe}",
           str(Path(__file__).with_name("foobar.robot"))]
    status = "not_run"
    exit_code = None
    try:
        proc = subprocess.run(cmd, check=False, env=env, timeout=180)
        exit_code = proc.returncode
        junit = result_dir / "junit.xml"
        if not junit.is_file():
            raise QualificationError("Robot did not create JUnit: host unqualified")
        cases = list(ET.parse(junit).getroot().iter("testcase"))
        if not cases:
            raise QualificationError("No executed host test cases")
        status = "passed" if exit_code == 0 and not any(
            any(x.tag in ("failure", "error", "skipped") for x in c) for c in cases) else "failed"
    except subprocess.TimeoutExpired:
        status = "failed"
        exit_code = 124
    evidence = {p.name: sha256(p) for p in result_dir.glob("*.xml")}
    checkpoint = {
        "schema_version": 1, "kind": "host-e2e-smoke", "project": manifest["project"],
        "status": status, "qualification": "host-window-only-not-component-qualified",
        "source_commit": manifest["source_commit"], "foobar_version": manifest["foobar_version"],
        "template": provenance, "desktop": desktop, "test_dependencies": packages,
        "robot_exit_code": exit_code, "evidence_sha256": evidence,
        "first_party_dll_line_coverage": None, "first_party_worker_line_coverage": None,
        "audible_output_verified": None, "native_seek_latency_ms": None,
    }
    (destination / "checkpoint.json").write_text(json.dumps(checkpoint, indent=2) + "\n", encoding="utf-8")
    return 0 if status == "passed" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        return run(args.manifest, args.template, args.output)
    except (OSError, QualificationError, ValueError, subprocess.SubprocessError, ET.ParseError) as exc:
        print(f"Host E2E not qualified: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
