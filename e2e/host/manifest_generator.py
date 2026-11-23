"""Generate a digest-pinned manifest for an isolated portable foobar2000 E2E VM.

No downloader, installer, security-policy adjustment or test execution.
Supply a pre-provisioned template with Null Output selected; the generator
records the REQUEST, never asserts that foobar2000 actually selected it.
"""
from __future__ import annotations

import argparse
from importlib import metadata
import json
from pathlib import Path
import re
import subprocess

from e2e.host.runner import (QualificationError, checked_digest, read_manifest,
                             relative_path, sha256, template_digest)

RELEASE = re.compile(r"\d+\.\d+(?:\.\d+)*\Z")


def build_spec(template: Path, component_path: str, foobar_version: str,
               source_commit: str, output_backend: str, packages: dict[str, str]) -> dict:
    template = template.resolve(strict=True)
    if not (template / "portable_mode_enabled").is_file():
        raise QualificationError("An isolated portable_mode_enabled template is required")
    component = relative_path(component_path)
    host = Path("foobar2000.exe")
    if not RELEASE.fullmatch(foobar_version) or not re.fullmatch(r"[a-f0-9]{40}", source_commit):
        raise QualificationError("Full source commit and exact foobar2000 version required")
    if output_backend not in {"not_specified", "null", "pcm-capture", "wasapi-loopback"}:
        raise QualificationError("Unsupported requested output backend")
    if not all(RELEASE.fullmatch(v) for v in packages.values()):
        raise QualificationError("Pinned Robot/FlaUI version strings required")
    digest, _ = template_digest(template)
    if not (template / host).is_file() or not (template / component).is_file():
        raise QualificationError("Component or foobar2000 executable missing from template")
    return {
        "schema_version": 1, "project": "foo_doujinstyle",
        "source_commit": source_commit, "foobar_version": foobar_version,
        "template_sha256": checked_digest(digest, "template_sha256"),
        "host_path": host.as_posix(), "host_sha256": sha256(template / host),
        "component_path": component.as_posix(),
        "component_sha256": sha256(template / component),
        "robotframework_version": packages["robotframework"],
        "flaui_version": packages["robotframework-flaui"],
        "output_backend": output_backend,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--component-path", required=True,
                        help="Relative DLL path inside the portable template")
    parser.add_argument("--foobar-version", required=True)
    parser.add_argument("--output-backend", choices=("null", "pcm-capture", "wasapi-loopback", "not_specified"),
                        default="null")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Manifest output already exists; no overwrite permitted")
    source = Path(__file__).resolve().parents[2]
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=source,
                              capture_output=True, text=True, timeout=10, check=True).stdout.strip()
    changed = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                             cwd=source, capture_output=True, text=True, timeout=10, check=True)
    if changed.stdout.strip():
        parser.error("Refuse manifest generation from dirty source")
    try:
        packages = {name: metadata.version(name) for name in
                    ("robotframework", "robotframework-flaui")}
        spec = build_spec(args.template, args.component_path, args.foobar_version, revision,
                          args.output_backend, packages)
    except (metadata.PackageNotFoundError, QualificationError) as exc:
        parser.error(str(exc))
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(spec, handle, indent=2, sort_keys=True)
        handle.write("\n")
    read_manifest(args.output)
    print(f"Manifest: {args.output} (source {revision}, output requested: {args.output_backend})")
    print("Source/template integrity established; UI, Null Output, PCM and network isolation NOT verified.")


if __name__ == "__main__":
    main()
