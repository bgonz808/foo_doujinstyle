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
import time
import sys
from zipfile import ZipFile

from e2e.host.native_export import verify_exporter
from e2e.host.runner import (QualificationError, checked_digest, read_manifest,
                             sha256, verify_source_identity, verify_desktop_and_egress,
                             verify_test_dependencies, check_template)

PROFILES=("window","fixture-direct","fixture-zip-store","fixture-zip-deflate")
PAIRED_PROFILES=("fixture-local","fixture-direct","fixture-zip-store","fixture-zip-deflate")
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
    from e2e.host.windows_process import process_ids
    if process_ids("foobar2000.exe"):
        raise QualificationError("Existing foobar2000 process; stop safely before continuing")


def execute(args):
    if args.configure_disposable and not args.cli_only:
        raise QualificationError("Disposable configuration requires CLI-only paired mode")
    if args.cli_only and not args.paired:
        raise QualificationError("CLI-only readiness requires paired mode")
    manifest=read_manifest(args.manifest)
    root=args.template.resolve(strict=True)
    out=args.output.resolve()
    if out.exists() or root==out or root in out.parents or out in root.parents:
        raise QualificationError("New output directory outside template required")
    verify_source_identity(manifest)
    if args.hosted_ci != (manifest.get('execution_environment') == 'hosted_ci_synthetic') or (args.hosted_ci and not (args.paired and args.cli_only and args.configure_disposable)):
        raise QualificationError('Hosted synthetic execution requires explicit paired CLI configuration')
    verify_desktop_and_egress(hosted_ci=args.hosted_ci)
    verify_test_dependencies(manifest,hosted_ci=args.hosted_ci)
    check_template(root,manifest)
    verify_package(args.package,args.package_sha256,manifest)
    verify_exporter(args.etw_exporter,args.etw_exporter_sha256)
    no_host()
    out.mkdir(parents=True,exist_ok=False)
    profiles=PAIRED_PROFILES if args.paired else PROFILES
    report={"schema_version":1,"kind":"host-readiness-anchors",
            "source_commit":manifest["source_commit"],"package_sha256":args.package_sha256.lower(),
            "component_sha256":manifest["component_sha256"],"etw_exporter_sha256":args.etw_exporter_sha256.lower(),
            "profiles":[{"profile":p,"status":"not_run"} for p in profiles],
            "execution_environment":"hosted_ci_synthetic" if args.hosted_ci else "offline_guest",
            "paired_common_pcm_requested":args.paired,"output_pcm_validated":None,"screenshots_visual_content_verified":None}
    summary=out/"readiness.json"
    def save():
        summary.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    save()
    order=report["profiles"]
    trial_index=getattr(args,"trial_index",0)
    if trial_index:
        if not args.paired or not 0<=trial_index<20:
            raise QualificationError("Invalid paired trial index")
        rotation=trial_index%len(order)
        order=order[rotation:]+order[:rotation]
    report["execution_order"]=[row["profile"] for row in order]
    save()
    for row in order:
        anchor_start=time.monotonic()
        row["status"]="started"
        save()
        try:
            no_host()
            profile=row["profile"]
            dest=out/profile
            cmd=[sys.executable,"-m","e2e.host.runner","--manifest",str(args.manifest.resolve()),
                "--template",str(root),"--output",str(dest),"--profile",profile]
            if profile!="window":
                cmd += ["--etw-exporter",str(args.etw_exporter.resolve()),"--etw-exporter-sha256",args.etw_exporter_sha256]
            if args.paired:
                cmd += ["--require-common-pcm"]
            if args.cli_only:
                cmd += ["--cli-only"]
            if args.hosted_ci:
                cmd += ["--hosted-ci"]
            if args.configure_disposable:
                cmd += ["--configure-disposable"]
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
            if args.paired and not (record.get("common_dsp_pcm") or {}).get("qualified"):
                raise QualificationError("Common PCM marker required in every paired arm")
            no_host()
            row.update(status="passed",wall_seconds=time.monotonic()-anchor_start,checkpoint_sha256=sha256(checkpoint),
                       fixture_qualification=record.get("fixture_qualification"),
                       common_dsp_pcm=record.get("common_dsp_pcm"),
                       fixture_manifest_sha256=record.get("fixture_manifest_sha256"),
                       screenshots=record.get("screenshots"))
            print("HOST-ANCHOR "+json.dumps({"profile":profile,"trial_index":trial_index,"wall_seconds":row["wall_seconds"],"common_dsp_pcm":row["common_dsp_pcm"]}),flush=True)
            save()
        except (OSError,ValueError,subprocess.SubprocessError) as exc:
            row.update(status="failed",wall_seconds=time.monotonic()-anchor_start,reason=str(exc))
            print("HOST-ANCHOR-FAILED "+json.dumps(row),flush=True)
            save()
            return 1
    return 0


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ("manifest","template","output","package"):
        p.add_argument("--"+name,type=Path,required=True)
    p.add_argument("--trial-index",type=int,default=0)
    p.add_argument("--hosted-ci",action="store_true")
    p.add_argument("--configure-disposable",action="store_true")
    p.add_argument("--cli-only",action="store_true")
    p.add_argument("--paired",action="store_true",help="Require common DSP observation in all four arms")
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
