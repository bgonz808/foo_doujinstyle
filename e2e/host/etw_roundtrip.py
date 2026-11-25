"""Real Windows ETW roundtrip of the standalone native provider.

This proves transport/export/QPC plumbing, never GUI, decoder or output audio.
No installation, networking or security-policy adjustment.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from e2e.host.native_export import export
from hashlib import sha256
from e2e.host.trace import TraceWriter, load_events, summarize, TraceError

PROVIDER="{a4ab84a0-75db-4f17-b7e3-f1aa0d260d4a}"
NATIVE=("component.track.open.begin","component.decoder.initialize.begin",
        "component.decoder.initialize.end","component.decoder.first_pcm","component.track.open.end")


def verify_native(events, run_id, pid):
    native=[x for x in events if x["source"]=="component"]
    if [x["event"] for x in native]!=list(NATIVE):
        raise TraceError("Unexpected native probe milestone sequence")
    if any(x["run_id"]!=run_id or x["pid"]!=pid or
           x["scenario_id"]!="etwprobe" or x["attempt_id"]!="etwprobe-0001" or
           x["clock"]["domain"]!="win-qpc:"+run_id for x in native):
        raise TraceError("Native probe identity mismatch")
    if len({x["clock"]["frequency_hz"] for x in native})!=1 or any(
        a["clock"]["tick"]>b["clock"]["tick"] for a,b in zip(native,native[1:])):
        raise TraceError("Native probe clock mismatch")
    if native[3]["metrics"].get("sample_count")!=480:
        raise TraceError("Native probe sample payload mismatch")


def run(probe, exporter, out):
    if sys.platform!="win32":
        raise TraceError("Actual Windows ETW required")
    if not probe.is_file():
        raise TraceError("Compiled native ETW probe absent")
    out.mkdir(parents=True,exist_ok=False)
    run_id=uuid4().hex
    session="DoujinProbe-"+run_id[:12]
    etl=out/"component.etl"
    env=dict(os.environ,DOUJIN_E2E_RUN_ID=run_id,DOUJIN_E2E_SCENARIO_ID="etwprobe",
             DOUJIN_E2E_ATTEMPT_ID="etwprobe-0001")
    def command(args):
        p=subprocess.run(args,capture_output=True,text=True,timeout=45,check=False)
        if p.stdout:
            print(p.stdout,flush=True)
        if p.stderr:
            print(p.stderr,file=sys.stderr,flush=True)
        if p.returncode:
            raise TraceError(f"{args[0]} failed with exit {p.returncode}")
    command(["logman","start",session,"-p",PROVIDER,"0xffffffffffffffff","0xff","-o",str(etl),"-ets"])
    try:
        with TraceWriter(out/"robot.jsonl",run_id=run_id,scenario_id="etwprobe",
                         attempt_id="etwprobe-0001",source="robot") as writer:
            writer.mark("robot.play.intent")
            with subprocess.Popen([str(probe.resolve())],env=env) as child:
                pid=child.pid
                try:
                    if child.wait(timeout=15)!=0:
                        raise TraceError("Native probe failed")
                except BaseException:
                    child.kill()
                    child.wait()
                    raise
    finally:
        command(["logman","stop",session,"-ets"])
    exporter_sha=sha256(exporter.read_bytes()).hexdigest()
    export(exporter,exporter_sha,etl,out/"component.jsonl",run_id)
    verify_native(load_events(out),run_id,pid)
    report=summarize(out)
    obs=next(x for x in report["latencies"] if x["metric"]=="play.intent_to_first_pcm")["observations"]
    if len(obs)!=1 or obs[0]["status"]!="measured":
        raise TraceError("Python and native QPC domains not comparable")
    result={"qualification":"native_etw_transport_only","host_e2e_executed":False,
            "exporter_sha256":exporter_sha,"native_pid":pid,"run_id":run_id,"synthetic_sample_payload":480,
            "synthetic_dispatch_to_event_ns":obs[0]["duration_ns"],"event_count":report["event_count"]}
    (out/"roundtrip.json").write_text(json.dumps(result,indent=2)+"\n")
    print("ETW-ROUNDTRIP "+json.dumps(result),flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--probe",type=Path,required=True)
    p.add_argument("--exporter",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    run(a.probe,a.exporter,a.output)


if __name__=="__main__":
    main()
