"""Record packaged Python calls and optionally executed lines for a fixture."""

import argparse
import json
from pathlib import Path
import runpy
import sys
import threading


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--line-report", type=Path)
    parser.add_argument("worker", type=Path)
    parser.add_argument("worker_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    worker = args.worker.resolve(strict=True)
    prefix = worker.as_posix() + "/"
    called = set()
    executed = {}

    def member(frame):
        filename = frame.f_code.co_filename.replace("\\", "/")
        return filename[len(prefix):] if filename.startswith(prefix) else None

    def record(frame, event, _):
        if event != "call":
            return
        code = frame.f_code
        if filename := member(frame):
            called.add((filename, getattr(code, "co_qualname", code.co_name)))

    def trace(frame, event, _):
        filename = member(frame)
        if filename is None:
            return None
        if event == "line" and frame.f_lineno > 0:
            executed.setdefault(filename, set()).add(frame.f_lineno)
        return trace

    original_argv = sys.argv
    original_profile = sys.getprofile()
    original_thread_profile = threading.getprofile()
    original_trace = sys.gettrace()
    original_thread_trace = threading.gettrace()
    sys.argv = [str(worker), *args.worker_args]
    try:
        threading.setprofile(record)
        sys.setprofile(record)
        if args.line_report is not None:
            threading.settrace(trace)
            sys.settrace(trace)
        runpy.run_path(str(worker), run_name="__main__")
    finally:
        if args.line_report is not None:
            sys.settrace(original_trace)
            threading.settrace(original_thread_trace)
        sys.setprofile(original_profile)
        threading.setprofile(original_thread_profile)
        sys.argv = original_argv
        args.report.write_text(json.dumps(sorted(called.copy()), indent=2) + "\n", encoding="utf-8")
        if args.line_report is not None:
            lines = {filename: sorted(numbers) for filename, numbers in sorted(executed.copy().items())}
            report = {"executed_python_lines": sum(len(numbers) for numbers in lines.values()), "modules": lines}
            args.line_report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
