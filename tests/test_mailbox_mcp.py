from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MCP = ROOT / "agent_bridge" / "mailbox_mcp.py"


class MailboxMcpTests(unittest.TestCase):
    def test_initialize_and_tools_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "AGENT_BRIDGE_STATE_DIR": tmp}
            payload = "\n".join(
                [
                    json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
                    json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
                    "",
                ]
            )
            proc = subprocess.run(
                ["python3", str(MCP)],
                input=payload,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rows = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
        self.assertEqual(rows[0]["result"]["serverInfo"]["name"], "agent-mailbox")
        tool_names = {tool["name"] for tool in rows[1]["result"]["tools"]}
        self.assertEqual(tool_names, {"mailbox_send", "mailbox_inbox", "mailbox_read"})


if __name__ == "__main__":
    unittest.main()
