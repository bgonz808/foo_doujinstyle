"""Source-bound test-only build receipt; not a signature or release attestation.

Trust an independently obtained main-run receipt digest, not the receipt's
self-asserted source identity. No network or executable launch.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from zipfile import ZipFile
from e2e.host.runner import checked_digest,sha256,QualificationError
from e2e.host.native_export import verify_exporter

MEMBER="x64/foo_doujinstyle.dll"
LICENSES=("LICENSE.nlohmann-json","LICENSE.utf8proc")
EXPECTED=sorted((*LICENSES,MEMBER))
def package_dll(package):
    if not package.name.endswith("-e2e-trace.fb2k-component"):
        raise QualificationError("Test-only package required")
    with ZipFile(package) as z:
        if sorted(z.namelist())!=EXPECTED or not 0<z.getinfo(MEMBER).file_size<=128*1024**2:
            raise QualificationError("Unexpected test package members")
        for name in LICENSES:
            if not 0<z.getinfo(name).file_size<=64*1024:
                raise QualificationError("Invalid license member size")
            z.read(name)  # CRC validation; no extraction
        h=hashlib.sha256()
        with z.open(MEMBER) as stream:
            for block in iter(lambda:stream.read(1024**2),b""): h.update(block)
        return h.hexdigest()

def create(package,exporter,dll,source,version,run,attempt,ref):
    if not re.fullmatch("[0-9a-f]{40}",source) or not re.fullmatch(r"\d+\.\d+\.\d+",version):
        raise QualificationError("Invalid source/version")
    if not str(run).isdigit() or not str(attempt).isdigit():
        raise QualificationError("Invalid workflow identity")
    component=package_dll(package)
    if component!=sha256(dll):
        raise QualificationError("Package DLL differs from compiled DLL")
    verify_exporter(exporter,sha256(exporter))
    return {"schema_version":1,"kind":"test-only-host-build-receipt","source_commit":source,
            "version":version,"workflow_run_id":str(run),"workflow_attempt":str(attempt),
            "ref":ref,"package_name":package.name,"package_sha256":sha256(package),
            "component_sha256":component,"exporter_sha256":sha256(exporter),
            "host_e2e_executed":False,"production_qualified":False}

def verify(receipt,expected,package,exporter,source,*,expected_ref='refs/heads/main'):
    if sha256(receipt)!=checked_digest(expected,"receipt_sha256"):
        raise QualificationError("Receipt digest mismatch")
    d=json.loads(receipt.read_text(encoding="utf-8"))
    if (d.get("schema_version")!=1 or d.get("kind")!="test-only-host-build-receipt" or
        d.get("ref")!=expected_ref or d.get("source_commit")!=source or
        d.get("host_e2e_executed") is not False or d.get("production_qualified") is not False or
        not str(d.get("workflow_run_id","")).isdigit() or not str(d.get("workflow_attempt","")).isdigit()):
        raise QualificationError("Not an exact-source main test build receipt")
    if d["package_name"]!=package.name or sha256(package)!=d["package_sha256"] or package_dll(package)!=d["component_sha256"]:
        raise QualificationError("Receipt/package mismatch")
    verify_exporter(exporter,d["exporter_sha256"])
    return d

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ("package","exporter","dll","output"): p.add_argument("--"+k,type=Path,required=True)
    a=p.parse_args()
    source=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()
    if source!=os.environ["GITHUB_SHA"]:
        raise QualificationError("Actual checkout differs from workflow SHA")
    d=create(a.package,a.exporter,a.dll,source,Path("VERSION").read_text().strip(),
             os.environ["GITHUB_RUN_ID"],os.environ["GITHUB_RUN_ATTEMPT"],os.environ["GITHUB_REF"])
    with a.output.open("x",encoding="utf-8") as out: json.dump(d,out,indent=2,sort_keys=True)
    print("HOST-BUILD-RECEIPT "+sha256(a.output),flush=True)

if __name__=="__main__": main()
