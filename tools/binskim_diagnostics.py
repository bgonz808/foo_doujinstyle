"""Sanitized, bounded BinSkim BA2024 linked-object diagnostics.

Never emit raw SARIF messages, filenames, source paths or arbitrary strings from
PDB metadata. Only fixed categories and a fixed allowlist of runtime libraries.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re

ARCHES = ("Win32", "x64", "ARM64EC")
PE_NAMES = ("foo_doujinstyle.dll", "publish_references.exe")
KNOWN_RUNTIME = ("msvcrt.lib", "msvcrtd.lib", "msvcprt.lib", "msvcprtd.lib",
                 "libcmt.lib", "libcmtd.lib", "libcpmt.lib", "libcpmtd.lib",
                 "vcruntime.lib", "vcruntimed.lib")
TOKEN = re.compile(r"(?i)\b[a-z][a-z0-9_.-]{0,79}\.(?:lib|obj)\b")


def categories(value: str) -> Counter:
    result = Counter()
    for name in TOKEN.findall(value):
        value = name.lower()
        if value in KNOWN_RUNTIME or value.startswith(("vcruntime", "msvcp", "ucrt")):
            result["msvc_runtime"] += 1
        elif value.startswith(("foobar", "pfc", "component_client")):
            result["foobar_sdk"] += 1
        elif value in {"component.obj", "search.obj", "descriptor.obj",
                       "publish_references.obj"}:
            result["first_party"] += 1
        elif value.startswith("sqlite"):
            result["sqlite"] += 1
        elif value.startswith("utf8proc"):
            result["utf8proc"] += 1
        else:
            result["other_linked"] += 1
    return result


def inspect_reports(folder: Path) -> dict:
    report = {"schema_version": 1, "rule": "BA2024",
              "targets": {}, "complete_reports": 0}
    for arch in ARCHES:
        for name in PE_NAMES:
            target = f"{arch}/{name}"
            path = folder / f"{arch}-{name}.sarif"
            row = {"present": path.is_file(), "finding_count": 0,
                   "categories": {}, "details_available": False}
            if path.is_file():
                try:
                    obj = json.loads(path.read_text(encoding="utf-8"))
                    runs = obj.get("runs", [])
                    if not isinstance(runs, list):
                        raise ValueError("bad runs")
                    count = Counter()
                    for run in runs:
                        for result in run.get("results", []):
                            # SARIF kind is optional: omission means fail, as in the security gate.
                            kind = result.get("kind", "fail")
                            if result.get("ruleId") != "BA2024" or kind not in ("fail", "review", "open"):
                                continue
                            row["finding_count"] += 1
                            message = result.get("message") or {}
                            texts = [message.get("text", "")] + (message.get("arguments") or [])
                            for raw in texts:
                                if isinstance(raw, str):
                                    count.update(categories(raw))
                                    if raw.strip():
                                        row["details_available"] = True
                    row["categories"] = dict(sorted(count.items()))
                    # Schema intentionally contains neither raw module names nor message bytes.
                    # A missing message is still a security failure, never a clean claim.
                    report["complete_reports"] += 1
                except (ValueError, TypeError, KeyError, UnicodeError):
                    row["parse_error"] = True
            report["targets"][target] = row
    return report


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--reports", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    result = inspect_reports(a.reports)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(result, sort_keys=True, indent=2) + "\n"
    a.output.write_text(content, encoding="utf-8")
    print(content)


if __name__ == "__main__":
    main()
