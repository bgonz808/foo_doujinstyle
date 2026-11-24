# Issue lifecycle and proof-of-work discipline

GitHub Issues are the persistent engineering state for this repo. A conversation,
merged PR, CI log or personal note **does not replace issue updates**.

## Mandatory read-before-write / write-back

1. **Start:** read the owning issue, its linked dependencies and historic PR
   heads, `docs/WORKSTREAM_LEDGER.md`, current `VERSION` and source `main` SHA.
   Record a dated status and a specific first task **before implementing**.
2. **During:** update the issue whenever scope, dependency, extracted code,
   security decision, or accepted evidence changes. Reference the unique
   candidate version and exact SHA; clearly separate source-only, native
   build, fixture testing, host GUI, decoded PCM, output PCM and release status.
3. **Merge:** write the exact merge SHA and CI run URL, measured test/coverage
   denominators and residual work **in the owning issue**. Mark work completed
   or superseded only when every remaining requirement has another explicit
   owner. Preserve source PR heads; do not call a closed historic PR merged.
4. **Handoff:** ensure `**Remaining / next action:**` names a concrete next
   executable step, verified blocker, or owner, not “continue work.”

## Machine-readable issue body contract

Every non-PR issue includes exactly one leading or interior section:

```markdown
## Current work status (audited YYYY-MM-DD)

**Lifecycle:** In progress — precise scope.

**Verified progress:** Tests run and evidence actually seen.

**Remaining / next action:** Specific unresolved work or no further action.

**Evidence / owner:** Links to PR, CI run, code or the owning follow-up issue.
```

Audited dates use YYYY-MM-DD. Update the date **when reviewing real evidence**
or recording a genuinely new decision, not merely to keep a metric green.
These are tracked statuses, not GitHub labels. The state terminology is:

- **Open:** `Ready for implementation`, `Planned/design developed`,
  `Design started`, `Investigation started`, `In progress`,
  `Partially complete`, or `Blocked on` an explicitly described dependency.
- **Closed:** `Completed` (scope done, evidence linked) or
  `Superseded/resolved` (all unfinished scope has a linked owner).

An incomplete feature must not be closed merely because a test harness was
built. A green BinSkim scan does not establish a successful Windows host run;
a plotted 648-case matrix is not 648 executed cases.

## Automated audit (issue #45)

`.github/workflows/issue-hygiene.yml` runs on a weekly schedule or an
authorized manual dispatch, **not** on untrusted pull requests. It uses
only `contents: read` + `issues: read`, GitHub's short-lived scoped token,
and does not create comments or mutate issues. It writes a compact JSON
artifact with issue numbers, permalinks, lifecycle states, source SHA,
errors and stale-status warnings. It does **not** publish issue bodies,
source files, proprietary media, credentials or sensitive paths.

Missing or duplicated headings, missing fields, mismatched GitHub open/closed
states, future audit dates, empty issue results and duplicate issue identities
fail the audit. An open status older than 60 days emits a warning by default,
not a blocker to unrelated native security builds. For explicit policy testing
`--fail-on-stale` can make it a failure. GitHub API/pagination failures are
errors, never an empty green pass.

Offline negative tests run inside the required `workflow-security` CI check:

```bash
python3 -m unittest -v tools.test_issue_status_audit
python3 -m tools.issue_status_audit --output /tmp/issue-audit.json
```

The live command is intended for reviewed environments with read-only
GitHub Issues access; local offline tests use `--fixture` to avoid network.
This automation checks **hygiene and state consistency**, not semantic truth.
Actual feature claims still require their source/CI/host evidence to be read
and independently assessed.
