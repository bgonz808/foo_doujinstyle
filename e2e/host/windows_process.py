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
