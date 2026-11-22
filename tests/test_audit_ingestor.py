# Ref: docs/adr/0001-arquitectura-orquestador-lotes-mas-cli-determinista.md
"""Unit tests for project_manager.audit_ingestor (Post-Audit Mode)."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project_manager.audit_ingestor import (
    AuditIngestError,
    build_finding_issue_payload,
    ingest_audit_report,
    load_and_validate_audit_report,
)
from project_manager.models import CeremonyLevel


def _sample_audit_report() -> dict[str, object]:
    """Return a sample `audit_report.json` matching Codebase-Auditor-Agent schema."""
    return {
        "repo_root": "/tmp/repo",
        "summary": "2 hallazgos prioritarios y 1 menor detectados.",
        "findings": [
            {
                "id": "AUD-001",
                "priority": "LOW",
                "category": "dead_code",
                "file": "src/project_manager/legacy.py",
                "start_line": 10,
                "end_line": 25,
                "symbol": "unused_helper",
                "evidence": "Función sin consumidores en el repositorio.",
                "impact": "Ruido de mantenimiento menor.",
                "proposed_issue": {
                    "title": "chore(legacy): eliminar helper muerto unused_helper",
                    "body": "Eliminar `src/project_manager/legacy.py` unused_helper.",
                    "labels": ["tech-debt"],
                },
            },
            {
                "id": "AUD-002",
                "priority": "HIGH",
                "category": "adr_violation",
                "file": "src/project_manager/inspector.py",
                "start_line": 120,
                "end_line": 230,
                "symbol": "inspect_batch",
                "evidence": "Desalineación con ADR-0001 en docs/adr/0001-arquitectura.md.",
                "impact": "Deriva arquitectónica de alto impacto.",
                "proposed_issue": {
                    "title": "refactor(inspector): alinear inspect_batch con ADR-0001",
                    "body": (
                        "### Contexto / Problema\n"
                        "Alinear `src/project_manager/inspector.py` con `docs/adr/0001-arquitectura.md`.\n\n"
                        "### Objetivos Técnicos\n"
                        "-Separar responsabilidad.\n\n"
                        "### Criterios de Aceptación\n"
                        "- Tests en verde."
                    ),
                    "labels": ["audit", "architecture"],
                },
            },
            {
                "id": "AUD-003",
                "priority": "MEDIUM",
                "category": "dry_violation",
                "file": "src/project_manager/inspector.py",
                "start_line": 40,
                "end_line": 85,
                "symbol": "resolve_repo",
                "evidence": "Duplicación en validación de rutas.",
                "impact": "Riesgo de divergencia en mantenimiento.",
                "proposed_issue": {},
            },
        ],
    }


class TestAuditIngestor(unittest.TestCase):
    """Tests for `audit_ingestor` filtering, dry-run preview, confirm creation, and errors."""

    def _write_report(self, tmp_dir: str, payload: object) -> Path:
        report_file = Path(tmp_dir) / "audit_report.json"
        report_file.write_text(json.dumps(payload), encoding="utf-8")
        return report_file

    def test_build_finding_issue_payload_fallback(self) -> None:
        finding = {
            "id": "AUD-099",
            "priority": "MEDIUM",
            "file": "src/mod.py",
            "start_line": 12,
            "end_line": 34,
            "symbol": "my_func",
            "evidence": "Duplicated block",
            "impact": "Maintenance cost",
        }
        title, body, labels = build_finding_issue_payload(finding)
        self.assertEqual(title, "refactor: atender hallazgo AUD-099 en my_func")
        self.assertIn("`src/mod.py` (L12-L34)", body)
        self.assertIn("`my_func`", body)
        self.assertEqual(labels, [])

    @patch("project_manager.audit_ingestor.run_cmd")
    def test_dry_run_filters_and_sorts_by_priority_without_calling_gh(
        self,
        mock_run: unittest.mock.MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report_path = self._write_report(tmp, _sample_audit_report())
            report = ingest_audit_report(
                "JerbilX/Project-Manager-Agent",
                report_path=report_path,
                min_priority="MEDIUM",
                confirm=False,
            )

        mock_run.assert_not_called()
        self.assertTrue(report.dry_run)
        self.assertEqual(report.total_findings, 3)
        self.assertEqual(report.selected_count, 2)
        self.assertEqual(report.skipped_count, 1)
        self.assertEqual([item.finding_id for item in report.items], ["AUD-002", "AUD-003"])
        self.assertEqual([item.priority for item in report.items], ["HIGH", "MEDIUM"])
        self.assertTrue(all(item.status == "dry-run" for item in report.items))
        self.assertTrue(all(item.issue_number is None for item in report.items))
        self.assertEqual(report.items[0].suggested_ceremony, CeremonyLevel.N2)
        self.assertTrue(report.items[0].requires_adr_review)
        self.assertIn("src/project_manager/inspector.py", report.items[0].referenced_files)
        self.assertEqual(report.items[1].suggested_ceremony, CeremonyLevel.N1)
        self.assertEqual(report.created_issue_numbers, [])
        self.assertEqual(report.recommended_order, [])

    @patch("project_manager.audit_ingestor.run_cmd")
    def test_confirm_creates_issues_and_computes_batch_plan(
        self,
        mock_run: unittest.mock.MagicMock,
    ) -> None:
        mock_run.side_effect = [
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="https://github.com/JerbilX/Project-Manager-Agent/issues/21\n",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="https://github.com/JerbilX/Project-Manager-Agent/issues/22\n",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="https://github.com/JerbilX/Project-Manager-Agent/issues/23\n",
                stderr="",
            ),
        ]

        with tempfile.TemporaryDirectory() as tmp:
            report_path = self._write_report(tmp, _sample_audit_report())
            report = ingest_audit_report(
                "JerbilX/Project-Manager-Agent",
                report_path=report_path,
                min_priority="LOW",
                confirm=True,
            )

        self.assertFalse(report.dry_run)
        self.assertEqual(mock_run.call_count, 3)
        # First call must be HIGH priority (AUD-002), then MEDIUM (AUD-003), then LOW (AUD-001)
        self.assertEqual(
            [item.finding_id for item in report.items],
            ["AUD-002", "AUD-003", "AUD-001"],
        )
        self.assertEqual(report.created_issue_numbers, [21, 22, 23])
        self.assertEqual(report.recommended_order, [21, 22, 23])
        self.assertEqual(report.adr_review_issues, [21])
        self.assertEqual(
            report.file_collisions,
            {"src/project_manager/inspector.py": [21, 22]},
        )

    def test_validation_and_error_handling(self) -> None:
        with self.assertRaises(AuditIngestError):
            load_and_validate_audit_report("/nonexistent/audit_report.json")

        with tempfile.TemporaryDirectory() as tmp:
            bad_json = Path(tmp) / "bad.json"
            bad_json.write_text("{not valid json", encoding="utf-8")
            with self.assertRaises(AuditIngestError):
                load_and_validate_audit_report(bad_json)

            no_findings = self._write_report(tmp, {"repo_root": "/tmp"})
            with self.assertRaises(AuditIngestError):
                load_and_validate_audit_report(no_findings)

            bad_priority = self._write_report(
                tmp,
                {"findings": [{"id": "AUD-1", "priority": "URGENT"}]},
            )
            with self.assertRaises(AuditIngestError):
                load_and_validate_audit_report(bad_priority)

            valid_report = self._write_report(tmp, _sample_audit_report())
            with self.assertRaises(AuditIngestError):
                ingest_audit_report(
                    "JerbilX/Project-Manager-Agent",
                    report_path=valid_report,
                    min_priority="INVALID",
                )

    @patch("project_manager.audit_ingestor.run_cmd")
    def test_confirm_gh_failure_raises_audit_ingest_error(
        self,
        mock_run: unittest.mock.MagicMock,
    ) -> None:
        from project_manager.inspector import InspectorError

        mock_run.side_effect = InspectorError("gh auth failed")
        with tempfile.TemporaryDirectory() as tmp:
            report_path = self._write_report(tmp, _sample_audit_report())
            with self.assertRaises(AuditIngestError):
                ingest_audit_report(
                    "JerbilX/Project-Manager-Agent",
                    report_path=report_path,
                    min_priority="HIGH",
                    confirm=True,
                )


if __name__ == "__main__":
    unittest.main()
