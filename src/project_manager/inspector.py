# Ref: docs/adr/0001-arquitectura-orquestador-lotes-mas-cli-determinista.md
"""Deterministic repository and backlog inspector using `git` and `gh` CLI."""

from __future__ import annotations

import base64
import heapq
import json
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from project_manager.models import (
    DEFAULT_RECOMMENDED_MODELS,
    BatchExecutionPlan,
    BatchInspectionBundle,
    BatchIssue,
    CeremonyLevel,
    GitWorkspaceStatus,
    ModelProfile,
    OpenPRInfo,
)

_REMOTE_RE = re.compile(r"(?:github\.com[:/])([^/]+/[^/]+?)(?:\.git)?/?$")
_REPO_SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_FILE_PATH_RE = re.compile(
    r"(?:`|\b)((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.[A-Za-z0-9]+|"
    r"README\.md|CONTRIBUTING\.md|pyproject\.toml|Makefile|Dockerfile)(?:`|\b)"
)
_DEPENDS_RE = re.compile(
    r"(?:depends?\s+on|depende\s+de|after|requires|requiere|blocked\s+by|bloquead[oa]\s+por)"
    r"\s+#(\d+)",
    re.IGNORECASE,
)
_ADR_KEYWORDS_RE = re.compile(
    r"\b(?:adr|docs/adr|arquitectura|architecture|persistencia|persistence|schema|esquema|"
    r"migraci[oó]n|migration|breaking\s+change|nuevo\s+m[oó]dulo|new\s+module)\b",
    re.IGNORECASE,
)
_NEGATED_ADR_ES_RE = re.compile(
    r"\b(?:sin|no\s+(?:requiere|necesita|implica|hay|afecta|toca))\s+"
    r"(?:cambios?\s+(?:de|en)\s+|necesidad\s+de\s+|requerir\s+|nuevo\s+|una?\s+|la\s+|el\s+){0,3}"
    r"(?:adr(?:s)?|docs/adr(?:/[^\s`)]*)?|arquitectura|persistencia|esquema|"
    r"migraci[oó]n(?:es)?|breaking\s+change|nuevo\s+m[oó]dulo)"
    r"(?:\s*(?:,|y|o|ni)\s+(?:de\s+|en\s+)?(?:adr(?:s)?|docs/adr(?:/[^\s`)]*)?|arquitectura|"
    r"persistencia|esquema|migraci[oó]n(?:es)?|breaking\s+change|nuevo\s+m[oó]dulo))*\b",
    re.IGNORECASE,
)
_NEGATED_ADR_EN_RE = re.compile(
    r"\b(?:without|no|does\s+not\s+require|doesn't\s+require|not\s+requiring)\s+"
    r"(?:any\s+|an?\s+|the\s+|new\s+)?(?:(?:schema|persistence|architecture|architectural|"
    r"adr|breaking|migration)\s+){0,2}"
    r"(?:changes?|updates?|modifications?|needed|required|adr|docs/adr(?:/[^\s`)]*)?|"
    r"schema|persistence|architecture|migration|new\s+module)"
    r"(?:\s*(?:,|and|or)\s+(?:schema|persistence|architecture|adr|migration|changes?))*\b",
    re.IGNORECASE,
)
_PRESERVED_ADR_RE = re.compile(
    r"\b(?:preserving|preserve|respecting|respect|following|follow|honoring|according\s+to|"
    r"compatible\s+with|respetando|preservando|manteniendo|siguiendo|conforme\s+a|seg[uú]n)\s+"
    r"(?:(?:el|la|los|las|the|existing|current|vigente(?:s)?)\s+){0,2}"
    r"(?:adr(?:-\d+|\s*#?\d+)?|docs/adr(?:/[^\s`)]*)?)\b",
    re.IGNORECASE,
)
_ANALYSIS_TITLE_RE = re.compile(
    r"\b(?:analyze|analizar|analysis|an[aá]lisis)\b",
    re.IGNORECASE,
)
_N2_SECTION_RE = re.compile(r"###\s+Objetivos\s+T[eé]cnicos", re.IGNORECASE)
_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
_CEREMONY_LINE_RE = re.compile(
    r"^\s*(?:[-*+>]\s+)?(?:\*\*|__|\*|_)?ceremon(?:y|ia)(?:\*\*|__|\*|_)?\s*:"
    r"\s*(?:\*\*|__|\*|_)?\s*[`*_]*(n1|n2)\b[`*_]*(?:[\s(].*)?$",
    re.IGNORECASE,
)
_LEGACY_CEREMONY_HEADING_RE = re.compile(
    r"^\s*#{1,6}\s+nivel\s+estimado\b(.*)$",
    re.IGNORECASE,
)
_LEGACY_CEREMONY_VALUE_RE = re.compile(
    r"^\s*(?:[-*+>]\s+)?[`*_]*(n1|n2)\b[`*_]*(?:[\s(–—:-].*)?$",
    re.IGNORECASE,
)
_MODEL_LINE_RE = re.compile(
    r"^\s*(?:[-*+>]\s+)?(?:\*\*|__|\*|_)?model(?:o)?(?:\*\*|__|\*|_)?\s*:"
    r"\s*(?:\*\*|__|\*|_)?\s*[`*_]*(fast|balanced|deep)\b[`*_]*(.*)$",
    re.IGNORECASE,
)


