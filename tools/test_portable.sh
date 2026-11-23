#!/usr/bin/env bash
# Offline native/SQLite contract regression; no live provider requests.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${CC:=clang}"
: "${CXX:=clang++}"
: "${PYTHON:=python3}"
COVERAGE_FLAGS=()
if [[ "${QUALITY_COVERAGE:-0}" == "1" ]]; then
  mkdir -p build-linux/quality/profiles build-linux/quality/tests
  COVERAGE_FLAGS=(-fprofile-instr-generate -fcoverage-mapping)
  export LLVM_PROFILE_FILE="$PWD/build-linux/quality/profiles/%p-%m.profraw"
fi
mkdir -p build-linux/sqlite
"$PYTHON" tools/build_identity.py --header build-linux/doujinstyle_build_version.hpp >/dev/null
"$PYTHON" native/sqlite_source.py build-linux/sqlite
"$CC" -std=c11 -O2 -fPIC -I build-linux/sqlite -c native/sqlite_amalgamation.c -o build-linux/sqlite3.o
"$CC" -std=c11 -O2 -fPIC -DUTF8PROC_STATIC -c native/third_party/utf8proc/utf8proc.c -o build-linux/utf8proc.o
"$CC" -std=c11 -O2 -fPIC -shared -I build-linux/sqlite native/sqlite_amalgamation.c -o build-linux/libsqlite3.so
"$CXX" -std=c++20 -O0 -g0 -Wall -Wextra -Werror -pthread "${COVERAGE_FLAGS[@]}" -I native -I build-linux -I build-linux/sqlite \
  native/publish_references.cpp native/descriptor.cpp build-linux/sqlite3.o build-linux/utf8proc.o \
  -lz -lcrypto -o build-linux/publish_references
build-linux/publish_references --version
export DOUJINSTYLE_TEST_PUBLISHER="$PWD/build-linux/publish_references"
export LD_PRELOAD="$PWD/build-linux/libsqlite3.so${LD_PRELOAD:+:$LD_PRELOAD}"
if [[ "${QUALITY_COVERAGE:-0}" == "1" ]]; then
  "$PYTHON" -m tools.quality.unittest_runner --start . --pattern 'test*.py' \
    --suite-name portable-root --output build-linux/quality/tests/portable-root.xml
  # Root discovery already includes native/test*.py; never run them twice.
  "$PYTHON" - <<'__QUALITY_DISCOVERY_GUARD__'
from xml.etree import ElementTree as ET
root = ET.parse('build-linux/quality/tests/portable-root.xml').getroot()
native = [case for case in root.iter('testcase') if
          case.get('classname', '').startswith('native.test_')]
if len(native) < 20:
    raise SystemExit('FAIL: native contract test discovery unexpectedly incomplete')
print(f'Native unittest cases discovered from repository root: {len(native)}')
__QUALITY_DISCOVERY_GUARD__
else
  "$PYTHON" -m unittest discover -q -p 'test*.py'
  "$PYTHON" -m unittest discover -s native -p 'test*.py' -q
  "$PYTHON" -m unittest tools.test_build_identity -q
fi
"$PYTHON" native/pack_search_worker.py --inventory > build-linux/python-inventory.json
"$PYTHON" - <<'__INVENTORY_PY__'
import json
from pathlib import Path
x=json.loads(Path('build-linux/python-inventory.json').read_text())
assert x['python_lines'] <= 1375, x['python_lines']
assert x['fallback_python_lines'] == 0
print('Runtime Python:', x['python_lines'], 'lines; fallbacks:', x['fallback_python_lines'])
__INVENTORY_PY__

if [[ "${QUALITY_COVERAGE:-0}" == "1" ]]; then
  "$PYTHON" -m tools.quality.coverage \
    --binary build-linux/publish_references \
    --profiles build-linux/quality/profiles --source-root native \
    --scope portable-native-publisher --output build-linux/quality \
    --llvm-profdata "${LLVM_PROFDATA:-llvm-profdata}" \
    --llvm-cov "${LLVM_COV:-llvm-cov}"
fi
