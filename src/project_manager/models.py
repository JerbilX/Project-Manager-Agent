# Ref: docs/adr/0001-arquitectura-orquestador-lotes-mas-cli-determinista.md
"""Domain models for Project-Manager-Agent."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class CeremonyLevel(StrEnum):
    """Ceremony classification for an issue under issue-driven-workflow."""

    N1 = "N1"
    N2 = "N2"


class GateStage(StrEnum):
    """Deterministic orchestration gate stages."""

    PRE_TASK = "pre-task"
    POST_MERGE = "post-merge"


@dataclass(slots=True)
class GitWorkspaceStatus:
    """Local Git working tree and branch synchronization status."""

    branch: str
    is_clean: bool
    synced_with_origin_dev: bool
    head_sha: str = ""
    origin_dev_sha: str = ""
    dirty_files: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BatchIssue:
    """GitHub issue metadata and preliminary triage signals for batch execution."""

    number: int
    title: str
    body: str
    state: str
    url: str
    labels: list[str] = field(default_factory=list)
    suggested_ceremony: CeremonyLevel = CeremonyLevel.N1
    requires_adr_review: bool = False
    referenced_files: list[str] = field(default_factory=list)
    depends_on_issues: list[int] = field(default_factory=list)


@dataclass(slots=True)
class OpenPRInfo:
    """Summary of an open Pull Request in the target repository."""

    number: int
    title: str
    state: str
    base_ref: str
    head_ref: str
    url: str


@dataclass(slots=True)
class BatchInspectionBundle:
    """Full deterministic context collected during Phase 1 (Batch Triage)."""

    repo: str
    local_path: str | None
    git_status: GitWorkspaceStatus | None
    issues: list[BatchIssue] = field(default_factory=list)
    open_prs: list[OpenPRInfo] = field(default_factory=list)
    governance_docs: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize inspection bundle to a JSON-compatible dictionary."""
        return asdict(self)


@dataclass(slots=True)
class GateCheckResult:
    """Result of a deterministic pre-task or post-merge gate evaluation."""

    repo: str
    stage: GateStage
    passed: bool
    checks: dict[str, bool] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize gate check result to a JSON-compatible dictionary."""
        return asdict(self)


@dataclass(slots=True)
class BatchSummaryItem:
    """Final execution status for a single issue in a batch."""

    issue_number: int
    issue_title: str
    issue_state: str
    issue_url: str
    pr_number: int | None = None
    pr_state: str | None = None
    pr_url: str | None = None
    completed: bool = False


@dataclass(slots=True)
class BatchSummaryReport:
    """Consolidated end-of-batch report for Architect release evaluation."""

    repo: str
    local_path: str | None
    git_status: GitWorkspaceStatus | None
    all_completed: bool
    ready_for_release_eval: bool
    items: list[BatchSummaryItem] = field(default_factory=list)
    markdown_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize batch summary report to a JSON-compatible dictionary."""
        return asdict(self)
