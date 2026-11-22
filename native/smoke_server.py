#!/usr/bin/env python3
"""Serve a loopback fixture for foobar2000 Media Library, streaming, and seek tests."""
import argparse
from array import array
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import math
from pathlib import Path
import random
import re
import sys
import time
import wave
import zipfile


def audio(seek_benchmark):
    if seek_benchmark:
        # Low-level deterministic noise remains hard to compress and crosses many checkpoints.
        rate, channels, seconds = 48000, 2, 218
        noise = random.Random(217)
        raw = bytearray(noise.randbytes(rate * channels * seconds * 2))
        high_byte = bytes(((value & 7) - 4) & 255 for value in range(256))
        raw[1::2] = raw[1::2].translate(high_byte)
        samples = bytes(raw)
    else:
        rate, channels, seconds = 16000, 1, 15
        tone = array("h", (int(5500 * math.sin(2 * math.pi * 440 * n / rate)) for n in range(rate * seconds)))
        if sys.byteorder != "little":
            tone.byteswap()
        samples = tone.tobytes()
    output = BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setparams((channels, 2, rate, 0, "NONE", "not compressed"))
        stream.writeframes(samples)
    return output.getvalue(), rate, channels, seconds


def archive(track, method):
    out = BytesIO()
    with zipfile.ZipFile(out, "w") as stream:
        stream.writestr(zipfile.ZipInfo("unrelated.bin"), bytes(3 * 1024 * 1024))
        # A fixed timestamp keeps the validator stable across fixture restarts.
        member = zipfile.ZipInfo("disc/Remote tone.wav")
        stream.writestr(member, track, compress_type=method)
    return out.getvalue()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        data = self.server.fixtures.get(self.path)
        if data is None:
            self.send_error(404)
            return
        requested = self.headers.get("Range")
        etag = self.server.etags[self.path]
        if self.headers.get("If-Range") not in (None, etag):
            requested = None
        offset, end = 0, len(data) - 1
        if requested:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested)
            if not match or not any(match.groups()):
                self.send_error(416)
                return
            first, last = match.groups()
            if first:
                offset = int(first)
                if last:
                    end = min(end, int(last))
            else:
                offset = max(0, len(data) - int(last))
            if not 0 <= offset <= end < len(data):
                self.send_error(416)
                return
        if requested and self.server.range_delay:
            time.sleep(self.server.range_delay)
        self.send_response(206 if requested else 200)
        self.send_header("Content-Length", str(end - offset + 1))
        self.send_header("Content-Type", "audio/wav" if self.path.endswith(".wav") else "application/zip")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("ETag", etag)
        if requested:
            self.send_header("Content-Range", f"bytes {offset}-{end}/{len(data)}")
        self.end_headers()
        self.wfile.write(data[offset:end + 1])
        print(f"{self.path}  Range={requested!r}  Referer={self.headers.get('Referer')!r}  "
              f"bytes={end - offset + 1}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path, help="folder to put the three local .djt files in")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--seek-benchmark", action="store_true", help="generate a 40 MiB WAV in each archive")
    parser.add_argument("--range-delay-ms", type=int, default=0, help="add this delay before each HTTP range response")
    args = parser.parse_args()
    if args.range_delay_ms < 0:
        parser.error("--range-delay-ms must be nonnegative")
    wav, rate, channels, seconds = audio(args.seek_benchmark)
    with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
        server.fixtures = {
            "/direct.wav": wav,
            "/stored.zip": archive(wav, zipfile.ZIP_STORED),
            "/deflated.zip": archive(wav, zipfile.ZIP_DEFLATED),
        }
        server.etags = {name: f'"{sha256(data).hexdigest()}"' for name, data in server.fixtures.items()}
        server.range_delay = args.range_delay_ms / 1000
        args.folder.mkdir(parents=True, exist_ok=True)
        for name in server.fixtures:
            member = (f"MEMBER=disc/Remote tone.wav\nREFERRER=http://127.0.0.1:{server.server_port}/fixture\n"
                      if name.endswith(".zip") else "")
            (args.folder / f"{name[1:]}.djt").write_text(
                f"DOUJIN-TRACK 1\nURL=http://127.0.0.1:{server.server_port}{name}\n"
                f"{member}FILENAME=Remote tone.wav\nTITLE={name[1:]}\nALBUM=Doujin archive fixture\n"
                f"ARTIST=Local test\nDURATION_MS={seconds * 1000}\nSAMPLERATE={rate}\nCHANNELS={channels}\n"
                f"BITSPERSAMPLE=16\nBITRATE_KBPS={rate * channels * 16 // 1000}\n", encoding="utf-8")
        print(f"Serving at 127.0.0.1:{server.server_port} with descriptors in {args.folder.resolve()}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
