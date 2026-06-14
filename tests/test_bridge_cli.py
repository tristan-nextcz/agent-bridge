from __future__ import annotations

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
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            expected_project = repo.resolve()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"Project: {expected_project}", proc.stdout)
        self.assertIn("--permission-mode plan", proc.stdout)


if __name__ == "__main__":
    unittest.main()
