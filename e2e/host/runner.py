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
from uuid import uuid4

from e2e.host.trace import summarize, load_events
from e2e.host.paired_evidence import qualify_common
from e2e.host.native_export import export, verify_exporter
from e2e.host.etw_session import EtwSession
from e2e.host.fixture_assets import make_fixture, write_reference, extract_local_control
from e2e.host.fixture_evidence import qualify as qualify_fixture
from e2e.host.screenshot_evidence import collect as collect_screenshots
from tools.media_matrix import fixture_server

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
    if doc.get("output_backend", "not_specified") not in (
            "not_specified", "null", "pcm-capture", "wasapi-loopback"):
        raise QualificationError("Unsupported output backend; qualify the sink explicitly")
    for field in ("robotframework_version", "flaui_version"):
        if field == "flaui_version" and doc.get(field) is None and doc.get("execution_environment") == "hosted_ci_synthetic":
            continue
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


def verify_desktop_and_egress(*, hosted_ci=False) -> dict:
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
    if not isinstance(adapters, list):
        raise QualificationError("Invalid adapter evidence")
    if hosted_ci:
        if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
            raise QualificationError("Hosted pilot restricted to GitHub-hosted Actions")
    elif adapters:
        raise QualificationError("Network-connected guest; offline host run denied")
    return {"session_id": session_id.value, "screen": [width, height],
            "guest_network_adapters_up": len(adapters),
            "execution_environment": "hosted_ci_synthetic" if hosted_ci else "offline_guest",
            "independent_egress_isolation_verified": False}


def verify_test_dependencies(manifest: dict, *, hosted_ci=False) -> dict:
    packages = {"robotframework": manifest["robotframework_version"],
                "robotframework-flaui": manifest["flaui_version"]}
    if hosted_ci:
        packages.pop("robotframework-flaui")  # CLI suite has no FlaUI import.
    for name, expected in packages.items():
        try:
            found = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise QualificationError(f"Missing offline-provisioned test package: {name}") from exc
        if found != expected:
            raise QualificationError(f"Test dependency mismatch for {name}: expected {expected}, found {found}")
    return packages


