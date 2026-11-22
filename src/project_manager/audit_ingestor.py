# Ref: docs/adr/0001-arquitectura-orquestador-lotes-mas-cli-determinista.md
"""Post-Audit Mode (`ingest-audit`): ingest `codebase-auditor` reports into GitHub Issues."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from project_manager.inspector import (
    InspectorError,
    classify_issue,
    plan_batch_execution,
    resolve_repo,
    run_cmd,
)
from project_manager.models import (
    AuditIngestItem,
    AuditIngestReport,
    AuditPriority,
    BatchIssue,
)

_PRIORITY_RANK: dict[str, int] = {
    AuditPriority.HIGH.value: 3,
    AuditPriority.MEDIUM.value: 2,
    AuditPriority.LOW.value: 1,
}

_ISSUE_URL_NUM_RE = re.compile(r"/issues/(\d+)(?:\b|$)")
_FALLBACK_NUM_RE = re.compile(r"#?(\d+)\b")


class AuditIngestError(RuntimeError):
    """Raised when an `audit_report.json` payload is invalid or issue creation fails."""


def _validate_priority(raw_priority: str, context: str) -> str:
    """Validate and normalize a priority string ('HIGH', 'MEDIUM', 'LOW')."""
    normalized = raw_priority.strip().upper()
    if normalized not in _PRIORITY_RANK:
        raise AuditIngestError(
            f"Invalid priority '{raw_priority}' in {context} (expected HIGH, MEDIUM, or LOW)."
        )
    return normalized


def _optional_int(value: Any, field_name: str, finding_id: str) -> int | None:
    """Parse an optional positive line number integer from a finding field."""
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise AuditIngestError(
            f"Invalid '{field_name}' in finding '{finding_id}': expected integer or null."
        )
    try:
        return int(value)
    except ValueError as exc:
        raise AuditIngestError(
            f"Invalid '{field_name}' in finding '{finding_id}': expected integer or null."
        ) from exc


def build_finding_issue_payload(finding: dict[str, Any]) -> tuple[str, str, list[str]]:
    """Build `(title, body, labels)` from a `codebase-auditor` finding dict."""
    finding_id = str(finding.get("id") or "AUD-?").strip()
    proposed = finding.get("proposed_issue")
    if proposed is not None and not isinstance(proposed, dict):
        raise AuditIngestError(
            f"Invalid 'proposed_issue' in finding '{finding_id}': expected a JSON object."
        )
    proposed_dict = proposed or {}

    raw_labels = proposed_dict.get("labels")
    if raw_labels is not None and not isinstance(raw_labels, list):
        raise AuditIngestError(
            f"Invalid 'proposed_issue.labels' in finding '{finding_id}': expected a list."
        )

    title = str(proposed_dict.get("title") or "").strip()
    body = str(proposed_dict.get("body") or "").strip()
    labels = [str(lbl).strip() for lbl in (raw_labels or []) if str(lbl).strip()]

    if not title:
        symbol = finding.get("symbol") or finding.get("file") or "codebase"
        title = f"refactor: atender hallazgo {finding.get('id', 'AUD')} en {symbol}"

    if not body:
        body = (
            f"### Contexto / Evidencia\n"
            f"- **Prioridad:** {finding.get('priority', 'MEDIUM')}\n"
            f"- **Archivo:** `{finding.get('file', '')}` "
            f"(L{finding.get('start_line', '?')}-L{finding.get('end_line', '?')})\n"
            f"- **Símbolo:** `{finding.get('symbol', '-')}`\n"
            f"- **Evidencia:** {finding.get('evidence', '')}\n"
            f"- **Impacto:** {finding.get('impact', '')}\n"
        )

    return title, body, labels


def load_and_validate_audit_report(report_path: str | Path) -> dict[str, Any]:
    """Load and validate a `codebase-auditor` JSON report from disk."""
    path = Path(report_path).expanduser()
    if not path.is_file():
        raise AuditIngestError(f"Audit report file not found: {report_path}")

    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AuditIngestError(f"Could not read audit report '{report_path}': {exc}") from exc

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise AuditIngestError(f"Invalid JSON in audit report '{report_path}': {exc}") from exc

    if not isinstance(data, dict):
        raise AuditIngestError("Audit report root must be a JSON object.")

    findings = data.get("findings")
    if not isinstance(findings, list):
        raise AuditIngestError("Audit report must contain a 'findings' list.")

    for idx, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise AuditIngestError(f"Finding at index {idx} must be a JSON object.")
        finding_id = str(finding.get("id") or f"index-{idx}").strip()
        raw_priority = str(finding.get("priority") or "")
        _validate_priority(raw_priority, f"finding '{finding_id}'")
        _optional_int(finding.get("start_line"), "start_line", finding_id)
        _optional_int(finding.get("end_line"), "end_line", finding_id)
        build_finding_issue_payload(finding)

    return data


def _parse_created_issue_output(output: str, finding_id: str) -> tuple[int, str]:
    """Extract `(issue_number, issue_url)` from `gh issue create` stdout."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    last_line = lines[-1] if lines else output.strip()

    url_match = _ISSUE_URL_NUM_RE.search(output)
    if url_match:
        return int(url_match.group(1)), last_line

    fallback_match = _FALLBACK_NUM_RE.search(last_line)
    if fallback_match:
        return int(fallback_match.group(1)), last_line

    raise AuditIngestError(
        f"Could not parse created issue number from gh output for '{finding_id}': {output.strip()}"
    )


