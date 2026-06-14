#!/usr/bin/env python3
"""Dependency-free stdio MCP server exposing the agent mailbox as native tools.

This is the *primary* Claude Code <-> Codex bridge: both CLIs register this
server, so each agent drives the same file-backed mailbox through real MCP
tools (no shell-script middleman). It is still an async shared mailbox, not
live session-injection IPC — the two CLIs are separate vendor apps with no
direct IPC. The same store (`agent_mailbox/messages.jsonl`) is shared with the
`mailbox.py` CLI, so the shell tools and the MCP interoperate.

Implements just enough of MCP (JSON-RPC 2.0 over newline-delimited stdio):
  initialize, notifications/initialized, tools/list, tools/call.
Tools: mailbox_send, mailbox_inbox, mailbox_read.

Register with Codex:
  codex mcp add mailbox -- python3 /Users/tts/Code/agent-bridge/agent_bridge/mailbox_mcp.py
Register with Claude Code globally:
  claude mcp add --scope user mailbox -- python3 /Users/tts/Code/agent-bridge/agent_bridge/mailbox_mcp.py
(Desktop apps need a restart / their own MCP settings to load it.)

Global local dev tooling. Mailbox state is stored under ~/.local/state/agent-bridge/.
"""
import json
import os
import sys

try:
    from . import mailbox as mb
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import mailbox as mb  # type: ignore[no-redef]

PROTO = "2024-11-05"

TOOLS = [
    {
        "name": "mailbox_send",
        "description": "Send a message to another agent's mailbox (e.g. to=codex or to=claude).",
        "inputSchema": {
            "type": "object",
            "required": ["from", "to", "subject", "body"],
            "properties": {
                "from": {"type": "string"},
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "ref": {"type": "string", "description": "Optional id of the message this replies to."},
            },
        },
    },
    {
        "name": "mailbox_inbox",
        "description": "List messages addressed to an agent (newest last).",
        "inputSchema": {
            "type": "object",
            "required": ["to"],
            "properties": {
                "to": {"type": "string"},
                "unread_only": {"type": "boolean"},
            },
        },
    },
    {
        "name": "mailbox_read",
        "description": "Read a message by id and mark it read.",
        "inputSchema": {
            "type": "object",
            "required": ["id"],
            "properties": {"id": {"type": "string"}},
        },
    },
]


def _append(msg):
    os.makedirs(os.path.dirname(mb.MAILBOX), exist_ok=True)
    with open(mb.MAILBOX, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(msg) + "\n")


def _rewrite(msgs):
    os.makedirs(os.path.dirname(mb.MAILBOX), exist_ok=True)
    tmp = mb.MAILBOX + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for m in msgs:
            fh.write(json.dumps(m) + "\n")
    os.replace(tmp, mb.MAILBOX)  # atomic


def _send(args):
    msgs = mb._load()
    m = {
        "id": f"m{len(msgs) + 1:04d}",
        "ts": mb._now(),
        "from": args["from"],
        "to": args["to"],
        "subject": args["subject"],
        "body": args["body"],
        "status": "unread",
    }
    if args.get("ref"):
        m["ref"] = args["ref"]
    _append(m)
    return f'sent {m["id"]}'


def _inbox(args):
    to = args["to"]
    uo = args.get("unread_only", False)
    rows = [
        f'{m["id"]} [{m.get("status", "unread")}] {m["ts"]} from {m["from"]}: {m["subject"]}'
        for m in mb._load()
        if m["to"] == to and (not uo or m.get("status", "unread") != "read")
    ]
    return "\n".join(rows) if rows else "(empty)"


def _read(args):
    msgs = mb._load()
    found = None
    for m in msgs:
        if m["id"] == args["id"]:
            found = m
            m["status"] = "read"
    if found is not None:
        _rewrite(msgs)
        return json.dumps(found, indent=2)
    return f'no message {args["id"]}'


DISPATCH = {"mailbox_send": _send, "mailbox_inbox": _inbox, "mailbox_read": _read}


def reply(id_, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": id_}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        method = req.get("method")
        id_ = req.get("id")
        if method == "initialize":
            reply(id_, {
                "protocolVersion": PROTO,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "agent-mailbox", "version": "1.0.0"},
            })
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            reply(id_, {"tools": TOOLS})
        elif method == "tools/call":
            params = req.get("params", {})
            name = params.get("name")
            args = params.get("arguments", {}) or {}
            fn = DISPATCH.get(name)
            if not fn:
                reply(id_, error={"code": -32601, "message": f"unknown tool {name}"})
                continue
            try:
                text = fn(args)
                reply(id_, {"content": [{"type": "text", "text": text}], "isError": False})
            except Exception as e:  # noqa: BLE001
                reply(id_, {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True})
        elif id_ is not None:
            reply(id_, error={"code": -32601, "message": f"unknown method {method}"})


if __name__ == "__main__":
    main()
