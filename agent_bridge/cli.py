#!/usr/bin/env python3
"""Generic local agent bridge.

The public entry point is:

    agent code bridge

The bridge invokes fresh, bounded headless turns of configured agent CLIs. It is
filesystem/process based by design: no daemon, no IPC, and no assumption that
the caller is Codex or Claude.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from typing import Any


BRIDGE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = BRIDGE_DIR / "agents.json"
STATE_DIR = Path(os.environ.get("AGENT_BRIDGE_STATE_DIR", Path.home() / ".local/state/agent-bridge")).expanduser()
TRANSCRIPT_DIR = STATE_DIR / "transcripts"
BRIDGE_LOG = STATE_DIR / "bridge_agents.log"
PROJECT_DIR = Path.cwd()


class BridgeError(RuntimeError):
    pass


def load_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    agents = data.get("agents")
    if not isinstance(agents, list) or not agents:
        raise BridgeError(f"{path} must define a non-empty agents list")
    seen: set[str] = set()
    for agent in agents:
        agent_id = agent.get("id")
        if not isinstance(agent_id, str) or not agent_id:
            raise BridgeError(f"{path} contains an agent without an id")
        if agent_id in seen:
            raise BridgeError(f"{path} contains duplicate agent id {agent_id!r}")
        seen.add(agent_id)
    return data


def agent_map(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {agent["id"]: agent for agent in config["agents"]}


def discover_project_dir() -> Path:
    try:
        output = subprocess.check_output(
            ["git", "-C", str(Path.cwd()), "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        if output:
            return Path(output).resolve()
    except (OSError, subprocess.CalledProcessError):
        pass
    return Path.cwd().resolve()


def run_git(args: list[str]) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(PROJECT_DIR), *args],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def utc_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def resolve_command(agent: dict[str, Any]) -> str:
    env_name = agent.get("env_command")
    if env_name and os.environ.get(env_name):
        return os.environ[env_name]
    command = agent.get("command")
    if not command:
        raise BridgeError(f"agent {agent['id']} has no command")
    resolved = shutil.which(command)
    return resolved or command


def print_agent_list(agents: dict[str, dict[str, Any]]) -> None:
    for index, agent_id in enumerate(agents, start=1):
        agent = agents[agent_id]
        label = agent.get("label", agent_id)
        description = agent.get("description", "")
        suffix = f" - {description}" if description else ""
        print(f"{index}. {agent_id} ({label}){suffix}")


def split_selection(raw: str) -> list[str]:
    return [part.strip().lower() for part in raw.replace(",", " ").split() if part.strip()]


def resolve_agent_ids(raw: str, agents: dict[str, dict[str, Any]]) -> list[str]:
    if raw.strip().lower() in {"all", "*"}:
        return list(agents)
    resolved: list[str] = []
    ids = list(agents)
    for part in split_selection(raw):
        if part.isdigit():
            index = int(part)
            if index < 1 or index > len(ids):
                raise BridgeError(f"agent index {index} is out of range")
            agent_id = ids[index - 1]
        else:
            matches = [agent_id for agent_id in ids if agent_id == part or agent_id.startswith(part)]
            if len(matches) != 1:
                raise BridgeError(f"agent selection {part!r} did not match exactly one agent")
            agent_id = matches[0]
        if agent_id not in resolved:
            resolved.append(agent_id)
    return resolved


def prompt_line(label: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or (default or "")


def interactive_options(args: argparse.Namespace, agents: dict[str, dict[str, Any]]) -> argparse.Namespace:
    if not sys.stdin.isatty():
        missing = []
        if not args.source:
            missing.append("--from")
        if not args.targets:
            missing.append("--to")
        if missing:
            raise BridgeError(f"non-interactive bridge call is missing: {', '.join(missing)}")
        return args

    print("Available agents:")
    print_agent_list(agents)
    print("")

    if not args.source:
        args.source = prompt_line("Calling agent or instance", os.environ.get("AGENT_BRIDGE_CALLER", "human"))

    if not args.targets:
        args.targets = prompt_line("Target agent(s), comma-separated names/numbers or 'all'")

    if not args.mode:
        args.mode = prompt_line("Mode: review or code", "review")

    if not args.prompt:
        print("Task prompt. End with a blank line:")
        lines: list[str] = []
        while True:
            line = input()
            if not line:
                break
            lines.append(line)
        args.prompt = "\n".join(lines).strip()

    return args


def read_prompt(args: argparse.Namespace) -> str:
    if args.prompt:
        return args.prompt
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return ""


def build_scope(source: str, target: dict[str, Any], mode: str) -> str:
    branch = run_git(["branch", "--show-current"]) or "unknown"
    head = run_git(["rev-parse", "--short", "HEAD"]) or "unknown"
    status = run_git(["status", "--short", "--branch"]) or "unknown"
    target_label = target.get("label", target["id"])
    action = "edit local files and run local tests" if mode == "code" else "return analysis only"
    no_edit = "" if mode == "code" else " Do not modify files."
    return f"""[AGENT CODE BRIDGE - {mode.upper()}]
