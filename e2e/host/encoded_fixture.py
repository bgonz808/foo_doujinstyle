"""Frozen encoded media -> host-local first-PCM oracle; no encoding at execution."""
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
import math
from pathlib import Path
import re
import struct
from urllib.parse import urlsplit
import zipfile
from types import MappingProxyType
from tools.media_matrix import generated_pcm, load_frozen_fixtures, zip_bytes
from e2e.host.fixture_assets import ARCHIVE_METHODS

FRAMES=1024
PROFILES=("fixture-local","fixture-direct","fixture-zip-store","fixture-zip-deflate")
DEFAULT_CASES=("mp3-id3v23","aac-mp4")

def digest(data):
    return sha256(data).hexdigest()

@dataclass(frozen=True)
class Fixture:
    ident: str
    extension: str
    media: bytes
    archives: object
    lock_sha256: str

    @property
    def member(self):
        return "disc/Remote tone."+self.extension

def load_cases(root, ids=DEFAULT_CASES):
    matrix=json.loads((root/"quality/media-support.json").read_text(encoding="utf-8"))
    frozen,provenance=load_frozen_fixtures(root/"quality/audio-fixtures/lock.json",matrix,generated_pcm())
    cases={c["id"]:c for c in matrix["codec_container_fixtures"]}
    if not ids or len(set(ids))!=len(ids):
        raise ValueError("Invalid fixture selection")
    for ident in ids:
        data,_=frozen[ident]
        ext=cases[ident]["extension"]
        if not re.fullmatch(r"[a-z0-9]{1,8}",ext):
            raise ValueError("Invalid fixture extension")
        member="disc/Remote tone."+ext
        archives={}
        for name,method in ARCHIVE_METHODS.items():
            archive=zip_bytes(member,data,method)
            with zipfile.ZipFile(BytesIO(archive)) as z:
                item=z.getinfo(member)
                if item.compress_type!=method or z.read(item)!=data:
                    raise ValueError("Encoded member identity mismatch")
            archives[name+".zip"]=archive
        yield Fixture(ident,ext,data,MappingProxyType(archives),provenance["lock_sha256"])

def write_reference(folder,base,profile,fixture):
    if profile not in PROFILES or urlsplit(base).hostname!="127.0.0.1" or urlsplit(base).scheme!="http":
        raise ValueError("Invalid synthetic route")
    direct=profile=="fixture-direct"
    if profile=="fixture-local":
        target=folder/("local-control."+fixture.extension)
        with target.open("xb") as out:
            out.write(fixture.media)
    else:
        target=folder/(profile+".djt")
        suffix=fixture.extension if direct else "zip"
        url=base+"/asset/"+profile+"."+suffix+"?mode=normal"
        member="" if direct else "MEMBER="+fixture.member+"\n"
        with target.open("x",encoding="utf-8",newline="\n") as out:
            out.write("DOUJIN-TRACK 1\nURL="+url+"\n"+member+"FILENAME=Remote tone."+fixture.extension+
                      "\nTITLE=Encoded synthetic fixture\n")
    controls=[]
    for name,method in ARCHIVE_METHODS.items():
        archive=fixture.archives[name+".zip"]
        with zipfile.ZipFile(BytesIO(archive)) as z:
            item=z.getinfo(fixture.member)
            if z.read(item)!=fixture.media or item.compress_type!=method:
                raise ValueError("Archive identity mismatch")
            controls.append({"profile":name,"sha256":digest(archive),"zip_method":method,
                             "member_header_offset":item.header_offset,"member_bytes":item.file_size})
    (folder/"fixture-manifest.json").write_text(json.dumps({
        "kind":"encoded-first-pcm-v1","case":fixture.ident,"profile":profile,
        "audio_sha256":digest(fixture.media),"audio_size_bytes":len(fixture.media),
        "fixture_lock_sha256":fixture.lock_sha256,"archive_controls":controls},indent=2),encoding="utf-8")
    return target

def read_pcm(path):
    if path.is_symlink() or not path.is_file() or not 16<=path.stat().st_size<=16+FRAMES*8*8:
        raise ValueError("Invalid PCM capture size")
    raw=path.read_bytes()
    magic,rate,channels,frames=struct.unpack_from("<4I",raw)
    if magic!=0x314d4350 or not 1<=rate<=768000 or not 1<=channels<=8 or frames!=FRAMES or len(raw)!=16+frames*channels*8:
        raise ValueError("Invalid PCM capture header")
    values=struct.unpack_from("<"+"d"*(frames*channels),raw,16)
    if not all(math.isfinite(x) for x in values) or max(values)==min(values):
        raise ValueError("Nonfinite or constant PCM capture")
    return {"status":"captured","rate":rate,"channels":channels,"frames":frames,"sha256":digest(raw)}

def compare(local,candidate):
    a,b=read_pcm(local),read_pcm(candidate)
    return {"qualified":a==b,"reference":a,"candidate":b,
            "comparison":"exact_same_host_float64_first_1024_frames",
            "seek_qualified":False,"full_decode_qualified":False}

def execute(a):
    from e2e.host.runner import run
    from e2e.host.run_readiness import no_host
    a.output.mkdir(parents=True,exist_ok=False)
    report={"status":"incomplete","cases":[],"oracle":"same_host_local_first_1024_frames",
            "encoder_invoked":False,"seek_qualified":False}
    def save():
        (a.output/"encoded-report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    save()
    for fixture in load_cases(Path("."),tuple(a.case or DEFAULT_CASES)):
        case={"id":fixture.ident,"audio_sha256":digest(fixture.media),"arms":[]}
        report["cases"].append(case)
        local=None
        for profile in PROFILES:
            no_host()
            root=a.output/fixture.ident/profile
            arm={"profile":profile,"status":"failed"}
            case["arms"].append(arm)
            try:
                code=run(a.manifest,a.template,root,profile=profile,exporter=a.exporter,
                    exporter_sha256=a.exporter_sha256,require_common_pcm=True,cli_only=True,
                    configure_disposable=True,hosted_ci=True,encoded_fixture=fixture)
                checkpoint=json.loads((root/"checkpoint.json").read_text(encoding="utf-8"))
                pcm=root/"fixture/first-pcm.bin"
                arm.update(exit_code=code,checkpoint_status=checkpoint["status"],
                    common_dsp_pcm=checkpoint["common_dsp_pcm"],
                    fixture_manifest_sha256=checkpoint["fixture_manifest_sha256"])
                if code==0 and checkpoint["status"]=="passed":
                    if profile=="fixture-local":
                        arm["pcm"]=read_pcm(pcm)
                        local=pcm
                        arm["status"]="passed"
                    elif local is not None:
                        arm["pcm_comparison"]=compare(local,pcm)
                        if arm["pcm_comparison"]["qualified"]:
                            arm["status"]="passed"
            except (OSError,ValueError,KeyError,TypeError) as exc:
                arm["failure"]=type(exc).__name__
            save()
            no_host()
    report["status"]="passed" if report["cases"] and all(
        len(c["arms"])==len(PROFILES) and all(x["status"]=="passed" for x in c["arms"])
        for c in report["cases"]) else "failed"
    save()
    return 0 if report["status"]=="passed" else 1

if __name__=="__main__":
    import argparse
    p=argparse.ArgumentParser()
    for key in ("manifest","template","output","exporter"):
        p.add_argument("--"+key,type=Path,required=True)
    p.add_argument("--exporter-sha256",required=True)
    p.add_argument("--case",action="append")
    raise SystemExit(execute(p.parse_args()))
