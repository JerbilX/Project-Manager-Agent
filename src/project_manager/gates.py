# Ref: docs/adr/0001-arquitectura-orquestador-lotes-mas-cli-determinista.md
"""Deterministic pre-task / post-merge gates and end-of-batch summary generator."""

from __future__ import annotations

import json
import re
from typing import Any

from project_manager.inspector import (
    InspectorError,
    collect_git_workspace_status,
    resolve_repo,
    run_cmd,
)
from project_manager.models import (
    BatchSummaryItem,
    BatchSummaryReport,
    GateCheckResult,
    GateStage,
    GitWorkspaceStatus,
)

_ISSUE_CLOSE_RE = re.compile(
    r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?|refs?)\s+#(\d+)",
    re.IGNORECASE,
)
_BRANCH_ISSUE_RE = re.compile(r"(?:^|[/-])issue-(\d+)(?:$|[/-])", re.IGNORECASE)


class GateError(RuntimeError):
    """Raised when a gate or batch summary command cannot be evaluated."""


def _evaluate_workspace_invariants(git_status: GitWorkspaceStatus) -> tuple[dict[str, bool], list[str]]:
    """Evaluate local `dev` branch, clean working tree, and `origin/dev` sync invariants."""
    checks = {
        "on_dev_branch": git_status.branch == "dev",
        "working_tree_clean": git_status.is_clean,
        "synced_with_origin_dev": git_status.synced_with_origin_dev,
    }
    errors: list[str] = []
    if not checks["on_dev_branch"]:
        errors.append(f"Expected current branch to be 'dev', found '{git_status.branch}'.")
    if not checks["working_tree_clean"]:
        dirty_preview = ", ".join(git_status.dirty_files[:5])
        errors.append(f"Working tree is not clean ({dirty_preview}).")
    if not checks["synced_with_origin_dev"]:
        errors.append(
            f"Local 'dev' ({git_status.head_sha[:7]}) is not synced with "
            f"'origin/dev' ({git_status.origin_dev_sha[:7]})."
        )
    return checks, errors


def run_pre_task_gate(repo_or_path: str, fetch: bool = True) -> GateCheckResult:
    """Validate that the local workspace is clean on `dev` and synced with `origin/dev`."""
    try:
        repo, local_path = resolve_repo(repo_or_path)
    except InspectorError as exc:
        raise GateError(str(exc)) from exc

    if local_path is None:
        raise GateError("pre-task gate requires a local repository path.")

    try:
        git_status = collect_git_workspace_status(local_path, fetch=fetch)
    except InspectorError as exc:
        raise GateError(str(exc)) from exc

    checks, errors = _evaluate_workspace_invariants(git_status)
    return GateCheckResult(
        repo=repo,
        stage=GateStage.PRE_TASK,
        passed=all(checks.values()),
        checks=checks,
        errors=errors,
        details={
            "branch": git_status.branch,
            "head_sha": git_status.head_sha,
            "origin_dev_sha": git_status.origin_dev_sha,
            "dirty_files": git_status.dirty_files,
        },
    )


def _fetch_issue_and_pr_state(repo: str, issue_number: int, pr_number: int) -> tuple[str, str, str]:
    """Fetch upper-cased issue state, PR state, and PR base branch from GitHub."""
    issue_res = run_cmd(
        ["gh", "issue", "view", str(issue_number), "--repo", repo, "--json", "number,state,title,url"]
    )
    pr_res = run_cmd(
        ["gh", "pr", "view", str(pr_number), "--repo", repo, "--json", "number,state,baseRefName,mergedAt,url"]
    )
    issue_data = json.loads(issue_res.stdout)
    pr_data = json.loads(pr_res.stdout)
    issue_state = (issue_data.get("state") or "").upper()
    pr_state = (pr_data.get("state") or "").upper()
    pr_base = pr_data.get("baseRefName") or ""
    return issue_state, pr_state, pr_base


def run_post_merge_gate(
    repo_or_path: str,
    issue_number: int,
    pr_number: int,
    fetch: bool = True,
) -> GateCheckResult:
    """Validate that PR was merged to `dev`, Issue is CLOSED, and local workspace is synced on `dev`."""
    try:
        repo, local_path = resolve_repo(repo_or_path)
    except InspectorError as exc:
        raise GateError(str(exc)) from exc

    if local_path is None:
        raise GateError("post-merge gate requires a local repository path.")

    try:
        git_status = collect_git_workspace_status(local_path, fetch=fetch)
        issue_state, pr_state, pr_base = _fetch_issue_and_pr_state(repo, issue_number, pr_number)
    except InspectorError as exc:
        raise GateError(str(exc)) from exc

    checks, errors = _evaluate_workspace_invariants(git_status)
    checks["issue_closed"] = issue_state == "CLOSED"
    checks["pr_merged_to_dev"] = pr_state == "MERGED" and pr_base == "dev"

    if not checks["issue_closed"]:
        errors.append(f"Issue #{issue_number} is not CLOSED (current state: '{issue_state}').")
    if not checks["pr_merged_to_dev"]:
        errors.append(
            f"PR #{pr_number} is not MERGED into 'dev' (state: '{pr_state}', base: '{pr_base}')."
        )

    return GateCheckResult(
        repo=repo,
        stage=GateStage.POST_MERGE,
        passed=all(checks.values()),
        checks=checks,
        errors=errors,
        details={
            "branch": git_status.branch,
            "head_sha": git_status.head_sha,
            "origin_dev_sha": git_status.origin_dev_sha,
            "issue_number": issue_number,
            "issue_state": issue_state,
            "pr_number": pr_number,
            "pr_state": pr_state,
            "pr_base": pr_base,
        },
    )


