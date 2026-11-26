"""Large deterministic PCM with unique sample-position oracle; synthetic only."""
from array import array
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import random
import sys
import wave
import zipfile
from e2e.host.fixture_assets import MEMBER,ARCHIVE_METHODS

RATE=32000
SECONDS=120
TARGETS=(90,10,90,45)
PATTERN_FRAMES=32

def sample(frame):
    x=(frame*0x9e3779b1)&0xffffffff
    x^=x>>16
    x=(x*0x85ebca6b)&0xffffffff
    x^=x>>13
    return (x&0x7fff)-16384

def make_seek_fixture():
    values=array("h",(sample(i) for i in range(RATE*SECONDS)))
    if sys.byteorder!="little": values.byteswap()
    out=BytesIO()
    with wave.open(out,"wb") as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(RATE)
        wav.writeframes(values.tobytes())
    media=out.getvalue()
    pad=random.Random(287).randbytes(512*1024)
    assets={}
    for profile,method in ARCHIVE_METHODS.items():
        stream=BytesIO()
        with zipfile.ZipFile(stream,"w") as z:
            z.writestr(zipfile.ZipInfo("disc/prefix.bin"),pad,compress_type=zipfile.ZIP_STORED)
            z.writestr(zipfile.ZipInfo(MEMBER),media,compress_type=method,compresslevel=6)
            z.writestr(zipfile.ZipInfo("disc/suffix.bin"),pad[::-1],compress_type=zipfile.ZIP_STORED)
        assets[profile+".zip"]=stream.getvalue()
    return media,assets

def write_seek_reference(folder,base,profile,media,assets):
    controls=[]
    for name,method in ARCHIVE_METHODS.items():
        archive=assets[name+".zip"]
        with zipfile.ZipFile(BytesIO(archive)) as z:
            member=z.getinfo(MEMBER)
            data=z.read(member)
            if data!=media or member.compress_type!=method: raise ValueError("Exact seek fixture identity mismatch")
            controls.append({"profile":name,"archive_bytes":len(archive),"archive_sha256":sha256(archive).hexdigest(),
                "member_header_offset":member.header_offset,"member_compressed_bytes":member.compress_size,
                "member_uncompressed_bytes":member.file_size,"zip_method":method,"crc32":member.CRC})
    if profile.startswith("stock-"):
        key="fixture-zip-"+profile.rsplit("-",1)[-1]+".zip"
        path=folder/"stock-control.zip"
        path.write_bytes(assets[key])
        if sha256(path.read_bytes()).hexdigest()!=sha256(assets[key]).hexdigest():
            raise ValueError("Stock archive identity mismatch")
    elif profile=="fixture-local":
        path=folder/"local-control.wav"
        path.write_bytes(data)
    else:
        path=folder/(profile+".djt")
        direct=profile=="fixture-direct"
        url=base+"/asset/"+profile+(".wav" if direct else ".zip")+"?mode=normal"
        member_line="" if direct else "MEMBER="+MEMBER+"\n"
        path.write_text("DOUJIN-TRACK 1\nURL="+url+"\n"+member_line+"FILENAME=Remote tone.wav\nTITLE=Position oracle\n"
            +"DURATION_MS="+str(SECONDS*1000)+"\nSAMPLERATE="+str(RATE)+"\nCHANNELS=1\nBITSPERSAMPLE=16\n",encoding="utf-8")
    (folder/"fixture-manifest.json").write_text(json.dumps({"kind":"seek-position-oracle-v1","profile":profile,
        "audio_sha256":sha256(media).hexdigest(),"audio_size_bytes":len(media),"rate":RATE,"seconds":SECONDS,
        "target_frames":[x*RATE for x in TARGETS],"pattern_frames":PATTERN_FRAMES,"archive_controls":controls},indent=2),encoding="utf-8")
    return path

