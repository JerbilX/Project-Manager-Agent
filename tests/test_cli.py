# Ref: docs/adr/0001-arquitectura-orquestador-lotes-mas-cli-determinista.md
"""Unit tests for project_manager.cli."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from project_manager.cli import main
from project_manager.models import (
    BatchInspectionBundle,
    BatchSummaryItem,
    BatchSummaryReport,
    GateCheckResult,
    GateStage,
    GitWorkspaceStatus,
)


class TestCLI(unittest.TestCase):
    """Tests for `pm-agent` CLI subcommands (`inspect`, `gate`, `batch-summary`)."""

    @patch("project_manager.cli.inspect_batch")
    def test_cli_inspect_to_file(self, mock_inspect: unittest.mock.MagicMock) -> None:
        mock_inspect.return_value = BatchInspectionBundle(
            repo="JerbilX/Project-Manager-Agent",
            local_path="/tmp/repo",
            git_status=GitWorkspaceStatus(branch="dev", is_clean=True, synced_with_origin_dev=True),
        )
        with tempfile.TemporaryDirectory() as tmp:
            out_file = Path(tmp) / "inspect.json"
            code = main(["inspect", "/tmp/repo", "--issues", "1,2", "--no-fetch", "--out", str(out_file)])
            self.assertEqual(code, 0)
            payload = json.loads(out_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["repo"], "JerbilX/Project-Manager-Agent")

    @patch("project_manager.cli.run_pre_task_gate")
    def test_cli_gate_pre_task_exit_codes(self, mock_gate: unittest.mock.MagicMock) -> None:
        mock_gate.return_value = GateCheckResult(
            repo="JerbilX/Repo",
            stage=GateStage.PRE_TASK,
            passed=True,
            checks={"on_dev_branch": True},
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["gate", "/tmp/repo", "--stage", "pre-task", "--no-fetch"])
        self.assertEqual(code, 0)

        mock_gate.return_value = GateCheckResult(
            repo="JerbilX/Repo",
            stage=GateStage.PRE_TASK,
            passed=False,
            checks={"on_dev_branch": False},
            errors=["Not on dev"],
        )
        buf_fail = io.StringIO()
        with redirect_stdout(buf_fail):
            code_fail = main(["gate", "/tmp/repo", "--stage", "pre-task", "--no-fetch"])
        self.assertEqual(code_fail, 1)

    @patch("project_manager.cli.run_post_merge_gate")
    def test_cli_gate_post_merge_validation(self, mock_post: unittest.mock.MagicMock) -> None:
        err_buf = io.StringIO()
        with redirect_stderr(err_buf):
            code_missing = main(["gate", "/tmp/repo", "--stage", "post-merge"])
        self.assertEqual(code_missing, 1)
        self.assertIn("requires both --issue", err_buf.getvalue())

        mock_post.return_value = GateCheckResult(
            repo="JerbilX/Repo",
            stage=GateStage.POST_MERGE,
            passed=True,
            checks={"issue_closed": True, "pr_merged_to_dev": True},
        )
        out_buf = io.StringIO()
        with redirect_stdout(out_buf):
            code_ok = main(
                ["gate", "/tmp/repo", "--stage", "post-merge", "--issue", "1", "--pr", "2", "--no-fetch"]
            )
        self.assertEqual(code_ok, 0)

    @patch("project_manager.cli.build_batch_summary")
    def test_cli_batch_summary_markdown(self, mock_summary: unittest.mock.MagicMock) -> None:
        mock_summary.return_value = BatchSummaryReport(
            repo="JerbilX/Repo",
            local_path="/tmp/repo",
            git_status=None,
            all_completed=True,
            ready_for_release_eval=True,
            items=[
                BatchSummaryItem(
                    issue_number=1,
                    issue_title="Init",
                    issue_state="CLOSED",
                    issue_url="u1",
                    pr_number=2,
                    pr_state="MERGED",
                    pr_url="u2",
                    completed=True,
                )
            ],
            markdown_summary="## Batch Execution Summary — `JerbilX/Repo`",
        )
        out_buf = io.StringIO()
        with redirect_stdout(out_buf):
            code = main(
                ["batch-summary", "/tmp/repo", "--issues", "1", "--format", "markdown", "--no-fetch"]
            )
        self.assertEqual(code, 0)
        self.assertIn("Batch Execution Summary", out_buf.getvalue())


if __name__ == "__main__":
    unittest.main()
