# Agent Bridge

Global local bridge for bounded collaboration between coding agents from any project.

The bridge has two surfaces:

- `agent code bridge` invokes a fresh headless turn of a configured agent CLI for review or
  local coding work.
- `mailbox_mcp.py` exposes a small shared mailbox as MCP tools for async handoff.

The bridge is local developer infrastructure. It is not a daemon and it does not attach to
existing UI sessions.

## Install

```bash
cd /Users/tts/Code/agent-bridge
scripts/install.sh
```

That creates:

```text
~/.local/bin/agent -> /Users/tts/Code/agent-bridge/bin/agent
```

`~/.local/bin` must be on `PATH`.

## Configure MCP

Register the mailbox globally in Claude Code:

```bash
claude mcp add --scope user mailbox -- python3 /Users/tts/Code/agent-bridge/agent_bridge/mailbox_mcp.py
```

Register the mailbox globally in Codex:

```bash
codex mcp add mailbox -- python3 /Users/tts/Code/agent-bridge/agent_bridge/mailbox_mcp.py
```

The mailbox tools are:

- `mailbox_send`
- `mailbox_inbox`
- `mailbox_read`
- `trace_events`
- `finding_emit`
- `findings_list`
- `finding_read`
- `verdict_record`
- `verdicts_list`

## Use

From any git worktree:

```bash
agent code bridge --from human --to claude --mode review \
  --prompt "Review the current diff for concrete defects."

agent code bridge --from human --to codex --mode code \
  --prompt "Implement the scoped change and run focused tests."
```

The bridge targets the current git root by default. Use `--project-dir` to target a different
worktree:

```bash
agent code bridge --project-dir /path/to/repo --from human --to claude --mode review \
  --prompt "Review this release checklist."
```

List configured agents:

```bash
agent code bridge --list
```

Dry-run without invoking model CLIs:

```bash
agent code bridge --from human --to claude --mode review --dry-run \
  --prompt "Show the command you would run."
```

Run a bounded adversarial loop:

```bash
agent code loop --builder codex --critic claude --verifier claude --max-turns 1 \
  --prompt "Implement the scoped change and look for blocking defects."
```

Inspect trace events and structured findings:

```bash
agent code trace --run-id run_...
agent code findings create --run-id run_... --severity high --claim "..."
agent code verdicts record --run-id run_... --status fail --summary "..."
```

## State

Runtime state is outside repositories:

```text
~/.local/state/agent-bridge/
  bridge_agents.log
  events.jsonl
  findings.jsonl
  verdicts.jsonl
  transcripts/
  mailbox/messages.jsonl
```

Override with:

```bash
export AGENT_BRIDGE_STATE_DIR=/path/to/state
```

## Safety

- No live Domino actions, credential use, deploys, teardowns, or direct GitHub pushes.
- `review` mode is analysis-only.
- `code` mode may edit local files in the target worktree; review diffs before committing.
- Keep public branch names, PR titles, and repo-visible artifacts neutral and logical. Do not
  expose agent/tool identity in branch names.

## Development

```bash
python3 -m py_compile agent_bridge/*.py
python3 -m unittest discover -s tests
```
