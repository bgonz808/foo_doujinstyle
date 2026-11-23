"""Robot keywords for the pre-launched isolated foobar2000 host.

CLI command success cannot prove playback. Native ETW first PCM must follow.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


class FixtureCommands:
    ROBOT_LIBRARY_SCOPE = "SUITE"

    def __init__(self):
        self.executable = Path(os.environ.get("DOUJIN_E2E_HOST_EXE", ""))
        self.reference = Path(os.environ.get("DOUJIN_E2E_FIXTURE_REF", ""))
        if (not self.executable.is_file() or self.executable.name.lower() != "foobar2000.exe"
                or not self.reference.is_file() or self.reference.suffix.lower() != ".djt"):
            raise ValueError("A real foobar2000 executable and fixture .djt are required")

    def _dispatch(self, *args: str) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Host CLI dispatch requires Windows")
        completed = subprocess.run([str(self.executable), *args], check=False,
                                   stdin=subprocess.DEVNULL, capture_output=True, timeout=15)
        if completed.returncode:
            raise RuntimeError(f"foobar2000 command returned {completed.returncode}")

    def add_fixture_to_playlist(self) -> None:
        self._dispatch("/add", str(self.reference))

    def play_fixture(self) -> None:
        self._dispatch("/play")
