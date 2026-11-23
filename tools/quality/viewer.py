"""Render a portable, self-contained quality report from canonical JSON/XML evidence.

No CDN, JavaScript, untrusted HTML, network access, or runtime dependencies.
The generated HTML is a *view*; checkpoint.json, coverage JSON and JUnit XML
remain authoritative and are not modified.
"""
from __future__ import annotations

import argparse
from html import escape
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET


def e(value: object) -> str:
    if value is None:
        return "Not measured"
    return escape(str(value), quote=True)


def percentage(metric: dict | None) -> str:
    if not isinstance(metric, dict) or metric.get("percent") is None:
        return "Not measured"
    return f"{metric['percent']:.1f}%"


def progress(metric: dict | None) -> str:
    if not isinstance(metric, dict) or metric.get("percent") is None:
        return '<span class="muted">Not measured</span>'
    pct = max(0.0, min(100.0, float(metric["percent"])))
    return ('<div class="meter" role="meter" aria-label="Coverage" '
            f'aria-valuemin="0" aria-valuemax="100" aria-valuenow="{pct:.1f}">'
            f'<span style="width:{pct:.1f}%"></span></div>')


def cases_from_junit(folder: Path | None) -> list[dict]:
    if folder is None or not folder.is_dir():
        return []
    cases = []
    for path in sorted(folder.glob("*.xml")):
        doc = ET.parse(path).getroot()
        if doc.tag not in ("testsuite", "testsuites"):
            raise ValueError(f"Not a JUnit report: {path.name}")
        for case in doc.iter("testcase"):
            node = next((x for x in case if x.tag in ("failure", "error")), None)
            skipped = next((x for x in case if x.tag == "skipped"), None)
            state = "failed" if node is not None else "skipped" if skipped is not None else "passed"
            name = case.get("name")
            if not name:
                raise ValueError("JUnit case without name")
            cases.append({"id": f"{case.get('classname', '')}.{name}".lstrip("."),
                          "status": state, "suite": path.name})
            if len(cases) > 20000:
                raise ValueError("Too many test cases for a lightweight report")
    return sorted(cases, key=lambda item: ({"failed": 0, "skipped": 1, "passed": 2}[item["status"]],
                                                 item["id"]))


def table(headers: tuple[str, ...], rows: list[list[str]], *, cls: str = "") -> str:
    h = "".join(f"<th scope='col'>{e(x)}</th>" for x in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
                   for row in rows)
    if not body:
        body = f"<tr><td colspan='{len(headers)}' class='muted'>No records</td></tr>"
    return f"<div class='table-scroll'><table class='{cls}'><thead><tr>{h}</tr></thead><tbody>{body}</tbody></table></div>"


