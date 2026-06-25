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
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from typing import Any

from .correlation import add_meta_args, child_turn_meta, ensure_run_meta, extract_meta, format_meta, safe_fragment, utc_stamp
from .findings import (
    create_finding,
    format_findings,
    format_verdicts,
    list_findings,
    list_verdicts,
    read_finding,
    record_verdict,
)
from .trace import emit_event, events_path, format_events, load_events


BRIDGE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = BRIDGE_DIR / "agents.json"
STATE_DIR = Path(os.environ.get("AGENT_BRIDGE_STATE_DIR", Path.home() / ".local/state/agent-bridge")).expanduser()
TRANSCRIPT_DIR = STATE_DIR / "transcripts"
BRIDGE_LOG = STATE_DIR / "bridge_agents.log"
PROJECT_DIR = Path.cwd()


class BridgeError(RuntimeError):
    pass


@dataclass(frozen=True)
class SpawnDecision:
    mode: str
    score: int
    reasons: list[str]


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


IMPLEMENTATION_TERMS = {
    "add",
    "build",
    "change",
    "create",
    "fix",
    "implement",
    "refactor",
    "update",
}
COMPLEXITY_TERMS = {
    "api",
    "backwards compatible",
    "compatibility",
    "concurrency",
    "controller",
    "migration",
    "schema",
    "security",
    "trace",
    "workflow",
}
REVIEW_ONLY_TERMS = {
    "assess",
    "audit",
    "check",
    "inspect",
    "review",
    "smoke",
    "summarize",
}
VAGUE_TERMS = {
    "quick",
    "basic",
    "maybe",
    "thing",
    "this",
    "unclear",
}
FILE_SUFFIXES = (".py", ".js", ".ts", ".tsx", ".json", ".md", ".toml", ".yaml", ".yml", ".sh")


def _contains_any(text: str, terms: set[str], words: set[str]) -> bool:
    return any((term in text if " " in term else term in words) for term in terms)


def assess_spawn_decision(prompt: str, *, policy: str, max_turns: int) -> SpawnDecision:
    if policy == "full":
        return SpawnDecision("full_loop", 999, ["forced full loop by --spawn-policy full"])
    if policy == "adversarial-only":
        return SpawnDecision("adversarial_only", 0, ["forced single adversarial review by --spawn-policy adversarial-only"])

    text = " ".join(prompt.lower().split())
    words = text.split()
    word_set = {word.strip("`'\"(),:;.") for word in words}
    score = 0
    reasons: list[str] = []

    has_impl = _contains_any(text, IMPLEMENTATION_TERMS, word_set)
    has_review_only = _contains_any(text, REVIEW_ONLY_TERMS, word_set) and not has_impl
    has_path = any(token.strip("`'\"(),:;").endswith(FILE_SUFFIXES) or "/" in token for token in words)

    if has_impl:
        score += 2
        reasons.append("implementation verb present")
    if len(words) >= 35:
        score += 1
        reasons.append("prompt has enough detail")
    if has_path:
        score += 1
        reasons.append("concrete file or path scope present")
    if _contains_any(text, COMPLEXITY_TERMS, word_set):
        score += 1
        reasons.append("complexity/risk signal present")
    if max_turns > 1:
        score += 1
        reasons.append("caller requested multiple turns")
    if "adversarial" in text or "red team" in text:
        score += 1
        reasons.append("adversarial validation requested")

    if has_review_only:
        score -= 2
        reasons.append("review-only request")
    if len(words) < 12 or _contains_any(text, VAGUE_TERMS, word_set):
        score -= 1
        reasons.append("prompt is short or vague")

    if has_impl and score >= 4:
        return SpawnDecision("full_loop", score, reasons)
    if not reasons:
        reasons.append("insufficient shape/depth signals")
    return SpawnDecision("adversarial_only", score, reasons)


