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
            archive.writestr("padding.bin", padding, compress_type=zipfile.ZIP_STORED)
            archive.writestr(MEMBER, media, compress_type=method, compresslevel=6)
        value = output.getvalue()
        if len(value) > 2 * 1024 * 1024:
            raise ValueError("Fixture beyond bounded size")
        assets[name + ".zip"] = value
    return media, assets


def write_reference(folder: Path, base_url: str, mode: str,
                    media: bytes, archive: bytes) -> Path:
    if mode not in ARCHIVE_METHODS:
        raise ValueError("Unknown fixture mode")
    uri = urlsplit(base_url)
    if uri.scheme != "http" or uri.hostname != "127.0.0.1" or uri.username or uri.password:
        raise ValueError("Only local HTTP fixtures accepted")
    if not folder.is_dir():
        raise ValueError("Fixture output directory must exist")
    target = folder / (mode + ".djt")
    if target.exists():
        raise ValueError("Refuse to overwrite fixture")
    url = f"{base_url}/asset/{mode}.zip?mode=normal"
    target.write_text(
        "DOUJIN-TRACK 1\n"
        f"URL={url}\nMEMBER={MEMBER}\n"
        f"REFERRER={base_url}/asset/{mode}.zip\n"
        "FILENAME=Remote tone.wav\n"
        f"TITLE={mode}\nALBUM=Isolated E2E fixture\nARTIST=Synthetic fixture\n"
        f"DURATION_MS={AUDIO_SECONDS * 1000}\n"
        f"SAMPLERATE={AUDIO_RATE}\nCHANNELS=1\nBITSPERSAMPLE=16\nBITRATE_KBPS=256\n",
        encoding="utf-8", newline="\n")
    manifest = {
        "schema_version": 1, "kind": "host-fixture-source",
        "mode": mode, "remote_path": f"/asset/{mode}.zip",
        "archive_sha256": sha256(archive).hexdigest(),
        "archive_size_bytes": len(archive),
        "audio_sha256": sha256(media).hexdigest(),
        "audio_size_bytes": len(media),
        "audio_member": MEMBER, "source_media": "generated-pcm",
        "host_qualified": False,
    }
    (folder / "fixture-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target
