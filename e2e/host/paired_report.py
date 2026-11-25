"""Revalidate four anchor arms and report common-boundary deltas.

One observation per arm, NOT a statistical regression gate, PCM-content
comparison or output latency. Trusted local evidence only; hashes are
consistency bindings, not signatures.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import statistics
from e2e.host.trace import load_events
from e2e.host.paired_evidence import qualify_common,verify_cli_config
from e2e.host.fixture_evidence import qualify as qualify_fixture
from e2e.host.trace import summarize
from e2e.host.runner import sha256,QualificationError
from e2e.host.ui_discovery import write_json

PROFILES=("fixture-local","fixture-direct","fixture-zip-store","fixture-zip-deflate")
STAGES=("robot.play.dispatch.return","host.play.accepted",
        "component.track.open.begin","component.archive.member.ready",
        "component.track.open.end","component.decoder.initialize.begin",
        "component.decoder.initialize.end","component.decoder.first_pcm",
        "component.observer.sdk_play_call","component.observer.play_starting","component.observer.chunk_received",
        "component.observer.first_pcm","output.pcm.first","output.wasapi.rendered")
def require(ok,message):
    if not ok: raise QualificationError(message)

def stage_offsets(events,pid):
    starts=[e for e in events if e["event"]=="robot.play.intent" and e["source"]=="robot"]
    require(len(starts)==1,"Unique play intent required")
    start=starts[0]
    out={}
    for name in STAGES:
        rows=[e for e in events if e["event"]==name and
              (e["run_id"],e["scenario_id"],e["attempt_id"])==(start["run_id"],start["scenario_id"],start["attempt_id"])]
        expected_source=("robot" if name.startswith("robot.") else "component" if name.startswith("component.") else
                         "host" if name.startswith("host.") else "output")
        expected_pid=start["pid"] if expected_source=="robot" else pid
        rows=[e for e in rows if e["source"]==expected_source and e["pid"]==expected_pid]
        value=None
        reason="missing_or_ambiguous"
        if len(rows)==1:
            c=rows[0]["clock"]; a=start["clock"]
            if c["domain"]==a["domain"] and c["frequency_hz"]==a["frequency_hz"] and c["tick"]>=a["tick"]:
                value=(c["tick"]-a["tick"])*1_000_000_000//a["frequency_hz"]
                reason=None
            else: reason="incomparable_or_preintent"
        out[name]={"offset_ns":value,"reason":reason,"observations":len(rows)}
    return out

def read_bound(path,digest):
    require(isinstance(digest,str) and re.fullmatch("[0-9a-f]{64}",digest) and sha256(path)==digest,
            "Evidence digest mismatch: "+path.name)
    return json.loads(path.read_text(encoding="utf-8"))

def report(root):
    ready=json.loads((root/"readiness.json").read_text(encoding="utf-8"))
    require(ready.get("paired_common_pcm_requested") is True,"Paired run required")
    environment=ready.get("execution_environment","offline_guest")
    require(environment in ("offline_guest","hosted_ci_synthetic"),"Unknown execution environment")
    method="foobar2000_sdk_event_add_then_play_ipc" if environment=="hosted_ci_synthetic" else "foobar2000_cli_add_then_play_ipc"
    rows=ready["profiles"]
    require([r["profile"] for r in rows]==list(PROFILES) and all(r["status"]=="passed" for r in rows),"All four arms required")
    result=[]
    identity=None
    controls=None
    runs=set()
    for row in rows:
        profile=row["profile"]; folder=root/profile
        ck=read_bound(folder/"checkpoint.json",row["checkpoint_sha256"])
        require(ck["status"]=="passed" and ck["profile"]==profile and ck["cli_only"] is True and
                ck["playback_method"]==method and ck["output_backend_verified"]=="null_sdk_core_config",
                "CLI configuration not qualified")
        require(ck["source_commit"]==ready["source_commit"] and ck["template"]["component_sha256"]==ready["component_sha256"] and
                ck["etw_exporter_sha256"]==ready["etw_exporter_sha256"],"Build identity mismatch")
        require(ck.get("desktop",{}).get("execution_environment","offline_guest")==environment,"Mixed execution environments")
        if environment=="hosted_ci_synthetic":
            require(ck.get("desktop",{}).get("host_firewall",{}).get("rule_verified") is True,"Hosted process firewall evidence absent")
        host=read_bound(folder/"robot"/"cli-host.json",ck["cli_host_evidence_sha256"])
        require(host["run_id"]==ck["run_id"] and ck["run_id"] not in runs,"Host identity mismatch")
        runs.add(ck["run_id"])
        trace=folder/"trace"
        files={p.name for p in trace.glob("*.jsonl")}
        require(files=={"robot.jsonl","component.jsonl"},"Unexpected trace sources")
        for name in files:
            require(sha256(trace/name)==ck["trace_evidence_sha256"][name],"Trace digest mismatch")
        events=load_events(trace)
        require(all(e["run_id"]==ck["run_id"] for e in events),"Trace run mismatch")
        common=qualify_common(events,local=profile=="fixture-local")
        require(common["qualified"] and common["observer_pid"]==host["pid"] and verify_cli_config(events,host["pid"]),
                "Common boundary/SDK configuration mismatch")
        if environment=="hosted_ci_synthetic":
            offsets=stage_offsets(events,host["pid"])
            require(offsets["component.observer.sdk_play_call"]["offset_ns"] is not None,
                    "SDK Play dispatch evidence missing")
            playlist=[e for e in events if e["event"]=="component.observer.playlist_ready" and e["pid"]==host["pid"]]
            require(len(playlist)==1 and playlist[0]["metrics"].get("sample_count")==1,
                    "Exact playlist evidence missing")
        m=read_bound(folder/"fixture"/"fixture-manifest.json",ck["fixture_manifest_sha256"])
        http=read_bound(folder/"fixture"/"http-requests.json",ck["fixture_http_sha256"])
        require(m["mode"]==profile,"Fixture mode mismatch")
        key=(ck["source_commit"],ck["foobar_version"],ck["template"]["tree_sha256"],
             ck["template"]["host_sha256"],ck["template"]["component_sha256"],ck["playback_method"],
             ck["cache_state"],m["audio_sha256"],m["audio_size_bytes"])
        if identity is None: identity=key
        require(key==identity,"Paired host/member identity mismatch")
        if profile=="fixture-local":
            controls={x["profile"]:x for x in m["archive_controls"]}
            require(set(controls)==set(PROFILES[2:]) and not http,"Local archive or network mismatch")
            path=folder/"fixture"/"local-control.wav"
            require(sha256(path)==m["audio_sha256"] and path.stat().st_size==m["audio_size_bytes"],"Local member changed")
        else:
            require(qualify_fixture(summarize(trace),http,require_range=profile!="fixture-direct",require_window=False)["qualified"],
                    "Remote native/HTTP evidence missing")
            if profile=="fixture-direct":
                require(m["source_sha256"]==m["audio_sha256"],"Direct file mismatch")
            else:
                c=controls[profile]
                require((c["archive_sha256"],c["member"],c["header_offset"],c["audio_sha256"],c["audio_size_bytes"])==
                        (m["archive_sha256"],m["audio_member"],m["member_header_offset"],m["audio_sha256"],m["audio_size_bytes"]),
                        "Archive/member locator mismatch")
        result.append({"profile":profile,"playback_method":ck["playback_method"],"run_id":ck["run_id"],
            "audio_sha256":m["audio_sha256"],"archive_sha256":m.get("archive_sha256"),
            "common_pcm_ns":common["duration_ns"],"stages_from_play_intent":stage_offsets(events,host["pid"]),
            "http_requests":len(http),"http_body_bytes":sum(x["bytes_sent"] for x in http),
            "http_range_requests":sum(x.get("status")==206 for x in http),
            "audio_size_bytes":m["audio_size_bytes"]})
    local=result[0]["common_pcm_ns"]
    for arm in result: arm["delta_vs_local_ns"]=arm["common_pcm_ns"]-local
    return {"schema_version":1,"qualification":"four_single_attempt_common_dsp_anchors",
            "source_commit":ready["source_commit"],"output_backend":"null_sdk_core_config",
            "execution_environment":environment,"independent_egress_isolation_verified":False,
            "playback_method":method,"arms":result,
            "sample_count_per_arm":1,"pcm_content_verified":False,"actual_output_measured":False,
            "statistical_regression_qualified":False}

def summarize_trials(trials, requested):
    """Only fully bound, independently validated pairs contribute to summaries."""
    require(type(requested) is int and 1<=requested<=20 and len(trials)<=requested,"Invalid trial count")
    require(all(t["sample_count_per_arm"]==1 for t in trials),"Single-pair reports required")
    seen=set()
    identity=None
    for t in trials:
        require([a["profile"] for a in t["arms"]]==list(PROFILES),"Unexpected arms")
        key=(t["source_commit"],t["execution_environment"],t["output_backend"],t["playback_method"],
             tuple((a["audio_sha256"],a["archive_sha256"],a["audio_size_bytes"]) for a in t["arms"]))
        if identity is None: identity=key
        require(key==identity,"Mixed trial source or media")
        for a in t["arms"]:
            require(a["run_id"] not in seen,"Repeated host run")
            seen.add(a["run_id"])
    summary={}
    fields=("common_pcm_ns","delta_vs_local_ns","http_requests","http_range_requests","http_body_bytes")
    for profile in PROFILES:
        rows=[a for t in trials for a in t["arms"] if a["profile"]==profile]
        summary[profile]={"sample_count":len(rows)}
        for field in fields:
            values=[a[field] for a in rows]
            summary[profile][field]={"values":values,"min":min(values) if values else None,
                "median":statistics.median(values) if values else None,
                "max":max(values) if values else None}
    return {"schema_version":1,"qualification":"repeated_common_dsp_pairs_not_output_audio",
            "requested_trials":requested,"completed_trials":len(trials),
            "status":"passed" if len(trials)==requested else "incomplete",
            "source_commit":identity[0] if identity else None,"trials":trials,"summary":summary,
            "pcm_content_verified":False,"actual_output_measured":False,
            "network_scope":"loopback server body writes during attempt, not WAN or wire bytes",
            "statistical_regression_qualified":False}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    write_json(a.output,report(a.root))

if __name__=="__main__": main()
