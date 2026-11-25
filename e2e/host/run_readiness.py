"""Run the initial host anchors in separate processes; stop on first failure.

No installation or isolation policy changes. The caller obtains the source-
matched test package and independently verifies its expected package hash.
An unqualified test package is never a production release.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

from e2e.host.native_export import verify_exporter
from e2e.host.runner import (QualificationError, checked_digest, read_manifest,
                             sha256, verify_source_identity, verify_desktop_and_egress,
                             verify_test_dependencies, check_template)

PROFILES=("window","fixture-direct","fixture-zip-store","fixture-zip-deflate")
MEMBER="x64/foo_doujinstyle.dll"


def verify_package(package, expected, manifest):
    if not package.name.endswith("-e2e-trace.fb2k-component"):
        raise QualificationError("Expected test-only ETW package")
    if sha256(package)!=checked_digest(expected,"package_sha256"):
        raise QualificationError("Test package digest mismatch")
    with ZipFile(package) as z:
        dlls=[n for n in z.namelist() if n.lower().endswith(".dll")]
        if dlls!=[MEMBER] or z.getinfo(MEMBER).file_size>128*1024**2:
            raise QualificationError("Unexpected test package DLL inventory")
        h=hashlib.sha256()
        with z.open(MEMBER) as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b""):
                h.update(chunk)
        if h.hexdigest()!=manifest["component_sha256"]:
            raise QualificationError("Installed DLL not bound to test package")


def no_host():
    p=subprocess.run(["powershell.exe","-NoProfile","-NonInteractive","-Command",
        "$ErrorActionPreference='Stop'; @(Get-Process -Name foobar2000 -ErrorAction SilentlyContinue).Count"],
        capture_output=True,text=True,timeout=15,check=False)
    if p.returncode!=0 or p.stdout.strip()!="0":
        raise QualificationError("Existing or uninspectable foobar2000 process; stop safely before continuing")


def execute(args):
    manifest=read_manifest(args.manifest)
    root=args.template.resolve(strict=True)
    out=args.output.resolve()
    if out.exists() or root==out or root in out.parents or out in root.parents:
        raise QualificationError("New output directory outside template required")
    verify_source_identity(manifest)
    verify_desktop_and_egress()
    verify_test_dependencies(manifest)
    check_template(root,manifest)
    verify_package(args.package,args.package_sha256,manifest)
    verify_exporter(args.etw_exporter,args.etw_exporter_sha256)
    no_host()
    out.mkdir(parents=True,exist_ok=False)
    report={"schema_version":1,"kind":"host-readiness-anchors",
            "source_commit":manifest["source_commit"],"package_sha256":args.package_sha256.lower(),
            "component_sha256":manifest["component_sha256"],"etw_exporter_sha256":args.etw_exporter_sha256.lower(),
            "profiles":[{"profile":p,"status":"not_run"} for p in PROFILES],
            "output_pcm_validated":None,"screenshots_visual_content_verified":None}
    summary=out/"readiness.json"
    def save():
        summary.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    save()
    for row in report["profiles"]:
        try:
            no_host()
            profile=row["profile"]
            dest=out/profile
            cmd=[sys.executable,"-m","e2e.host.runner","--manifest",str(args.manifest.resolve()),
                "--template",str(root),"--output",str(dest),"--profile",profile]
            if profile!="window":
                cmd += ["--etw-exporter",str(args.etw_exporter.resolve()),"--etw-exporter-sha256",args.etw_exporter_sha256]
            run=subprocess.run(cmd,check=False,timeout=420)
            row["exit_code"]=run.returncode
            checkpoint=dest/"checkpoint.json"
            if not checkpoint.is_file():
                raise QualificationError("Missing checkpoint")
            record=json.loads(checkpoint.read_text(encoding="utf-8"))
            if (record.get("source_commit")!=manifest["source_commit"] or record.get("profile")!=profile
                or record.get("template",{}).get("component_sha256")!=manifest["component_sha256"]
                or run.returncode!=0 or record.get("status")!="passed"):
                raise QualificationError("Anchor failed or evidence identity mismatch")
            no_host()
            row.update(status="passed",checkpoint_sha256=sha256(checkpoint),
                       fixture_qualification=record.get("fixture_qualification"),
                       screenshots=record.get("screenshots"))
            save()
        except (OSError,ValueError,subprocess.SubprocessError) as exc:
            row.update(status="failed",reason=str(exc))
            save()
            return 1
    return 0


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ("manifest","template","output","package"):
        p.add_argument("--"+name,type=Path,required=True)
    p.add_argument("--package-sha256",required=True)
    p.add_argument("--etw-exporter",type=Path,required=True)
    p.add_argument("--etw-exporter-sha256",required=True)
    args=p.parse_args()
    try:
        return execute(args)
    except (OSError,ValueError,subprocess.SubprocessError) as exc:
        print("Readiness not qualified:",exc,file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
