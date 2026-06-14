from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_bridge.correlation import child_turn_meta
from agent_bridge.findings import create_finding, list_findings, list_verdicts, record_verdict
from agent_bridge.trace import emit_event, load_events


class TraceAndFindingTests(unittest.TestCase):
    def test_child_turn_metadata_does_not_reuse_caller_turn_identity(self) -> None:
        child = child_turn_meta(
            {"run_id": "run-a", "loop_id": "loop-a", "turn_id": "caller-turn", "role": "caller"},
            role="critic",
            attempt=2,
            parent_id="caller-turn",
        )
        self.assertEqual(child["run_id"], "run-a")
        self.assertEqual(child["loop_id"], "loop-a")
        self.assertEqual(child["role"], "critic")
        self.assertEqual(child["attempt"], 2)
        self.assertEqual(child["parent_id"], "caller-turn")
        self.assertNotEqual(child["turn_id"], "caller-turn")

    def test_trace_events_are_append_only_and_filterable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"AGENT_BRIDGE_STATE_DIR": tmp}):
            first = emit_event("run.created", run_id="run-a", data={"x": 1})
            second = emit_event("agent.dispatched", run_id="run-a", meta={"run_id": "run-a", "role": "builder"})
            emit_event("run.created", run_id="run-b")
            rows = load_events(run_id="run-a")
            event_file = Path(tmp) / "events.jsonl"

            self.assertEqual([row["id"] for row in rows], [first["id"], second["id"]])
            self.assertEqual(rows[1]["role"], "builder")
            raw = [json.loads(line) for line in event_file.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(raw), 3)
            self.assertTrue(all("ts" in row for row in raw))

    def test_findings_and_verdicts_validate_status_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"AGENT_BRIDGE_STATE_DIR": tmp}):
            finding = create_finding(
                run_id="run-a",
                severity="high",
                claim="unsafe behavior",
                evidence=["agent_bridge/cli.py:1"],
                owner_role="critic",
            )
            verdict = record_verdict(
                run_id="run-a",
                status="fail",
                summary="blocking finding remains",
                blocking_findings=[finding["id"]],
            )

            self.assertEqual(list_findings(run_id="run-a")[0]["id"], finding["id"])
            self.assertEqual(list_verdicts(run_id="run-a")[0]["id"], verdict["id"])

            with self.assertRaises(ValueError):
                create_finding(run_id="run-a", severity="urgent", claim="bad")
            with self.assertRaises(ValueError):
                record_verdict(run_id="run-a", status="maybe", summary="bad")


if __name__ == "__main__":
    unittest.main()
