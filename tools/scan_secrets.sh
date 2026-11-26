#!/usr/bin/env bash
# Initial observation only: do not auto-baseline, suppress or publish findings.
set -euo pipefail
umask 077
start=$SECONDS
work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT
archive="$work/gitleaks.tar.gz"
curl -fsSL --retry 3 --proto '=https' --tlsv1.2 \
  https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz \
  -o "$archive"
printf '%s  %s\n' '551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb' "$archive" | sha256sum -c -
tar -xzf "$archive" -C "$work" gitleaks
scanner="$work/gitleaks"
"$scanner" version
# Force the bundled default rules; do not load repository overrides/ignore files.
printf '[extend]\nuseDefault = true\n' > "$work/default.toml"
touch "$work/.gitleaksignore"
unset GITLEAKS_CONFIG GITLEAKS_CONFIG_TOML
flags=(--config "$work/default.toml" --gitleaks-ignore-path "$work/.gitleaksignore"
       --ignore-gitleaks-allow --redact=100 --no-banner --no-color --exit-code 23 --timeout 120)
# Fresh synthetic test credential, not a real credential. Never print the input.
set +e
printf 'token = "ghp_%s"\n' "$(openssl rand -hex 18)" |
  "$scanner" stdin "${flags[@]}" > "$work/self-test.log" 2>&1
rc=$?
set -e
if [[ "$rc" != 23 ]]; then
  echo 'Gitleaks synthetic positive self-test failed' >&2
  exit 1
fi
[[ "$(git rev-parse --is-shallow-repository)" == false ]]
[[ "$(git rev-parse HEAD)" == "$GITHUB_SHA" ]]
count="$(git rev-list --count HEAD)"
[[ "$count" =~ ^[1-9][0-9]*$ ]]
set +e
"$scanner" git "${flags[@]}" --log-opts='--full-history -m HEAD' \
  --report-format json --report-path "$work/report.json" . \
  > "$work/scan.log" 2>&1
rc=$?
set -e
# Never emit raw scanner logs or upload a report, even on error.
if [[ "$rc" != 0 && "$rc" != 23 ]]; then
  echo "Gitleaks scanner failure (exit $rc)" >&2
  exit 1
fi
python3 - "$work/report.json" "$rc" "$count" "$((SECONDS-start))" <<'PY'
import json, pathlib, sys
p, rc, commits, elapsed = sys.argv[1:]
# Gitleaks emits an empty array for a clean JSON report. Fail closed on
# missing/malformed evidence, including a scan that claims findings but no report.
data = json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
if not isinstance(data, list) or bool(data) != (rc == "23"):
    raise SystemExit("Inconsistent Gitleaks report")
print(f"SECRET-SCAN-METRIC mode=initial_observation commits={commits} findings={len(data)} seconds={elapsed}")
if data:
    print("::warning::Secret scan findings require private maintainer triage; no finding contents retained")
PY
