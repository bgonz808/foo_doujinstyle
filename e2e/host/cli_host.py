"""Single-attempt CLI-only Robot host; signals synchronize, ETW qualifies."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

class CliHost:
    ROBOT_LIBRARY_SCOPE="SUITE"
    def __init__(self):
        if sys.platform!="win32":
            raise RuntimeError("Windows CLI host required")
        self.exe=Path(os.environ["DOUJIN_E2E_HOST_EXE"]).resolve(strict=True)
        self.ref=Path(os.environ["DOUJIN_E2E_FIXTURE_REF"]).resolve(strict=True)
        self.run=os.environ["DOUJIN_E2E_RUN_ID"]
        if not re.fullmatch("[0-9a-f]{32}",self.run) or self.exe.name.lower()!="foobar2000.exe":
            raise ValueError("Invalid host identity")
        self.child=None
        self.handles={}
        self.sdk_ipc=os.environ.get("DOUJIN_E2E_SDK_IPC")=="1"
        self.k=ctypes.WinDLL("kernel32",use_last_error=True)
        self.k.CreateEventW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.BOOL,wintypes.LPCWSTR]
        self.k.CreateEventW.restype=wintypes.HANDLE
        self.k.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
        self.k.WaitForSingleObject.restype=wintypes.DWORD
        self.k.SetEvent.argtypes=[wintypes.HANDLE]
        self.k.SetEvent.restype=wintypes.BOOL
        self.k.CloseHandle.argtypes=[wintypes.HANDLE]
        self.k.CloseHandle.restype=wintypes.BOOL

    def launch_cli_host(self):
        from e2e.host.run_readiness import no_host
        no_host()
        if os.environ.get("DOUJIN_E2E_CLI_MODE")!="1":
            raise RuntimeError("CLI profile not enabled")
        try:
            events=("ready","added","pcm")
            if self.sdk_ipc:
                events+=("add-command","play-command")
                if os.environ.get("DOUJIN_E2E_SEEK_ORACLE")=="1":
                    events+=tuple(f"seek{i}-{kind}" for i in range(1,5) for kind in ("command","pcm","callback"))
            for suffix in events:
                ctypes.set_last_error(0)
                h=self.k.CreateEventW(None,not suffix.endswith("-command"),False,"Local\\DoujinE2E-"+self.run+"-"+suffix)
                if not h:
                    raise OSError("Cannot create event")
                if ctypes.get_last_error()==183:
                    self.k.CloseHandle(h)
                    raise OSError("Preexisting event")
                self.handles[suffix]=h
            self.child=subprocess.Popen([str(self.exe)],cwd=self.exe.parent,
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            print("HOST-PROCESS-LAUNCHED pid="+str(self.child.pid),flush=True)
            return self.child.pid
        except BaseException:
            self.close_cli_host()
            raise

    def _wait(self,suffix):
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            if self.child is None or self.child.poll() is not None:
                raise RuntimeError("Spawned host exited; returncode="+str(self.child.returncode if self.child else None))
            rc=self.k.WaitForSingleObject(self.handles[suffix],100)
            if rc==0:
                if self.child.poll() is not None:
                    raise RuntimeError("Host exited during wait")
                return
            if rc!=258:
                raise OSError("Event wait failed")
        from e2e.host.windows_process import host_module_status
        try:
            modules=host_module_status(self.child.pid)
        except OSError as exc:
            modules={"inspection_error":getattr(exc,"winerror",None)}
        raise TimeoutError("Missing SDK "+suffix+" signal; pid="+str(self.child.pid)+
                           "; modules="+json.dumps(modules,sort_keys=True))

    def wait_for_verified_cli_profile(self):
        self._wait("ready")
        from robot.libraries.BuiltIn import BuiltIn
        root=Path(BuiltIn().get_variable_value("${OUTPUT DIR}"))
        with (root/"cli-host.json").open("x",encoding="utf-8") as out:
            json.dump({"run_id":self.run,"pid":self.child.pid},out)

    def _dispatch(self,*args):
        if self.child is None or self.child.poll() is not None:
            raise RuntimeError("Spawned host not running")
        p=subprocess.run([str(self.exe),*args],cwd=self.exe.parent,
            stdin=subprocess.DEVNULL,capture_output=True,timeout=15,check=False)
        if p.returncode or self.child.poll() is not None:
            raise RuntimeError("CLI dispatch failed")
    def _command(self,suffix):
        if self.child is None or self.child.poll() is not None:
            raise RuntimeError("Spawned host not running")
        if not self.k.SetEvent(self.handles[suffix]):
            raise ctypes.WinError(ctypes.get_last_error())
    def add_cli_fixture(self):
        if self.sdk_ipc:
            self._command("add-command")
        else:
            self._dispatch("/add",str(self.ref))
        self._wait("added")
    def play_cli_fixture(self):
        if self.sdk_ipc:
            self._command("play-command")
        else:
            self._dispatch("/play")
    def wait_for_cli_pcm(self):
        self._wait("pcm")
        if os.environ.get("DOUJIN_E2E_STOCK_ARCHIVE") in ("local","remote"):
            from robot.libraries.BuiltIn import BuiltIn
            from e2e.host.windows_process import archive_module_inventory
            root=Path(BuiltIn().get_variable_value("${OUTPUT DIR}"))
            evidence=archive_module_inventory(self.child.pid,self.exe.parent)
            (root/"archive-modules.json").write_text(json.dumps(evidence,indent=2),encoding="utf-8")
    def seek_oracle_enabled(self):
        return os.environ.get("DOUJIN_E2E_SEEK_ORACLE")=="1"
    def seek_cli_fixture(self,ordinal):
        if not self.seek_oracle_enabled() or not self.sdk_ipc or not 1<=int(ordinal)<=4:
            raise ValueError("Unapproved seek operation")
        self._command(f"seek{int(ordinal)}-command")
    def wait_for_seek_pcm(self,ordinal):
        self._wait(f"seek{int(ordinal)}-pcm")
        self._wait(f"seek{int(ordinal)}-callback")
    def close_cli_host(self):
        try:
            if self.child is not None and self.child.poll() is None:
                try:
                    subprocess.run([str(self.exe),"/exit"],cwd=self.exe.parent,
                        stdin=subprocess.DEVNULL,capture_output=True,timeout=10,check=True)
                    self.child.wait(timeout=10)
                except (OSError,subprocess.SubprocessError):
                    self.child.terminate()
                    try: self.child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.child.kill()
                        self.child.wait(timeout=5)
                    raise RuntimeError("Forced child cleanup; trial failed")
        finally:
            for h in self.handles.values(): self.k.CloseHandle(h)
            self.handles.clear()