def render(checkpoint: dict, coverage: dict | None = None,
           cases: list[dict] | None = None) -> str:
    if checkpoint.get("kind") != "native-quality-checkpoint" or checkpoint.get("schema_version") != 1:
        raise ValueError("Unsupported quality checkpoint")
    if coverage is not None and (coverage.get("schema_version") != 1 or
                                 coverage.get("measured") is not True):
        raise ValueError("Unsupported native coverage input")
    cases = cases or []
    tests = checkpoint.get("tests") or {}
    native = checkpoint.get("native_coverage") or {}
    stats = native.get("summary") or {}
    migration = checkpoint.get("migration") or {}
    groups = checkpoint.get("features") or []
    identity = checkpoint.get("version") or "unversioned"
    status = checkpoint.get("status") or "unavailable"
    blocks = []
    put = blocks.append

    put("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none';"
        " style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; img-src data:\">"
        f"<title>{e(identity)} · Native quality evidence</title><style>")
    put("""*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:#f5f6f8;
color:#202938;font:15px/1.5 system-ui,-apple-system,'Segoe UI',sans-serif}
main{max-width:1120px;margin:auto;padding:32px 24px 72px}
h1{font-size:clamp(1.8rem,4vw,2.55rem);line-height:1.15;margin:9px 0 12px;letter-spacing:-.04em}
h2{font-size:1.3rem;letter-spacing:-.02em;margin:0 0 14px}
h3{font-size:1rem;margin:0 0 8px}
p{margin:8px 0}.muted,.hint{color:#5f6c7c}.kicker{font-size:12px;font-weight:700;
text-transform:uppercase;letter-spacing:.08em;color:#4b5a70}
.header{border-bottom:1px solid #d8dce3;padding-bottom:24px;margin-bottom:26px}
.meta{font-family:ui-monospace,Consolas,monospace;font-size:12px;color:#596477;overflow-wrap:anywhere}
nav{display:flex;flex-wrap:wrap;gap:16px;margin-top:18px}
a{color:#145a9e;text-decoration:underline;text-underline-offset:3px}
section{margin:30px 0 0}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
.panel,.metric{background:white;border:1px solid #dce1e8;border-radius:12px;padding:19px}
.metric .value{font-size:clamp(22px,3vw,31px);font-weight:700;letter-spacing:-.04em}
.metric small{color:#687386}.metric .unit{font-size:12px;color:#687386}
.subgrid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.meter{height:7px;border-radius:10px;background:#e8edf3;overflow:hidden;min-width:75px}
.meter span{display:block;height:100%;background:#356f91;border-radius:10px}
.table-scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;border-bottom:1px solid #e6eaf0;padding:10px 12px;vertical-align:middle}
th{font-size:12px;color:#4b5a70;background:#f4f6f8;white-space:nowrap}
td:first-child{font-weight:550}td{overflow-wrap:anywhere}
tr:last-child td{border-bottom:0}
.state{font-weight:650;font-size:12px;white-space:nowrap}
.state-failed,.state-error{color:#a22b2b}.state-passed{color:#24714c}
.state-partial,.state-skipped,.state-incomplete{color:#8a5d13}
.state-not_run,.state-not_defined{color:#647187}
code{font-size:12px;overflow-wrap:anywhere}details{margin-top:12px}
summary{cursor:pointer;font-weight:600}ul{padding-left:20px}
footer{margin-top:32px;border-top:1px solid #dce1e8;padding-top:20px;color:#5f6c7c;font-size:13px}
@media(max-width:800px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}.subgrid{grid-template-columns:1fr}}
@media(max-width:500px){main{padding:22px 12px 45px}.grid{gap:8px}.metric,.panel{padding:12px}td,th{padding:9px 8px}}
""")
    put("</style></head><body><main>")
    put(f"<header class='header'><div class='kicker'>Native quality evidence · v1</div>"
        f"<h1>Quality report <span class='muted'>{e(identity)}</span></h1>"
        f"<p><strong>{e(status.upper())}</strong> · {e(checkpoint.get('qualification'))}</p>"
        f"<p class='meta'>Commit: {e(checkpoint.get('source_commit'))} · "
        f"CI run: {e(checkpoint.get('run_id'))}</p>"
        "<nav aria-label='Report sections'><a href='#coverage'>Coverage</a>"
        "<a href='#features'>Feature readiness</a><a href='#tests'>Tests</a>"
        "<a href='#sources'>Source files</a><a href='#evidence'>Evidence</a></nav></header>")

    def metric(label: str, value: object, note: str) -> str:
        return f"<div class='metric'><div class='kicker'>{e(label)}</div><div class='value'>{e(value)}</div><small>{e(note)}</small></div>"

    put("<div class='grid'>")
    put(metric("C++ line coverage", percentage(stats.get("lines")), native.get("scope") or "Unmeasured"))
    put(metric("C++ branch coverage", percentage(stats.get("branches")), "Instrumented scope only"))
    put(metric("Tests passed", tests.get("passed") if "passed" in tests else "N/A",
               f"{tests.get('failed', '?')} failed · {tests.get('skipped', '?')} skipped"))
    put(metric("Packaged Python", migration.get("packaged_python_lines") if migration else "N/A",
               f"Baseline delta: {migration.get('delta_python_lines') if migration.get('delta_python_lines') is not None else 'N/A'}"))
    put("</div>")

    put("<section id='coverage'><h2>Native code coverage</h2><div class='panel'>")
    if native:
        put(f"<p>Measured scope: <strong>{e(native.get('scope'))}</strong>. "
            f"{e(native.get('instrumented_source_files'))} files · "
            f"{e(native.get('profile_count'))} profiles.</p>")
        for key in ("lines", "branches", "functions", "regions"):
            val = stats.get(key)
            if not val:
                continue
            put(f"<div style='display:grid;grid-template-columns:100px 80px minmax(80px,1fr) 130px;"
                "align-items:center;gap:10px;margin-top:13px'>"
                f"<strong>{e(key.title())}</strong><span>{e(percentage(val))}</span>"
                f"{progress(val)}<span class='muted'>{e(val.get('covered'))}/{e(val.get('count'))}</span></div>")
    else:
        put("<p>No instrumented native coverage was collected.</p>")
    put("<p class='hint'>Only instrumented first-party code is in the denominator. "
        "Whole-plugin and foobar2000 E2E code coverage are <strong>not measured</strong>.</p></div></section>")

    put("<section id='features'><h2>Feature readiness</h2><div class='panel'>")
    feature_rows = []
    for group in groups:
        contract = group.get("contracts") or {}
        host = group.get("host_e2e") or {}
        def mark(obj: dict) -> str:
            state = str(obj.get("status", "not_run"))
            css = re.sub(r"[^a-z_]", "", state)
            count = obj.get("matched_cases", 0)
            return f"<span class='state state-{css}'>{e(state)}</span> <span class='muted'>({e(count)} cases)</span>"
        feature_rows.append([f"<code>{e(group.get('id'))}</code>",
                             e(group.get("migration_state")), mark(contract), mark(host),
                             e(group.get("issue"))])
    put(table(("Feature", "Migration", "Contract", "Host E2E", "Issue"), feature_rows))
    put("<p class='hint'>A missing E2E scenario is not equivalent to a passing test. "
        "Feature status comes from observed JUnit cases, not planned names.</p></div></section>")

    put("<section id='tests'><h2>Test execution</h2><div class='panel'>")
    put(f"<p>{e(tests.get('total'))} executions · {e(tests.get('unique_test_ids'))} unique IDs · "
        f"{e(tests.get('duplicate_executions'))} repeats · {e(tests.get('skipped'))} skipped</p>")
    if cases:
        unexpected = [case for case in cases if case["status"] != "passed"]
        put("<h3>Skipped and failed cases</h3>")
        put(table(("Test", "Outcome", "Suite"), [
            [f"<code>{e(c['id'])}</code>", f"<span class='state state-{e(c['status'])}'>{e(c['status'])}</span>",
             e(c["suite"])] for c in unexpected]))
        put(f"<details><summary>All {len(cases)} test executions</summary>")
        put(table(("Test", "Outcome", "Suite"), [
            [f"<code>{e(c['id'])}</code>", e(c["status"]), e(c["suite"])] for c in cases]))
        put("</details>")
    else:
        put("<p class='muted'>JUnit details not supplied; only checkpoint totals available.</p>")
    put("</div></section>")

    put("<section id='sources'><h2>Instrumented source files</h2><div class='panel'>")
    files = coverage.get("files", []) if coverage else []
    sorted_files = sorted(files, key=lambda f: (
        -((f.get("coverage", {}).get("lines", {}).get("count") or 0) -
          (f.get("coverage", {}).get("lines", {}).get("covered") or 0)),
        str(f.get("path", ""))))
    file_rows = []
    for item in sorted_files:
        cov = item.get("coverage") or {}
        line = cov.get("lines") or {}
        branch = cov.get("branches") or {}
        missed = (line.get("count") or 0) - (line.get("covered") or 0)
        file_rows.append([f"<code>{e(item.get('path'))}</code>", e(percentage(line)),
                          e(f"{line.get('covered', 0)}/{line.get('count', 0)}"),
                          e(missed), e(percentage(branch))])
    put(table(("Path", "Lines", "Covered / total", "Uncovered", "Branches"), file_rows))
    put("<p class='hint'>Ordered by uncovered lines. Source annotations are available "
        "using LLVM's own HTML coverage viewer when the instrumented binary and profile are retained.</p></div></section>")

    put("<section id='evidence'><h2>Evidence and provenance</h2><div class='panel'>")
    ev = checkpoint.get("evidence") or {}
    evidence_rows = [[e("LLVM coverage JSON"), f"<code>{e(ev.get('coverage_summary_sha256'))}</code>"],
                     [e("Packaged inventory"), f"<code>{e(ev.get('packaged_inventory_sha256'))}</code>"],
                     [e("Feature manifest"), f"<code>{e(ev.get('features_manifest_sha256'))}</code>"]]
    for item in ev.get("junit", []):
        evidence_rows.append([f"JUnit: <code>{e(item.get('path'))}</code>",
                              f"<code>{e(item.get('sha256'))}</code>"])
    # Relative links work only when the whole Actions artifact is extracted.
    # Never allow a JUnit-supplied path to create a link outside tests/.
    put("<p><strong>Canonical files:</strong> "
        "<a href='checkpoint.json'>Checkpoint JSON</a> · "
        "<a href='../coverage-summary.json'>Coverage JSON</a> · "
        "<a href='../coverage.lcov'>LCOV</a></p>")
    junit_links = []
    for item in ev.get("junit", []):
        filename = item.get("path")
        if isinstance(filename, str) and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}\.xml", filename):
            junit_links.append(f"<a href='../tests/{e(filename)}'>{e(filename)}</a>")
    if junit_links:
        put("<p><strong>JUnit:</strong> " + " · ".join(junit_links) + "</p>")
    put(table(("Input", "SHA-256"), evidence_rows))
    put("<p class='hint'>This HTML is an unsigned rendering of the checkpoint. "
        "Use the original JSON, JUnit XML, LCOV and independently verified artifact digests for audits.</p>")
    put("</div></section>")
    put("<footer>No JavaScript · no external resources · generated from canonical test evidence. "
        "Runtime qualification and security attestations are separate.</footer></main></body></html>")
    return "\n".join(blocks)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--checkpoint", type=Path, required=True)
    ap.add_argument("--coverage", type=Path)
    ap.add_argument("--junit-dir", type=Path)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    try:
        checkpoint = json.loads(args.checkpoint.read_text(encoding="utf-8"))
        coverage = (json.loads(args.coverage.read_text(encoding="utf-8"))
                    if args.coverage and args.coverage.is_file() else None)
        cases = cases_from_junit(args.junit_dir)
        text = render(checkpoint, coverage, cases)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"HTML quality report: {args.output} ({len(cases)} test executions)")
        return 0
    except (ValueError, OSError, ET.ParseError, json.JSONDecodeError, TypeError, KeyError) as exc:
        print(f"FAIL: HTML quality report: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