def _pr_matches_issue(pr_data: dict[str, Any], issue_number: int) -> bool:
    """Return True if a PR references the given issue number via branch name, title, or body."""
    head_ref = pr_data.get("headRefName") or ""
    for match in _BRANCH_ISSUE_RE.finditer(head_ref):
        if int(match.group(1)) == issue_number:
            return True

    for text in (pr_data.get("body") or "", pr_data.get("title") or ""):
        for match in _ISSUE_CLOSE_RE.finditer(text):
            if int(match.group(1)) == issue_number:
                return True
    return False


def _format_batch_markdown(
    repo: str,
    items: list[BatchSummaryItem],
    all_completed: bool,
    ready_for_release_eval: bool,
    git_status: GitWorkspaceStatus | None,
) -> str:
    """Format a Markdown executive report for the completed batch."""
    lines = [
        f"## Batch Execution Summary — `{repo}`",
        "",
        "| Issue | Título | Estado Issue | PR Asociado | Estado PR | Completado |",
        "| :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    for item in items:
        pr_cell = f"#{item.pr_number}" if item.pr_number is not None else "—"
        pr_state_cell = item.pr_state or "—"
        done_cell = "YES" if item.completed else "NO"
        lines.append(
            f"| #{item.issue_number} | {item.issue_title} | `{item.issue_state}` | "
            f"{pr_cell} | `{pr_state_cell}` | **{done_cell}** |"
        )

    lines.append("")
    if git_status is not None:
        lines.append(
            f"- **Workspace Git:** branch `{git_status.branch}`, "
            f"clean=`{git_status.is_clean}`, "
            f"synced_with_origin_dev=`{git_status.synced_with_origin_dev}`"
        )
    lines.append(f"- **Batch Completed:** `{all_completed}`")
    lines.append(f"- **Ready for Release Evaluation (`dev -> main`):** `{ready_for_release_eval}`")
    return "\n".join(lines)


def _collect_summary_item(
    repo: str,
    issue_number: int,
    raw_prs: list[dict[str, Any]],
) -> BatchSummaryItem:
    """Collect a single `BatchSummaryItem` matching an issue to its latest PR."""
    issue_res = run_cmd(
        ["gh", "issue", "view", str(issue_number), "--repo", repo, "--json", "number,title,state,url"]
    )
    issue_data = json.loads(issue_res.stdout)
    issue_state = (issue_data.get("state") or "").upper()

    matching_prs = [pr for pr in raw_prs if _pr_matches_issue(pr, issue_number)]
    matching_prs.sort(
        key=lambda pr: (
            1 if (pr.get("state") or "").upper() == "MERGED" else 0,
            int(pr.get("number", 0)),
        ),
        reverse=True,
    )
    matched_pr = matching_prs[0] if matching_prs else None
    pr_number = int(matched_pr["number"]) if matched_pr else None
    pr_state = (matched_pr.get("state") or "").upper() if matched_pr else None
    pr_url = matched_pr.get("url") if matched_pr else None

    return BatchSummaryItem(
        issue_number=int(issue_data["number"]),
        issue_title=issue_data.get("title", "") or "",
        issue_state=issue_state,
        issue_url=issue_data.get("url", "") or "",
        pr_number=pr_number,
        pr_state=pr_state,
        pr_url=pr_url,
        completed=bool(issue_state == "CLOSED" and pr_state == "MERGED"),
    )


def build_batch_summary(
    repo_or_path: str,
    issue_numbers: list[int],
    fetch: bool = True,
) -> BatchSummaryReport:
    """Build the final batch execution summary across all target issues and linked PRs."""
    if not issue_numbers:
        raise GateError("batch-summary requires at least one issue number (--issues).")

    try:
        repo, local_path = resolve_repo(repo_or_path)
        git_status = collect_git_workspace_status(local_path, fetch=fetch) if local_path else None
        prs_res = run_cmd(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                repo,
                "--state",
                "all",
                "--limit",
                "100",
                "--json",
                "number,title,body,state,baseRefName,headRefName,url",
            ],
            check=False,
        )
        raw_prs: list[dict[str, Any]] = (
            json.loads(prs_res.stdout)
            if prs_res.returncode == 0 and prs_res.stdout.strip()
            else []
        )
        items = [_collect_summary_item(repo, num, raw_prs) for num in issue_numbers]
    except InspectorError as exc:
        raise GateError(str(exc)) from exc

    all_completed = all(item.completed for item in items)
    git_ready = (
        bool(
            git_status.branch == "dev"
            and git_status.is_clean
            and git_status.synced_with_origin_dev
        )
        if git_status is not None
        else True
    )
    ready_for_release_eval = bool(all_completed and git_ready)
    markdown = _format_batch_markdown(
        repo=repo,
        items=items,
        all_completed=all_completed,
        ready_for_release_eval=ready_for_release_eval,
        git_status=git_status,
    )

    return BatchSummaryReport(
        repo=repo,
        local_path=str(local_path) if local_path else None,
        git_status=git_status,
        all_completed=all_completed,
        ready_for_release_eval=ready_for_release_eval,
        items=items,
        markdown_summary=markdown,
    )
