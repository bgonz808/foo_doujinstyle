"""Adjudicate a redacted Gitleaks report without printing finding contents.

Keep the detector's default rules and empty ignore file. An immutable,
historical false positive is not permission for any future occurrence.
"""
import json
from pathlib import Path
import sys

# Confirmed English prose, not a credential. Reviewed 2026-11-26.
# The exact historical line is preserved by Git; current prose was reworded.
HISTORICAL = {
    "Commit": "29d969763faf1d1492b5418773e27a596017df6c",
    "File": "native/MIGRATION.md",
    "RuleID": "generic-api-key",
    "StartLine": 111,
    "EndLine": 111,
}
FINGERPRINT = "{Commit}:{File}:{RuleID}:{StartLine}".format(**HISTORICAL)


def verdict(data, rc):
    if rc not in (0, 23) or not isinstance(data, list) or bool(data) != (rc == 23):
        raise ValueError("Inconsistent scanner verdict")
    accepted = 0
    unexpected = 0
    for row in data:
        if not isinstance(row, dict) or not isinstance(row.get("Fingerprint"), str):
            raise ValueError("Invalid finding record")
        match = (row["Fingerprint"] == FINGERPRINT
                 and all(row.get(k) == v for k, v in HISTORICAL.items()))
        # Admit at most one report record, even if the same fingerprint repeats.
        if match and accepted == 0:
            accepted += 1
        else:
            unexpected += 1
    return accepted, unexpected


def main():
    if len(sys.argv) != 5:
        raise SystemExit("Expected report, scanner exit, commit count and seconds")
    p, rc, commits, seconds = sys.argv[1:]
    try:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
        known, new = verdict(data, int(rc))
        commits, seconds = int(commits), int(seconds)
        if commits <= 0 or seconds < 0:
            raise ValueError("Invalid metrics")
    except (OSError, ValueError, TypeError, UnicodeError):
        raise SystemExit("Invalid or incomplete secret scan evidence") from None
    print(f"SECRET-SCAN-METRIC mode=blocking commits={commits} findings={len(data)} "
          f"historical_false_positives={known} unadjudicated={new} seconds={seconds}")
    if new:
        raise SystemExit("Unadjudicated finding(s): reproduce locally for private triage")
    if not known:
        raise SystemExit("Historical adjudication no longer reproduced; review pinned detector and history")


if __name__ == "__main__":
    main()
