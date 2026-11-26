"""Version-addressed synthetic seek benchmark evidence, descriptive not a latency gate.

Use trusted local artifacts, independently obtained receipt/digest for provenance.
No executable loading, network, publication, or source modification.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path
from e2e.host.seek_http_phases import analyze_phases
from e2e.host.trace import load_events

BASE=("fixture-local","fixture-direct","fixture-zip-store","fixture-zip-deflate",
      "stock-local-store","stock-local-deflate","stock-remote-store","stock-remote-deflate",
      "stock-http-store","stock-http-deflate")
DELAYED=("fixture-zip-store","fixture-zip-deflate","stock-remote-store","stock-remote-deflate")
SCENARIOS=tuple((p,0) for p in BASE)+tuple((p,20) for p in DELAYED)
METRICS=("request_count","content_response_count","body_bytes","unique_body_bytes","duplicate_body_bytes",
         "body_amplification","small_body_requests_le_1024","full_status_200_requests","incomplete_requests",
         "response_elapsed_sum_ns","response_active_union_ns","connections")
PROTOCOL="seek-position-oracle-v1-http-phase-v1"

def require(ok,message):
    if not ok: raise ValueError(message)

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def read(path):
    return json.loads(path.read_text(encoding="utf-8"))

def bound(path,expected):
    require(isinstance(expected,str) and re.fullmatch("[0-9a-f]{64}",expected) and digest(path)==expected,
            "Evidence digest mismatch: "+path.name)
    return read(path)

def ratio(a,b):
    return a/b if a is not None and b not in (None,0) else None

def phase_metrics(phase):
    return {**{k:phase[k] for k in METRICS},
            "crossing_requests":len(phase.get("crossing_or_clock_ambiguous_ids",[]))}

def extract(root,receipt):
    suite=read(root/"seek-report.json")
    require(suite["status"]=="passed","Incomplete suite")
    rows=suite["profiles"]
    require(tuple((x["profile"],x["request_delay_ms"]) for x in rows)==SCENARIOS,"Scenario matrix mismatch")
    identity=None; fixtures=None; results={}; seen=set()
    for row in rows:
        p,delay=row["profile"],row["request_delay_ms"]
        name=p+(f"-delay{delay}" if delay else "")
        folder=root/name; ck=read(folder/"checkpoint.json")
        require(ck["status"]==row["status"]=="passed" and row["exit_code"]==0 and ck["robot_exit_code"]==0,
                "Failed scenario")
        require(ck["profile"]==p and ck["synthetic_request_delay_ms"]==delay and
                ck["seek_oracle"]["qualified"] and ck["common_dsp_pcm"]["qualified"] and
                ck["output_backend_verified"]=="null_sdk_core_config" and ck["native_capture_status"]=="exported",
                "Playback evidence mismatch")
        require(ck["run_id"] not in seen,"Repeated run ID");seen.add(ck["run_id"])
        require(ck["source_commit"]==receipt["source_commit"] and
                ck["template"]["component_sha256"]==receipt["component_sha256"] and
                ck["etw_exporter_sha256"]==receipt["exporter_sha256"],"Build receipt mismatch")
        key={"source_commit":ck["source_commit"],"component_sha256":ck["template"]["component_sha256"],
             "host_sha256":ck["template"]["host_sha256"],"template_sha256":ck["template"]["tree_sha256"],
             "foobar_version":ck["foobar_version"],"exporter_sha256":ck["etw_exporter_sha256"],
             "playback_method":ck["playback_method"],"cache_state":ck["cache_state"],
             "output_backend":ck["output_backend_verified"],
             "execution_environment":ck["desktop"]["execution_environment"]}
        if identity is None: identity=key
        require(identity==key,"Mixed build or environment")
        mf=bound(folder/"fixture/fixture-manifest.json",ck["fixture_manifest_sha256"])
        require(mf["kind"]=="seek-position-oracle-v1" and mf["profile"]==p,"Fixture protocol mismatch")
        fixture={k:mf[k] for k in ("audio_sha256","audio_size_bytes","rate","seconds","target_frames","pattern_frames","archive_controls")}
        if fixtures is None: fixtures=fixture
        require(fixtures==fixture,"Mixed fixture bytes")
        http=bound(folder/"fixture/http-requests.json",ck["fixture_http_sha256"])
        for filename in ("robot.jsonl","component.jsonl"):
            require(digest(folder/"trace"/filename)==ck["trace_evidence_sha256"][filename],"Trace digest mismatch")
        events=load_events(folder/"trace")
        require(all(e["run_id"]==ck["run_id"] for e in events),"Trace run mismatch")
        old=ck["seek_http_phases"]
        phase=analyze_phases(events,http,old["calibration_before"],old["calibration_after"])
        require(phase==old,"Phase re-extraction mismatch")
        seek=ck["seek_oracle"]
        require([x["target_frame"] for x in seek["seeks"]]==mf["target_frames"],"Seek target mismatch")
        start=next(e for e in events if e["event"]=="component.observer.sdk_play_call")
        end=next(e for e in events if e["event"]=="component.observer.first_pcm")
        require(start["pid"]==end["pid"] and start["clock"]["domain"]==end["clock"]["domain"] and
                start["clock"]["frequency_hz"]==end["clock"]["frequency_hz"] and end["clock"]["tick"]>=start["clock"]["tick"],
                "SDK clock mismatch")
        startup_sdk=(end["clock"]["tick"]-start["clock"]["tick"])*1000000000//start["clock"]["frequency_hz"]
        total=phase_metrics(phase["total"])
        startup=phase_metrics(next(v for v in phase["phases"] if v["phase"]=="play_to_pcm"))
        archive=next((c for c in mf["archive_controls"] if c["profile"].endswith(p.rsplit("-",1)[-1])),None)
        source_size=archive["archive_bytes"] if archive else mf["audio_size_bytes"]
        remote=bool(total["request_count"])
        results[name]={"profile":p,"request_delay_ms":delay,"run_id":ck["run_id"],
            "first_pcm_intent_ns":ck["common_dsp_pcm"]["duration_ns"],"first_pcm_sdk_ns":startup_sdk,
            "seeks":[{k:x[k] for k in ("target_frame","intent_to_matched_pcm_ns","sdk_to_matched_pcm_ns")} for x in seek["seeks"]],
            "startup":startup,"total":total,"phases":{v["phase"]:phase_metrics(v) for v in phase["phases"]},
            "normalized":{"startup_body_per_encoded_audio_byte":ratio(startup["body_bytes"],mf["audio_size_bytes"]),
                          "total_body_per_encoded_audio_byte":ratio(total["body_bytes"],mf["audio_size_bytes"]),
                          "startup_unique_source_fraction":ratio(startup["unique_body_bytes"],source_size) if remote else None,
                          "total_unique_source_fraction":ratio(total["unique_body_bytes"],source_size) if remote else None},
            "unmeasured":{"process_cpu_ns":None,"physical_disk_read_bytes":None,"physical_disk_write_bytes":None,
                          "peak_working_set_bytes":None,"decoder_consumed_bytes":None,"tcp_wire_bytes":None,
                          "actual_output_latency_ns":None,"underruns":None,"cursor_lock_wait_ns":None}}
    for name,arm in results.items():
        local=results["fixture-local"]
        method=arm["profile"].rsplit("-",1)[-1]
        stock=results.get("stock-local-"+method)
        direct=results["fixture-direct"]
        controls={"extracted_local":local,"direct_http":direct}
        if stock: controls["stock_local_same_method"]=stock
        suffix="-delay20" if arm["request_delay_ms"] else ""
        for label,prefix in (("native_remote_same_method","fixture-zip-"),("stock_virtual_same_method","stock-remote-"),
                             ("stock_native_http_same_method","stock-http-")):
            match=results.get(prefix+method+suffix)
            if match: controls[label]=match
        arm["comparisons"]={}
        for label,control in controls.items():
            arm["comparisons"][label]={
                "control_profile":control["profile"],"control_delay_ms":control["request_delay_ms"],
                "same_delay":arm["request_delay_ms"]==control["request_delay_ms"],
                "startup_sdk_delta_ns":arm["first_pcm_sdk_ns"]-control["first_pcm_sdk_ns"],
                "startup_sdk_ratio":ratio(arm["first_pcm_sdk_ns"],control["first_pcm_sdk_ns"]),
                "total_body_delta":arm["total"]["body_bytes"]-control["total"]["body_bytes"],
                "total_body_ratio":ratio(arm["total"]["body_bytes"],control["total"]["body_bytes"]),
                "request_delta":arm["total"]["request_count"]-control["total"]["request_count"],
                "request_ratio":ratio(arm["total"]["request_count"],control["total"]["request_count"])}
    return {"schema_version":1,"protocol":PROTOCOL,"status":"passed","sample_count_per_scenario":1,
            "statistical_regression_qualified":False,"identity":identity,
            "build":{"version":receipt["version"],"workflow_run_id":receipt["workflow_run_id"],
                     "workflow_attempt":receipt["workflow_attempt"],"ref":receipt["ref"],
                     "package_sha256":receipt["package_sha256"],"release_tag":None},
            "fixtures":fixtures,"archive_template_inventory":suite["archive_template_inventory"],
            "arms":results,"notes":"Server body writes, not decoder consumption/wire bytes. Null Output. Fresh process/profile, OS cache unverified. K=1 descriptive only."}

def compare(baseline,candidate):
    for key in ("schema_version","protocol","fixtures","archive_template_inventory"):
        require(baseline[key]==candidate[key],"Incomparable "+key)
    for key in ("host_sha256","foobar_version","playback_method","cache_state","output_backend","execution_environment"):
        require(baseline["identity"][key]==candidate["identity"][key],"Incomparable "+key)
    require(baseline["status"]==candidate["status"]=="passed" and
            set(baseline["arms"])==set(candidate["arms"]),"Incomparable scenarios")
    def flatten(a):
        result={k:a[k] for k in ("first_pcm_intent_ns","first_pcm_sdk_ns")}
        for phase,values in {"total":a["total"],**a["phases"]}.items():
            for k,v in values.items(): result[phase+"."+k]=v
        for i,seek in enumerate(a["seeks"],1):
            for k in ("sdk_to_matched_pcm_ns","intent_to_matched_pcm_ns"):result[f"seek_{i}.{k}"]=seek[k]
        for k,v in a["normalized"].items():result["normalized."+k]=v
        return result
    arms={}
    for name,a in baseline["arms"].items():
        old,new=flatten(a),flatten(candidate["arms"][name])
        require(set(old)==set(new),"Metric schema mismatch")
        arms[name]={k:{"baseline":v,"candidate":new[k],
                       "delta":new[k]-v if new[k] is not None and v is not None else None,
                       "ratio":ratio(new[k],v)} for k,v in old.items()}
    return {"schema_version":1,"status":"descriptive_only","statistical_regression_qualified":False,
            "baseline":{"identity":baseline["identity"],"build":baseline["build"]},
            "candidate":{"identity":candidate["identity"],"build":candidate["build"]},"arms":arms}

def main():
    p=argparse.ArgumentParser()
    sub=p.add_subparsers(dest="mode",required=True)
    e=sub.add_parser("extract")
    for key in ("root","receipt","output"):e.add_argument("--"+key,type=Path,required=True)
    c=sub.add_parser("compare")
    for key in ("baseline","candidate","output"):c.add_argument("--"+key,type=Path,required=True)
    a=p.parse_args()
    result=extract(a.root,read(a.receipt)) if a.mode=="extract" else compare(read(a.baseline),read(a.candidate))
    with a.output.open("x",encoding="utf-8") as out:json.dump(result,out,indent=2,sort_keys=True);out.write("\n")

if __name__=="__main__":main()
