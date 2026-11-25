"""Strict single-attempt common-boundary latency; never output audio proof."""
from e2e.host.trace import compare_pair

def qualify_common(events, *, local):
    starts=[x for x in events if x["event"]=="robot.play.intent" and x["source"]=="robot"]
    ends=[x for x in events if x["event"]=="component.observer.first_pcm" and x["source"]=="component"]
    wrong=[x for x in events if x["event"]=="component.decoder.first_pcm"]
    if len(starts)!=1 or len(ends)!=1 or (local and wrong):
        return {"qualified":False,"duration_ns":None,"reason":"missing_or_ambiguous_common_marker"}
    a,b=starts[0],ends[0]
    if (a["run_id"],a["scenario_id"],a["attempt_id"])!=(b["run_id"],b["scenario_id"],b["attempt_id"]) or b["metrics"].get("sample_count",0)<=0:
        return {"qualified":False,"duration_ns":None,"reason":"marker_identity_or_sample_mismatch"}
    result=compare_pair([a,b],"robot.play.intent","component.observer.first_pcm","common_dsp")
    obs=result["observations"][0]
    return {"qualified":obs["status"]=="measured","duration_ns":obs["duration_ns"],
            "reason":obs.get("reason","common_dsp_pcm_not_output"),"observer_pid":b["pid"]}
