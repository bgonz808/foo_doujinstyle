"""Read-only GitHub issue lifecycle audit; never mutates remote metadata.

An issue body's *audited* status is separate from GitHub's open/closed state.
Do not count pull requests as issues or a planned test as executed evidence.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlencode
from urllib.request import Request, urlopen

REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
HEADER = re.compile(r"(?m)^## Current work status \(audited (\d{4}-\d{2}-\d{2})\)\s*$")
FIELDS = ("Lifecycle", "Verified progress", "Remaining / next action", "Evidence / owner")
OPEN_STATES = (
    "In progress", "Partially complete", "Investigation started",
    "Design started", "Planned/design developed", "Blocked on",
    "Ready for implementation",
)
CLOSED_STATES = ("Completed", "Superseded/resolved")


def issue_status(issue: dict, *, today: date, stale_days: int) -> tuple[list[str], list[str], dict]:
    number = issue.get("number")
    body = issue.get("body") or ""
    state = issue.get("state")
    errors: list[str] = []
    warnings: list[str] = []
    stamps = HEADER.findall(body)
    if len(stamps) != 1:
        errors.append(f"expected exactly one dated work-status heading, got {len(stamps)}")
    when: date | None = None
    if len(stamps) == 1:
        try:
            when = date.fromisoformat(stamps[0])
            if when > today:
                errors.append("audit date lies in the future")
            if (today - when).days > stale_days and state == "open":
                warnings.append(f"status unreviewed for {(today - when).days} days")
        except ValueError:
            errors.append("invalid YYYY-MM-DD audit date")
    values: dict[str, str] = {}
    for field in FIELDS:
        found = re.findall(r"(?m)^\*\*" + re.escape(field) + r":\*\* ([^\r\n]+)$", body)
        if len(found) != 1 or not found[0].strip(" ."):
            errors.append(f"{field}: expected one nonempty status field")
        else:
            values[field] = found[0].strip()
    lifecycle = values.get("Lifecycle", "")
    if state not in ("open", "closed"):
        errors.append("unknown GitHub issue state")
    elif not any(lifecycle.startswith(s) for s in (OPEN_STATES if state == "open" else CLOSED_STATES)):
        errors.append(f"lifecycle contradicts GitHub issue state '{state}'")
    if state == "open" and not values.get("Remaining / next action"):
        errors.append("open issue lacks actionable next step")
    if not issue.get("html_url"):
        errors.append("missing issue permalink")
    return errors, warnings, {
        "number": number, "url": issue.get("html_url"), "state": state,
        "lifecycle": lifecycle, "audited": when.isoformat() if when else None,
        "errors": errors, "warnings": warnings,
    }


def audit(issues: list[dict], *, repo: str, today: date, stale_days: int,
          source_sha: str = "") -> dict:
    if not REPO.fullmatch(repo):
        raise ValueError("expected owner/repository")
    if stale_days < 1:
        raise ValueError("stale-days must be positive")
    issues = [i for i in issues if "pull_request" not in i]
    if not issues:
        raise ValueError("no GitHub issues retrieved; refusing an empty pass")
    seen: set[int] = set()
    results = []
    for issue in issues:
        number = issue.get("number")
        if type(number) is not int or number <= 0 or number in seen:
            raise ValueError("missing or duplicated GitHub issue number")
        seen.add(number)
        results.append(issue_status(issue, today=today, stale_days=stale_days)[2])
    results.sort(key=lambda row: row["number"])
    return {
        "schema_version": 1, "kind": "read-only-issue-lifecycle-audit",
        "repository": repo, "source_commit": source_sha,
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "issue_count": len(results),
        "open_count": sum(row["state"] == "open" for row in results),
        "closed_count": sum(row["state"] == "closed" for row in results),
        "error_count": sum(len(row["errors"]) for row in results),
        "stale_warning_count": sum(len(row["warnings"]) for row in results),
        "results": results,
    }


def fetch_issues(repo: str, token: str) -> list[dict]:
    if not REPO.fullmatch(repo):
        raise ValueError("expected owner/repository")
    result = []
    for page in range(1, 101):
        url = f"https://api.github.com/repos/{repo}/issues?" + urlencode(
            {"state": "all", "per_page": 100, "page": page})
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "foo-doujinstyle-issue-lifecycle-audit",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        with urlopen(Request(url, headers=headers), timeout=20) as response:
            data = json.load(response)
        if not isinstance(data, list):
            raise ValueError("unexpected GitHub issues API response")
        result.extend(data)
        if len(data) < 100:
            return result
    raise ValueError("issue pagination exceeded 100 pages")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="bgonz808/foo_doujinstyle")
    parser.add_argument("--fixture", type=Path, help="offline API response JSON for tests")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stale-days", type=int, default=60)
    parser.add_argument("--fail-on-stale", action="store_true")
    args = parser.parse_args(argv)
    issues = (json.loads(args.fixture.read_text(encoding="utf-8"))
              if args.fixture else fetch_issues(args.repo, os.environ.get("GITHUB_TOKEN", "")))
    if not isinstance(issues, list):
        parser.error("fixture must be GitHub issues API array")
    report = audit(issues, repo=args.repo, today=date.today(), stale_days=args.stale_days,
                   source_sha=os.environ.get("GITHUB_SHA", ""))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Issue lifecycle audit: {report['issue_count']} issues, "
          f"{report['error_count']} errors, {report['stale_warning_count']} stale warnings")
    for row in report["results"]:
        for message in row["errors"]:
            print(f"ERROR #{row['number']}: {message}")
        for message in row["warnings"]:
            print(f"WARNING #{row['number']}: {message}")
    return int(report["error_count"] > 0 or
               (args.fail_on_stale and report["stale_warning_count"] > 0))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError) as exc:
        sys.exit(f"Issue lifecycle audit could not complete: {exc}")
