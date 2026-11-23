"""Robot keywords that record intent before (not after) GUI dispatch.

The event record is written after dispatch, minimizing observer-effect latency
while preserving the true pre-dispatch QPC tick. Native milestones are separate.
"""
from __future__ import annotations

import os
from pathlib import Path

from e2e.host.trace import TraceWriter, clock_sample, TraceError


class TraceKeywords:
    ROBOT_LIBRARY_SCOPE = "SUITE"

    def __init__(self):
        required = ("DOUJIN_E2E_RUN_ID", "DOUJIN_E2E_TRACE_PATH",
                    "DOUJIN_E2E_SCENARIO_ID", "DOUJIN_E2E_ATTEMPT_ID")
        if any(not os.environ.get(x) for x in required):
            raise TraceError("Missing isolated test trace environment")
        self.writer = TraceWriter(
            Path(os.environ["DOUJIN_E2E_TRACE_PATH"]),
            run_id=os.environ["DOUJIN_E2E_RUN_ID"],
            scenario_id=os.environ["DOUJIN_E2E_SCENARIO_ID"],
            attempt_id=os.environ["DOUJIN_E2E_ATTEMPT_ID"],
            source="robot",
        )

    def run_marked_action(self, event: str, robot_keyword: str, *args):
        """Invoke a Robot keyword, timestamping intent immediately before it.

        Never add a file write between the intent tick and the GUI action.
        """
        if event not in {"robot.play.intent", "robot.seek.intent",
                         "robot.stop.intent", "robot.host.launch.intent"}:
            raise TraceError("Not an approved automation trigger")
        from robot.libraries.BuiltIn import BuiltIn
        sample = clock_sample(self.writer.run_id)
        try:
            return BuiltIn().run_keyword(robot_keyword, *args)
        finally:
            self.writer.mark_at(event, sample)

    def mark_trace_event(self, event: str):
        if event not in {"robot.host.window.visible", "robot.play.dispatch.return"}:
            raise TraceError("Not an approved post-action milestone")
        self.writer.mark(event)

    def close_trace(self):
        self.writer.close()
