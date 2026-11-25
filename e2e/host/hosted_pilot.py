"""Exact-run synthetic playback on a standard ephemeral GitHub Windows runner."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from e2e.host.build_receipt import verify
from e2e.host.runner import (sha256,template_digest,verify_source_identity,
    verify_desktop_and_egress,QualificationError)
from e2e.host.prepare_guest import install_copy
from e2e.host.paired_report import report
from e2e.host.ui_discovery import write_json

VERSION="2.26.1"
ROBOT="7.3.1"
COMPONENT="profile/user-components-x64/foo_doujinstyle/foo_doujinstyle.dll"

def execute(a):
    start=time.monotonic()
    out=a.output.resolve()
    out.mkdir(parents=True,exist_ok=False)
    status={"schema_version":1,"execution_environment":"hosted_ci_synthetic",
            "host_e2e_executed":False,"independent_egress_isolation_verified":False}
    stage_marks=[]
    def save(stage):
        elapsed=time.monotonic()-start
        stage_marks.append({'stage':stage,'elapsed_seconds':elapsed,'previous_stage_seconds':elapsed-(stage_marks[-1]['elapsed_seconds'] if stage_marks else 0)})
        status.update(stage=stage,elapsed_seconds=elapsed,stage_marks=stage_marks)
        (out/"pilot.json").write_text(json.dumps(status,indent=2)+"\n",encoding="utf-8")
    save("preflight")
    try:
        status["desktop"]=verify_desktop_and_egress(hosted_ci=True)
        source=os.environ["GITHUB_SHA"]
        verify_source_identity({"source_commit":source})
        d=verify(a.receipt,a.receipt_sha256,a.package,a.exporter,source,expected_ref=os.environ["GITHUB_REF"])
        if d["workflow_run_id"]!=os.environ["GITHUB_RUN_ID"] or d["workflow_attempt"]!=os.environ["GITHUB_RUN_ATTEMPT"]:
            raise QualificationError("Receipt not from this workflow attempt")
        if d["version"]!=Path("VERSION").read_text().strip():
            raise QualificationError("Version mismatch")
        if importlib.metadata.version("robotframework")!=ROBOT:
            raise QualificationError("Unexpected Robot version")
        base=a.base.resolve(strict=True)
        host_sha=sha256(base/"foobar2000.exe")
        tree,_=template_digest(base)
        status.update(source_commit=source,receipt_sha256=a.receipt_sha256,host_sha256=host_sha,
                      base_template_sha256=tree,python=sys.version,robot_version=ROBOT,
                      flaui_installed_for_test=False)
        save("prepare")
        dest=out/"prepared"
        component=install_copy(base,dest,a.package,d["package_sha256"],COMPONENT,tree,host_sha)
        manifest={"schema_version":1,"project":"foo_doujinstyle","source_commit":source,
          "foobar_version":VERSION,"template_sha256":template_digest(dest)[0],
          "host_path":"foobar2000.exe","host_sha256":host_sha,"component_path":COMPONENT,
          "component_sha256":component,"robotframework_version":ROBOT,"flaui_version":None,
          "output_backend":"null","execution_environment":"hosted_ci_synthetic"}
        manifest_path=out/"manifest.json"
        write_json(manifest_path,manifest)
        save("anchors")
        cmd=[sys.executable,"-m","e2e.host.run_readiness","--hosted-ci","--paired","--cli-only","--configure-disposable",
            "--manifest",str(manifest_path),"--template",str(dest),"--output",str(out/"anchors"),
            "--package",str(a.package.resolve()),"--package-sha256",d["package_sha256"],
            "--etw-exporter",str(a.exporter.resolve()),"--etw-exporter-sha256",d["exporter_sha256"]]
        subprocess.run(cmd,check=True,timeout=1800)
        save("report")
        write_json(out/"paired-report.json",report(out/"anchors"))
        status["host_e2e_executed"]=True
        save("passed")
        return 0
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as exc:
        status["failure"]=str(exc)
        save(status["stage"]+"_failed")
        print("Hosted pilot failed:",exc,file=sys.stderr)
        return 2

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ("base","output","receipt","package","exporter"):
        p.add_argument("--"+k,type=Path,required=True)
    p.add_argument("--receipt-sha256",required=True)
    return execute(p.parse_args())
if __name__=="__main__": raise SystemExit(main())
