"""Bounded ETW lifecycle diagnostics; a cleanup attempt never upgrades a failure."""
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import time

# Fixed categories and numeric error codes only. Arbitrary command output
# can include paths, machine names and other private environment details.
MESSAGES = (
    "the data collector set was not found",
    "the data collector set already exists",
    "access is denied",
    "the system cannot find the file specified",
    "the system cannot find the path specified",
    "the parameter is incorrect",
    "the operation completed successfully",
    "the requested operation requires elevation",
    "the instance name passed was not recognized as valid",
    "the process cannot access the file because it is being used by another process",
    "not enough storage is available to process this command",
    "the maximum number of sessions has been reached",
)
CODES = re.compile(rb"\b0x[0-9a-fA-F]{8}\b")


def diagnostic(raw):
    raw = raw or b""
    if isinstance(raw, str):
        raw = raw.encode("utf-8", "replace")
    # logman is localized; capture code and digest even when no English
    # message matches. Never publish the raw stdout or stderr.
    lowered = raw.lower().replace(b"\x00", b"")
    return {"bytes": len(raw), "sha256": sha256(raw).hexdigest(),
            "known_messages": [msg for msg in MESSAGES if msg.encode("ascii") in lowered],
            "hex_codes": sorted(set(x.decode("ascii").lower() for x in CODES.findall(lowered)))[:32]}


class EtwSession:
    def __init__(self, name: str, etl: Path, evidence: Path, invoke=subprocess.run):
        if not re.fullmatch(r"DoujinE2E-[0-9a-f]{12}", name):
            raise ValueError("Invalid ETW session identity")
        self.name, self.etl, self.evidence, self.invoke = name, etl, evidence, invoke
        self.history = []
        self.started = False

    def command(self, action, extra=(), label=None):
        began = time.monotonic_ns()
        row = {"action": label or action, "returncode": None, "outcome": "unknown"}
        try:
            result = self.invoke(["logman", action, self.name, *extra, "-ets"],
                                 capture_output=True, timeout=25, check=False)
            row.update(returncode=result.returncode,
                       outcome="success" if result.returncode == 0 else "nonzero",
                       stdout=diagnostic(result.stdout), stderr=diagnostic(result.stderr))
        except subprocess.TimeoutExpired as exc:
            row.update(outcome="timeout", stdout=diagnostic(exc.stdout), stderr=diagnostic(exc.stderr))
        except OSError as exc:
            row.update(outcome="os_error", errno=exc.errno, winerror=getattr(exc, "winerror", None))
        finally:
            row["duration_ns"] = time.monotonic_ns() - began
            self.history.append(row)
            doc = {"schema_version": 1, "session": self.name, "commands": self.history,
                   "raw_output_retained": False}
            self.evidence.write_text(json.dumps(doc, indent=2)+"\n", encoding="utf-8")
        return row["outcome"] == "success"

    def start(self):
        self.started = self.command("start", ("-p", "{a4ab84a0-75db-4f17-b7e3-f1aa0d260d4a}",
                                "0xffffffffffffffff", "0xff", "-o", str(self.etl)))
        if not self.started:
            # Start may have timed out after creating the session. Query only
            # our unique session; cleanup is best effort and never qualifies.
            if self.command("query", label="query_after_start_failure"):
                self.command("stop", label="cleanup_after_start_failure")
        return self.started

    def stop(self):
        if not self.started:
            raise RuntimeError("ETW session not started")
        self.started = False
        if self.command("stop"):
            return True
        # Do not export or qualify a trace after an unsuccessful initial stop,
        # even if cleanup succeeds. Record query and cleanup separately.
        if self.command("query", label="query_after_stop_failure"):
            self.command("stop", label="cleanup_after_stop_failure")
        return False