def _build_preview_item(finding: dict[str, Any]) -> tuple[AuditIngestItem, list[int]]:
    """Construct a dry-run `AuditIngestItem` and preliminary dependencies from a finding."""
    finding_id = str(finding.get("id") or "AUD-?").strip()
    priority = str(finding.get("priority") or "").strip().upper()
    file_path = str(finding.get("file") or "").strip()
    start_line = _optional_int(finding.get("start_line"), "start_line", finding_id)
    end_line = _optional_int(finding.get("end_line"), "end_line", finding_id)
    symbol = str(finding.get("symbol") or "").strip()
    evidence = str(finding.get("evidence") or "").strip()
    impact = str(finding.get("impact") or "").strip()

    title, body, labels = build_finding_issue_payload(finding)
    ceremony, requires_adr, referenced_files, depends_on = classify_issue(
        0, title, body, labels
    )
    if file_path and file_path not in referenced_files:
        referenced_files = [file_path, *referenced_files]

    item = AuditIngestItem(
        finding_id=finding_id,
        priority=priority,
        title=title,
        body=body,
        labels=labels,
        file=file_path,
        start_line=start_line,
        end_line=end_line,
        symbol=symbol,
        evidence=evidence,
        impact=impact,
        suggested_ceremony=ceremony,
        requires_adr_review=requires_adr,
        referenced_files=referenced_files,
        status="dry-run",
        issue_number=None,
        issue_url="",
    )
    return item, depends_on


def _create_github_issue_for_item(repo: str, item: AuditIngestItem) -> tuple[int, str]:
    """Create a GitHub issue via `gh issue create` for an `AuditIngestItem`."""
    cmd = ["gh", "issue", "create", "--repo", repo, "--title", item.title, "--body", item.body]
    for label in item.labels:
        cmd.extend(["--label", label])

    try:
        proc = run_cmd(cmd)
    except InspectorError as exc:
        raise AuditIngestError(
            f"Failed to create GitHub issue for finding '{item.finding_id}': {exc}"
        ) from exc

    return _parse_created_issue_output(proc.stdout, item.finding_id)


def ingest_audit_report(
    repo_or_path: str,
    report_path: str | Path,
    min_priority: str = AuditPriority.HIGH.value,
    confirm: bool = False,
) -> AuditIngestReport:
    """Ingest a `codebase-auditor` report in `--dry-run` (default) or `--confirm` mode."""
    normalized_min = _validate_priority(min_priority, "--min-priority")
    min_rank = _PRIORITY_RANK[normalized_min]

    try:
        repo, _ = resolve_repo(repo_or_path)
    except InspectorError as exc:
        raise AuditIngestError(str(exc)) from exc

    data = load_and_validate_audit_report(report_path)
    raw_findings: list[dict[str, Any]] = data.get("findings") or []
    summary = str(data.get("summary") or "").strip()

    indexed_selected: list[tuple[int, int, dict[str, Any]]] = []
    skipped_count = 0

    for idx, finding in enumerate(raw_findings):
        priority = str(finding.get("priority") or "").strip().upper()
        rank = _PRIORITY_RANK[priority]
        if rank < min_rank:
            skipped_count += 1
            continue
        indexed_selected.append((-rank, idx, finding))

    indexed_selected.sort(key=lambda entry: (entry[0], entry[1]))

    items: list[AuditIngestItem] = []
    created_batch_issues: list[BatchIssue] = []
    created_issue_numbers: list[int] = []

    for _, _, finding in indexed_selected:
        item, depends_on = _build_preview_item(finding)
        if confirm:
            issue_num, issue_url = _create_github_issue_for_item(repo, item)
            item.status = "created"
            item.issue_number = issue_num
            item.issue_url = issue_url
            created_issue_numbers.append(issue_num)
            created_batch_issues.append(
                BatchIssue(
                    number=issue_num,
                    title=item.title,
                    body=item.body,
                    state="OPEN",
                    url=issue_url,
                    labels=item.labels,
                    suggested_ceremony=item.suggested_ceremony,
                    requires_adr_review=item.requires_adr_review,
                    referenced_files=item.referenced_files,
                    depends_on_issues=[dep for dep in depends_on if dep != issue_num],
                )
            )
        items.append(item)

    plan = plan_batch_execution(created_batch_issues) if created_batch_issues else None

    return AuditIngestReport(
        repo=repo,
        report_path=str(Path(report_path)),
        min_priority=normalized_min,
        dry_run=not confirm,
        summary=summary,
        total_findings=len(raw_findings),
        selected_count=len(items),
        skipped_count=skipped_count,
        items=items,
        created_issue_numbers=created_issue_numbers,
        recommended_order=plan.recommended_order if plan else [],
        adr_review_issues=plan.adr_review_issues if plan else [],
        file_collisions=plan.file_collisions if plan else {},
    )
