"""Offline media fixture lab: deterministic audio, ZIP, loopback fault injection and transport evidence.

NOT a foobar2000 playback benchmark. The host E2E runner must drive/measure
foobar2000 independently and may consume generated assets and trace schema.
No live provider URLs, copyrighted media, credentials, or remote requests.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import math
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import wave
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

SCHEMA = 1
MAX_ASSET = 4 * 1024 * 1024
TITLE = "東方 音響 regression — Café"
ARTIST = "Synthetic / 研究用"
ALBUM = "Offline Audio Fixture"
MEMBER = "音源/テスト・tone.wav"
ZIP_METHODS = {"zip-stored": ZIP_STORED, "zip-deflate": ZIP_DEFLATED}
MODE = {"normal", "delay", "throttle", "ignore-range", "etag-change", "close",
        "rate-limit", "unavailable", "truncate", "redirect"}
OPENER = build_opener(ProxyHandler({}))


def digest(content: bytes) -> str:
    return sha256(content).hexdigest()


def json_out(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def generated_pcm(seconds: float = 2.0, rate: int = 16000) -> bytes:
    """Repeatable mono PCM with distinct half-second frequency markers."""
    samples = bytearray()
    total = int(seconds * rate)
    for n in range(total):
        section = (n // (rate // 2)) % 4
        frequency = (220, 440, 880, 1760)[section]
        value = int(10500 * math.sin(2 * math.pi * frequency * n / rate))
        samples.extend(struct.pack("<h", value))
    stream = BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(bytes(samples))
    return stream.getvalue()


def zip_bytes(member: str, media: bytes, method: int) -> bytes:
    if len(media) > MAX_ASSET:
        raise ValueError("Fixture media exceeds bounded archive limit")
    archive = BytesIO()
    with ZipFile(archive, "w") as output:
        for name, content, compression in (
            (member, media, method),
            ("説明/README.txt", f"{TITLE}\nSynthetic test content only.\n".encode(), ZIP_STORED),
        ):
            info = ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = compression
            info.external_attr = 0o644 << 16
            output.writestr(info, content, compresslevel=6)
    return archive.getvalue()


def corrupted_zip(archive: bytes) -> bytes:
    """Deliberately wrong *central directory* CRC; zipfile must reject read()."""
    payload = bytearray(archive)
    mark = payload.find(b"PK\x01\x02")
    if mark < 0 or mark + 20 > len(payload):
        raise ValueError("No ZIP central directory to corrupt")
    original = struct.unpack_from("<I", payload, mark + 16)[0]
    struct.pack_into("<I", payload, mark + 16, original ^ 0xFFFFFFFF)
    return bytes(payload)


def check_tools(tool: str) -> str | None:
    location = shutil.which(tool)
    if location is None:
        return None
    process = subprocess.run([location, "-version"], capture_output=True, text=True,
                             timeout=8, check=True)
    return process.stdout.splitlines()[0].strip()


def media_render(source: Path, destination: Path, args: list[str], *,
                 ffmpeg: str = "ffmpeg") -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", destination.name):
        raise ValueError("Unsafe fixture filename")
    command = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
               "-y", "-i", str(source), "-map", "0:a:0", "-map_metadata", "-1",
               "-metadata", f"title={TITLE}", "-metadata", f"artist={ARTIST}",
               "-metadata", f"album={ALBUM}", "-metadata", "track=2/4",
               "-threads", "1", *args, str(destination)]
    subprocess.run(command, check=True, capture_output=True, timeout=25)
    if not destination.is_file() or destination.stat().st_size == 0 or destination.stat().st_size > MAX_ASSET:
        raise ValueError("Invalid or excessive FFmpeg fixture output")


def ffprobe_record(path: Path) -> dict | None:
    if not shutil.which("ffprobe"):
        return None
    proc = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                           "stream=codec_name,codec_type,sample_rate,channels:stream_tags:"
                           "format=format_name:format_tags",
                           "-of", "json", str(path)],
                          check=True, capture_output=True, timeout=10)
    data = json.loads(proc.stdout)
    streams = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    stream = streams[0] if streams else {}
    tags = {k.lower(): v for k, v in {**stream.get("tags", {}),
                                     **data.get("format", {}).get("tags", {})}.items()}
    return {"codec": stream.get("codec_name"),
            "container": data.get("format", {}).get("format_name"),
            "tags": tags}


def validate_matrix(matrix: dict) -> None:
    if matrix.get("schema_version") != SCHEMA:
        raise ValueError("Unexpected support-matrix schema")
    if matrix.get("supported_remote_archive_methods") != ["zip-stored", "zip-deflate"]:
        raise ValueError("Archive support matrix out of sync")
    cases = matrix.get("codec_container_fixtures")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Missing codec matrix")
    ids = set()
    for case in cases:
        ident = case.get("id")
        if (not isinstance(ident, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,55}", ident)
                or ident in ids or case.get("tier") not in {"required", "ffmpeg", "gap"}
                or not isinstance(case.get("args"), list)
                or not all(isinstance(arg, str) and len(arg) < 128 for arg in case["args"])):
            raise ValueError("Invalid codec/container case")
        ids.add(ident)


def create_fixtures(folder: Path, matrix: dict, *, include_ffmpeg: bool) -> dict:
    validate_matrix(matrix)
    folder.mkdir(parents=True, exist_ok=True)
    wav = generated_pcm()
    (folder / "pcm-wav.wav").write_bytes(wav)
    ffmpeg_identity = check_tools("ffmpeg") if include_ffmpeg else None
    ffprobe_identity = check_tools("ffprobe") if include_ffmpeg else None
    ffmpeg_binary_sha256 = digest(Path(shutil.which("ffmpeg")).read_bytes()) if ffmpeg_identity else None
    ffprobe_binary_sha256 = digest(Path(shutil.which("ffprobe")).read_bytes()) if ffprobe_identity else None
    source_sha = os.environ.get("GITHUB_SHA", "")
    if not re.fullmatch(r"[a-fA-F0-9]{40}", source_sha):
        source_sha = None
    pr_head = os.environ.get("MEDIA_MATRIX_PR_HEAD_SHA", "")
    if not re.fullmatch(r"[a-fA-F0-9]{40}", pr_head):
        pr_head = None
    manifest = {
        "schema_version": SCHEMA, "kind": "media-fixture-manifest",
        "source_commit": source_sha,
        "pr_head_commit": pr_head,
        "ci_run_id": os.environ.get("GITHUB_RUN_ID") or None,
        "audio_markers": {"sample_rate_hz": 16000, "channels": 1,
                          "duration_ms": 2000, "segment_ms": 500,
                          "frequencies_hz": [220, 440, 880, 1760],
                          "purpose": "decode-or-loopback-audio-identification",
                          "qualification": "not-yet-tested-in-foobar2000"},
        "component_version": Path("VERSION").read_text(encoding="utf-8").strip()
                             if Path("VERSION").is_file() else None,
        "purpose": "offline-synthetic-only",
        "qualification": "not-foobar2000-host-qualified",
        "generator": "tools/media_matrix.py", "seed": "sines-220-440-880-1760-v1",
        "metadata": {"title": TITLE, "artist": ARTIST, "album": ALBUM},
        "toolchain": {"ffmpeg": ffmpeg_identity, "ffprobe": ffprobe_identity,
                      "ffmpeg_binary_sha256": ffmpeg_binary_sha256,
                      "ffprobe_binary_sha256": ffprobe_binary_sha256},
        "support_matrix_sha256": digest(json.dumps(matrix, sort_keys=True, ensure_ascii=False).encode()),
        "cases": [], "assets": {},
    }

    for case in matrix["codec_container_fixtures"]:
        ident, ext = case["id"], case["extension"]
        record = {"id": ident, "codec": case["codec"], "container": case["container"],
                  "extension": ext, "classification": "audio_candidate"
                  if "." + ext in matrix["audio_candidate_extensions"] else "unclassified_gap",
                  "tier": case["tier"]}
        if case["tier"] == "required":
            data = wav
        elif not ffmpeg_identity:
            record.update(status="not_generated", reason="FFmpeg not enabled or unavailable")
            manifest["cases"].append(record)
            continue
        else:
            filename = f"{ident}.{ext}"
            path = folder / filename
            try:
                media_render(folder / "pcm-wav.wav", path, case["args"])
                data = path.read_bytes()
                record["probe"] = ffprobe_record(path)
                if not record["probe"] or record["probe"]["codec"] != case["codec"]:
                    raise ValueError(f"FFprobe codec mismatch: {record['probe']}")
            except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
                record.update(status="not_generated", reason=f"{type(exc).__name__}: {str(exc)[:160]}")
                path.unlink(missing_ok=True)
                manifest["cases"].append(record)
                continue
        filename = f"{ident}.{ext}"
        if case["tier"] == "required":
            (folder / filename).write_bytes(data)
        item = {"path": filename, "sha256": digest(data), "size": len(data),
                "media_id": ident, "method": "direct"}
        manifest["assets"][f"direct:{ident}"] = item
        record.update(status="generated", direct=item["sha256"])
        for name, method in ZIP_METHODS.items():
            member = f"音源/{ident}.{ext}"
            archived = zip_bytes(member, data, method)
            archive_name = f"{ident}-{name}.zip"
            (folder / archive_name).write_bytes(archived)
            key = f"{name}:{ident}"
            manifest["assets"][key] = {"path": archive_name, "sha256": digest(archived),
                                       "size": len(archived), "media_id": ident,
                                       "member": member, "method": name}
        manifest["cases"].append(record)

    # Negative fixtures: malformed CRC and unsupported compression.
    corrupted = corrupted_zip((folder / "pcm-wav-zip-stored.zip").read_bytes())
    (folder / "bad-crc.zip").write_bytes(corrupted)
    manifest["assets"]["negative:crc"] = {
        "path": "bad-crc.zip", "sha256": digest(corrupted), "size": len(corrupted),
        "method": "intentionally-invalid-zip", "expected": "reject-on-read"}
    bzip = zip_bytes("音源/unsupported.wav", wav, __import__("zipfile").ZIP_BZIP2)
    (folder / "unsupported-bzip2.zip").write_bytes(bzip)
    manifest["assets"]["negative:bzip2"] = {
        "path": "unsupported-bzip2.zip", "sha256": digest(bzip), "size": len(bzip),
        "method": "zip-bzip2", "expected": "not-supported-by-native-remote-reader"}
    json_out(folder / "manifest.json", manifest)
    return manifest


class _LocalOnlyRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        uri = urlsplit(newurl)
        if uri.scheme != "http" or uri.hostname != "127.0.0.1" or uri.username or uri.password:
            raise ValueError("Fixture redirect attempted to leave loopback")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


CLIENT = build_opener(ProxyHandler({}), _LocalOnlyRedirect())


@contextmanager
def fixture_server(assets: dict[str, bytes], *, latency_ms: int = 0, bps: int = 0):
    """All requests bound to loopback. Injection modes never redirect externally."""
    if latency_ms < 0 or latency_ms > 500 or bps < 0 or (bps and bps < 4096):
        raise ValueError("Unsafe fixture fault limits")
    if any(not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,96}", key) or len(data) > MAX_ASSET
           for key, data in assets.items()):
        raise ValueError("Unsafe or unbounded fixture assets")

    class EventLog(list):
        def __init__(self):
            super().__init__()
            self.condition = threading.Condition()

        def append(self, item):
            with self.condition:
                super().append(item)
                self.condition.notify_all()

        def wait_count(self, count: int, timeout: float = 2.0) -> bool:
            with self.condition:
                return self.condition.wait_for(lambda: len(self) >= count, timeout=timeout)

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format, *args):
            pass

        def do_GET(self):
            start = time.perf_counter_ns()
            uri = urlsplit(self.path)
            key = uri.path.removeprefix("/asset/")
            mode = uri.query.removeprefix("mode=") or "normal"
            if not uri.path.startswith("/asset/") or key not in self.server.assets or mode not in MODE:
                self.send_error(404)
                return
            data = self.server.assets[key]
            etag = '"' + digest(data) + '"'
            if mode == "redirect":
                self.send_response(302)
                self.send_header("Location", f"/asset/{key}?mode=normal")
                self.send_header("Content-Length", "0")
                self.end_headers()
                self._record(key, mode, 302, 0, start)
                return
            if mode in {"rate-limit", "unavailable"}:
                status = 429 if mode == "rate-limit" else 503
                self.send_response(status)
                self.send_header("Retry-After", "1")
                self.send_header("Content-Length", "0")
                self.end_headers()
                self._record(key, mode, status, 0, start)
                return
            requested = self.headers.get("Range")
            if mode in {"ignore-range", "etag-change"} or (self.headers.get("If-Range") not in (None, etag)):
                requested = None
            first, last, status = 0, len(data) - 1, 200
            if requested:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested)
                if not match or not any(match.groups()):
                    self._range_error(key, mode, len(data), start)
                    return
                left, right = match.groups()
                if left:
                    first = int(left)
                    if right:
                        last = min(last, int(right))
                else:
                    first = max(0, len(data) - int(right))
                if first >= len(data) or first > last:
                    self._range_error(key, mode, len(data), start)
                    return
                status = 206
            if mode == "delay" or latency_ms:
                time.sleep((latency_ms if latency_ms else 35) / 1000)
            body = data[first:last + 1]
            self.send_response(status)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("ETag", etag if mode != "etag-change" else '"rotated"')
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Type", "application/octet-stream")
            if status == 206:
                self.send_header("Content-Range", f"bytes {first}-{last}/{len(data)}")
            if mode in {"close", "truncate"}:
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            if mode == "truncate":
                transmitted = body[:max(0, len(body) // 2)]
                self.wfile.write(transmitted)
            elif mode == "throttle" or bps:
                rate = bps if bps else 65536
                transmitted = body
                for offset in range(0, len(body), 4096):
                    chunk = body[offset:offset + 4096]
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    time.sleep(len(chunk) / rate)
            else:
                transmitted = body
                self.wfile.write(body)
            self._record(key, mode, status, len(transmitted), start)

        def _range_error(self, key, mode, size, start):
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            self._record(key, mode, 416, 0, start)

        def _record(self, key, mode, status, transferred, start):
            entry = {"asset": key, "mode": mode, "status": status,
                     "bytes_sent": transferred, "range": self.headers.get("Range"),
                     "elapsed_ms": round((time.perf_counter_ns() - start) / 1e6, 3)}
            self.server.events.append(entry)

    server = Server(("127.0.0.1", 0), Handler)
    server.assets = assets
    server.events = EventLog()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", server.events
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def http_get(base: str, key: str, *, headers: dict | None = None, mode: str = "normal"):
    uri = urlsplit(base)
    if uri.scheme != "http" or uri.hostname != "127.0.0.1" or mode not in MODE:
        raise ValueError("Only loopback fixture endpoints are permitted")
    request = Request(f"{base}/asset/{key}?mode={mode}", headers=headers or {})
    with CLIENT.open(request, timeout=8) as response:
        return response.status, dict(response.headers), response.read()


def probe_transport(folder: Path, manifest: dict, *,
                    latency_ms: int = 0, bps: int = 0, repeats: int = 3) -> dict:
    """Measure fixture transport/extraction ONLY; not audio decoding or first sound."""
    if not 1 <= repeats <= 20:
        raise ValueError("Bounded repetitions required")
    assets = {key: (folder / item["path"]).read_bytes() for key, item
              in manifest["assets"].items() if not key.startswith("negative:")}
    rows = []
    orders = (
        ("direct", "zip-stored", "zip-deflate"),
        ("zip-deflate", "direct", "zip-stored"),
        ("zip-stored", "zip-deflate", "direct"),
    )
    with fixture_server(assets, latency_ms=latency_ms, bps=bps) as (base, events):
        for case in manifest["cases"]:
            if case["status"] != "generated" or case["classification"] != "audio_candidate":
                continue
            direct = f"direct:{case['id']}"
            expected_hash = manifest["assets"][direct]["sha256"]
            for repetition in range(repeats):
                for form in orders[repetition % len(orders)]:
                    key = f"{form}:{case['id']}"
                    before = len(events)
                    begin, cpu = time.perf_counter_ns(), time.process_time_ns()
                    status, response_headers, payload = http_get(base, key)
                    if form != "direct":
                        member = manifest["assets"][key]["member"]
                        with ZipFile(BytesIO(payload)) as arc:
                            payload = arc.read(member)
                    duration = round((time.perf_counter_ns() - begin) / 1e6, 3)
                    cpu_ms = round((time.process_time_ns() - cpu) / 1e6, 3)
                    if status != 200 or digest(payload) != expected_hash:
                        raise ValueError(f"Fixture transport mismatch for {key}")
                    if not events.wait_count(before + 1):
                        raise RuntimeError("Fixture server did not record completed HTTP request")
                    calls = events[before:]
                    rows.append({"case": case["id"], "repetition": repetition,
                                 "route": form, "status": "fixture-bytes-verified",
                                 "duration_ms": duration, "fixture_process_cpu_ms": cpu_ms,
                                 "response_bytes": sum(e["bytes_sent"] for e in calls),
                                 "request_count": len(calls),
                                 "payload_sha256": expected_hash,
                                 "network_profile": {"latency_ms": latency_ms, "bps": bps}})
    by_id = {}
    for row in rows:
        by_id.setdefault(row["case"], {}).setdefault(row["repetition"], {})[row["route"]] = row
    pairs = []
    for case, rounds in sorted(by_id.items()):
        for repetition, measured in sorted(rounds.items()):
            direct = measured["direct"]
            for route in ("zip-stored", "zip-deflate"):
                archive = measured[route]
                pairs.append({"case": case, "repetition": repetition, "archive": route,
                              "relative_to_direct_ms": round(archive["duration_ms"] - direct["duration_ms"], 3),
                              "response_byte_ratio": round(archive["response_bytes"] / direct["response_bytes"], 6),
                              "baseline": "direct-same-encoded-audio-bytes",
                              "inference": "fixture-transport-only-not-playback"})
    from statistics import median
    aggregates = []
    for case, rounds in sorted(by_id.items()):
        for route in ("direct", "zip-stored", "zip-deflate"):
            values = [r[route]["duration_ms"] for r in rounds.values()]
            aggregates.append({"case": case, "route": route, "n": len(values),
                               "median_ms": round(median(values), 3),
                               "p95_ms": None if len(values) < 20 else round(
                                   sorted(values)[math.ceil(.95 * len(values)) - 1], 3)})
    result = {"schema_version": SCHEMA, "kind": "media-transport-observation",
              "qualification": "fixture-only-not-native-or-host",
              "repetitions": repeats, "first_audio_ms": None, "seek_to_audio_ms": None,
              "native_cpu": None, "native_memory": None,
              "trace": rows, "requests": list(events), "pairs": pairs, "aggregates": aggregates,
              "case_count": len(by_id)}
    json_out(folder / "transport-results.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, default=Path("quality/media-support.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--include-ffmpeg", action="store_true")
    parser.add_argument("--latency-ms", type=int, default=0)
    parser.add_argument("--bps", type=int, default=0)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    matrix = json.loads(args.matrix.read_text(encoding="utf-8"))
    manifest = create_fixtures(args.output, matrix, include_ffmpeg=args.include_ffmpeg)
    result = probe_transport(args.output, manifest, latency_ms=args.latency_ms, bps=args.bps,
                             repeats=args.repeats)
    for case in manifest["cases"]:
        if case["status"] != "generated" and case["tier"] != "gap" and args.include_ffmpeg:
            raise RuntimeError(f"Failed to generate expected codec fixture {case['id']}: {case.get('reason')}")
    missing = sum(1 for case in manifest["cases"] if case["status"] != "generated")
    summary = ("# Offline media compatibility fixture lab\n\n"
               f"- Generated media formats: {len(manifest['cases']) - missing}/{len(manifest['cases'])}\n"
               f"- Verified direct/archive transport paths: {len(result['trace'])}\n"
               "- First audio, native seek latency and foobar2000 decoding: **not measured**\n"
               "- These measurements include only synthetic loopback transfer and Python ZIP extraction.\n"
               "- Real audio timing requires the Windows Robot/FlaUI host runner.\n")
    (args.output / "summary.md").write_text(summary, encoding="utf-8")
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
