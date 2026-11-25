"""Digest-pinned native TDH export; reject incomplete output."""
from pathlib import Path
import subprocess
from e2e.host.trace import load_events, TraceError
import hashlib

def verify_exporter(path, expected):
    if path is None or expected is None or not path.is_file() or path.name.lower()!="etw_export.exe":
        raise TraceError("Pinned test-only etw_export.exe required")
    if len(expected)!=64 or any(c not in "0123456789abcdef" for c in expected.lower()):
        raise TraceError("Invalid exporter digest")
    if hashlib.sha256(path.read_bytes()).hexdigest()!=expected.lower():
        raise TraceError("Exporter digest mismatch")

def export(exporter, expected, etl, output, run_id):
    verify_exporter(exporter,expected)
    try:
        with output.open("xb") as stream:
            p=subprocess.run([str(exporter.resolve()),str(etl.resolve()),run_id],
                stdout=stream,stderr=subprocess.PIPE,timeout=90,check=False)
        if p.returncode:
            raise TraceError("Native TDH exporter rejected ETL: "+p.stderr.decode(errors="replace")[:2000])
        records=load_events(output.parent)
        if not any(x["source"]=="component" for x in records):
            raise TraceError("No native events")
    except FileExistsError:
        raise
    except (OSError,ValueError,subprocess.SubprocessError):
        output.unlink(missing_ok=True)
        raise