def run(manifest_path: Path, template: Path, destination: Path, *,
        profile: str = "window", exporter: Path | None = None,
        exporter_sha256: str | None = None, require_common_pcm: bool = False, cli_only: bool = False, configure_disposable: bool = False, hosted_ci: bool = False, external_reference: Path | None = None, seek_test: bool = False, request_delay_ms: int = 0) -> int:
    live = profile in ("live-local","live-remote")
    stock = profile in ("stock-local-store","stock-local-deflate","stock-remote-store","stock-remote-deflate","stock-http-store","stock-http-deflate")
    stock_http = profile in ("stock-http-store","stock-http-deflate")
    local = profile in ("fixture-local","live-local","stock-local-store","stock-local-deflate")
    if type(request_delay_ms) is not int or request_delay_ms not in (0,20) or (request_delay_ms and not seek_test):
        raise QualificationError("Only bounded synthetic seek request delay is supported")
    if stock and not seek_test:
        raise QualificationError("Stock archive control requires synthetic seek mode")
    if seek_test and (live or profile=="window" or not hosted_ci or not cli_only or not configure_disposable):
        raise QualificationError("Position oracle requires disposable hosted synthetic playback")
    if live:
        from e2e.host.live_provider_smoke import authorized
        if not hosted_ci or not authorized() or external_reference is None or not external_reference.is_file():
            raise QualificationError("Live playback requires explicitly authorized hosted lane and reference")
    elif external_reference is not None:
        raise QualificationError("External track forbidden for synthetic fixtures")
    if not stock and profile not in ("window", "fixture-local", "fixture-direct", "fixture-zip-store", "fixture-zip-deflate","live-local","live-remote"):
        raise QualificationError("Unsupported host E2E profile")
    if configure_disposable and not cli_only:
        raise QualificationError("Configuration mutation requires disposable CLI-only mode")
    if cli_only and (profile=="window" or not require_common_pcm):
        raise QualificationError("CLI mode requires playback and common PCM")
    if profile != "window":
        verify_exporter(exporter, exporter_sha256)
    manifest = read_manifest(manifest_path)
    if hosted_ci != (manifest.get("execution_environment") == "hosted_ci_synthetic") or (hosted_ci and not (cli_only and configure_disposable and require_common_pcm)):
        raise QualificationError("Hosted synthetic mode requires explicit CLI-only disposable manifest and flags")
    if destination.exists():
        raise QualificationError("Destination must be new and empty")
    template = template.resolve(strict=True)
    dest_abs = destination.resolve(strict=False)
    if template == dest_abs or template in dest_abs.parents or dest_abs in template.parents:
        raise QualificationError("Results directory overlaps immutable template")
    verify_source_identity(manifest)
    desktop = verify_desktop_and_egress(hosted_ci=hosted_ci)
    packages = verify_test_dependencies(manifest, hosted_ci=hosted_ci)
    provenance = check_template(template, manifest)
    destination.mkdir(parents=True, exist_ok=False)
    portable = destination / "portable"
    result_dir = destination / "robot"
    result_dir.mkdir()
    trace_dir = destination / "trace"
    trace_dir.mkdir()
    run_id = uuid4().hex
    if profile != "window" and manifest.get("output_backend") != "null":
        raise QualificationError("Fixture playback requires explicitly requested Null Output")
    # Ignore no files: copying the full pin prevents implicit user-profile fallback.
    shutil.copytree(template, portable)
    if check_template(portable, manifest) != provenance:
        raise QualificationError("Portable copy differed from pinned source")
    exe = portable / manifest["host_path"]
    env = os.environ.copy()
    if hosted_ci:
        from e2e.host.hosted_firewall import constrain_host
        if profile=="live-remote":
            desktop["host_firewall"]={"rule_verified":False,"scope":"application_https_provider_policy_only"}
        else:
            desktop["host_firewall"] = constrain_host(exe, run_id)
        if live:
            desktop["execution_environment"]="hosted_ci_live_provider"
        allowed = {"systemroot","windir","systemdrive","comspec","path","pathext","temp","tmp","userprofile","appdata","localappdata","programdata","programfiles","programfiles(x86)","commonprogramfiles","commonprogramfiles(x86)","username","computername","os","number_of_processors","processor_architecture","public","allusersprofile","homedrive","homepath"}
        env = {k:v for k,v in env.items() if k.lower() in allowed}
    for key in list(env):
        if key.lower() in ("http_proxy", "https_proxy", "all_proxy", "no_proxy"):
            env.pop(key)
    env.update({
        "DOUJIN_E2E_RUN_ID": run_id,
        "DOUJIN_E2E_TRACE_PATH": str(trace_dir / "robot.jsonl"),
        "DOUJIN_E2E_SCENARIO_ID": "hostlaunch" if profile == "window" else "fixtureplay",
        "DOUJIN_E2E_ATTEMPT_ID": "hostlaunch-0001" if profile == "window" else "fixtureplay-0001",
        "DOUJIN_E2E_HOST_EXE": str(exe),
        "DOUJIN_E2E_CLI_MODE": "1" if cli_only else "0",
        "DOUJIN_E2E_SDK_IPC": "1" if hosted_ci else "0",
        "DOUJIN_E2E_CONFIGURE_DISPOSABLE": "1" if configure_disposable else "0",
        "DOUJIN_E2E_SOURCE_COMMIT": manifest["source_commit"],
        "DOUJIN_E2E_COMPONENT_SHA256": manifest["component_sha256"],
        "DOUJIN_E2E_FOOBAR_VERSION": manifest["foobar_version"],
    })
    if seek_test:
        env["DOUJIN_E2E_SEEK_ORACLE"]="1"
    if live:
        env["DOUJIN_LIVE_PROVIDER_SMOKE"]="1"
        env["DOUJIN_LIVE_HTTP_LOG"]=str(destination/"live-http.jsonl")
    suite = "fixture_cli.robot" if cli_only else ("foobar.robot" if profile == "window" else "fixture.robot")
    cmd = [sys.executable, "-m", "robot", "--name", "doujinstyle.host",
           "--outputdir", str(result_dir), "--output", "output.xml",
           "--xunit", "junit.xml", "--variable", f"HOST_EXE:{exe}",
           str(Path(__file__).with_name(suite))]
    status = "not_run"
    exit_code = None
    native_capture = "not_requested"
    capture_error = None
    requests = []
    seek_clock_before=seek_clock_after=None
    if profile == "window":
        try:
            proc = subprocess.run(cmd, check=False, env=env, timeout=180)
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            exit_code = 124
    else:
        if seek_test:
            from e2e.host.seek_fixture import make_seek_fixture, write_seek_reference
            media,assets=make_seek_fixture()
        else:
            media, assets = (b"",{}) if live else make_fixture()
        all_archives = assets
        if live:
            assets={"unused":b"x"}
        elif local:
            assets = {}
        elif stock:
            key="fixture-zip-"+profile.rsplit("-",1)[-1]+".zip"
            assets={key:assets[key]}
        elif profile == "fixture-direct":
            assets = {"fixture-direct.wav": media}
        else:
            assets = {profile + ".zip": assets[profile + ".zip"]}
        fixture_dir = destination / "fixture"
        fixture_dir.mkdir()
        # The host and all network operations run inside the same offline VM.
        with fixture_server(assets, latency_ms=request_delay_ms, **({'asset_limit':16*1024*1024} if seek_test else {})) as (base, events):
            if live:
                reference=external_reference.resolve(strict=True)
                (fixture_dir/"fixture-manifest.json").write_text(json.dumps({"kind":"live-reference",
                    "sha256":sha256(reference)}),encoding="utf-8")
            elif seek_test:
                reference=write_seek_reference(fixture_dir,base,profile,media,all_archives)
            else:
                reference = (extract_local_control(fixture_dir, all_archives, media) if profile == "fixture-local"
                             else write_reference(fixture_dir, base, profile, media, next(iter(assets.values()))))
            if stock:
                env["DOUJIN_E2E_STOCK_ARCHIVE"]="local" if local else "http" if stock_http else "remote"
                if not local:
                    env["DOUJIN_E2E_STOCK_URL"]=base+"/asset/"+next(iter(assets))+"?mode=normal"
            env["DOUJIN_E2E_EXPECTED_TRACK"] = str(reference.resolve())
            env["DOUJIN_E2E_FIXTURE_REF"] = str(reference)
            if seek_test:
                from e2e.host.seek_http_phases import calibrate
                seek_clock_before=calibrate(run_id)
            session_name = "DoujinE2E-" + run_id[:12]
            etl = trace_dir / "component.etl"
            session = EtwSession(session_name, etl, trace_dir / "etw-session.json")
            if not session.start():
                raise QualificationError("Native ETW trace session could not be started")
            native_capture = "started"
            try:
                try:
                    if live:
                        with (result_dir/"private-runner.log").open("xb") as log:
                            proc=subprocess.run(cmd,check=False,env=env,timeout=180,stdout=log,stderr=subprocess.STDOUT)
                    else:
                        proc = subprocess.run(cmd, check=False, env=env, timeout=180)
                    exit_code = proc.returncode
                except subprocess.TimeoutExpired:
                    exit_code = 124
            finally:
                native_capture = "stopped" if session.stop() else "stop_failed"
            if seek_test:
                seek_clock_after=calibrate(run_id)
            with events.condition:
                requests = [dict(x) for x in events]
            (fixture_dir / "http-requests.json").write_text(
                json.dumps(requests, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if native_capture == "stopped":
            try:
                export(exporter, exporter_sha256, etl, trace_dir / "component.jsonl", run_id)
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                native_capture = "export_failed"
                capture_error = str(exc)
            else:
                native_capture = "exported"
        else:
            capture_error = "Native ETW capture did not stop cleanly"
    junit = result_dir / "junit.xml"
    if junit.is_file():
        cases = list(ET.parse(junit).getroot().iter("testcase"))
        status = "passed" if (cases and exit_code == 0 and not any(
            any(x.tag in ("failure", "error", "skipped") for x in case)
            for case in cases)) else "failed"
    else:
        status = "failed"
    ui_states = {p.parent.name: sha256(p) for p in sorted((result_dir / "ui").glob("*/state.json"))}
    expected_states = {"main-window"} if profile == "window" else {"main-window", "playlist-added", "post-play-dispatch"}
    if status == "passed" and not cli_only and set(ui_states) != expected_states:
        status = "failed"
    screenshots = collect_screenshots(result_dir)
    if status == "passed" and not cli_only and screenshots["status"] != "present":
        status = "failed"  # An unillustrated host pass is not screenshot-qualified.

    try:
        trace_report = summarize(trace_dir)
        observed = set(trace_report["event_names"])
        required = {"clock.sync", "robot.host.launch.intent",
                    "component.observer.config_ready" if cli_only else "robot.host.window.visible"}
        if status == "passed" and not required.issubset(observed):
            status = "failed"  # A green GUI verdict without its required trace is not qualified.
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        trace_report = {"schema_version": 1, "status": "invalid",
                        "reason": type(exc).__name__, "event_count": None}
        status = "failed"
    fixture_qualification = None
    common_pcm = None
    seek_evidence=None
    stock_evidence=None
    if profile != "window" and native_capture == "exported":
        try:
            state_path = result_dir/"cli-host.json" if cli_only else result_dir/"ui"/"main-window"/"state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            all_events=load_events(trace_dir)
            common_pcm = qualify_common(all_events,local=local or stock)
            if cli_only:
                from e2e.host.paired_evidence import verify_cli_config
                if not verify_cli_config(all_events,state.get("pid")):
                    common_pcm={"qualified":False,"duration_ns":None,"reason":"sdk_configuration_evidence_missing"}
            if (state.get("run_id") != run_id or type(state.get("pid")) is not int or
                    (common_pcm.get("qualified") and common_pcm.get("observer_pid") != state["pid"])):
                common_pcm = {"qualified":False,"duration_ns":None,"reason":"observer_host_pid_mismatch"}
        except (OSError,ValueError,KeyError,TypeError):
            common_pcm = {"qualified":False,"duration_ns":None,"reason":"invalid_common_evidence"}
    if profile != "window" and (require_common_pcm or profile == "fixture-local"):
        if not common_pcm or not common_pcm["qualified"] or (local and requests):
            status = "failed"
    if profile not in ("window", "fixture-local") and not live and not stock:
        fixture_qualification = qualify_fixture(trace_report, requests, require_range=profile != "fixture-direct",require_window=not cli_only)
        if status == "passed" and (
                native_capture != "exported" or not fixture_qualification["qualified"]):
            status = "failed"
    if stock:
        from e2e.host.seek_fixture import qualify_stock
        try:
            modules=json.loads((result_dir/"archive-modules.json").read_text(encoding="utf-8"))
            stock_evidence=qualify_stock(load_events(trace_dir),requests,modules,local=local,native_http=stock_http)
        except (OSError,ValueError,KeyError,TypeError):
            stock_evidence={"qualified":False,"reason":"missing_stock_route_evidence"}
        if not stock_evidence["qualified"]: status="failed"
    if seek_test:
        from e2e.host.seek_fixture import qualify_seeks
        try:
            state=json.loads((result_dir/"cli-host.json").read_text(encoding="utf-8"))
            seek_evidence=qualify_seeks(load_events(trace_dir),state["pid"],requests,local=local)
        except (OSError,ValueError,KeyError,TypeError):
            seek_evidence={"qualified":False,"reason":"invalid_or_missing_seek_trace"}
        if not seek_evidence["qualified"]: status="failed"
    seek_http_phases=None
    if seek_test:
        from e2e.host.seek_http_phases import analyze_phases
        try:
            seek_http_phases=analyze_phases(load_events(trace_dir),requests,seek_clock_before,seek_clock_after)
        except (OSError,ValueError,KeyError,TypeError):
            seek_http_phases={"status":"invalid_or_missing_phase_evidence"}
        if seek_http_phases["status"]!="observed": status="failed"
    (trace_dir / "summary.json").write_text(
        json.dumps(trace_report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    evidence = {p.name: sha256(p) for p in result_dir.glob("*.xml")}
    trace_hashes = {p.name: sha256(p) for p in trace_dir.glob("*") if p.is_file()}
    checkpoint = {
        "schema_version": 1, "kind": "host-e2e-smoke", "project": manifest["project"],
        "profile": profile, "status": status,
        "qualification": ("host-window-only-not-component-qualified" if profile == "window"
                          else ("live-common-pcm-not-content-or-output-qualified" if live else "host-decoder-and-http-not-audio-output-qualified")),
        "source_commit": manifest["source_commit"], "foobar_version": manifest["foobar_version"],
        "template": provenance, "desktop": desktop, "test_dependencies": packages,
        "run_id": run_id, "robot_exit_code": exit_code,
        "etw_exporter_sha256": exporter_sha256,
        "native_capture_status": native_capture, "native_capture_error": capture_error,
        "fixture_qualification": fixture_qualification,
        "common_dsp_pcm": common_pcm,
        "common_dsp_required": require_common_pcm or profile=="fixture-local",
        "cli_only": cli_only,
        "configure_disposable_requested": configure_disposable,
        "playback_method": None if profile=="window" else ("foobar2000_sdk_event_add_then_play_ipc" if hosted_ci else "foobar2000_cli_add_then_play_ipc"),
        "timing_controller": "robot_sdk_event_ipc" if hosted_ci else ("robot_cli" if cli_only else "robot_flaui_with_cli_playback"),
        "playback_source_type": None if profile=="window" else ("stock_archive_local" if stock and local else "stock_archive_native_http" if stock_http else "stock_archive_virtual_outer" if stock else "native_filesystem" if local else "doujinstyle_djt"),
        "cache_state": "fresh_portable_copy_new_host_os_cache_unverified",
        "cli_host_evidence_sha256": sha256(result_dir/"cli-host.json") if cli_only and (result_dir/"cli-host.json").is_file() else None,
        "fixture_manifest_sha256": sha256(destination/"fixture"/"fixture-manifest.json") if profile!="window" else None,
        "fixture_http_sha256": sha256(destination/"fixture"/"http-requests.json") if profile!="window" else None,
        "evidence_sha256": evidence, "trace_evidence_sha256": trace_hashes,
        "ui_state_evidence_sha256": ui_states,
        "screenshots": screenshots, "screenshots_visual_content_verified": None,
        "trace": {"status": trace_report["status"], "event_count": trace_report.get("event_count"),
                  "measured_latency_count": sum(
                      sum(o["status"] == "measured" for o in m["observations"])
                      for m in trace_report.get("latencies", []))},
        "output_backend_requested": manifest.get("output_backend", "not_specified"),
        "output_backend_verified": "null_sdk_core_config" if cli_only and common_pcm and common_pcm["qualified"] else None,
        "first_party_dll_line_coverage": None, "first_party_worker_line_coverage": None,
        "audible_output_verified": None, "native_seek_latency_ms": None,
        "seek_oracle":seek_evidence,"stock_archive":stock_evidence,
        "seek_http_phases":seek_http_phases,"synthetic_request_delay_ms":request_delay_ms,
    }
    (destination / "checkpoint.json").write_text(json.dumps(checkpoint, indent=2) + "\n", encoding="utf-8")
    return 0 if status == "passed" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", choices=("window", "fixture-local", "fixture-direct", "fixture-zip-store", "fixture-zip-deflate"),
                        default="window", help="One qualified scenario/attempt per process")
    parser.add_argument("--hosted-ci",action="store_true")
    parser.add_argument("--cli-only",action="store_true")
    parser.add_argument("--configure-disposable",action="store_true")
    parser.add_argument("--require-common-pcm",action="store_true")
    parser.add_argument("--etw-exporter", type=Path)
    parser.add_argument("--etw-exporter-sha256")
    args = parser.parse_args()
    try:
        return run(args.manifest, args.template, args.output, profile=args.profile, exporter=args.etw_exporter,
                   exporter_sha256=args.etw_exporter_sha256,require_common_pcm=args.require_common_pcm,cli_only=args.cli_only,configure_disposable=args.configure_disposable,hosted_ci=args.hosted_ci)
    except (OSError, QualificationError, ValueError, subprocess.SubprocessError, ET.ParseError) as exc:
        print(f"Host E2E not qualified: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
