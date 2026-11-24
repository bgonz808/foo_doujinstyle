"""Repo-specific, fail-closed GitHub Actions trust-boundary policy.

Not a YAML linter or substitute for actionlint/zizmor.  Only checks a few
invariants where the consequence of accidentally changing one is high.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

# Audited versions: both use Node.js 24. A complete SHA pin is necessary but
# does not by itself prove the referenced action executes immutable inputs.
APPROVED_ACTIONS = {
    "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",  # v7.0.1
    "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",  # v7.0.1
    "actions/download-artifact": "9000827ccba6bdab643e8b6fd33ac0654aef8333",  # v8.0.2
    "actions/attest": "1e69f48acb82d1966a394da916b4c1698aa569d6",  # v4.2.2
}
USE = re.compile(r"(?m)^\s*-\s*uses:\s*([^\s#]+)")
SHA = re.compile(r"^[a-f0-9]{40}$")
JOB_USE = re.compile(r"(?m)^\s{4}uses:\s*([^\s#]+)")
REVIEWED_REUSABLE = {".github/workflows/sqlite-cache-repeat.yml":
                     "./.github/workflows/sqlite-cross-runner-experiment.yml"}
RESTRICTED = re.compile(
    r"(?m)^\s*(?:permissions:\s*(?:write-all|read-all)|"
    r"(?:contents|actions|packages|id-token|security-events|attestations):\s*write)\s*(?:#.*)?$"
)
TRIGGERS = re.compile(r"(?m)^\s*(?:pull_request_target|workflow_run|repository_dispatch):")
REVIEWED_SIGNER = ".github/workflows/windows-build.yml"
SIGNER_HEADER = "\n  provenance:\n"



def audit(root: Path) -> list[str]:
    workflows = root / ".github" / "workflows"
    errors = []
    files = sorted([*workflows.glob("*.yml"), *workflows.glob("*.yaml")])
    if not files:
        return [f"No GitHub Actions workflows found in {workflows}"]
    for path in files:
        name = path.relative_to(root).as_posix()
        body = path.read_text(encoding="utf-8")
        if not re.search(r"(?m)^permissions:\s*\n\s{2}contents:\s*read\s*$", body):
            errors.append(f"{name}: top-level permissions must start with contents: read")
        if TRIGGERS.search(body):
            errors.append(f"{name}: untrusted privileged workflow trigger is forbidden")
        if name != REVIEWED_SIGNER:
            if RESTRICTED.search(body):
                errors.append(f"{name}: write permissions are prohibited outside reviewed CI provenance")
        else:
            prefix, separator, signer = body.partition(SIGNER_HEADER)
            if not separator:
                errors.append(f"{name}: reviewed provenance job is missing")
            elif RESTRICTED.search(prefix):
                errors.append(f"{name}: elevated token outside provenance job")
            else:
                # The CI builder cannot sign PRs. Its separate signing job is
                # restricted to trusted main source and completed build gates.
                required = (
                    "name: ci-provenance",
                    "if: (github.event_name == 'push' || github.event_name == 'workflow_dispatch') && github.ref == 'refs/heads/main'",
                    "needs: [component]",
                    "runs-on: ubuntu-24.04",
                    "permissions:\n      contents: read\n      actions: read\n      attestations: write\n      id-token: write",
                    "python3 -m tools.attestation_gate",
                    "--source-commit \"$SOURCE_COMMIT\" --run-id \"$CI_RUN_ID\"",
                    "name: foo_doujinstyle-windows-wip",
                    "name: foo_doujinstyle-binskim-evidence",
                    "subject-path: dist/package/*.fb2k-component",
                    "predicate-path: dist/security-predicate.json",
                )
                if any(token not in signer for token in required):
                    errors.append(f"{name}: main-only provenance contract incomplete")
                writes = re.findall(r"(?m)^\s+([a-z-]+):\s*write\s*(?:#.*)?$", signer)
                if len(writes) != 2 or set(writes) != {"attestations", "id-token"}:
                    errors.append(f"{name}: unexpected provenance token scope")
                if signer.count("uses: actions/attest@") != 2:
                    errors.append(f"{name}: two package-bound attestations required")
                if re.search(r"(?m)^  [A-Za-z][A-Za-z0-9_-]*:\s*$", signer):
                    errors.append(f"{name}: no jobs may follow the privileged signer")
                if "environment: signed-releases" in signer:
                    errors.append(f"{name}: CI provenance must not assert release promotion")
        if re.search(r"(?m)^\s*runs-on:\s*(?:self-hosted|\[.*self-hosted)", body):
            errors.append(f"{name}: self-hosted runner needs a reviewed threat model")
        for call in JOB_USE.finditer(body):
            if call.group(1) != REVIEWED_REUSABLE.get(name):
                errors.append(f"{name}: unreviewed reusable workflow: {call.group(1)}")
            if re.search(r"(?m)^\s{4}secrets\s*:", body):
                errors.append(f"{name}: reusable workflow must not receive secrets")
        for match in USE.finditer(body):
            target = match.group(1)
            if target.startswith("./"):
                continue
            action, sep, revision = target.rpartition("@")
            if not sep or not SHA.fullmatch(revision):
                errors.append(f"{name}: unpinned or malformed action: {target}")
                continue
            allowed = APPROVED_ACTIONS.get(action)
            if allowed is None or revision != allowed:
                errors.append(f"{name}: action not present at approved digest: {target}")
            if action == "actions/checkout":
                # Ensure the token is not left in .git/config after checkout.
                # actionlint confirms the YAML structure; here we inspect the
                # immediate `with` stanza for the mandatory setting.
                after = body[match.end():]
                after = re.split(r"(?m)^\s{6}-\s+(?:name|uses):", after, maxsplit=1)[0]
                if not re.search(r"(?m)^\s+persist-credentials:\s*false\s*$", after):
                    errors.append(f"{name}: checkout must set persist-credentials: false")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    errors = audit(args.root)
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if errors:
        print(f"CI trust-boundary audit: FAIL ({len(errors)} findings)", file=sys.stderr)
        return 1
    print("CI trust-boundary audit: PASS (known Actions, read-only tokens, safe triggers)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
