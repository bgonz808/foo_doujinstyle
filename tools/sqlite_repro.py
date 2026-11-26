"""Standalone same-runner SQLite MSBuild repeat, no foobar SDK or final link."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from hashlib import sha256

from tools.reproducibility import digest, source_receipt
from tools.reproducibility_detail import archive_detail, coff_detail
from tools.toolchain_inventory import tree_digest


def require(condition,message):
    if not condition:
        raise ValueError(message)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--msbuild",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    require(os.name=="nt" and os.environ.get("GITHUB_ACTIONS")=="true","Disposable Windows CI only")
    root=Path(__file__).resolve().parents[1]
    native=root/"native"
    build=native/"build"
    require(not build.exists(),"Standalone experiment requires fresh checkout")
    src=source_receipt(root)
    report={"schema_version":1,"source":src,"scope":"standalone x64 SQLite native MSBuild /m:1 /nr:false, fixed original project paths; no full toolchain lock or final-link qualification",
            "msbuild":digest(args.msbuild),"runs":[]}
    for _ in range(2):
        if build.exists():
            shutil.rmtree(build)
        cmd=[str(args.msbuild),str(native/"sqlite_shared.vcxproj"),"/t:Rebuild","/m:1","/nr:false","/v:minimal",
             "/p:Configuration=Release","/p:Platform=x64","/p:PlatformToolset=v143",
             "/p:VCToolsVersion=14.44.35207","/p:WindowsTargetPlatformVersion=10.0.26100.0"]
        start=time.monotonic()
        proc=subprocess.run(cmd,cwd=native,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180)
        elapsed=round(time.monotonic()-start,3)
        require(proc.returncode==0,"Standalone SQLite MSBuild failed")
        objroot=build/"shared-obj"/"sqlite"/"x64"
        objects=list(objroot.rglob("sqlite_amalgamation.obj"))
        logs=list(objroot.rglob("CL.command.1.tlog"))
        require(len(objects)==1 and len(logs)==1,"Missing standalone object or tracked command")
        lib=build/"shared-lib"/"x64"/"doujinstyle_sqlite.lib"
        report["runs"].append({"seconds":elapsed,"object":{**digest(objects[0]),"coff":coff_detail(objects[0].read_bytes())},
           "library":{**digest(lib),"members":archive_detail(lib.read_bytes())},
           "command":digest(logs[0]),"generated":tree_digest(build/"generated"/"x64"/"sqlite")})
        require(source_receipt(root)==src,"Source changed during SQLite experiment")
    a,b=report["runs"]
    report["object_equal"]=a["object"]["sha256"]==b["object"]["sha256"]
    report["library_equal"]=a["library"]["sha256"]==b["library"]["sha256"]
    report["command_equal"]=a["command"]==b["command"]
    report["generated_equal"]=a["generated"]==b["generated"]
    args.output.write_text(json.dumps(report,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    print("SQLITE-ONLY object_equal="+str(report["object_equal"])+" library_equal="+str(report["library_equal"]))


if __name__=="__main__":
    main()
