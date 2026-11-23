# Native quality evidence — migration and post-migration

`quality/features.json` is the reviewed feature/test traceability manifest.
The build generates `build-linux/quality/evidence/checkpoint.json` and
`summary.md`. No generated checkpoint is committed. Historical
`VALIDATION-v*.md` documents remain an archive, not the current runbook.

## Evidence and limitations

- **LLVM source-based coverage:** Clang 23.1.6 instruments the portable native
  publisher (first-party C++ and included headers). Tests exercise this
  executable through real IPC and SQLite. `llvm-profdata-23` merges profiles
  and `llvm-cov-23` emits line/function/region/branch data and LCOV.
- **JUnit tests:** original `unittest` cases get individual stable test IDs,
  status, duration and failure detail. Robot Framework xUnit is also accepted.
- **Runtime inventory:** the packer counts actual packaged Python lines, not
  repository test/reference files. Deprecated-but-executable is not retired.
- **Features:** manifest patterns match observed test IDs. Missing/skipped
  tests remain `not_run` or `partial`, never treated as passed.
- **Identity:** SHA-256 for consumed evidence, Git commit, project version and
  Actions run ID identify the checkpoint.

**Denominator:** instrumented portable publisher only. Whole-DLL, Windows,
foobar2000-host and per-feature E2E coverage are **not measured** here.
Do not merge heterogeneous architecture coverage or infer absent coverage
from missing .profraw. Hard-terminated workers may not write coverage.

## Local use

```sh
QUALITY_COVERAGE=1 \
  LLVM_COV=llvm-cov-23 LLVM_PROFDATA=llvm-profdata-23 \
  CC=clang-23 CXX=clang++-23 bash tools/test_portable.sh
python3 -m tools.quality.report \
  --coverage build-linux/quality/coverage-summary.json \
  --junit-dir build-linux/quality/tests \
  --inventory build-linux/python-inventory.json \
  --features quality/features.json --version-file VERSION \
  --output build-linux/quality/evidence --require-coverage
```

`portable.yml` runs these steps automatically and uploads
`native-quality-portable` even if tests fail (except cancelled runs).
Evidence is unsigned WIP data, not release qualification or SLSA provenance.

## Migration work log

1. Update `quality/features.json` only for changed ownership, behavior,
   test identity, or migration state; GitHub issue/PR records the decision.
2. Increment `VERSION` for every source candidate. CI measures installed
   Python and real instrumented C++ coverage. Never credit annotation-only
   source changes as Python retirement.
3. Assess parity, negative paths, tests that are skipped/not_run, coverage
   denominator, and newly uncovered native code before deleting runtime Python.
4. Require actual foobar2000 host verification for release qualification.
5. Actions artifacts expire after 30 days. Keep durable decisions in GitHub
   issues/PRs; promoted release evidence needs durable retention separately.

## Reusing the reporter across projects

`tools/quality/report.py` and
`.github/actions/native-quality-report/action.yml` are project-independent:
JUnit XML, optional LLVM coverage JSON, optional Python inventory and
feature mapping. They do NOT build or instrument another project's code.

Caller (pin both action references to full verified commits in production):

```yaml
- name: Build and execute instrumented native tests
  run: ./ci/run-native-tests.sh
- uses: bgonz808/foo_doujinstyle/.github/actions/native-quality-report@<PINNED_FULL_SHA>
  with:
    coverage: build/quality/coverage-summary.json
    junit-directory: build/quality/junit
    features: quality/features.json
    output: build/quality/checkpoint
    require-coverage: 'true'
- uses: actions/upload-artifact@<PINNED_FULL_SHA>
  if: ${{ !cancelled() }}
  with:
    name: native-quality
    path: build/quality/
```

Each caller supplies its platform build/test adapter, generated coverage
and test mapping. This is suitable for `foo_qobuz` and `foo_opushd`,
but their repository/build configurations must be verified before enabling
their CI. Do not copy DoujinStyle's runtime inventory or migration markers.

After two consumers are validated, extract the generic composite action and
reporter to a separate public, versioned quality-tools repository. For now,
a SHA-pinned composite action provides direct reuse without maintaining
another runner or inventing a test framework.

## Robot E2E

Issue #17 tracks the real foobar2000 interactive desktop harness. Robot
Framework + FlaUI will produce JUnit XML, assertions and screenshots using
synthetic profile/transport fixtures. When it exists, merge Robot JUnit into
a **separate host-quality checkpoint**; do not report E2E code coverage until
the foobar2000 DLL and native child process are instrumented. Keep host
qualification distinct from portable test status.
