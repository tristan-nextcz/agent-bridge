#!/usr/bin/env python3
"""Minimal async mailbox for the Claude <-> Codex bridge.

JSONL, stdlib-only. Each line is one message: id, ts, from, to, subject, body.
This is the durable handoff channel — the two CLIs are separate vendor apps with
no live IPC, so collaboration happens by writing/reading messages here and by
one-shot headless invocations (see ask_codex.sh / ask_claude.sh).

Usage:
  mailbox.py send --from claude --to codex --subject "task" --body "..."
  mailbox.py read [--to codex] [--unread]        # print messages
  mailbox.py last [--to claude]                  # print the most recent match
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

STATE_DIR = os.path.expanduser(os.environ.get("AGENT_BRIDGE_STATE_DIR", "~/.local/state/agent-bridge"))
MAILBOX = os.path.join(STATE_DIR, "mailbox", "messages.jsonl")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load() -> list[dict]:
    if not os.path.exists(MAILBOX):
        return []
    out = []
    with open(MAILBOX, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def cmd_send(a) -> int:
    os.makedirs(os.path.dirname(MAILBOX), exist_ok=True)
    n = len(_load())
    msg = {"id": f"m{n + 1:04d}", "ts": _now(), "from": a.frm, "to": a.to,
           "subject": a.subject, "body": a.body}
    with open(MAILBOX, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(msg) + "\n")
    print(msg["id"])
    return 0


def _print(m: dict, full: bool) -> None:
    print(f"[{m['id']}] {m['ts']}  {m['from']} -> {m['to']}: {m['subject']}")
    body = m["body"] if full else (m["body"][:500] + ("…" if len(m["body"]) > 500 else ""))
    print("  " + body.replace("\n", "\n  "))


def cmd_read(a) -> int:
    msgs = _load()
    if a.to:
        msgs = [m for m in msgs if m["to"] == a.to]
    for m in msgs:
        _print(m, a.full)
    if not msgs:
        print("(mailbox empty)")
    return 0


def cmd_last(a) -> int:
    msgs = _load()
    if a.to:
        msgs = [m for m in msgs if m["to"] == a.to]
    if not msgs:
        print("(no matching message)")
        return 1
    _print(msgs[-1], True)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Claude<->Codex bridge mailbox")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("send")
    s.add_argument("--from", dest="frm", required=True)
    s.add_argument("--to", required=True)
    s.add_argument("--subject", default="")
    s.add_argument("--body", required=True)
    s.set_defaults(func=cmd_send)
    r = sub.add_parser("read")
    r.add_argument("--to", default=None)
    r.add_argument("--full", action="store_true")
    r.set_defaults(func=cmd_read)
    l = sub.add_parser("last")
    l.add_argument("--to", default=None)
    l.set_defaults(func=cmd_last)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
