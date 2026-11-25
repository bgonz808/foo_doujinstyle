"""Prepare a fresh digest-bound portable host template, offline.

Only copies an independently pinned publisher-provisioned base. Does not
install foobar2000, change networking, run arbitrary installers or claim
Null Output/desktop qualification. Never modifies the base or a personal profile.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys
from zipfile import ZipFile

from e2e.host.manifest_generator import build_spec
from e2e.host.native_export import verify_exporter
from e2e.host.run_readiness import MEMBER, no_host, verify_package
from e2e.host.runner import (QualificationError, checked_digest, relative_path,
    sha256, template_digest, verify_desktop_and_egress, verify_source_identity,
    verify_test_dependencies, check_template)

MAX_DLL=128*1024**2


def disjoint(a, b):
    return a!=b and a not in b.parents and b not in a.parents


def inspect_base(base, expected_tree, expected_host, component):
    if not (base/"portable_mode_enabled").is_file():
        raise QualificationError("Publisher-provisioned portable marker required")
    tree,count=template_digest(base)
    if tree!=checked_digest(expected_tree,"base_template_sha256"):
        raise QualificationError("Base template tree mismatch")
    host=base/"foobar2000.exe"
    if not host.is_file() or sha256(host)!=checked_digest(expected_host,"host_sha256"):
        raise QualificationError("Base host binary mismatch")
    # A clean base, not an upgrade of an existing personal installation.
    if any(p.name.casefold()=="foo_doujinstyle.dll" for p in base.rglob("*")):
        raise QualificationError("Base already contains DoujinStyle; start clean")
    if (base/component).exists():
        raise QualificationError("Component target already exists")
    return tree,count


def install_copy(base, dest, package, package_sha, component_path, expected_tree, expected_host):
    component=relative_path(component_path)
    if component.name.casefold()!="foo_doujinstyle.dll" or len(component.parts)<2:
        raise QualificationError("Explicit portable component DLL path required")
    if component.parts[0].casefold()!="user-components":
        raise QualificationError("Component must be under portable user-components")
    if not disjoint(base.resolve(),dest.resolve()) or dest.exists():
        raise QualificationError("Fresh output outside base required")
    original=inspect_base(base,expected_tree,expected_host,component)
    if not package.name.endswith("-e2e-trace.fb2k-component") or sha256(package)!=checked_digest(package_sha,"package_sha256"):
        raise QualificationError("Exact test-only package digest required")
    with ZipFile(package) as z:
        dlls=[p for p in z.namelist() if p.lower().endswith(".dll")]
        if dlls!=[MEMBER] or not 0<z.getinfo(MEMBER).file_size<=MAX_DLL:
            raise QualificationError("Unexpected package DLL inventory")
        shutil.copytree(base,dest)
        if template_digest(dest)!=original:
            raise QualificationError("Portable copy identity mismatch")
        target=dest/component
        target.parent.mkdir(parents=True,exist_ok=True)
        digest=hashlib.sha256()
        size=0
        with z.open(MEMBER) as source,target.open("xb") as output:
            for block in iter(lambda:source.read(1024**2),b""):
                size+=len(block)
                if size>MAX_DLL:
                    raise QualificationError("DLL exceeds limit")
                output.write(block)
                digest.update(block)
        if size!=z.getinfo(MEMBER).file_size or sha256(target)!=digest.hexdigest():
            raise QualificationError("Installed DLL bytes mismatch")
    if template_digest(base)!=original:
        raise QualificationError("Base changed during preparation")
    verify_package(package,package_sha,{"component_sha256":digest.hexdigest()})
    return digest.hexdigest()


def prepare(args):
    base=args.base.resolve(strict=True)
    out=args.output.resolve()
    if not disjoint(base,out) or out.exists():
        raise QualificationError("Fresh output directory outside base required")
    verify_source_identity({"source_commit":args.source_commit})
    desktop=verify_desktop_and_egress()
    no_host()
    packages={"robotframework":args.robotframework_version,"robotframework-flaui":args.flaui_version}
    verify_test_dependencies({"robotframework_version":args.robotframework_version,"flaui_version":args.flaui_version})
    # Import check only. Actual FlaUI desktop interaction remains an anchor test.
    p=subprocess.run([sys.executable,"-c","import robot; import FlaUILibrary"],capture_output=True,text=True,timeout=30)
    if p.returncode:
        raise QualificationError("Robot/FlaUI imports failed: "+p.stderr[:1000])
    verify_exporter(args.etw_exporter,args.etw_exporter_sha256)
    out.mkdir(parents=True,exist_ok=False)
    result={"schema_version":1,"kind":"guest-preparation","status":"failed",
            "source_commit":args.source_commit,"desktop":desktop,
            "package_sha256":args.package_sha256.lower(),
            "exporter_sha256":args.etw_exporter_sha256.lower(),
            "base_template_sha256":args.base_template_sha256.lower(),
            "output_backend_verified":None,"host_e2e_executed":False}
    report=out/"preparation.json"
    try:
        dest=out/"template"
        component_sha=install_copy(base,dest,args.package,args.package_sha256,
            args.component_path,args.base_template_sha256,args.host_sha256)
        spec=build_spec(dest,args.component_path,args.foobar_version,args.source_commit,"null",packages)
        if spec["component_sha256"]!=component_sha:
            raise QualificationError("Manifest DLL mismatch")
        check_template(dest,{**spec,"host_path":relative_path(spec["host_path"]),
                             "component_path":relative_path(spec["component_path"])})
        # Publish the manifest only after all preflight checks pass.
        (out/"manifest.json").write_text(json.dumps(spec,indent=2,sort_keys=True)+"\n",encoding="utf-8")
        result.update(status="prepared",component_sha256=component_sha,
                      template_sha256=spec["template_sha256"],manifest_sha256=sha256(out/"manifest.json"))
    except (OSError,ValueError,subprocess.SubprocessError) as exc:
        result["reason"]=str(exc)
        raise
    finally:
        report.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    return 0


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ("base","output","package","etw-exporter"):
        p.add_argument("--"+name,type=Path,required=True)
    for name in ("source-commit","base-template-sha256","host-sha256","package-sha256",
                 "etw-exporter-sha256","component-path","foobar-version","robotframework-version","flaui-version"):
        p.add_argument("--"+name,required=True)
    args=p.parse_args()
    try:
        return prepare(args)
    except (OSError,ValueError,subprocess.SubprocessError) as exc:
        print("Guest preparation failed:",exc,file=sys.stderr)
        return 2

if __name__=="__main__":
    raise SystemExit(main())
