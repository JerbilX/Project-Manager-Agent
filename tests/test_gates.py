# Ref: docs/adr/0001-arquitectura-orquestador-lotes-mas-cli-determinista.md
"""Unit tests for project_manager.gates."""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from project_manager.gates import (
    GateError,
    build_batch_summary,
    run_post_merge_gate,
    run_pre_task_gate,
)
from project_manager.models import GateStage, GitWorkspaceStatus


def _proc(stdout: str = "", returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["cmd"], returncode=returncode, stdout=stdout, stderr=stderr)


class TestGates(unittest.TestCase):
    """Tests for pre-task gate, post-merge gate, and batch summary builder."""

    @patch("project_manager.gates.collect_git_workspace_status")
    @patch("project_manager.gates.resolve_repo")
    def test_run_pre_task_gate_pass_and_fail(
        self,
        mock_resolve: unittest.mock.MagicMock,
        mock_git: unittest.mock.MagicMock,
    ) -> None:
        mock_resolve.return_value = ("JerbilX/Repo", Path("/tmp/repo"))
        mock_git.return_value = GitWorkspaceStatus(
            branch="dev",
            is_clean=True,
            synced_with_origin_dev=True,
            head_sha="1111111",
            origin_dev_sha="1111111",
        )
        res = run_pre_task_gate("/tmp/repo", fetch=False)
        self.assertEqual(res.stage, GateStage.PRE_TASK)
        self.assertTrue(res.passed)
        self.assertEqual(res.errors, [])

        mock_git.return_value = GitWorkspaceStatus(
            branch="feat/issue-1",
            is_clean=False,
            synced_with_origin_dev=False,
            head_sha="2222222",
            origin_dev_sha="1111111",
            dirty_files=["M src/main.py"],
        )
        failed = run_pre_task_gate("/tmp/repo", fetch=False)
        self.assertFalse(failed.passed)
        self.assertEqual(len(failed.errors), 3)

    @patch("project_manager.gates.resolve_repo")
    def test_run_pre_task_gate_requires_local_path(
        self,
        mock_resolve: unittest.mock.MagicMock,
    ) -> None:
        mock_resolve.return_value = ("JerbilX/Repo", None)
        with self.assertRaises(GateError):
            run_pre_task_gate("JerbilX/Repo", fetch=False)

    @patch("project_manager.gates.run_cmd")
    @patch("project_manager.gates.collect_git_workspace_status")
    @patch("project_manager.gates.resolve_repo")
    def test_run_post_merge_gate_pass_and_fail(
        self,
        mock_resolve: unittest.mock.MagicMock,
        mock_git: unittest.mock.MagicMock,
        mock_run: unittest.mock.MagicMock,
    ) -> None:
        mock_resolve.return_value = ("JerbilX/Repo", Path("/tmp/repo"))
        mock_git.return_value = GitWorkspaceStatus(
            branch="dev",
            is_clean=True,
            synced_with_origin_dev=True,
            head_sha="3333333",
            origin_dev_sha="3333333",
        )
        mock_run.side_effect = [
            _proc(json.dumps({"number": 4, "state": "CLOSED", "title": "Issue 4", "url": "u"})),
            _proc(
                json.dumps(
                    {
                        "number": 10,
                        "state": "MERGED",
                        "baseRefName": "dev",
                        "mergedAt": "2026-11-22T15:00:00Z",
                        "url": "u2",
                    }
                )
            ),
        ]
        res = run_post_merge_gate("/tmp/repo", issue_number=4, pr_number=10, fetch=False)
        self.assertTrue(res.passed)
        self.assertTrue(res.checks["issue_closed"])
        self.assertTrue(res.checks["pr_merged_to_dev"])

        mock_run.side_effect = [
            _proc(json.dumps({"number": 4, "state": "OPEN", "title": "Issue 4", "url": "u"})),
            _proc(
                json.dumps(
                    {
                        "number": 10,
                        "state": "OPEN",
                        "baseRefName": "dev",
                        "mergedAt": None,
                        "url": "u2",
                    }
                )
            ),
        ]
        failed = run_post_merge_gate("/tmp/repo", issue_number=4, pr_number=10, fetch=False)
        self.assertFalse(failed.passed)
        self.assertFalse(failed.checks["issue_closed"])
        self.assertFalse(failed.checks["pr_merged_to_dev"])

    @patch("project_manager.gates.run_cmd")
    @patch("project_manager.gates.collect_git_workspace_status")
    @patch("project_manager.gates.resolve_repo")
    def test_build_batch_summary(
        self,
        mock_resolve: unittest.mock.MagicMock,
        mock_git: unittest.mock.MagicMock,
        mock_run: unittest.mock.MagicMock,
    ) -> None:
        mock_resolve.return_value = ("JerbilX/Repo", Path("/tmp/repo"))
        mock_git.return_value = GitWorkspaceStatus(
            branch="dev",
            is_clean=True,
            synced_with_origin_dev=True,
            head_sha="4444444",
            origin_dev_sha="4444444",
        )
        prs_payload = [
            {
                "number": 11,
                "title": "feat(core): issue 4",
                "body": "Closes #4",
                "state": "MERGED",
                "baseRefName": "dev",
                "headRefName": "feat/issue-4-core",
                "url": "https://github.com/JerbilX/Repo/pull/11",
            },
            {
                "number": 12,
                "title": "fix(ui): issue 5",
                "body": "Closes #5",
                "state": "MERGED",
                "baseRefName": "dev",
                "headRefName": "fix/issue-5-ui",
                "url": "https://github.com/JerbilX/Repo/pull/12",
            },
            {
                "number": 20,
                "title": "release: v0.1.0",
                "body": "Includes Closes #4 and Closes #5",
                "state": "MERGED",
                "baseRefName": "main",
                "headRefName": "dev",
                "url": "https://github.com/JerbilX/Repo/pull/20",
            },
        ]
        mock_run.side_effect = [
            _proc(json.dumps(prs_payload)),
            _proc(json.dumps({"number": 4, "title": "Issue 4", "state": "CLOSED", "url": "u4"})),
            _proc(json.dumps({"number": 5, "title": "Issue 5", "state": "CLOSED", "url": "u5"})),
        ]
        report = build_batch_summary("/tmp/repo", issue_numbers=[4, 5], fetch=False)
        self.assertTrue(report.all_completed)
        self.assertTrue(report.ready_for_release_eval)
        self.assertEqual(len(report.items), 2)
        self.assertEqual(report.items[0].pr_number, 11)
        self.assertEqual(report.items[1].pr_number, 12)
        self.assertIn("## Batch Execution Summary", report.markdown_summary)

        with self.assertRaises(GateError):
            build_batch_summary("/tmp/repo", issue_numbers=[], fetch=False)


if __name__ == "__main__":
    unittest.main()
