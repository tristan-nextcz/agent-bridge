"""Structured findings and verdicts for adversarial bridge loops."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

try:
    from .correlation import iso_now, new_id
    from .trace import emit_event, state_dir
except ImportError:
    from correlation import iso_now, new_id  # type: ignore[no-redef]
    from trace import emit_event, state_dir  # type: ignore[no-redef]


SEVERITIES = {"info", "low", "medium", "high", "critical"}
FINDING_STATUSES = {"open", "accepted", "rebutted", "resolved", "wontfix"}
VERDICT_STATUSES = {"pass", "fail", "blocked", "needs-work"}


def findings_path() -> Path:
    return state_dir() / "findings.jsonl"


def verdicts_path() -> Path:
    return state_dir() / "verdicts.jsonl"


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _as_list(values: Iterable[str] | str | None) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        return [values] if values else []
    return [value for value in values if value]


def create_finding(
    *,
    run_id: str,
    severity: str,
    claim: str,
    evidence: Iterable[str] | str | None = None,
    reproduction: str = "",
    status: str = "open",
    owner_role: str = "",
    rebuttal: str = "",
    resolution: str = "",
) -> dict[str, Any]:
    if severity not in SEVERITIES:
        raise ValueError(f"severity must be one of: {', '.join(sorted(SEVERITIES))}")
    if status not in FINDING_STATUSES:
        raise ValueError(f"status must be one of: {', '.join(sorted(FINDING_STATUSES))}")
    if not run_id:
        raise ValueError("run_id is required")
    if not claim:
        raise ValueError("claim is required")
    now = iso_now()
    record = {
        "id": new_id("finding"),
        "run_id": run_id,
        "severity": severity,
        "claim": claim,
        "evidence": _as_list(evidence),
        "reproduction": reproduction,
        "status": status,
        "owner_role": owner_role,
        "rebuttal": rebuttal,
        "resolution": resolution,
        "ts": now,
        "updated_ts": now,
    }
    _append_jsonl(findings_path(), record)
    emit_event("finding.raised", run_id=run_id, data={"finding_id": record["id"], "severity": severity, "status": status})
    return record


def list_findings(
    *,
    run_id: str | None = None,
    status: str | None = None,
    severity: str | None = None,
) -> list[dict[str, Any]]:
    rows = _load_jsonl(findings_path())
    if run_id:
        rows = [row for row in rows if row.get("run_id") == run_id]
    if status:
        rows = [row for row in rows if row.get("status") == status]
    if severity:
        rows = [row for row in rows if row.get("severity") == severity]
    return rows


def read_finding(finding_id: str) -> dict[str, Any] | None:
    for row in _load_jsonl(findings_path()):
        if row.get("id") == finding_id:
            return row
    return None


def record_verdict(
    *,
    run_id: str,
    status: str,
    summary: str,
    blocking_findings: Iterable[str] | str | None = None,
    evidence: Iterable[str] | str | None = None,
) -> dict[str, Any]:
    if status not in VERDICT_STATUSES:
        raise ValueError(f"status must be one of: {', '.join(sorted(VERDICT_STATUSES))}")
    if not run_id:
        raise ValueError("run_id is required")
    if not summary:
        raise ValueError("summary is required")
    record = {
        "id": new_id("verdict"),
        "run_id": run_id,
        "status": status,
        "summary": summary,
        "blocking_findings": _as_list(blocking_findings),
        "evidence": _as_list(evidence),
        "ts": iso_now(),
    }
    _append_jsonl(verdicts_path(), record)
    emit_event("verdict.recorded", run_id=run_id, data={"verdict_id": record["id"], "status": status})
    return record


def list_verdicts(*, run_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    rows = _load_jsonl(verdicts_path())
    if run_id:
        rows = [row for row in rows if row.get("run_id") == run_id]
    if status:
        rows = [row for row in rows if row.get("status") == status]
    return rows


def format_findings(rows: Iterable[dict[str, Any]]) -> str:
    out = [f"{row['id']} [{row['severity']}/{row['status']}] run={row['run_id']} {row['claim']}" for row in rows]
    return "\n".join(out) if out else "(empty)"


def format_verdicts(rows: Iterable[dict[str, Any]]) -> str:
    out = [f"{row['id']} [{row['status']}] run={row['run_id']} {row['summary']}" for row in rows]
    return "\n".join(out) if out else "(empty)"
