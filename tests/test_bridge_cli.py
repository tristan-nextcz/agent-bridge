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
                    "--spawn-policy",
                    "full",
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
        self.assertEqual(len(rows), 9)
        self.assertIn('"type": "run.created"', rows[0])
        self.assertIn('"type": "dispatch.policy_evaluated"', rows[1])
        self.assertIn('"role": "builder"', rows[2])
        self.assertIn('"role": "critic"', rows[4])
        self.assertIn('"role": "verifier"', rows[6])
        self.assertIn('"type": "run.completed"', rows[-1])

    def test_loop_auto_uses_one_adversarial_agent_for_vague_prompt(self) -> None:
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
                    "3",
                    "--prompt",
                    "quick check this",
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
        self.assertEqual(proc.stdout.count("[dry-run] claude:"), 1)
        self.assertIn("dispatch_decision: adversarial_only", proc.stdout)
        dispatched = [row for row in rows if row["type"] == "agent.dispatched"]
        self.assertEqual(len(dispatched), 1)
        self.assertEqual(dispatched[0]["role"], "adversarial")
        self.assertEqual(dispatched[0]["data"]["target"], "claude")

    def test_loop_auto_allows_full_loop_for_scoped_implementation(self) -> None:
        prompt = (
            "Implement a schema and trace controller update in agent_bridge/cli.py "
            "and tests/test_bridge_cli.py with backwards compatible workflow coverage "
            "and adversarial validation."
        )
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
                    "--prompt",
                    prompt,
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
        self.assertEqual(proc.stdout.count("[dry-run] claude:"), 3)
        self.assertIn("dispatch_decision: full_loop", proc.stdout)
        self.assertEqual([row["role"] for row in rows if row["type"] == "agent.dispatched"], ["builder", "critic", "verifier"])

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

    def test_bridge_writes_correlation_to_transcript_header_and_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "agents.json"
            state = Path(tmp) / "state"
            config.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "id": "helper",
                                "label": "Helper",
                                "adapter": "argv",
                                "command": "python3",
                                "args": ["-c", "print('agent output')"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            env = {**os.environ, "AGENT_BRIDGE_STATE_DIR": str(state)}
            proc = subprocess.run(
                [
                    str(AGENT),
                    "code",
                    "bridge",
                    "--config",
                    str(config),
                    "--from",
                    "human",
                    "--to",
                    "helper",
                    "--mode",
                    "review",
                    "--prompt",
                    "transcript smoke",
                    "--run-id",
                    "run.transcript",
                    "--loop-id",
                    "loop.transcript",
                    "--parent-id",
                    "parent-transcript",
                    "--attempt",
                    "3",
                ],
                cwd=str(ROOT),
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            transcripts = list((state / "transcripts").glob("*.txt"))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(len(transcripts), 1)
            transcript_name = transcripts[0].name
            text = transcripts[0].read_text(encoding="utf-8")
        self.assertIn("run_transcript_", transcript_name)
        self.assertIn("_helper_", transcript_name)
        self.assertIn("correlation: ", text)
        self.assertIn("run_id=run.transcript", text)
        self.assertIn("loop_id=loop.transcript", text)
        self.assertIn("parent_id=parent-transcript", text)
        self.assertIn("attempt=3", text)
        self.assertIn("role=helper", text)
        self.assertRegex(text, r"turn_id=turn_helper_")

    def test_session_start_hook_outputs_context_json(self) -> None:
        proc = subprocess.run(
            [str(AGENT), "code", "hook", "session-start", "--client", "codex"],
            cwd=str(ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        output = payload["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "SessionStart")
        self.assertIn("Agent Bridge session bootstrap", output["additionalContext"])
        self.assertIn("never spawns agents", output["additionalContext"])
        self.assertIn(str(ROOT / "agent_bridge" / "mailbox_mcp.py"), output["additionalContext"])

    def test_hooks_install_is_idempotent_for_codex_and_claude(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "HOME": tmp, "AGENT_BRIDGE_HOOK_AGENT": "/tmp/agent"}
            codex_dir = Path(tmp) / ".codex"
            claude_dir = Path(tmp) / ".claude"
            codex_dir.mkdir()
            claude_dir.mkdir()
            (codex_dir / "hooks.json").write_text('{"hooks":{"SessionStart":[]}}\n', encoding="utf-8")
            (claude_dir / "settings.json").write_text('{"model":"opus","hooks":{}}\n', encoding="utf-8")

            for _ in range(2):
                proc = subprocess.run(
                    [str(AGENT), "code", "hooks", "install", "--client", "both"],
                    cwd=str(ROOT),
                    env=env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)

            codex = json.loads((codex_dir / "hooks.json").read_text(encoding="utf-8"))
            claude = json.loads((claude_dir / "settings.json").read_text(encoding="utf-8"))

        codex_hooks = codex["hooks"]["SessionStart"][0]["hooks"]
        claude_hooks = claude["hooks"]["SessionStart"][0]["hooks"]
        self.assertEqual(
            [hook["command"] for hook in codex_hooks].count("'/tmp/agent' code hook session-start --client codex"),
            1,
        )
        self.assertEqual(
            [hook["command"] for hook in claude_hooks].count("'/tmp/agent' code hook session-start --client claude"),
            1,
        )
        self.assertEqual(claude["model"], "opus")

    def test_hooks_install_uses_windows_cmd_wrapper_for_cmd_shim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                **os.environ,
                "HOME": tmp,
                "AGENT_BRIDGE_HOOK_AGENT": r"C:\Users\me\.local\bin\agent.cmd",
            }
            codex_dir = Path(tmp) / ".codex"
            codex_dir.mkdir()
            proc = subprocess.run(
                [str(AGENT), "code", "hooks", "install", "--client", "codex"],
                cwd=str(ROOT),
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            codex = json.loads((codex_dir / "hooks.json").read_text(encoding="utf-8"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        hook = codex["hooks"]["SessionStart"][0]["hooks"][0]
        self.assertEqual(
            hook["command"],
            r'cmd /d /c ""C:\Users\me\.local\bin\agent.cmd" code hook session-start --client codex"',
        )


if __name__ == "__main__":
    unittest.main()
