#!/usr/bin/env bash
# Offline native/SQLite contract regression; no live provider requests.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${CC:=clang}"
: "${CXX:=clang++}"
: "${PYTHON:=python3}"
# Keep the existing O0 baseline by default; fortified toolchains may need O1.
: "${PORTABLE_CPP_OPT:=0}"
case "$PORTABLE_CPP_OPT" in
  0|1) ;;
  *) echo 'PORTABLE_CPP_OPT must be 0 or 1' >&2; exit 2 ;;
esac
echo "PORTABLE-METRIC cpp_optimization=$PORTABLE_CPP_OPT"
mkdir -p build-linux/sqlite
"$PYTHON" tools/build_identity.py --header build-linux/doujinstyle_build_version.hpp >/dev/null
"$PYTHON" native/sqlite_source.py build-linux/sqlite
# Both independent compile lanes use identical flags to the former serial build.
# The publisher and SQLite are the expensive TUs; overlap them without a
# competing architecture or unbounded worker pool. All children must succeed.
compile_sqlite_lane() {
  "$CC" -std=c11 -O2 -fPIC -I build-linux/sqlite -c native/sqlite_amalgamation.c -o build-linux/sqlite3.o &&
    "$CC" -std=c11 -O2 -fPIC -DUTF8PROC_STATIC -c native/third_party/utf8proc/utf8proc.c -o build-linux/utf8proc.o &&
    "$CC" -shared build-linux/sqlite3.o -o build-linux/libsqlite3.so
}
compile_cpp_lane() {
  local flags=(-std=c++20 "-O$PORTABLE_CPP_OPT" -g0 -Wall -Wextra -Werror -pthread -I native -I build-linux -I build-linux/sqlite)
  "$CXX" "${flags[@]}" -c native/publish_references.cpp -o build-linux/publish_references.o &&
    "$CXX" "${flags[@]}" -c native/descriptor.cpp -o build-linux/descriptor.o &&
    "$CXX" "${flags[@]}" -c native/worker_build_identity.cpp -o build-linux/worker_build_identity.o &&
    "$CXX" -std=c++20 -O2 -Wall -Wextra -Wpedantic -Werror -I native native/test_facet_sort.cpp -o build-linux/test_facet_sort
}
timed() {
  local label="$1" start=$SECONDS rc=0
  shift
  "$@" || rc=$?
  printf 'PORTABLE-METRIC stage=%s seconds=%s exit=%s\n' "$label" "$((SECONDS-start))" "$rc"
  return "$rc"
}
start=$SECONDS
timed sqlite_lane compile_sqlite_lane &
sqlite_pid=$!
timed cpp_lane compile_cpp_lane &
cpp_pid=$!
failed=0
wait "$sqlite_pid" || failed=1
wait "$cpp_pid" || failed=1
if (( failed )); then
  echo 'Portable compilation failed' >&2
  exit 1
fi
timed publisher_link "$CXX" -pthread build-linux/publish_references.o build-linux/descriptor.o build-linux/worker_build_identity.o \
  build-linux/sqlite3.o build-linux/utf8proc.o -lz -lcrypto -o build-linux/publish_references
echo "PORTABLE-METRIC stage=compile_and_link seconds=$((SECONDS-start)) workers=2"
build-linux/publish_references --version
build-linux/test_facet_sort
export DOUJINSTYLE_TEST_PUBLISHER="$PWD/build-linux/publish_references"
export LD_PRELOAD="$PWD/build-linux/libsqlite3.so${LD_PRELOAD:+:$LD_PRELOAD}"
"$PYTHON" -m tools.portable_test_suite
"$PYTHON" native/pack_search_worker.py --inventory > build-linux/python-inventory.json
"$PYTHON" - <<'__INVENTORY_PY__'
import json
from pathlib import Path
x=json.loads(Path('build-linux/python-inventory.json').read_text())
assert x['python_lines'] <= 1375, x['python_lines']
assert x['fallback_python_lines'] == 0
print('Runtime Python:', x['python_lines'], 'lines; fallbacks:', x['fallback_python_lines'])
__INVENTORY_PY__