You are {target_label}, invoked headlessly by {source} through a generic local agent bridge.

Project: {PROJECT_DIR}
Branch: {branch}
HEAD: {head}
Git status at dispatch:
{status}

Task contract: {action}.{no_edit}

Hard limits: no live Domino actions, no credential use, no deploy, no teardown, no browser
automation unless explicitly requested for local UI verification, no direct GitHub push, and no
secrets. Keep changes scoped to this worktree, preserve the repo's generic/de-identified
positioning, and report files changed plus verification performed.
"""


def fill_template(parts: list[str], values: dict[str, str]) -> list[str]:
    return [part.format(**values) for part in parts]


def command_for_agent(
    agent: dict[str, Any],
    *,
    source: str,
    mode: str,
    prompt: str,
    scope: str,
    budget_usd: str,
) -> list[str]:
    command = resolve_command(agent)
    adapter = agent.get("adapter")
    if adapter == "claude_code":
        permission_mode = "acceptEdits" if mode == "code" else "plan"
        return [
            command,
            "-p",
            prompt,
            "--append-system-prompt",
            scope,
            "--add-dir",
            str(PROJECT_DIR),
            "--permission-mode",
            permission_mode,
            "--max-budget-usd",
            budget_usd,
            "--output-format",
            "text",
        ]
    if adapter == "codex_exec":
        sandbox = "workspace-write" if mode == "code" else "read-only"
        combined_prompt = f"{scope}\n\n[BRIDGE REQUEST FROM {source}]\n{prompt}"
        return [
            command,
            "exec",
            combined_prompt,
            "-C",
            str(PROJECT_DIR),
            "-s",
            sandbox,
            "-a",
            "never",
        ]
    if adapter == "argv":
        templates = agent.get(f"{mode}_args") or agent.get("args")
        if not isinstance(templates, list):
            raise BridgeError(f"agent {agent['id']} adapter=argv needs args or {mode}_args")
        values = {
            "prompt": prompt,
            "scope": scope,
            "project_dir": str(PROJECT_DIR),
            "mode": mode,
            "source": source,
            "target": agent["id"],
            "budget_usd": budget_usd,
        }
        return [command, *fill_template([str(part) for part in templates], values)]
    raise BridgeError(f"agent {agent['id']} has unsupported adapter {adapter!r}")


def write_header(transcript: Path, *, source: str, target: str, mode: str, prompt: str, cmd: list[str]) -> None:
    safe_cmd = [cmd[0], *("<prompt/scope>" if part.startswith("[AGENT CODE BRIDGE") else part for part in cmd[1:])]
    with transcript.open("a", encoding="utf-8") as handle:
        handle.write(f"=== Agent bridge request {utc_stamp()} ===\n")
        handle.write(f"project: {PROJECT_DIR}\nsource: {source}\ntarget: {target}\nmode: {mode}\n")
        handle.write(f"command: {shlex.join(safe_cmd)}\n\n")
        handle.write(prompt)
        handle.write("\n\n=== Agent response ===\n")


def invoke_target(
    agent: dict[str, Any],
    *,
    source: str,
    mode: str,
    prompt: str,
    budget_usd: str,
    dry_run: bool,
) -> int:
    scope = build_scope(source, agent, mode)
    cmd = command_for_agent(agent, source=source, mode=mode, prompt=prompt, scope=scope, budget_usd=budget_usd)
    if dry_run:
        print(f"[dry-run] {agent['id']}: {shlex.join(cmd)}")
        return 0

    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    transcript = TRANSCRIPT_DIR / f"{agent['id']}_{utc_stamp()}.txt"
    write_header(transcript, source=source, target=agent["id"], mode=mode, prompt=prompt, cmd=cmd)

    with transcript.open("a", encoding="utf-8") as transcript_handle, BRIDGE_LOG.open("a", encoding="utf-8") as log_handle:
        process = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="")
            transcript_handle.write(line)
            log_handle.write(line)
        rc = process.wait()
    print(f"\n[transcript] {transcript}", file=sys.stderr)
    return rc


def parse_bridge_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="agent code bridge",
        description="Invoke one or more configured local coding agents through a generic bridge.",
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to bridge agent config JSON")
    parser.add_argument("--project-dir", help="Project/worktree directory. Defaults to the current git root.")
    parser.add_argument("--from", dest="source", help="Calling agent or instance, e.g. codex, claude, human")
    parser.add_argument("--to", dest="targets", help="Target agent ids, numbers, comma list, or 'all'")
    parser.add_argument("--mode", choices=["review", "code"], help="Bridge mode")
    parser.add_argument("--prompt", help="Task prompt. If omitted in non-interactive mode, stdin is used.")
    parser.add_argument("--budget-usd", default=os.environ.get("AGENT_BRIDGE_BUDGET_USD", "0.50"))
    parser.add_argument("--list", action="store_true", help="List configured agents and exit")
    parser.add_argument("--dry-run", action="store_true", help="Print target commands without invoking agents")
    return parser.parse_args(argv)


def bridge(argv: list[str]) -> int:
    global PROJECT_DIR
    args = parse_bridge_args(argv)
    PROJECT_DIR = Path(args.project_dir).expanduser().resolve() if args.project_dir else discover_project_dir()
    config = load_config(Path(args.config))
    agents = agent_map(config)
    if args.list:
        print_agent_list(agents)
        return 0

    args = interactive_options(args, agents)
    source = args.source or "human"
    mode = args.mode or "review"
    if mode not in {"review", "code"}:
        raise BridgeError("mode must be review or code")
    prompt = read_prompt(args)
    if not prompt:
        raise BridgeError("a task prompt is required")
    if not args.targets:
        raise BridgeError("at least one target agent is required")

    targets = resolve_agent_ids(args.targets, agents)
    rc = 0
    for target_id in targets:
        target_rc = invoke_target(
            agents[target_id],
            source=source,
            mode=mode,
            prompt=prompt,
            budget_usd=str(args.budget_usd),
            dry_run=args.dry_run,
        )
        if target_rc != 0:
            rc = target_rc
    return rc


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "code" and argv[1] == "bridge":
        return bridge(argv[2:])
    if len(argv) >= 1 and argv[0] == "bridge":
        return bridge(argv[1:])
    print("usage: agent code bridge [options]", file=sys.stderr)
    print("       agent bridge [options]", file=sys.stderr)
    return 2


def main_entry() -> None:
    raise SystemExit(main(sys.argv[1:]))


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except BridgeError as exc:
        print(f"agent code bridge: {exc}", file=sys.stderr)
        raise SystemExit(2)