class InspectorError(RuntimeError):
    """Raised when `git` or `gh` fails to inspect the repository or backlog."""


def run_cmd(
    cmd: list[str],
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Execute a subprocess command and return the completed process."""
    result = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        raise InspectorError(f"Command failed ({' '.join(cmd)}): {stderr}")
    return result


def parse_github_repo_from_remote(remote_url: str) -> str | None:
    """Extract `owner/repo` from a GitHub HTTPS or SSH remote URL."""
    match = _REMOTE_RE.search(remote_url.strip())
    return match.group(1) if match else None


def resolve_repo(repo_or_path: str) -> tuple[str, Path | None]:
    """Resolve an `owner/repo` slug and optional local repository path."""
    candidate = Path(repo_or_path).expanduser()
    if candidate.exists() and candidate.is_dir():
        local_path = candidate.resolve()
        remote_res = run_cmd(["git", "remote", "get-url", "origin"], cwd=local_path, check=False)
        if remote_res.returncode == 0:
            parsed = parse_github_repo_from_remote(remote_res.stdout)
            if parsed:
                return parsed, local_path
        gh_res = run_cmd(
            ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
            cwd=local_path,
            check=False,
        )
        if gh_res.returncode == 0 and gh_res.stdout.strip():
            return gh_res.stdout.strip(), local_path
        raise InspectorError(f"Could not resolve GitHub repository from local path: {local_path}")

    slug = repo_or_path.strip()
    if not _REPO_SLUG_RE.match(slug):
        raise InspectorError(
            f"Invalid repository identifier (expected owner/repo or local path): {repo_or_path}"
        )
    return slug, None


def parse_issue_list_arg(raw_issues: str | None) -> list[int]:
    """Parse a comma-separated string of issue numbers (e.g. '4,5,6') into unique ints."""
    if not raw_issues or not raw_issues.strip():
        return []
    result: list[int] = []
    seen: set[int] = set()
    for token in raw_issues.split(","):
        cleaned = token.strip().lstrip("#")
        if not cleaned:
            continue
        if not cleaned.isdigit() or int(cleaned) <= 0:
            raise InspectorError(f"Invalid issue number in list: '{token.strip()}'")
        num = int(cleaned)
        if num not in seen:
            seen.add(num)
            result.append(num)
    return result


def collect_git_workspace_status(local_path: Path, fetch: bool = True) -> GitWorkspaceStatus:
    """Inspect local Git branch, working tree cleanliness, and sync with `origin/dev`."""
    if fetch:
        run_cmd(["git", "fetch", "--prune", "origin"], cwd=local_path, check=False)

    branch_res = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=local_path)
    branch = branch_res.stdout.strip()

    status_res = run_cmd(["git", "status", "--porcelain"], cwd=local_path)
    dirty_lines = [line.strip() for line in status_res.stdout.splitlines() if line.strip()]
    is_clean = len(dirty_lines) == 0

    head_res = run_cmd(["git", "rev-parse", "HEAD"], cwd=local_path, check=False)
    head_sha = head_res.stdout.strip() if head_res.returncode == 0 else ""

    origin_dev_res = run_cmd(["git", "rev-parse", "origin/dev"], cwd=local_path, check=False)
    origin_dev_sha = origin_dev_res.stdout.strip() if origin_dev_res.returncode == 0 else ""

    synced = bool(branch == "dev" and head_sha and origin_dev_sha and head_sha == origin_dev_sha)

    return GitWorkspaceStatus(
        branch=branch,
        is_clean=is_clean,
        synced_with_origin_dev=synced,
        head_sha=head_sha,
        origin_dev_sha=origin_dev_sha,
        dirty_files=dirty_lines,
    )


def _iter_non_fenced_lines(text: str) -> Iterator[str]:
    """Yield lines from markdown text while skipping fenced code blocks."""
    in_fence = False
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            yield line


def parse_ceremony_header(body: str) -> CeremonyLevel | None:
    """Extract explicit `**Ceremony:** <N1|N2>` or legacy `## Nivel Estimado` metadata."""
    legacy_ceremony: CeremonyLevel | None = None
    expect_legacy_value = False
    for line in _iter_non_fenced_lines(body or ""):
        match = _CEREMONY_LINE_RE.match(line)
        if match:
            return CeremonyLevel(match.group(1).upper())

        if expect_legacy_value:
            if not line.strip():
                continue
            val_match = _LEGACY_CEREMONY_VALUE_RE.match(line)
            if val_match and legacy_ceremony is None:
                legacy_ceremony = CeremonyLevel(val_match.group(1).upper())
            expect_legacy_value = False

        heading_match = _LEGACY_CEREMONY_HEADING_RE.match(line)
        if heading_match and legacy_ceremony is None:
            tail = heading_match.group(1).strip().lstrip(":").strip()
            if tail:
                val_match = _LEGACY_CEREMONY_VALUE_RE.match(tail)
                if val_match:
                    legacy_ceremony = CeremonyLevel(val_match.group(1).upper())
            else:
                expect_legacy_value = True

    return legacy_ceremony


def parse_model_header(body: str) -> tuple[ModelProfile, str] | None:
    """Extract `(model_profile, recommended_model)` from `**Model:** ...` in an issue body."""
    for line in _iter_non_fenced_lines(body or ""):
        match = _MODEL_LINE_RE.match(line)
        if not match:
            continue
        profile = ModelProfile(match.group(1).lower())
        tail = match.group(2).strip()
        if "(" in tail and ")" in tail:
            start = tail.find("(")
            end = tail.rfind(")")
            raw_hint = tail[start + 1 : end] if end > start else ""
        else:
            raw_hint = tail.lstrip("-–—: ").strip()
        hint = raw_hint.strip().strip("`*_").strip()
        recommended = hint or DEFAULT_RECOMMENDED_MODELS[profile]
        return profile, recommended
    return None


def _strip_negated_and_preserved_adr_mentions(text: str) -> str:
    """Remove negated ADR/schema/persistence phrases and ADR preservation references."""
    cleaned = _NEGATED_ADR_ES_RE.sub(" ", text)
    cleaned = _NEGATED_ADR_EN_RE.sub(" ", cleaned)
    cleaned = _PRESERVED_ADR_RE.sub(" ", cleaned)
    return cleaned


def resolve_issue_model(
    body: str,
    ceremony: CeremonyLevel,
    requires_adr_review: bool,
) -> tuple[ModelProfile, str]:
    """Resolve `model_profile` and `recommended_model` from `**Model:**` or Hydrogen-First fallback."""
    explicit = parse_model_header(body)
    if explicit is not None:
        return explicit

    del ceremony  # Ceremony != Model under Hydrogen-First routing
    profile = ModelProfile.BALANCED if requires_adr_review else ModelProfile.FAST
    return profile, DEFAULT_RECOMMENDED_MODELS[profile]


def classify_issue(
    number: int,
    title: str,
    body: str,
    labels: list[str],
) -> tuple[CeremonyLevel, bool, list[str], list[int]]:
    """Derive preliminary ceremony level (N1/N2), ADR requirement, files, and dependencies."""
    combined = f"{title}\n{body}"
    lower_labels = {lbl.lower() for lbl in labels}
    explicit_ceremony = parse_ceremony_header(body or "")

    referenced_files: list[str] = []
    seen_files: set[str] = set()
    for match in _FILE_PATH_RE.finditer(combined):
        path_str = match.group(1).strip("`.,:;()[]")
        if path_str and path_str not in seen_files:
            seen_files.add(path_str)
            referenced_files.append(path_str)

    depends_on: list[int] = []
    seen_deps: set[int] = set()
    for match in _DEPENDS_RE.finditer(combined):
        dep_num = int(match.group(1))
        if dep_num != number and dep_num not in seen_deps:
            seen_deps.add(dep_num)
            depends_on.append(dep_num)

    adr_text = _strip_negated_and_preserved_adr_mentions(combined)
    requires_adr = bool(
        _ADR_KEYWORDS_RE.search(adr_text)
        or any(lbl in {"adr", "architecture", "arquitectura", "n2"} for lbl in lower_labels)
    )

    is_feat_title = title.lower().startswith("feat") and not _ANALYSIS_TITLE_RE.search(title)
    is_n2 = bool(
        explicit_ceremony == CeremonyLevel.N2
        or requires_adr
        or "n2" in lower_labels
        or _N2_SECTION_RE.search(body or "")
        or is_feat_title
    )
    if (explicit_ceremony == CeremonyLevel.N1 or "n1" in lower_labels) and not requires_adr:
        is_n2 = False

    ceremony = CeremonyLevel.N2 if is_n2 else CeremonyLevel.N1
    return ceremony, requires_adr, referenced_files, depends_on


def _build_batch_issue(data: dict[str, Any]) -> BatchIssue:
    """Construct a `BatchIssue` from `gh issue` JSON data."""
    num = int(data["number"])
    title = data.get("title", "") or ""
    body = data.get("body", "") or ""
    state = data.get("state", "") or ""
    url = data.get("url", "") or ""
    raw_labels = data.get("labels") or []
    labels = [
        lbl.get("name", "") if isinstance(lbl, dict) else str(lbl)
        for lbl in raw_labels
        if (lbl.get("name") if isinstance(lbl, dict) else lbl)
    ]
    ceremony, requires_adr, files, depends_on = classify_issue(num, title, body, labels)
    model_profile, recommended_model = resolve_issue_model(body, ceremony, requires_adr)
    return BatchIssue(
        number=num,
        title=title,
        body=body,
        state=state,
        url=url,
        labels=labels,
        suggested_ceremony=ceremony,
        requires_adr_review=requires_adr,
        model_profile=model_profile,
        recommended_model=recommended_model,
        referenced_files=files,
        depends_on_issues=depends_on,
    )


def collect_batch_issues(
    repo: str,
    issue_numbers: list[int] | None = None,
    label: str | None = None,
    limit: int = 30,
) -> list[BatchIssue]:
    """Collect target batch issues either by explicit IDs or open backlog listing."""
    if issue_numbers:
        issues: list[BatchIssue] = []
        for num in issue_numbers:
            res = run_cmd(
                [
                    "gh",
                    "issue",
                    "view",
                    str(num),
                    "--repo",
                    repo,
                    "--json",
                    "number,title,body,state,url,labels",
                ]
            )
            data = json.loads(res.stdout)
            issues.append(_build_batch_issue(data))
        return issues

    cmd = [
        "gh",
        "issue",
        "list",
        "--repo",
        repo,
        "--state",
        "open",
        "--limit",
        str(limit),
        "--json",
        "number,title,body,state,url,labels",
    ]
    if label:
        cmd.extend(["--label", label])

    res = run_cmd(cmd)
    raw_list = json.loads(res.stdout) if res.stdout.strip() else []
    issues = [_build_batch_issue(item) for item in raw_list]
    issues.sort(key=lambda item: item.number)
    return issues


def collect_open_prs(repo: str) -> list[OpenPRInfo]:
    """Collect currently open Pull Requests in the repository."""
    res = run_cmd(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--json",
            "number,title,state,baseRefName,headRefName,url",
        ],
        check=False,
    )
    if res.returncode != 0 or not res.stdout.strip():
        return []
    try:
        raw_prs = json.loads(res.stdout)
    except json.JSONDecodeError:
        return []
    return [
        OpenPRInfo(
            number=int(item["number"]),
            title=item.get("title", "") or "",
            state=item.get("state", "") or "",
            base_ref=item.get("baseRefName", "") or "",
            head_ref=item.get("headRefName", "") or "",
            url=item.get("url", "") or "",
        )
        for item in raw_prs
    ]


def should_include_adr(content: str) -> bool:
    """Include ADRs unless explicitly marked rejected or superseded in frontmatter."""
    if not content.startswith("---"):
        return True
    parts = content.split("---", 2)
    if len(parts) < 3:
        return True
    frontmatter = parts[1].lower()
    for line in frontmatter.splitlines():
        stripped = line.strip()
        if stripped.startswith("status:"):
            status_val = stripped.split(":", 1)[1].strip()
            if status_val.startswith("rejected") or status_val.startswith("superseded"):
                return False
    return True


def _read_remote_file_or_dir(repo: str, path: str, ref: str) -> dict[str, str]:
    """Fetch a file or markdown files in a directory from GitHub Contents API."""
    endpoint = f"repos/{repo}/contents/{path}?ref={ref}"
    res = run_cmd(["gh", "api", endpoint], check=False)
    if res.returncode != 0:
        return {}

    try:
        payload: Any = json.loads(res.stdout)
    except json.JSONDecodeError:
        return {}

    docs: dict[str, str] = {}
    if isinstance(payload, dict) and payload.get("type") == "file" and payload.get("content"):
        decoded = base64.b64decode(payload["content"]).decode("utf-8", errors="replace")
        docs[path] = decoded
    elif isinstance(payload, list):
        for entry in sorted(payload, key=lambda item: item.get("name", "")):
            name = entry.get("name", "")
            entry_path = entry.get("path", "")
            if entry.get("type") == "file" and name.endswith(".md") and entry_path:
                docs.update(_read_remote_file_or_dir(repo, entry_path, ref))
    return docs


def collect_governance_docs(
    repo: str,
    local_path: Path | None,
    ref: str = "dev",
) -> dict[str, str]:
    """Collect `CONTRIBUTING.md`, `.agents/rules/`, and active `docs/adr/` documents."""
    docs: dict[str, str] = {}

    if local_path is not None:
        contributing = local_path / "CONTRIBUTING.md"
        if contributing.is_file():
            docs["CONTRIBUTING.md"] = contributing.read_text(encoding="utf-8", errors="replace")

        for rel_dir in (".agents/rules", "docs/adr"):
            target_dir = local_path / rel_dir
            if target_dir.is_dir():
                for md_file in sorted(target_dir.glob("*.md")):
                    content = md_file.read_text(encoding="utf-8", errors="replace")
                    if rel_dir == "docs/adr" and not should_include_adr(content):
                        continue
                    docs[f"{rel_dir}/{md_file.name}"] = content
        return docs

    for target in ("CONTRIBUTING.md", ".agents/rules", "docs/adr"):
        fetched = _read_remote_file_or_dir(repo, target, ref)
        for doc_path, content in fetched.items():
            if doc_path.startswith("docs/adr/") and not should_include_adr(content):
                continue
            docs[doc_path] = content

    return docs


def _issue_priority_key(issue: BatchIssue) -> tuple[int, int]:
    """Return deterministic priority key: ADR-required N2 issues first, then lowest issue number."""
    adr_rank = (
        0
        if (issue.suggested_ceremony == CeremonyLevel.N2 and issue.requires_adr_review)
        else 1
    )
    return (adr_rank, issue.number)


def detect_file_collisions(issues: list[BatchIssue]) -> dict[str, list[int]]:
    """Return paths in `referenced_files` touched by 2 or more issues in the batch."""
    file_to_issues: dict[str, set[int]] = {}
    for issue in issues:
        for path in issue.referenced_files:
            file_to_issues.setdefault(path, set()).add(issue.number)
    return {
        path: sorted(issue_nums)
        for path, issue_nums in sorted(file_to_issues.items())
        if len(issue_nums) >= 2
    }


def _find_dependency_cycles(deps_graph: dict[int, list[int]]) -> list[list[int]]:
    """Detect strongly connected dependency cycles using Tarjan's algorithm."""
    index = 0
    indices: dict[int, int] = {}
    lowlink: dict[int, int] = {}
    on_stack: set[int] = set()
    stack: list[int] = []
    cycles: list[list[int]] = []

    def strongconnect(node: int) -> None:
        nonlocal index
        indices[node] = index
        lowlink[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)

        for dep in deps_graph.get(node, []):
            if dep not in indices:
                strongconnect(dep)
                lowlink[node] = min(lowlink[node], lowlink[dep])
            elif dep in on_stack:
                lowlink[node] = min(lowlink[node], indices[dep])

        if lowlink[node] == indices[node]:
            scc: list[int] = []
            while True:
                member = stack.pop()
                on_stack.remove(member)
                scc.append(member)
                if member == node:
                    break
            if len(scc) > 1 or (len(scc) == 1 and scc[0] in deps_graph.get(scc[0], [])):
                cycles.append(sorted(scc))

    for node in sorted(deps_graph):
        if node not in indices:
            strongconnect(node)

    cycles.sort(key=lambda cycle: cycle[0])
    return cycles


def plan_batch_execution(issues: list[BatchIssue]) -> BatchExecutionPlan:
    """Compute deterministic topological order, ADR review list, file collisions, and cycles."""
    if not issues:
        return BatchExecutionPlan()

    issues_by_num = {issue.number: issue for issue in issues}
    deps_graph: dict[int, list[int]] = {}
    in_degree: dict[int, int] = {}
    dependents: dict[int, list[int]] = {}

    for issue in issues:
        valid_deps = sorted({dep for dep in issue.depends_on_issues if dep in issues_by_num})
        deps_graph[issue.number] = valid_deps
        in_degree[issue.number] = len(valid_deps)
        for dep in valid_deps:
            dependents.setdefault(dep, []).append(issue.number)

    dependency_cycles = _find_dependency_cycles(deps_graph)

    heap: list[tuple[int, int]] = []
    for num in sorted(issues_by_num):
        if in_degree[num] == 0:
            heapq.heappush(heap, _issue_priority_key(issues_by_num[num]))

    recommended_order: list[int] = []
    visited: set[int] = set()

    while len(recommended_order) < len(issues_by_num):
        if not heap:
            remaining = [num for num in issues_by_num if num not in visited]
            next_num = min(
                remaining,
                key=lambda num: (in_degree[num], *_issue_priority_key(issues_by_num[num])),
            )
            heapq.heappush(heap, _issue_priority_key(issues_by_num[next_num]))

        _, current = heapq.heappop(heap)
        if current in visited:
            continue
        visited.add(current)
        recommended_order.append(current)

        for dep_num in dependents.get(current, []):
            if dep_num in visited:
                continue
            in_degree[dep_num] -= 1
            if in_degree[dep_num] <= 0:
                heapq.heappush(heap, _issue_priority_key(issues_by_num[dep_num]))

    adr_review_issues = [
        num for num in recommended_order if issues_by_num[num].requires_adr_review
    ]
    file_collisions = detect_file_collisions(issues)

    return BatchExecutionPlan(
        recommended_order=recommended_order,
        adr_review_issues=adr_review_issues,
        file_collisions=file_collisions,
        dependency_cycles=dependency_cycles,
    )


def inspect_batch(
    repo_or_path: str,
    issue_numbers: list[int] | None = None,
    label: str | None = None,
    limit: int = 30,
    fetch: bool = True,
) -> BatchInspectionBundle:
    """Collect full deterministic context for Phase 1 batch triage and planning."""
    repo, local_path = resolve_repo(repo_or_path)
    git_status = collect_git_workspace_status(local_path, fetch=fetch) if local_path else None
    issues = collect_batch_issues(repo, issue_numbers=issue_numbers, label=label, limit=limit)
    open_prs = collect_open_prs(repo)
    governance_docs = collect_governance_docs(repo, local_path)
    plan = plan_batch_execution(issues)

    return BatchInspectionBundle(
        repo=repo,
        local_path=str(local_path) if local_path else None,
        git_status=git_status,
        issues=issues,
        open_prs=open_prs,
        governance_docs=governance_docs,
        recommended_order=plan.recommended_order,
        adr_review_issues=plan.adr_review_issues,
        file_collisions=plan.file_collisions,
        dependency_cycles=plan.dependency_cycles,
    )
