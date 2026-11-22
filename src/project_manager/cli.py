# Ref: docs/adr/0001-arquitectura-orquestador-lotes-mas-cli-determinista.md
"""CLI entrypoint for Project-Manager-Agent (`pm-agent`)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from project_manager.audit_ingestor import AuditIngestError, ingest_audit_report
from project_manager.gates import (
    GateError,
    build_batch_summary,
    run_post_merge_gate,
    run_pre_task_gate,
)
from project_manager.inspector import InspectorError, inspect_batch, parse_issue_list_arg
from project_manager.models import AuditPriority, GateStage


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for `pm-agent`."""
    parser = argparse.ArgumentParser(
        prog="pm-agent",
        description="Deterministic batch issue orchestrator CLI for issue-driven workflows.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_p = subparsers.add_parser("inspect", help="Collect Git, issues, PRs, and governance JSON.")
    inspect_p.add_argument("repo_or_path", nargs="?", default=".", help="Owner/repo slug or local path.")
    inspect_p.add_argument("--issues", help="Comma-separated issue IDs (e.g. '4,5,6').")
    inspect_p.add_argument("--label", help="Filter open backlog issues by label.")
    inspect_p.add_argument("--limit", type=int, default=30, help="Max open issues to fetch (default: 30).")
    inspect_p.add_argument("--no-fetch", action="store_true", help="Skip `git fetch --prune origin`.")
    inspect_p.add_argument("--out", help="Optional output file path (defaults to stdout).")

    gate_p = subparsers.add_parser("gate", help="Verify pre-task or post-merge workspace invariants.")
    gate_p.add_argument("repo_or_path", nargs="?", default=".", help="Local repository path.")
    gate_p.add_argument(
        "--stage",
        choices=[GateStage.PRE_TASK.value, GateStage.POST_MERGE.value],
        required=True,
        help="Gate stage ('pre-task' or 'post-merge').",
    )
    gate_p.add_argument("--issue", type=int, help="Issue number (required for post-merge).")
    gate_p.add_argument("--pr", type=int, help="Pull Request number (required for post-merge).")
    gate_p.add_argument("--no-fetch", action="store_true", help="Skip `git fetch --prune origin`.")
    gate_p.add_argument("--out", help="Optional output file path (defaults to stdout).")

    summary_p = subparsers.add_parser("batch-summary", help="Generate consolidated batch completion report.")
    summary_p.add_argument("repo_or_path", nargs="?", default=".", help="Owner/repo slug or local path.")
    summary_p.add_argument("--issues", required=True, help="Comma-separated issue IDs (e.g. '4,5,6').")
    summary_p.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="json",
        help="Output format ('json' or 'markdown').",
    )
    summary_p.add_argument("--no-fetch", action="store_true", help="Skip `git fetch --prune origin`.")
    summary_p.add_argument("--out", help="Optional output file path (defaults to stdout).")

    audit_p = subparsers.add_parser(
        "ingest-audit",
        help="Preview or create GitHub Issues from a codebase-auditor audit_report.json.",
    )
    audit_p.add_argument("repo_or_path", nargs="?", default=".", help="Owner/repo slug or local path.")
    audit_p.add_argument("--report", required=True, help="Path to codebase-auditor audit_report.json.")
    audit_p.add_argument(
        "--min-priority",
        type=str.upper,
        choices=[
            AuditPriority.HIGH.value,
            AuditPriority.MEDIUM.value,
            AuditPriority.LOW.value,
        ],
        default=AuditPriority.HIGH.value,
        help="Minimum finding severity to ingest (default: HIGH).",
    )
    audit_p.add_argument(
        "--confirm",
        action="store_true",
        help="Create GitHub Issues via `gh issue create` (defaults to safe --dry-run).",
    )
    audit_p.add_argument("--out", help="Optional output file path (defaults to stdout).")

    return parser


def _write_or_print(content: str, out_path: str | None) -> None:
    """Write output content to file or print to stdout."""
    if out_path:
        target = Path(out_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content + "\n", encoding="utf-8")
    else:
        print(content)


def main(argv: list[str] | None = None) -> int:
    """CLI main entrypoint."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "inspect":
            issue_numbers = parse_issue_list_arg(args.issues)
            bundle = inspect_batch(
                args.repo_or_path,
                issue_numbers=issue_numbers or None,
                label=args.label,
                limit=args.limit,
                fetch=not args.no_fetch,
            )
            payload = json.dumps(bundle.to_dict(), indent=2, ensure_ascii=False)
            _write_or_print(payload, args.out)
            return 0

        if args.command == "gate":
            fetch = not args.no_fetch
            if args.stage == GateStage.PRE_TASK.value:
                result = run_pre_task_gate(args.repo_or_path, fetch=fetch)
            else:
                if args.issue is None or args.pr is None:
                    raise GateError("--stage post-merge requires both --issue <id> and --pr <pr_id>.")
                result = run_post_merge_gate(
                    args.repo_or_path,
                    issue_number=args.issue,
                    pr_number=args.pr,
                    fetch=fetch,
                )
            payload = json.dumps(result.to_dict(), indent=2, ensure_ascii=False)
            _write_or_print(payload, args.out)
            return 0 if result.passed else 1

        if args.command == "batch-summary":
            issue_numbers = parse_issue_list_arg(args.issues)
            report = build_batch_summary(
                args.repo_or_path,
                issue_numbers=issue_numbers,
                fetch=not args.no_fetch,
            )
            if args.format == "markdown":
                output = report.markdown_summary
            else:
                output = json.dumps(report.to_dict(), indent=2, ensure_ascii=False)
            _write_or_print(output, args.out)
            return 0

        if args.command == "ingest-audit":
            audit_report = ingest_audit_report(
                args.repo_or_path,
                report_path=args.report,
                min_priority=args.min_priority,
                confirm=args.confirm,
            )
            payload = json.dumps(audit_report.to_dict(), indent=2, ensure_ascii=False)
            _write_or_print(payload, args.out)
            return 0

    except (InspectorError, GateError, AuditIngestError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
