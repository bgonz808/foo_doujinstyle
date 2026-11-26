"""Bounded, same-guest QPC-to-perf-counter calibration and HTTP phase accounting.

Only server body writes, never decoder consumption or TCP acknowledgements.
Request time sums can overlap and are not critical-path durations.
"""
import time
from e2e.host.trace import clock_sample
from e2e.host.archive_touch import _union_size

def calibrate(run_id):
    samples=[]
    for _ in range(9):
        lo=time.perf_counter_ns()
        domain,tick,freq=clock_sample(run_id)
        hi=time.perf_counter_ns()
        ns=tick*1000000000//freq
        samples.append((hi-lo,lo-ns-1,hi-ns+1,domain,freq))
    _,lo,hi,domain,freq=min(samples)
    return {"offset_lo_ns":lo,"offset_hi_ns":hi,"domain":domain,"frequency_hz":freq}

def analyze_phases(events,requests,before,after):
    if not before or not after or before["domain"]!=after["domain"] or before["frequency_hz"]!=after["frequency_hz"]:
        raise ValueError("Clock calibration missing")
    low=max(before["offset_lo_ns"],after["offset_lo_ns"])
    high=min(before["offset_hi_ns"],after["offset_hi_ns"])
    if low>high or high-low>1000000:
        raise ValueError("Clock calibration inconsistent")
    def markers(name):
        return sorted((e for e in events if e["event"]==name),key=lambda e:e["clock"]["tick"])
    def bounds(event):
        clock=event["clock"]
        if clock["domain"]!=before["domain"] or clock["frequency_hz"]!=before["frequency_hz"]:
            raise ValueError("Mixed clock domains")
        ns=clock["tick"]*1000000000//clock["frequency_hz"]
        return ns+low,ns+high
    starts=markers("robot.play.intent")
    ends=markers("component.observer.first_pcm")
    seeks=markers("component.observer.sdk_seek_call")
    matches=markers("component.observer.seek_pcm_match")
    if len(starts)!=1 or len(ends)!=1 or len(seeks)!=4 or len(matches)!=4:
        raise ValueError("Missing phase boundaries")
    def summary(rows):
        content=[r for r in rows if r["status"] in (200,206)]
        by_asset={}
        for r in content:
            lo=r["response_offset"]; size=r["bytes_sent"]
            if type(lo) is not int or type(size) is not int or min(lo,size)<0: raise ValueError("Invalid range")
            by_asset.setdefault(r["asset"],[]).append((lo,lo+size))
        total=sum(r["bytes_sent"] for r in content)
        unique=sum(_union_size(v) for v in by_asset.values())
        return {"request_count":len(rows),"content_response_count":len(content),
                "body_bytes":total,"unique_body_bytes":unique,"duplicate_body_bytes":total-unique,
                "body_amplification":total/unique if unique else None,
                "small_body_requests_le_1024":sum(r["bytes_sent"]<=1024 for r in content),
                "full_status_200_requests":sum(r["status"]==200 for r in content),
                "incomplete_requests":sum(not r["response_complete"] for r in rows),
                "response_elapsed_sum_ns":sum(r["end_ns"]-r["start_ns"] for r in rows),
                "response_active_union_ns":_union_size([(r["start_ns"],r["end_ns"]) for r in rows]),
                "connections":len({r["connection_id"] for r in rows})}
    ids=set()
    for row in requests:
        if row["request_id"] in ids or row["end_ns"]<row["start_ns"]: raise ValueError("Invalid request ledger")
        ids.add(row["request_id"])
    windows=[("pre_play",None,starts[0]),("play_to_pcm",starts[0],ends[0])]
    windows.extend((f"seek_{i+1}",a,b) for i,(a,b) in enumerate(zip(seeks,matches)))
    phases=[]
    for name,start,end in windows:
        begin=bounds(start) if start else None
        finish=bounds(end)
        if begin and begin[1]>finish[0]: raise ValueError("Ambiguous phase order")
        inside=[]; crossing=[]
        for row in requests:
            lo,hi=row["start_ns"],row["end_ns"]
            if hi<=finish[0] and (begin is None or lo>=begin[1]): inside.append(row)
            elif lo<=finish[1] and (begin is None or hi>=begin[0]): crossing.append(row["request_id"])
        phases.append({"phase":name,**summary(inside),"crossing_or_clock_ambiguous_ids":crossing})
    return {"status":"observed","clock_offset_bounds_ns":[low,high],
            "calibration_before":before,"calibration_after":after,"total":summary(requests),"phases":phases,
            "measurement":"server_body_writes_not_decoder_consumption; phase totals exclude crossing requests; sum not critical path"}
