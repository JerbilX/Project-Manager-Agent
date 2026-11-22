# Ref: docs/adr/0001-arquitectura-orquestador-lotes-mas-cli-determinista.md
"""Unit tests for project_manager.inspector."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project_manager.inspector import (
    InspectorError,
    classify_issue,
    collect_batch_issues,
    collect_git_workspace_status,
    collect_governance_docs,
    detect_file_collisions,
    inspect_batch,
    parse_github_repo_from_remote,
    parse_issue_list_arg,
    plan_batch_execution,
    resolve_repo,
    should_include_adr,
)
from project_manager.models import BatchIssue, CeremonyLevel


def _proc(stdout: str = "", returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["cmd"], returncode=returncode, stdout=stdout, stderr=stderr)


class TestInspectorHelpers(unittest.TestCase):
    """Tests for URL parsing, issue list parsing, classification, and ADR filtering."""

    def test_parse_github_repo_from_remote(self) -> None:
        self.assertEqual(
            parse_github_repo_from_remote("https://github.com/JerbilX/Project-Manager-Agent.git"),
            "JerbilX/Project-Manager-Agent",
        )
        self.assertEqual(
            parse_github_repo_from_remote("git@github.com:JerbilX/PR-Reviewer-Agent.git"),
            "JerbilX/PR-Reviewer-Agent",
        )
        self.assertIsNone(parse_github_repo_from_remote("https://gitlab.com/foo/bar.git"))

    def test_parse_issue_list_arg(self) -> None:
        self.assertEqual(parse_issue_list_arg("4, 5, #6, 4"), [4, 5, 6])
        self.assertEqual(parse_issue_list_arg(""), [])
        self.assertEqual(parse_issue_list_arg(None), [])
        with self.assertRaises(InspectorError):
            parse_issue_list_arg("4,abc")
        with self.assertRaises(InspectorError):
            parse_issue_list_arg("0")

    def test_classify_issue_n2_and_dependencies(self) -> None:
        title = "feat(core): agregar nuevo módulo de persistencia y ADR"
        body = (
            "Depende de #2 y requires #3.\n"
            "Modificar `src/project_manager/models.py` y `docs/adr/0002-persistencia.md`.\n"
            "### Objetivos Técnicos\n- Implementar motor\n"
            "### Criterios de Aceptación\n- [ ] Tests en verde"
        )
        ceremony, requires_adr, files, deps = classify_issue(4, title, body, ["enhancement"])
        self.assertEqual(ceremony, CeremonyLevel.N2)
        self.assertTrue(requires_adr)
        self.assertIn("src/project_manager/models.py", files)
        self.assertIn("docs/adr/0002-persistencia.md", files)
        self.assertEqual(deps, [2, 3])

    def test_classify_issue_n1_simple_fix(self) -> None:
        title = "fix(cli): corregir typo en mensaje de ayuda de src/project_manager/cli.py"
        body = "Ajuste menor de texto en CLI."
        ceremony, requires_adr, files, deps = classify_issue(7, title, body, ["n1", "bug"])
        self.assertEqual(ceremony, CeremonyLevel.N1)
        self.assertFalse(requires_adr)
        self.assertEqual(files, ["src/project_manager/cli.py"])
        self.assertEqual(deps, [])

    def test_should_include_adr(self) -> None:
        accepted = "---\nstatus: accepted\ndate: 2026-11-22\n---\n# ADR 1"
        rejected = "---\nstatus: rejected\ndate: 2026-11-22\n---\n# ADR 2"
        superseded = "---\nstatus: superseded by ADR-0003\n---\n# ADR 1"
        self.assertTrue(should_include_adr(accepted))
        self.assertFalse(should_include_adr(rejected))
        self.assertFalse(should_include_adr(superseded))
        self.assertTrue(should_include_adr("# Sin frontmatter"))


class TestInspectorCollection(unittest.TestCase):
    """Tests for workspace inspection, governance loading, and batch issue collection."""

    def test_collect_governance_docs_local(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "CONTRIBUTING.md").write_text("Contributing guide", encoding="utf-8")
            rules_dir = root / ".agents" / "rules"
            rules_dir.mkdir(parents=True)
            (rules_dir / "style.md").write_text("Rule 1", encoding="utf-8")
            adr_dir = root / "docs" / "adr"
            adr_dir.mkdir(parents=True)
            (adr_dir / "0001-ok.md").write_text("---\nstatus: accepted\n---\n# Ok", encoding="utf-8")
            (adr_dir / "0002-old.md").write_text("---\nstatus: rejected\n---\n# No", encoding="utf-8")

            docs = collect_governance_docs("JerbilX/Repo", root)
            self.assertIn("CONTRIBUTING.md", docs)
            self.assertIn(".agents/rules/style.md", docs)
            self.assertIn("docs/adr/0001-ok.md", docs)
            self.assertNotIn("docs/adr/0002-old.md", docs)

    @patch("project_manager.inspector.run_cmd")
    def test_collect_git_workspace_status_synced_and_dirty(self, mock_run: unittest.mock.MagicMock) -> None:
        mock_run.side_effect = [
            _proc(""),  # git fetch
            _proc("dev\n"),  # branch
            _proc(""),  # status --porcelain (clean)
            _proc("abc1234\n"),  # HEAD
            _proc("abc1234\n"),  # origin/dev
        ]
        status = collect_git_workspace_status(Path("/tmp/repo"), fetch=True)
        self.assertEqual(status.branch, "dev")
        self.assertTrue(status.is_clean)
        self.assertTrue(status.synced_with_origin_dev)

        mock_run.side_effect = [
            _proc("feat/issue-1\n"),
            _proc(" M src/foo.py\n"),
            _proc("def5678\n"),
            _proc("abc1234\n"),
        ]
        dirty_status = collect_git_workspace_status(Path("/tmp/repo"), fetch=False)
        self.assertEqual(dirty_status.branch, "feat/issue-1")
        self.assertFalse(dirty_status.is_clean)
        self.assertFalse(dirty_status.synced_with_origin_dev)
        self.assertEqual(dirty_status.dirty_files, ["M src/foo.py"])

    @patch("project_manager.inspector.run_cmd")
    def test_collect_batch_issues_by_ids_and_list(self, mock_run: unittest.mock.MagicMock) -> None:
        issue_4 = {
            "number": 4,
            "title": "fix(ui): ajustar margen",
            "body": "Cambio simple en `src/ui.py`",
            "state": "OPEN",
            "url": "https://github.com/JerbilX/Repo/issues/4",
            "labels": [{"name": "n1"}],
        }
        mock_run.return_value = _proc(json.dumps(issue_4))
        issues = collect_batch_issues("JerbilX/Repo", issue_numbers=[4])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].number, 4)
        self.assertEqual(issues[0].suggested_ceremony, CeremonyLevel.N1)

        mock_run.return_value = _proc(json.dumps([issue_4]))
        listed = collect_batch_issues("JerbilX/Repo", issue_numbers=None, label="n1")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].labels, ["n1"])

    @patch("project_manager.inspector.run_cmd")
    def test_resolve_repo_and_inspect_batch(self, mock_run: unittest.mock.MagicMock) -> None:
        slug, local_path = resolve_repo("JerbilX/Project-Manager-Agent")
        self.assertEqual(slug, "JerbilX/Project-Manager-Agent")
        self.assertIsNone(local_path)

        with self.assertRaises(InspectorError):
            resolve_repo("not_a_valid_slug")

        mock_run.side_effect = [
            _proc(
                json.dumps(
                    [
                        {
                            "number": 2,
                            "title": "feat(api): nuevo endpoint",
                            "body": "### Objetivos Técnicos\n- Endpoint",
                            "state": "OPEN",
                            "url": "https://github.com/JerbilX/Project-Manager-Agent/issues/2",
                            "labels": [],
                        }
                    ]
                )
            ),  # gh issue list
            _proc(
                json.dumps(
                    [
                        {
                            "number": 5,
                            "title": "feat(api): wip",
                            "state": "OPEN",
                            "baseRefName": "dev",
                            "headRefName": "feat/issue-2-api",
                            "url": "https://github.com/JerbilX/Project-Manager-Agent/pull/5",
                        }
                    ]
                )
            ),  # gh pr list
            _proc("", returncode=1),  # remote CONTRIBUTING.md
            _proc("", returncode=1),  # remote .agents/rules
            _proc("", returncode=1),  # remote docs/adr
        ]
        bundle = inspect_batch("JerbilX/Project-Manager-Agent", fetch=False)
        self.assertEqual(bundle.repo, "JerbilX/Project-Manager-Agent")
        self.assertEqual(len(bundle.issues), 1)
        self.assertEqual(bundle.issues[0].suggested_ceremony, CeremonyLevel.N2)
        self.assertEqual(len(bundle.open_prs), 1)
        self.assertEqual(bundle.open_prs[0].number, 5)
        self.assertEqual(bundle.recommended_order, [2])
        self.assertEqual(bundle.adr_review_issues, [])
        self.assertEqual(bundle.file_collisions, {})
        self.assertEqual(bundle.dependency_cycles, [])


class TestBatchPlanning(unittest.TestCase):
    """Tests for deterministic topological sorting, ADR priority, cycles, and file collisions."""

    @staticmethod
    def _issue(
        number: int,
        ceremony: CeremonyLevel = CeremonyLevel.N1,
        requires_adr: bool = False,
        files: list[str] | None = None,
        deps: list[int] | None = None,
    ) -> BatchIssue:
        return BatchIssue(
            number=number,
            title=f"Issue #{number}",
            body="",
            state="OPEN",
            url=f"https://github.com/JerbilX/Repo/issues/{number}",
            suggested_ceremony=ceremony,
            requires_adr_review=requires_adr,
            referenced_files=files or [],
            depends_on_issues=deps or [],
        )

    def test_topological_order_chained_dependencies_and_external_deps(self) -> None:
        issues = [
            self._issue(1, deps=[2]),
            self._issue(2, deps=[3, 999]),  # 999 is outside batch
            self._issue(3),
        ]
        plan = plan_batch_execution(issues)
        self.assertEqual(plan.recommended_order, [3, 2, 1])
        self.assertEqual(plan.dependency_cycles, [])

    def test_prioritizes_n2_adr_review_and_lowest_issue_tiebreak(self) -> None:
        issues = [
            self._issue(2, ceremony=CeremonyLevel.N1, requires_adr=False),
            self._issue(5, ceremony=CeremonyLevel.N2, requires_adr=False),
            self._issue(8, ceremony=CeremonyLevel.N2, requires_adr=True),
            self._issue(4, ceremony=CeremonyLevel.N2, requires_adr=True, deps=[2]),
            self._issue(1, ceremony=CeremonyLevel.N1, requires_adr=False),
        ]
        plan = plan_batch_execution(issues)
        # 8 is ready N2+ADR -> first; then ready N1s in order 1, 2; 2 unlocks 4 (N2+ADR) before 5
        self.assertEqual(plan.recommended_order, [8, 1, 2, 4, 5])
        self.assertEqual(plan.adr_review_issues, [8, 4])

    def test_detects_dependency_cycles(self) -> None:
        issues = [
            self._issue(10, deps=[11]),
            self._issue(11, deps=[12]),
            self._issue(12, deps=[10]),
            self._issue(15, deps=[12]),
            self._issue(1),
        ]
        plan = plan_batch_execution(issues)
        self.assertEqual(plan.dependency_cycles, [[10, 11, 12]])
        self.assertEqual(plan.recommended_order[0], 1)
        self.assertEqual(set(plan.recommended_order), {1, 10, 11, 12, 15})

    def test_detect_file_collisions(self) -> None:
        issues = [
            self._issue(4, files=["src/models.py", "src/inspector.py"]),
            self._issue(2, files=["src/models.py", "README.md"]),
            self._issue(7, files=["src/inspector.py", "src/cli.py"]),
        ]
        collisions = detect_file_collisions(issues)
        self.assertEqual(
            collisions,
            {
                "src/inspector.py": [4, 7],
                "src/models.py": [2, 4],
            },
        )
        plan = plan_batch_execution(issues)
        self.assertEqual(plan.file_collisions, collisions)


if __name__ == "__main__":
    unittest.main()
