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

On Windows PowerShell:

```powershell
git clone https://github.com/tristan-nextcz/agent-bridge.git $HOME\Code\agent-bridge
cd $HOME\Code\agent-bridge
.\scripts\install.ps1
```

That creates:

```text
%USERPROFILE%\.local\bin\agent.cmd
%USERPROFILE%\.local\state\agent-bridge\
```

The installer adds `%USERPROFILE%\.local\bin` to the user `PATH` unless you pass
`-SkipPathUpdate`. Open a new terminal after install so `agent` is available everywhere.

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

## Configure Session Hooks

Install lightweight SessionStart hooks for both Codex and Claude:

```bash
agent code hooks install --client both
```

The hook injects a short reminder that Agent Bridge is available, points agents at the global
mailbox MCP path, and notes that `agent code loop` uses the auto dispatch gate. It does not spawn
agents, run network calls, or mutate project files during session startup.

Check hook status:

```bash
agent code hooks status --client both
```

On Windows, `.\scripts\install.ps1` runs the same hook installer automatically. To also attempt
MCP registration for both local CLIs, run:

```powershell
.\scripts\install.ps1 -RegisterMcp
```

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

By default, `agent code loop` uses `--spawn-policy auto`. The bridge scores the prompt for
implementation depth, concrete scope, and risk signals before spending on the full
builder/critic/verifier loop. If the request is vague, review-only, or too shallow to justify a
full spawn, it dispatches one analysis-only adversarial agent instead. Use
`--spawn-policy full` to force the full loop, or `--spawn-policy adversarial-only` to always run
the single-review fallback.

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
