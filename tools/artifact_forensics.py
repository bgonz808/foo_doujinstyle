"""Read-only LLVM artifact inspection. Emit only structured counts and digests."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
from tools.reproducibility import digest

# Only these fixed labels may leave the runner; unknown roles are hashed.
ROLES = {
    "old msf directory": "old-directory", "pdb stream": "pdb-info",
    "tpi stream": "tpi", "dbi stream": "dbi", "ipi stream": "ipi",
    "global symbol hash": "global-hash", "public symbol hash": "public-hash",
    "symbol records": "symbols", "section header data": "section-headers",
    "new fpo data": "new-fpo", "fpo data": "fpo", "string table": "strings",
}
PDB_VIEWS = ("streams", "summary", "modules", "section-contribs", "section-map",
             "string-table", "publics", "globals")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def stream_roles(raw):
    rows = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        m = re.match(r"^\s*Stream\s+(\d+)\s+\(\s*(\d+)\s+bytes?\s*\)\s*:\s*(.*)$", line)
        if m:
            role = m[3].strip().strip("[]").lower()
            safe = ROLES.get(role)
            if safe is None and role.startswith("module "):
                safe = "module"
            rows.append({"stream": int(m[1]), "size": int(m[2]),
                         "role": safe or "unclassified", "description_sha256": sha(m[3].encode())})
    return rows


def inspect(tool, flags, path):
    before = digest(path)
    # Keep decoder output in a private temporary file, never in console/artifact.
    with tempfile.TemporaryFile() as raw, tempfile.TemporaryFile() as err:
        proc = subprocess.run([str(tool), *flags, path.name], cwd=path.parent,
                              stdout=raw, stderr=err, timeout=120, check=False)
        raw.seek(0, 2)
        size = raw.tell()
        raw.seek(0)
        if size > 128 * 1024**2:
            raise ValueError("Forensic output exceeds diagnostic limit")
        content = raw.read()
        err.seek(0)
        error_digest = sha(err.read())
    if digest(path) != before:
        raise ValueError("Read-only inspector changed input")
    result = {"input": before, "tool": digest(tool), "args_sha256": sha(json.dumps(flags).encode()),
              "exit": proc.returncode, "stdout_sha256": sha(content), "stdout_bytes": size,
              "stdout_lines": len(content.splitlines()), "stderr_sha256": error_digest}
    if flags == ["dump", "-streams"] and not proc.returncode:
        result["streams"] = stream_roles(content)
    return result


def pdb_views(llvm, path):
    return {v: inspect(llvm / "llvm-pdbutil.exe", ["dump", "-" + v], path) for v in PDB_VIEWS}


def pe_views(llvm, path):
    return {v: inspect(llvm / "llvm-readobj.exe", [v], path)
            for v in ("--file-headers", "--sections", "--coff-debug-directory",
                      "--coff-load-config", "--coff-resources")}


def object_views(llvm, path):
    return {
        "records": inspect(llvm / "llvm-readobj.exe",
                           ["--file-headers", "--sections", "--relocations", "--symbols", "--codeview"], path),
        "instructions": inspect(llvm / "llvm-objdump.exe", ["-d"], path),
    }
