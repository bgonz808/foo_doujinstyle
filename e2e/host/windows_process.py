"""Native process inventory; no shell or inherited PowerShell configuration."""
import ctypes
from ctypes import wintypes
import sys

TH32CS_SNAPPROCESS=0x00000002
TH32CS_SNAPMODULE=0x00000008
TH32CS_SNAPMODULE32=0x00000010
ERROR_NO_MORE_FILES=18
INVALID_HANDLE_VALUE=ctypes.c_void_p(-1).value

class PROCESSENTRY32W(ctypes.Structure):
    _fields_=[("dwSize",wintypes.DWORD),("cntUsage",wintypes.DWORD),
              ("th32ProcessID",wintypes.DWORD),("th32DefaultHeapID",ctypes.c_size_t),
              ("th32ModuleID",wintypes.DWORD),("cntThreads",wintypes.DWORD),
              ("th32ParentProcessID",wintypes.DWORD),("pcPriClassBase",wintypes.LONG),
              ("dwFlags",wintypes.DWORD),("szExeFile",wintypes.WCHAR*260)]

class MODULEENTRY32W(ctypes.Structure):
    _fields_=[("dwSize",wintypes.DWORD),("th32ModuleID",wintypes.DWORD),
              ("th32ProcessID",wintypes.DWORD),("GlblcntUsage",wintypes.DWORD),
              ("ProccntUsage",wintypes.DWORD),("modBaseAddr",ctypes.c_void_p),
              ("modBaseSize",wintypes.DWORD),("hModule",wintypes.HMODULE),
              ("szModule",wintypes.WCHAR*256),("szExePath",wintypes.WCHAR*260)]

def _snapshot(flags,pid,entry_type,first_name,next_name,select):
    if sys.platform!="win32":
        raise OSError("Native Windows process inspection required")
    k=ctypes.WinDLL("kernel32",use_last_error=True)
    k.CreateToolhelp32Snapshot.argtypes=[wintypes.DWORD,wintypes.DWORD]
    k.CreateToolhelp32Snapshot.restype=wintypes.HANDLE
    k.CloseHandle.argtypes=[wintypes.HANDLE]
    k.CloseHandle.restype=wintypes.BOOL
    first=getattr(k,first_name)
    following=getattr(k,next_name)
    for fn in (first,following):
        fn.argtypes=[wintypes.HANDLE,ctypes.POINTER(entry_type)]
        fn.restype=wintypes.BOOL
    handle=k.CreateToolhelp32Snapshot(flags,pid)
    if handle is None or handle==INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry=entry_type()
        entry.dwSize=ctypes.sizeof(entry)
        values=[]
        ok=first(handle,ctypes.byref(entry))
        while ok:
            values.append(select(entry))
            ok=following(handle,ctypes.byref(entry))
        error=ctypes.get_last_error()
        if error!=ERROR_NO_MORE_FILES:
            raise ctypes.WinError(error)
        return values
    finally:
        k.CloseHandle(handle)

def process_ids(exe_name):
    rows=_snapshot(TH32CS_SNAPPROCESS,0,PROCESSENTRY32W,
                   "Process32FirstW","Process32NextW",
                   lambda e:(e.szExeFile.casefold(),int(e.th32ProcessID)))
    return [pid for name,pid in rows if name==exe_name.casefold()]

def host_module_status(pid):
    """Only known module presence, no absolute module paths or unrelated names."""
    names=set(_snapshot(TH32CS_SNAPMODULE|TH32CS_SNAPMODULE32,pid,MODULEENTRY32W,
                        "Module32FirstW","Module32NextW",lambda e:e.szModule.casefold()))
    return {"foobar2000_loaded":"foobar2000.exe" in names,
            "doujinstyle_loaded":"foo_doujinstyle.dll" in names}

# Allowlisted module inventory, relative to the pinned portable root only.
# A missing separate module does not establish absence of built-in support.
def _archive_module_name(name):
    name=name.casefold()
    return name=="foo_unpack.dll" or (name.startswith("foo_unpack_") and name.endswith(".dll"))

def archive_file_inventory(root):
    from pathlib import Path
    import hashlib
    root=Path(root).resolve(strict=True)
    rows=[]
    for p in root.rglob("*.dll"):
        if _archive_module_name(p.name) and p.is_file() and not p.is_symlink():
            resolved=p.resolve(strict=True)
            if not resolved.is_relative_to(root): raise ValueError("Archive module outside template")
            rows.append({"relative_path":resolved.relative_to(root).as_posix(),"sha256":hashlib.sha256(resolved.read_bytes()).hexdigest()})
    return sorted(rows,key=lambda x:x["relative_path"])

def archive_module_inventory(pid,root):
    from pathlib import Path
    root=Path(root).resolve(strict=True)
    files=archive_file_inventory(root)
    known={row["relative_path"].casefold():row for row in files}
    modules=_snapshot(TH32CS_SNAPMODULE|TH32CS_SNAPMODULE32,pid,MODULEENTRY32W,
        "Module32FirstW","Module32NextW",lambda e:(e.szModule,e.szExePath))
    loaded=[]
    for name,path in modules:
        if not _archive_module_name(name): continue
        p=Path(path).resolve(strict=True)
        if not p.is_relative_to(root): raise ValueError("Archive module outside portable host")
        key=p.relative_to(root).as_posix().casefold()
        if key not in known: raise ValueError("Loaded archive module missing file inventory")
        loaded.append(known[key])
    return {"inventory_complete":True,"pid":pid,"files":files,"loaded":loaded}
