"""Packaged Python AST burndown and host test readiness for migration PRs.

This is source packaging inventory, not runtime call coverage, Windows-host
correctness, or proof that Python is never imported by the native component.
"""
from __future__ import annotations
import argparse
import fnmatch
import json
from pathlib import Path
import re
import subprocess
import sys
from native.embed_worker import source_inventory

STATES = {"python-adapter-active", "native-implemented",
          "native-implemented-host-unqualified", "regression-not-qualified",
          "not-applicable"}

def report(root: Path, features: dict, inventory: dict, commit: str) -> dict:
    if re.fullmatch("[0-9a-f]{40}",commit) is None:
        raise ValueError("Full source SHA required")
    if features.get("schema_version") != 1:
        raise ValueError("Invalid feature manifest schema")
    baseline = features.get("migration_baseline") or {}
    before = baseline.get("packaged_python_lines")
    if type(before) is not int or before <= 0:
        raise ValueError("Missing credible Python baseline")
    now = inventory.get("python_lines")
    fallback = inventory.get("fallback_python_lines")
    deprecated = inventory.get("deprecated_active_lines")
    if any(type(v) is not int or v < 0 for v in (now,fallback,deprecated)):
        raise ValueError("Invalid AST inventory")
    if now > before:
        raise ValueError("Packaged Python runtime line regression (source inventory)")
    if fallback > now or deprecated > now:
        raise ValueError("Invalid Python marker count")
    seen = set()
    rows = []
    for item in features.get("features", []):
        fid = item.get("id")
        if (not isinstance(fid,str) or not re.fullmatch("[a-z][a-z0-9-]{1,70}",fid)
                or fid in seen or item.get("migration_state") not in STATES):
            raise ValueError("Invalid or repeated migration feature definition")
        seen.add(fid)
        suites = item.get("host_e2e_tests")
        contracts = item.get("contract_tests")
        if not isinstance(suites,list) or not suites or not all(
                isinstance(x,str) and x.startswith("doujinstyle.") for x in suites):
            raise ValueError("Feature missing named Windows-host test contract")
        if not isinstance(contracts,list) or not all(isinstance(x,str) for x in contracts):
            raise ValueError("Malformed portable contract names")
        rows.append({"id":fid,"migration_state":item["migration_state"],
                     "declared_contract_count":len(contracts),
                     "planned_host_suites":suites,
                     "host_cases_executed":0,"native_host_coverage":None,
                     "host_qualified":False,"safe_to_delete_python":False,
                     "qualification":"source-manifest-only-not-executed"})
    if not rows:
        raise ValueError("Empty migration feature inventory")
    return {
      "schema_version":1,
      "kind":"migration-source-and-unexecuted-host-plan",
      "source_commit":commit,
      "baseline_python_lines":before,
      "packaged_python_lines":now,
      "remaining_python_percent":round(100*now/before,3),
      "fallback_python_lines":fallback,
      "deprecated_active_lines":deprecated,
      "retired_python_lines_from_baseline":before-now,
      "host_e2e_cases_executed":0,
      "native_host_line_coverage":None,
      "retirement_qualified":False,
      "features":rows
    }

def main() -> int:
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,default=Path.cwd())
    p.add_argument("--features",type=Path,default=Path("quality/features.json"))
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    try:
        inventory=source_inventory(a.root)
        features=json.loads(a.features.read_text(encoding="utf-8"))
        commit=subprocess.check_output(["git","-C",str(a.root),"rev-parse","HEAD"],
                                        text=True).strip()
        result=report(a.root,features,inventory,commit)
        if a.output.exists():
            raise ValueError("Refusing to overwrite earlier migration measurements")
        a.output.parent.mkdir(parents=True,exist_ok=True)
        a.output.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n",encoding="utf-8")
        print("AST packaged Python:",result["packaged_python_lines"],
              "of baseline",result["baseline_python_lines"],
              "; host E2E not executed and no native host coverage")
        return 0
    except (OSError,ValueError,TypeError,KeyError,json.JSONDecodeError) as exc:
        print("FAIL migration audit:",exc,file=sys.stderr)
        return 1

if __name__=="__main__":
    raise SystemExit(main())
