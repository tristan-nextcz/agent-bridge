from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAILBOX = ROOT / "agent_bridge" / "mailbox.py"


class MailboxTests(unittest.TestCase):
    def test_send_and_read_use_state_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "AGENT_BRIDGE_STATE_DIR": tmp}
            send = subprocess.run(
                [
                    "python3",
                    str(MAILBOX),
                    "send",
                    "--from",
                    "codex",
                    "--to",
                    "claude",
                    "--subject",
                    "smoke",
                    "--body",
                    "hello",
                ],
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(send.returncode, 0, send.stderr)
            self.assertEqual(send.stdout.strip(), "m0001")
            self.assertTrue((Path(tmp) / "mailbox" / "messages.jsonl").exists())

            read = subprocess.run(
                ["python3", str(MAILBOX), "read", "--to", "claude", "--full"],
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(read.returncode, 0, read.stderr)
            self.assertIn("codex -> claude: smoke", read.stdout)
            self.assertIn("hello", read.stdout)


if __name__ == "__main__":
    unittest.main()
