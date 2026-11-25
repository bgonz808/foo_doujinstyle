"""Exercise actual Windows UIA inspection on our own synthetic WinForms window.

Not a foobar2000 or FlaUI/Robot interaction test. Never inspect another process.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from e2e.host.ui_discovery import inspect,candidates,write_json
from e2e.host.runner import QualificationError

def run(out):
    if sys.platform!="win32":
        raise QualificationError("Windows UIA required")
    out.mkdir(parents=True,exist_ok=False)
    exe=Path(shutil.which("powershell.exe") or "")
    if not exe.is_file():
        raise QualificationError("Windows PowerShell not found")
    script=Path(__file__).with_name("uia_probe_window.ps1")
    with subprocess.Popen([str(exe.resolve()),"-NoProfile","-STA","-File",str(script)],
                          stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL) as child:
        try:
            deadline=time.monotonic()+45
            last=None
            last_doc=None
            inspections=0
            while time.monotonic()<deadline:
                if child.poll() is not None:
                    raise QualificationError("Synthetic window exited before inspection")
                try:
                    doc=inspect(child.pid,exe)
                    last_doc=doc
                    inspections+=1
                    nodes=doc["nodes"]
                    named={n["name"]:n for n in nodes}
                    if ("Synthetic inspect button" in named and
                        "Synthetic inspect edit" in named and any(n["password"] for n in nodes)):
                        break
                    last="Required synthetic controls absent"
                except (OSError,ValueError,subprocess.SubprocessError) as exc:
                    last=str(exc)
                time.sleep(.25)
            else:
                if last_doc is not None:
                    write_json(out/"observed-tree.json",last_doc)
                    write_json(out/"observed-selectors.json",candidates(last_doc,child.pid))
                write_json(out/"failure.json",{"qualification":"failed_synthetic_uia_inspection",
                    "inspections":inspections,"reason":str(last)[:1000],
                    "node_count":len(last_doc["nodes"]) if last_doc else None,
                    "host_e2e_executed":False})
                if last_doc is not None:
                    print("UIA-OBSERVED "+json.dumps([{"name":n["name"],"role":n["control_type"],
                        "id":n["automation_id"],"password":n["password"],"patterns":n["patterns"]}
                        for n in last_doc["nodes"][:80]]),flush=True)
                raise QualificationError("Synthetic UIA readiness failed: "+str(last))
            write_json(out/"observed-tree.json",doc)
            button=named["Synthetic inspect button"]
            edit=named["Synthetic inspect edit"]
            if (doc["truncated"] or button["control_type"]!="ControlType.Button" or
                edit["control_type"]!="ControlType.Edit" or
                not any("InvokePattern" in p for p in button["patterns"]) or
                "SYNTHETIC_PASSWORD_MUST_NOT_APPEAR" in json.dumps(doc) or
                "Synthetic password label" in json.dumps(doc)):
                raise QualificationError("Synthetic role/pattern/password contract mismatch")
            write_json(out/"tree.json",doc)
            write_json(out/"selectors.json",candidates(doc,child.pid))
            result={"qualification":"windows_uia_inspection_only","host_e2e_executed":False,
                    "robot_flaui_interaction_executed":False,"node_count":len(nodes),
                    "button_invoke_pattern":True,"password_label_suppressed":True}
            write_json(out/"roundtrip.json",result)
            print("UIA-PROBE "+json.dumps(result),flush=True)
        finally:
            if child.poll() is None:
                child.terminate()
                try: child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
    return 0

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output",type=Path,required=True)
    return run(p.parse_args().output)

if __name__=="__main__":
    raise SystemExit(main())
