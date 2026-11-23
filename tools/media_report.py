"""Offline HTML viewer for generated media fixture evidence (never host performance)."""
from __future__ import annotations

from html import escape
import argparse
import json
from pathlib import Path
from statistics import median
import sys


def safe(value) -> str:
    return escape(str(value) if value is not None else "Not measured", quote=True)


def render(matrix: dict, manifest: dict, results: dict) -> str:
    if (matrix.get("schema_version") != 1 or manifest.get("kind") != "media-fixture-manifest"
            or results.get("kind") != "media-transport-observation"
            or results.get("qualification") != "fixture-only-not-native-or-host"):
        raise ValueError("Unsupported media evidence schema")
    rows = []
    all_pairs = results["pairs"]
    for case in manifest["cases"]:
        related = [p for p in all_pairs if p["case"] == case["id"]]
        route = {}
        for name in ("zip-stored", "zip-deflate"):
            nums = [p["relative_to_direct_ms"] for p in related if p["archive"] == name]
            bytes_ratio = [p["response_byte_ratio"] for p in related if p["archive"] == name]
            route[name] = (f"{median(nums):+.2f} ms, {median(bytes_ratio):.2f}× bytes"
                           if nums else "Not measured")
        state = case["status"]
        if case["classification"] != "audio_candidate":
            state += " (extension gap)"
        rows.append([safe(case["id"]), safe(case["container"]), safe(case["codec"]),
                     safe(state), safe(route["zip-stored"]), safe(route["zip-deflate"])])
    def table(head, body):
        headings = "".join("<th>" + safe(x) + "</th>" for x in head)
        entries = "".join("<tr>" + "".join("<td>" + field + "</td>" for field in row) + "</tr>"
                          for row in body)
        return ("<div class='scroll'><table><thead><tr>" + headings +
                "</tr></thead><tbody>" + entries + "</tbody></table></div>")
    html = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none';"
        " style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">",
        "<title>Offline media fixture matrix</title>",
        """<style>
*{box-sizing:border-box}body{margin:0;background:#f7f8fa;color:#202a39;
font:15px/1.55 system-ui,sans-serif}main{max-width:1100px;margin:auto;padding:26px 18px}
h1{line-height:1.18;letter-spacing:-.03em}h2{margin:26px 0 8px}
article{background:white;border:1px solid #dce1e8;border-radius:12px;padding:18px;margin:16px 0}
strong{font-weight:690}p{margin:8px 0}.muted{color:#5b6776}.scroll{overflow:auto}
table{border-collapse:collapse;width:100%;font-size:13px}
td,th{padding:10px;text-align:left;border-bottom:1px solid #e5e9ed;vertical-align:top}
th{background:#f1f4f7}tr:last-child td{border-bottom:0}
code{font:12px ui-monospace,monospace;overflow-wrap:anywhere}
a{color:#17609b}.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.stat{padding:15px;border:1px solid #dce1e8;border-radius:10px;background:white}
.num{display:block;font-size:24px;font-weight:730}@media(max-width:650px){.stats{grid-template-columns:1fr}}
</style></head><body><main>""",
        "<h1>Offline media compatibility fixtures</h1>",
        "<p><strong>Fixture and transport verification only.</strong> "
        "No foobar2000 decoding, first PCM, rendered audio, seek latency, native resource "
        "consumption or live-provider compatibility was measured.</p>",
        "<p class='muted'>Source: " + safe(manifest.get("source_commit")) +
        " · Component: " + safe(manifest.get("component_version")) +
        " · FFmpeg: " + safe(manifest.get("toolchain", {}).get("ffmpeg")) + "</p>",
    ]
    generated = sum(c["status"] == "generated" for c in manifest["cases"])
    html.extend([
        "<div class='stats'>",
        f"<div class='stat'><span class='num'>{generated}/{len(manifest['cases'])}</span>Generated codec/container candidates</div>",
        f"<div class='stat'><span class='num'>{safe(results['case_count'])}</span>Verified fixture transport cases</div>",
        f"<div class='stat'><span class='num'>{len(all_pairs)}</span>Paired direct/ZIP observations</div>",
        "</div><article><h2>Codec × container × ZIP method</h2>",
        "<p class='muted'>Difference versus fetching the same encoded file directly. "
        "Includes Python HTTP and ZIP client overhead; sizes include complete ZIP headers "
        "and metadata. Median across paired repetitions. Negative means faster in this "
        "synthetic transport run, not faster actual audio playback.</p>",
        table(("Case", "Container", "Codec", "Status", "ZIP STORED", "ZIP DEFLATE"), rows),
        "</article><article><h2>Support boundaries</h2>",
        "<p>Native remote archive methods: <strong>" +
        safe(", ".join(matrix["supported_remote_archive_methods"])) + "</strong>. "
        "All listed audio extensions are classification candidates only, not decoder certification.</p>",
        "<p>Recognized/not playable remotely: " +
        safe(", ".join(matrix["recognized_not_remote_playable"])) + "</p>",
        "<p>Unclassified host format gaps: " +
        safe(", ".join(matrix["extension_gaps_for_host_review"])) + "</p>",
        "<p>Deferred archive formats: " + safe(", ".join(matrix["archive_deferred"])) + "</p>",
        "<p>Deferred metadata handling: " + safe(", ".join(matrix["metadata_deferred"])) + "</p>",
        "</article><article><h2>Canonical evidence</h2>",
        "<p><a href='manifest.json'>Fixture manifest</a> · "
        "<a href='transport-results.json'>Transport observations</a> · "
        "<a href='tests/media-matrix.xml'>JUnit tests</a> · "
        "<a href='summary.md'>Summary</a></p>",
        "<p class='muted'>Each generated asset's SHA-256 is in the manifest. "
        "These are unsigned, WIP synthetic CI artifacts, not release attestations.</p>",
        "</article></main></body></html>",
    ])
    return "\n".join(html)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--matrix", type=Path, default=Path("quality/media-support.json"))
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    try:
        matrix = json.loads(a.matrix.read_text(encoding="utf-8"))
        manifest = json.loads((a.input / "manifest.json").read_text(encoding="utf-8"))
        results = json.loads((a.input / "transport-results.json").read_text(encoding="utf-8"))
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(render(matrix, manifest, results), encoding="utf-8")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"FAIL: media fixture report: {error}", file=sys.stderr)
        return 1
    print(f"Media fixture HTML: {a.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
