from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "bin" / "agent"


class BridgeCliTests(unittest.TestCase):
    def test_list_agents(self) -> None:
        proc = subprocess.run(
            [str(AGENT), "code", "bridge", "--list"],
            cwd=str(ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("claude", proc.stdout)
        self.assertIn("codex", proc.stdout)

    def test_dry_run_discovers_current_git_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "sample"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            env = {**os.environ, "AGENT_BRIDGE_STATE_DIR": str(Path(tmp) / "state")}
            proc = subprocess.run(
                [
                    str(AGENT),
                    "code",
                    "bridge",
                    "--from",
                    "human",
                    "--to",
                    "claude",
                    "--mode",
                    "review",
                    "--prompt",
                    "report scope",
                    "--dry-run",
                ],
                cwd=str(repo),
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            expected_project = repo.resolve()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"Project: {expected_project}", proc.stdout)
        self.assertIn("--permission-mode plan", proc.stdout)

    def test_loop_dry_run_emits_ordered_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "AGENT_BRIDGE_STATE_DIR": str(Path(tmp) / "state")}
            proc = subprocess.run(
                [
                    str(AGENT),
                    "code",
                    "loop",
                    "--builder",
                    "claude",
                    "--critic",
                    "claude",
                    "--verifier",
                    "claude",
                    "--max-turns",
                    "1",
                    "--prompt",
                    "loop smoke",
                    "--dry-run",
                ],
                cwd=str(ROOT),
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            events_path = Path(tmp) / "state" / "events.jsonl"
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout.count("[dry-run] claude:"), 3)
            self.assertIn("run_id:", proc.stdout)
            rows = [line for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(rows), 8)
        self.assertIn('"type": "run.created"', rows[0])
        self.assertIn('"role": "builder"', rows[1])
        self.assertIn('"role": "critic"', rows[3])
        self.assertIn('"role": "verifier"', rows[5])
        self.assertIn('"type": "run.completed"', rows[-1])

    def test_codex_dry_run_uses_current_exec_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "AGENT_BRIDGE_STATE_DIR": str(Path(tmp) / "state")}
            proc = subprocess.run(
                [
                    str(AGENT),
                    "code",
                    "bridge",
                    "--from",
                    "human",
                    "--to",
                    "codex",
                    "--mode",
                    "review",
                    "--prompt",
                    "review scope",
                    "--dry-run",
                ],
                cwd=str(ROOT),
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("codex exec", proc.stdout)
        self.assertIn("-s read-only", proc.stdout)
        self.assertNotIn("-a never", proc.stdout)

    def test_bridge_child_role_uses_target_not_forwarded_caller_role(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "AGENT_BRIDGE_STATE_DIR": str(Path(tmp) / "state")}
            proc = subprocess.run(
                [
                    str(AGENT),
                    "code",
                    "bridge",
                    "--from",
                    "human",
                    "--to",
                    "claude",
                    "--mode",
                    "review",
                    "--prompt",
                    "role smoke",
                    "--run-id",
                    "run-role",
                    "--loop-id",
                    "loop-role",
                    "--turn-id",
                    "caller-turn",
                    "--parent-id",
                    "parent-turn",
                    "--role",
                    "caller",
                    "--dry-run",
                ],
                cwd=str(ROOT),
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            rows = [
                json.loads(line)
                for line in (Path(tmp) / "state" / "events.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        self.assertEqual(proc.returncode, 0, proc.stderr)
        dispatched = next(row for row in rows if row["type"] == "agent.dispatched")
        self.assertEqual(dispatched["role"], "claude")
        self.assertEqual(dispatched["parent_id"], "parent-turn")
        self.assertNotEqual(dispatched["turn_id"], "caller-turn")


if __name__ == "__main__":
    unittest.main()