def build_scope(source: str, target: dict[str, Any], mode: str, meta: dict[str, Any] | None = None) -> str:
    branch = run_git(["branch", "--show-current"]) or "unknown"
    head = run_git(["rev-parse", "--short", "HEAD"]) or "unknown"
    status = run_git(["status", "--short", "--branch"]) or "unknown"
    target_label = target.get("label", target["id"])
    action = "edit local files and run local tests" if mode == "code" else "return analysis only"
    no_edit = "" if mode == "code" else " Do not modify files."
    meta = meta or {}
    correlation = format_meta(meta) or "none"
    return f"""[AGENT CODE BRIDGE - {mode.upper()}]
You are {target_label}, invoked headlessly by {source} through a generic local agent bridge.

Project: {PROJECT_DIR}
Branch: {branch}
HEAD: {head}
Correlation: {correlation}
Git status at dispatch:
{status}

Task contract: {action}.{no_edit}

Hard limits: no live production actions, no credential use, no deploy, no teardown, no browser
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


def write_header(
    transcript: Path,
    *,
    source: str,
    target: str,
    mode: str,
    prompt: str,
    cmd: list[str],
    meta: dict[str, Any] | None = None,
) -> None:
    safe_cmd = [cmd[0], *("<prompt/scope>" if part.startswith("[AGENT CODE BRIDGE") else part for part in cmd[1:])]
    with transcript.open("a", encoding="utf-8") as handle:
        handle.write(f"=== Agent bridge request {utc_stamp()} ===\n")
        handle.write(f"project: {PROJECT_DIR}\nsource: {source}\ntarget: {target}\nmode: {mode}\n")
        if meta:
            handle.write(f"correlation: {format_meta(meta)}\n")
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
    meta: dict[str, Any] | None = None,
) -> int:
    meta = meta or {}
    scope = build_scope(source, agent, mode, meta)
    cmd = command_for_agent(agent, source=source, mode=mode, prompt=prompt, scope=scope, budget_usd=budget_usd)
    emit_event(
        "agent.dispatched",
        run_id=meta.get("run_id"),
        meta=meta,
        data={"target": agent["id"], "mode": mode, "dry_run": dry_run, "project_dir": str(PROJECT_DIR)},
    )
    if dry_run:
        print(f"[dry-run] {agent['id']}: {shlex.join(cmd)}")
        emit_event(
            "agent.completed",
            run_id=meta.get("run_id"),
            meta=meta,
            data={"target": agent["id"], "mode": mode, "return_code": 0, "dry_run": True},
        )
        return 0

    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    prefix = safe_fragment(meta.get("run_id", agent["id"]))
    turn = safe_fragment(meta.get("turn_id", utc_stamp()))
    transcript = TRANSCRIPT_DIR / f"{prefix}_{turn}_{agent['id']}_{utc_stamp()}.txt"
    write_header(transcript, source=source, target=agent["id"], mode=mode, prompt=prompt, cmd=cmd, meta=meta)

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
    emit_event(
        "agent.completed",
        run_id=meta.get("run_id"),
        meta=meta,
        data={"target": agent["id"], "mode": mode, "return_code": rc, "dry_run": False, "transcript": str(transcript)},
    )
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
    add_meta_args(parser)
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
    base_meta = ensure_run_meta(extract_meta(args))
    emit_event(
        "run.created",
        run_id=base_meta.get("run_id"),
        meta=base_meta,
        data={"command": "bridge", "source": source, "mode": mode, "targets": targets, "dry_run": args.dry_run},
    )
    rc = 0
    for target_id in targets:
        target_meta = child_turn_meta(
            base_meta,
            role=target_id,
            attempt=int(base_meta.get("attempt", 1)),
            parent_id=base_meta.get("parent_id"),
        )
        target_rc = invoke_target(
            agents[target_id],
            source=source,
            mode=mode,
            prompt=prompt,
            budget_usd=str(args.budget_usd),
            dry_run=args.dry_run,
            meta=target_meta,
        )
        if target_rc != 0:
            rc = target_rc
    emit_event(
        "run.completed",
        run_id=base_meta.get("run_id"),
        meta=base_meta,
        data={"command": "bridge", "return_code": rc, "dry_run": args.dry_run},
    )
    return rc


def _json_print(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def _comma_values(values: list[str] | None) -> list[str]:
    if not values:
        return []
    out: list[str] = []
    for value in values:
        out.extend(part.strip() for part in value.split(",") if part.strip())
    return out


def _hook_agent_command(client: str) -> str:
    agent_bin = os.environ.get("AGENT_BRIDGE_HOOK_AGENT", os.path.expanduser("~/.local/bin/agent"))
    if agent_bin.lower().endswith((".cmd", ".bat")) or "\\" in agent_bin:
        return f'cmd /d /c ""{agent_bin}" code hook session-start --client {client}"'
    return f"'{agent_bin}' code hook session-start --client {client}"


def session_start_context(client: str) -> str:
    cwd = os.environ.get("PWD") or str(Path.cwd())
    git_root = ""
    try:
        git_root = subprocess.check_output(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        pass
    location = f" Current git root: {git_root}." if git_root else ""
    return (
        "Agent Bridge session bootstrap: global command `agent` is available for bounded local "
        "agent coordination. Use `agent code bridge` for one-shot headless review/code turns and "
        "`agent code loop` for adversarial loops; loop dispatch defaults to `--spawn-policy auto`, "
        "which falls back to one analysis-only adversarial agent unless the task is concrete enough "
        "for a full builder/critic/verifier spawn. Mailbox MCP, when registered, should point to "
        f"`{BRIDGE_DIR / 'mailbox_mcp.py'}`. This startup hook only injects "
        f"context and never spawns agents. Client: {client}.{location}"
    )


def hook_session_start(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="agent code hook session-start", description="Emit SessionStart hook context.")
    parser.add_argument("--client", choices=["codex", "claude"], required=True)
    parser.add_argument("--plain", action="store_true", help="Print plain context instead of hook JSON")
    args = parser.parse_args(argv)
    context = session_start_context(args.client)
    if args.plain:
        print(context)
    else:
        _json_print({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}})
    return 0


def _load_json_config(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise BridgeError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise BridgeError(f"{path} must contain a JSON object")
    return data


def _write_json_config(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_name(f"{path.name}.bak-{utc_stamp()}")
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _session_start_entries(config: dict[str, Any]) -> list[dict[str, Any]]:
    hooks = config.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise BridgeError("hooks must be a JSON object")
    entries = hooks.setdefault("SessionStart", [])
    if not isinstance(entries, list):
        raise BridgeError("hooks.SessionStart must be a list")
    return entries


def _ensure_command_hook(config: dict[str, Any], command: str) -> bool:
    entries = _session_start_entries(config)
    target_entry = None
    for entry in entries:
        if isinstance(entry, dict) and entry.get("matcher") == "startup|resume" and isinstance(entry.get("hooks"), list):
            target_entry = entry
            break
    if target_entry is None:
        target_entry = {"matcher": "startup|resume", "hooks": []}
        entries.append(target_entry)
    hooks = target_entry["hooks"]
    for hook in hooks:
        if isinstance(hook, dict) and hook.get("type") == "command" and hook.get("command") == command:
            return False
    hooks.append({"type": "command", "command": command})
    return True


def _config_path(client: str) -> Path:
    home = Path.home()
    if client == "codex":
        return home / ".codex" / "hooks.json"
    if client == "claude":
        return home / ".claude" / "settings.json"
    raise BridgeError(f"unsupported hook client {client!r}")


def install_session_hook(client: str) -> bool:
    path = _config_path(client)
    default = {"hooks": {}} if client == "codex" else {}
    config = _load_json_config(path, default)
    changed = _ensure_command_hook(config, _hook_agent_command(client))
    if changed:
        _write_json_config(path, config)
    return changed


def session_hook_installed(client: str) -> bool:
    path = _config_path(client)
    if not path.exists():
        return False
    config = _load_json_config(path, {})
    command = _hook_agent_command(client)
    for entry in config.get("hooks", {}).get("SessionStart", []):
        for hook in entry.get("hooks", []) if isinstance(entry, dict) else []:
            if isinstance(hook, dict) and hook.get("type") == "command" and hook.get("command") == command:
                return True
    return False


def hooks_cmd(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="agent code hooks", description="Install or inspect Agent Bridge session hooks.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    install = sub.add_parser("install")
    install.add_argument("--client", choices=["codex", "claude", "both"], default="both")
    status = sub.add_parser("status")
    status.add_argument("--client", choices=["codex", "claude", "both"], default="both")
    args = parser.parse_args(argv)

    clients = ["codex", "claude"] if args.client == "both" else [args.client]
    if args.cmd == "install":
        for client in clients:
            changed = install_session_hook(client)
            verb = "installed" if changed else "already installed"
            print(f"{client}: {verb} ({_config_path(client)})")
        return 0
    for client in clients:
        installed = session_hook_installed(client)
        print(f"{client}: {'installed' if installed else 'not installed'} ({_config_path(client)})")
    return 0


def trace_cmd(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="agent code trace", description="Inspect agent bridge trace events.")
    parser.add_argument("--run-id")
    parser.add_argument("--type", dest="event_type")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    rows = load_events(run_id=args.run_id, event_type=args.event_type)
    if args.json:
        _json_print(rows)
    else:
        print(format_events(rows))
    return 0


def findings_cmd(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="agent code findings", description="Create and inspect structured findings.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    create = sub.add_parser("create")
    create.add_argument("--run-id", required=True)
    create.add_argument("--severity", required=True)
    create.add_argument("--claim", required=True)
    create.add_argument("--evidence", action="append")
    create.add_argument("--reproduction", default="")
    create.add_argument("--status", default="open")
    create.add_argument("--owner-role", default="")
    create.add_argument("--rebuttal", default="")
    create.add_argument("--resolution", default="")
    create.add_argument("--json", action="store_true")

    list_parser = sub.add_parser("list")
    list_parser.add_argument("--run-id")
    list_parser.add_argument("--status")
    list_parser.add_argument("--severity")
    list_parser.add_argument("--json", action="store_true")

    read = sub.add_parser("read")
    read.add_argument("id")
    read.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    if args.cmd == "create":
        row = create_finding(
            run_id=args.run_id,
            severity=args.severity,
            claim=args.claim,
            evidence=args.evidence,
            reproduction=args.reproduction,
            status=args.status,
            owner_role=args.owner_role,
            rebuttal=args.rebuttal,
            resolution=args.resolution,
        )
        _json_print(row) if args.json else print(row["id"])
        return 0
    if args.cmd == "list":
        rows = list_findings(run_id=args.run_id, status=args.status, severity=args.severity)
        _json_print(rows) if args.json else print(format_findings(rows))
        return 0
    row = read_finding(args.id)
    if row is None:
        raise BridgeError(f"no finding {args.id}")
    _json_print(row) if args.json else print(format_findings([row]))
    return 0


def verdicts_cmd(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="agent code verdicts", description="Record and inspect loop verdicts.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    record = sub.add_parser("record")
    record.add_argument("--run-id", required=True)
    record.add_argument("--status", required=True)
    record.add_argument("--summary", required=True)
    record.add_argument("--blocking-finding", action="append", dest="blocking_findings")
    record.add_argument("--evidence", action="append")
    record.add_argument("--json", action="store_true")

    list_parser = sub.add_parser("list")
    list_parser.add_argument("--run-id")
    list_parser.add_argument("--status")
    list_parser.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    if args.cmd == "record":
        row = record_verdict(
            run_id=args.run_id,
            status=args.status,
            summary=args.summary,
            blocking_findings=_comma_values(args.blocking_findings),
            evidence=args.evidence,
        )
        _json_print(row) if args.json else print(row["id"])
        return 0
    rows = list_verdicts(run_id=args.run_id, status=args.status)
    _json_print(rows) if args.json else print(format_verdicts(rows))
    return 0


def parse_loop_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="agent code loop",
        description="Run a bounded builder -> critic -> verifier adversarial loop.",
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to bridge agent config JSON")
    parser.add_argument("--project-dir", help="Project/worktree directory. Defaults to the current git root.")
    parser.add_argument("--from", dest="source", default=os.environ.get("AGENT_BRIDGE_CALLER", "human"))
    parser.add_argument("--builder", default="codex", help="Agent id for code/build turns")
    parser.add_argument("--critic", default="claude", help="Agent id for adversarial review turns")
    parser.add_argument("--verifier", default="claude", help="Agent id for final verification turns")
    parser.add_argument("--max-turns", type=int, default=1)
    parser.add_argument("--budget-usd", default=os.environ.get("AGENT_BRIDGE_BUDGET_USD", "0.50"))
    parser.add_argument(
        "--spawn-policy",
        choices=["auto", "full", "adversarial-only"],
        default=os.environ.get("AGENT_BRIDGE_SPAWN_POLICY", "auto"),
        help="Dispatch policy. auto gates full loops; adversarial-only dispatches one review agent.",
    )
    parser.add_argument("--prompt", help="Loop task prompt. If omitted in non-interactive mode, stdin is used.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned dispatches without invoking agents")
    add_meta_args(parser)
    return parser.parse_args(argv)


def _loop_prompt(
    *,
    role: str,
    attempt: int,
    original_prompt: str,
    run_id: str,
    loop_id: str,
    decision: SpawnDecision,
) -> str:
    return f"""[ADVERSARIAL LOOP]
