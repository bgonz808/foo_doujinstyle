"""Measure serial range reuse over verified loopback TLS with curl and Node."""
import argparse
import csv
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time


def measure(port, certificate, protocol, window, total):
    command = ["curl"]
    options = ["--silent", "--show-error", "--fail", "--max-time", "10",
               "--noproxy", "*", "--cacert", str(certificate), protocol]
    expected = []
    url = f"https://127.0.0.1:{port}/archive.zip"
    for start in range(0, total, window):
        count = min(window, total - start)
        end = start + count - 1
        if expected:
            command.append("--next")
        expected.append((count, f"bytes {start}-{end}/{8 * 1024 * 1024}"))
        command.extend([*options, "--range", f"{start}-{end}", "--output", os.devnull,
                        "--write-out",
                        "%{response_code},%{http_version},%{num_connects},%{size_download},%header{content-range}\n",
                        url])
    started = time.perf_counter()
    response = subprocess.run(command, check=True, capture_output=True, text=True)
    elapsed = time.perf_counter() - started
    transfers = list(csv.reader(response.stdout.splitlines()))
    assert len(transfers) == len(expected)
    negotiated = "2" if protocol == "--http2" else "1.1"
    assert all(code == "206" and version == negotiated and (int(actual), content_range) == want
               for (code, version, _, actual, content_range), want in zip(transfers, expected, strict=True))
    return elapsed, sum(int(row[2]) for row in transfers), len(transfers)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delay-ms", type=int, default=30)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    if args.delay_ms < 0 or args.runs < 1:
        parser.error("require a nonnegative delay and at least one run")
    with tempfile.TemporaryDirectory() as folder:
        certificate, key = (Path(folder) / name for name in ("cert.pem", "key.pem"))
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                        "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1",
                        "-keyout", str(key), "-out", str(certificate)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        table = csv.writer(sys.stdout, lineterminator="\n")
        table.writerow(("protocol", "server_policy", "range_kib", "range_count",
                        "tcp_tls_connections", "body_bytes", "median_ms"))
        for protocol, policy in (("--http1.1", "close"), ("--http1.1", "keep-alive"), ("--http2", "keep-alive")):
            fixture = Path(__file__).with_name("seek_transport_fixture.mjs")
            with subprocess.Popen(["node", str(fixture), str(key), str(certificate), str(args.delay_ms), policy],
                                  stdout=subprocess.PIPE, text=True) as server:
                try:
                    port = int(server.stdout.readline())
                    for kib in (64, 256, 1024, 2048):
                        byte_count = 4 * 1024 * 1024
                        runs = [measure(port, certificate, protocol, kib * 1024, byte_count)
                                for _ in range(args.runs)]
                        durations, connections, ranges = zip(*runs, strict=True)
                        assert all(count == (ranges[0] if policy == "close" else 1) for count in connections)
                        table.writerow((protocol.removeprefix("--"), policy, kib, ranges[0], connections[0],
                                        byte_count, round(statistics.median(durations) * 1000, 1)))
                        sys.stdout.flush()
                finally:
                    server.terminate()


if __name__ == "__main__":
    main()
