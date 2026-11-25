"""Deterministic synthetic WAV in bounded ZIP STORE/DEFLATE archives."""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import random
from urllib.parse import urlsplit
import zipfile

from tools.media_matrix import generated_pcm

MEMBER = "disc/Remote tone.wav"
AUDIO_RATE = 16_000
AUDIO_SECONDS = 2
DIRECT = "fixture-direct"
ARCHIVE_METHODS = {"fixture-zip-store": zipfile.ZIP_STORED,
                   "fixture-zip-deflate": zipfile.ZIP_DEFLATED}


def make_fixture() -> tuple[bytes, dict[str, bytes]]:
    media = generated_pcm(seconds=AUDIO_SECONDS, rate=AUDIO_RATE)
    # Non-audio padding precedes target to expose needless whole-archive reads.
    padding = random.Random(113).randbytes(512 * 1024)
    assets = {}
    for name, method in ARCHIVE_METHODS.items():
        output = BytesIO()
        with zipfile.ZipFile(output, "w", allowZip64=True) as archive:
            archive.writestr(zipfile.ZipInfo("padding.bin"), padding, compress_type=zipfile.ZIP_STORED)
            archive.writestr(zipfile.ZipInfo(MEMBER), media, compress_type=method, compresslevel=6)
        value = output.getvalue()
        if len(value) > 2 * 1024 * 1024:
            raise ValueError("Fixture beyond bounded size")
        assets[name + ".zip"] = value
    return media, assets


def write_reference(folder: Path, base_url: str, mode: str,
                    media: bytes, archive: bytes) -> Path:
    if mode not in ARCHIVE_METHODS and mode != DIRECT:
        raise ValueError("Unknown fixture mode")
    uri = urlsplit(base_url)
    if uri.scheme != "http" or uri.hostname != "127.0.0.1" or uri.username or uri.password:
        raise ValueError("Only local HTTP fixtures accepted")
    if not folder.is_dir():
        raise ValueError("Fixture output directory must exist")
    target = folder / (mode + ".djt")
    if target.exists():
        raise ValueError("Refuse to overwrite fixture")
    direct = mode == DIRECT
    extension = "wav" if direct else "zip"
    remote_path = f"/asset/{mode}.{extension}"
    url = f"{base_url}{remote_path}?mode=normal"
    member_line = "" if direct else f"MEMBER={MEMBER}\n"
    target.write_text(
        "DOUJIN-TRACK 1\n"
        f"URL={url}\n{member_line}"
        f"REFERRER={base_url}{remote_path}\n"
        "FILENAME=Remote tone.wav\n"
        f"TITLE={mode}\nALBUM=Isolated E2E fixture\nARTIST=Synthetic fixture\n"
        f"DURATION_MS={AUDIO_SECONDS * 1000}\n"
        f"SAMPLERATE={AUDIO_RATE}\nCHANNELS=1\nBITSPERSAMPLE=16\nBITRATE_KBPS=256\n",
        encoding="utf-8", newline="\n")
    locator = None
    if not direct:
        with zipfile.ZipFile(BytesIO(archive)) as z:
            matches = [x for x in z.infolist() if x.filename == MEMBER]
            if len(matches) != 1 or matches[0].compress_type != ARCHIVE_METHODS[mode] or matches[0].file_size != len(media) or z.read(matches[0]) != media:
                raise ValueError("Remote fixture member identity mismatch")
            locator = matches[0].header_offset
    elif archive != media:
        raise ValueError("Direct fixture content mismatch")
    manifest = {
        "schema_version": 1, "kind": "host-fixture-source",
        "mode": mode, "remote_path": remote_path,
        "source_sha256": sha256(archive).hexdigest(), "source_size_bytes": len(archive),
        "archive_sha256": None if direct else sha256(archive).hexdigest(),
        "archive_size_bytes": None if direct else len(archive),
        "audio_sha256": sha256(media).hexdigest(),
        "audio_size_bytes": len(media),
        "audio_member": None if direct else MEMBER, "member_header_offset": locator, "source_media": "generated-pcm",
        "host_qualified": False,
    }
    (folder / "fixture-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target

def extract_local_control(folder: Path, assets: dict[str,bytes], expected: bytes) -> Path:
    """Read the literal member from both independently parsed archive forms."""
    records=[]
    control=None
    for profile,method in ARCHIVE_METHODS.items():
        archive=assets[profile+".zip"]
        with zipfile.ZipFile(BytesIO(archive)) as z:
            matches=[x for x in z.infolist() if x.filename==MEMBER]
            if len(matches)!=1 or matches[0].compress_type!=method or matches[0].file_size>2*1024*1024:
                raise ValueError("Ambiguous or invalid archive member")
            member=z.read(matches[0])
            if member!=expected or (control is not None and member!=control):
                raise ValueError("Exact archive member mismatch")
            control=member
            records.append({"profile":profile,"archive_sha256":sha256(archive).hexdigest(),
                "member":MEMBER,"header_offset":matches[0].header_offset,
                "audio_sha256":sha256(member).hexdigest(),"audio_size_bytes":len(member)})
    target=folder/"local-control.wav"
    with target.open("xb") as stream:
        stream.write(control)
    if target.read_bytes()!=control:
        raise ValueError("Local control bytes changed")
    with (folder/"fixture-manifest.json").open("x",encoding="utf-8") as out:
        json.dump({"schema_version":1,"kind":"host-fixture-source","mode":"fixture-local",
                   "audio_sha256":sha256(control).hexdigest(),"audio_size_bytes":len(control),
                   "archive_controls":records,"host_qualified":False},out,indent=2,sort_keys=True)
    return target