Run: {run_id}
Loop: {loop_id}
Role: {role}
Attempt: {attempt}
Dispatch decision: {decision.mode}
Decision score: {decision.score}
Decision reasons: {'; '.join(decision.reasons)}

Original task:
{original_prompt}

Role contract:
- builder: implement the requested change and run focused tests.
- critic: inspect the current worktree for concrete defects and emit structured findings when available.
- verifier: smoke test the current worktree and record whether blocking issues remain.
- adversarial: when full-loop criteria are not met, do one analysis-only adversarial review and say whether a larger spawn is justified.

Report files changed, checks run, and any blocking findings or verdicts.
"""


def loop(argv: list[str]) -> int:
    global PROJECT_DIR
    args = parse_loop_args(argv)
    if args.max_turns < 1:
        raise BridgeError("--max-turns must be at least 1")
    PROJECT_DIR = Path(args.project_dir).expanduser().resolve() if args.project_dir else discover_project_dir()
    config = load_config(Path(args.config))
    agents = agent_map(config)
    for target in (args.builder, args.critic, args.verifier):
        if target not in agents:
            raise BridgeError(f"unknown loop agent {target!r}")

    original_prompt = read_prompt(args)
    if not original_prompt:
        raise BridgeError("a loop task prompt is required")
    decision = assess_spawn_decision(original_prompt, policy=args.spawn_policy, max_turns=args.max_turns)

    base_meta = ensure_run_meta(extract_meta(args))
    base_meta.setdefault("loop_id", base_meta.get("run_id", "run").replace("run_", "loop_", 1))
    emit_event(
        "run.created",
        run_id=base_meta.get("run_id"),
        meta=base_meta,
        data={
            "command": "loop",
            "source": args.source,
            "builder": args.builder,
            "critic": args.critic,
            "verifier": args.verifier,
            "max_turns": args.max_turns,
            "spawn_policy": args.spawn_policy,
            "dispatch_decision": decision.mode,
            "decision_score": decision.score,
            "decision_reasons": decision.reasons,
            "dry_run": args.dry_run,
        },
    )
    emit_event(
        "dispatch.policy_evaluated",
        run_id=base_meta.get("run_id"),
        meta=base_meta,
        data={
            "command": "loop",
            "spawn_policy": args.spawn_policy,
            "decision": decision.mode,
            "score": decision.score,
            "reasons": decision.reasons,
        },
    )

    rc = 0
    parent_id = base_meta.get("parent_id")
    turn_count = args.max_turns if decision.mode == "full_loop" else 1
    for attempt in range(1, turn_count + 1):
        if decision.mode == "full_loop":
            phases = [
                ("builder", args.builder, "code"),
                ("critic", args.critic, "review"),
                ("verifier", args.verifier, "review"),
            ]
        else:
            phases = [("adversarial", args.critic, "review")]
        for role, target_id, mode in phases:
            turn_meta = child_turn_meta(base_meta, role=role, attempt=attempt, parent_id=parent_id)
            prompt = _loop_prompt(
                role=role,
                attempt=attempt,
                original_prompt=original_prompt,
                run_id=str(turn_meta["run_id"]),
                loop_id=str(turn_meta["loop_id"]),
                decision=decision,
            )
            target_rc = invoke_target(
                agents[target_id],
                source=args.source,
                mode=mode,
                prompt=prompt,
                budget_usd=str(args.budget_usd),
                dry_run=args.dry_run,
                meta=turn_meta,
            )
            parent_id = str(turn_meta["turn_id"])
            if target_rc != 0:
                rc = target_rc
                break
        if rc != 0:
            break

    emit_event(
        "run.completed",
        run_id=base_meta.get("run_id"),
        meta=base_meta,
        data={"command": "loop", "return_code": rc, "dry_run": args.dry_run, "events": str(events_path())},
    )
    print(f"run_id: {base_meta['run_id']}")
    print(f"loop_id: {base_meta['loop_id']}")
    print(f"dispatch_decision: {decision.mode}")
    print(f"decision_score: {decision.score}")
    print(f"events: {events_path()}")
    print(f"status: {'ok' if rc == 0 else 'failed'}")
    return rc


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "code" and argv[1] == "bridge":
        return bridge(argv[2:])
    if len(argv) >= 2 and argv[0] == "code" and argv[1] == "loop":
        return loop(argv[2:])
    if len(argv) >= 2 and argv[0] == "code" and argv[1] == "trace":
        return trace_cmd(argv[2:])
    if len(argv) >= 2 and argv[0] == "code" and argv[1] == "findings":
        return findings_cmd(argv[2:])
    if len(argv) >= 2 and argv[0] == "code" and argv[1] == "verdicts":
        return verdicts_cmd(argv[2:])
    if len(argv) >= 3 and argv[0] == "code" and argv[1] == "hook" and argv[2] == "session-start":
        return hook_session_start(argv[3:])
    if len(argv) >= 2 and argv[0] == "code" and argv[1] == "hooks":
        return hooks_cmd(argv[2:])
    if len(argv) >= 1 and argv[0] == "bridge":
        return bridge(argv[1:])
    print("usage: agent code bridge [options]", file=sys.stderr)
    print("       agent code loop [options]", file=sys.stderr)
    print("       agent code trace [options]", file=sys.stderr)
    print("       agent code findings <create|list|read> [options]", file=sys.stderr)
    print("       agent code verdicts <record|list> [options]", file=sys.stderr)
    print("       agent code hook session-start [options]", file=sys.stderr)
    print("       agent code hooks <install|status> [options]", file=sys.stderr)
    print("       agent bridge [options]", file=sys.stderr)
    return 2


def main_entry() -> None:
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (BridgeError, ValueError) as exc:
        print(f"agent: {exc}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (BridgeError, ValueError) as exc:
        print(f"agent: {exc}", file=sys.stderr)
        raise SystemExit(2)