def qualify_seeks(events,pid,requests,*,local):
    def events_for(name,source):
        return sorted([x for x in events if x["event"]==name and x["source"]==source],key=lambda x:x["clock"]["tick"])
    intents=events_for("robot.seek.intent","robot")
    calls=events_for("component.observer.sdk_seek_call","component")
    matches=events_for("component.observer.seek_pcm_match","component")
    callbacks=events_for("component.observer.seek_callback","component")
    observed=[]
    def report(ok,reason):
        return {"qualified":ok,"reason":reason,"target_frames":[x*RATE for x in TARGETS],
                "pattern_frames":PATTERN_FRAMES,"seeks":observed,"audio_endpoint_verified":False,
                "request_count":len(requests),"body_bytes":sum(x.get("bytes_sent",0) for x in requests)}
    if any(len(x)!=len(TARGETS) for x in (intents,calls,matches,callbacks)):
        return report(False,"missing_or_ambiguous_seek_markers")
    if local and requests: return report(False,"local_control_network_activity")
    prior=None
    for target,intent,call,match,callback in zip(TARGETS,intents,calls,matches,callbacks):
        rows=(intent,call,match,callback)
        clock=intent["clock"]
        ids={(x["run_id"],x["scenario_id"],x["attempt_id"],x["clock"]["domain"],x["clock"]["frequency_hz"]) for x in rows}
        frame=target*RATE
        if len(ids)!=1 or any(x["pid"]!=pid or x["metrics"].get("sample_count")!=frame for x in (call,match,callback)):
            return report(False,"seek_target_or_identity_mismatch")
        a,b,c,d=[x["clock"]["tick"] for x in rows]
        if not a<=b<=min(c,d) or (prior is not None and prior>a):
            return report(False,"seek_causal_order_mismatch")
        prior=max(c,d)
        observed.append({"target_frame":frame,"intent_to_matched_pcm_ns":(c-a)*1000000000//clock["frequency_hz"],
            "sdk_to_matched_pcm_ns":(c-b)*1000000000//clock["frequency_hz"],"callback_to_match_ns":(c-d)*1000000000//clock["frequency_hz"],
            "sdk_qpc":b,"match_qpc":c,"qpc_frequency":clock["frequency_hz"]})
    return report(True,"position_coded_pcm_matched")

def qualify_stock(events,requests,modules,*,local):
    names={x["event"] for x in events}
    native=bool(names & {"component.track.open.begin","component.decoder.first_pcm","component.decoder.seek.begin"})
    outer="component.archive.whole_outer.open" in names
    body=sum(x.get("bytes_sent",0) for x in requests)
    valid=modules.get("inventory_complete") is True
    ok=valid and not native and (not outer and not requests if local else outer and body>0)
    return {"qualified":bool(ok),"reason":"stock_unpack_path_no_native_decoder" if ok else "stock_route_or_module_inventory_mismatch",
            "native_track_input_observed":native,"whole_virtual_outer_observed":outer,
            "archive_module_inventory":modules,"http_body_bytes":body}

def execute(a):
    from e2e.host.runner import run
    from e2e.host.run_readiness import no_host
    out=a.output
    out.mkdir(parents=True,exist_ok=False)
    from e2e.host.windows_process import archive_file_inventory
    report={"status":"incomplete","profiles":[],"archive_template_inventory":archive_file_inventory(a.template)}
    def save(): (out/"seek-report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    save()
    for profile in ("fixture-local","fixture-direct","fixture-zip-store","fixture-zip-deflate",
                    "stock-local-store","stock-local-deflate","stock-remote-store","stock-remote-deflate"):
        no_host()
        root=out/profile
        try:
            code=run(a.manifest,a.template,root,profile=profile,exporter=a.exporter,
                exporter_sha256=a.exporter_sha256,require_common_pcm=True,cli_only=True,
                configure_disposable=True,hosted_ci=True,seek_test=True)
            checkpoint=json.loads((root/"checkpoint.json").read_text(encoding="utf-8"))
            report["profiles"].append({"profile":profile,"status":checkpoint["status"],"exit_code":code,
                "seek_oracle":checkpoint["seek_oracle"],"common_dsp_pcm":checkpoint["common_dsp_pcm"],
                "stock_archive":checkpoint.get("stock_archive"),"fixture_manifest_sha256":checkpoint["fixture_manifest_sha256"]})
        except (OSError,ValueError,KeyError,TypeError) as exc:
            report["profiles"].append({"profile":profile,"status":"failed","exit_code":2,
                "seek_oracle":{"qualified":False,"reason":type(exc).__name__}})
        save();no_host()
    report["status"]="passed" if all(x["exit_code"]==0 and x["status"]=="passed" and x["seek_oracle"]["qualified"] for x in report["profiles"]) else "failed"
    save()
    return 0 if report["status"]=="passed" else 1

if __name__=="__main__":
    import argparse
    p=argparse.ArgumentParser()
    for k in ("manifest","template","output","exporter"): p.add_argument("--"+k,type=Path,required=True)
    p.add_argument("--exporter-sha256",required=True)
    raise SystemExit(execute(p.parse_args()))
