"""One-command offline guest preparation, paired anchors and report.

Caller supplies independently authenticated portable base and exact main
test receipt digest. No installer, networking changes, or live requests.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from e2e.host.build_receipt import verify
from e2e.host.runner import verify_source_identity,verify_desktop_and_egress,QualificationError
from e2e.host.paired_report import report
from e2e.host.ui_discovery import write_json

def execute(a):
    source=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()
    verify_source_identity({"source_commit":source})
    verify_desktop_and_egress()
    d=verify(a.receipt,a.receipt_sha256,a.package,a.etw_exporter,source)
    if d["version"]!=Path(__file__).resolve().parents[2].joinpath("VERSION").read_text().strip():
        raise QualificationError("Receipt version mismatch")
    out=a.output.resolve()
    base=a.base.resolve(strict=True)
    if out.exists() or out==base or out in base.parents or base in out.parents:
        raise QualificationError("Fresh output outside base required")
    out.mkdir(parents=True,exist_ok=False)
    prep=out/"prepared"
    results=out/"anchors"
    common=["--package",str(a.package.resolve()),"--package-sha256",d["package_sha256"],
            "--etw-exporter",str(a.etw_exporter.resolve()),"--etw-exporter-sha256",d["exporter_sha256"]]
    state={"schema_version":1,"source_commit":source,"receipt_sha256":a.receipt_sha256,
           "host_e2e_executed":False,"status":"preparation_pending"}
    def save():
        (out/"execution.json").write_text(json.dumps(state,indent=2)+"\n",encoding="utf-8")
    save()
    commands=[
        ("prepare","e2e.host.prepare_guest",common+["--base",str(base),"--output",str(prep),
            "--source-commit",source,"--base-template-sha256",a.base_template_sha256,
            "--host-sha256",a.host_sha256,"--component-path",a.component_path,
            "--foobar-version",a.foobar_version,"--robotframework-version",a.robotframework_version,
            "--flaui-version",a.flaui_version]),
        ("anchors","e2e.host.run_readiness",common+["--manifest",str(prep/"manifest.json"),
            "--template",str(prep/"template"),"--output",str(results),"--paired","--cli-only","--configure-disposable"])]
    for stage,module,args in commands:
        state["status"]=stage+"_started";save()
        try:
            p=subprocess.run([sys.executable,"-m",module,*args],timeout=2100,check=False)
            if p.returncode:
                raise QualificationError(stage+" failed")
        except (OSError,ValueError,subprocess.SubprocessError):
            state["status"]=stage+"_failed";save();raise
        state["status"]=stage+"_completed";save()
    try:
        comparison=report(results)
        write_json(out/"paired-report.json",comparison)
    except (OSError,ValueError,KeyError,TypeError):
        state["status"]="report_failed";save();raise
    state.update(status="paired_common_pcm_anchors_passed",host_e2e_executed=True,
                 actual_output_measured=False,pcm_content_verified=False);save()
    return 0

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ("base","output","receipt","package","etw-exporter"):
        p.add_argument("--"+key,type=Path,required=True)
    for key in ("receipt-sha256","base-template-sha256","host-sha256","component-path","foobar-version",
                "robotframework-version","flaui-version"):
        p.add_argument("--"+key,required=True)
    p.add_argument("--accept-disposable-configuration",action="store_true",required=True,
                   help="Explicitly allow Null Output/sole DSP settings on fresh test copies only")
    try: return execute(p.parse_args())
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as exc:
        print("Guest execution incomplete:",exc,file=sys.stderr);return 2

if __name__=="__main__": raise SystemExit(main())
