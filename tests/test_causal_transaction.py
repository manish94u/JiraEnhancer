from __future__ import annotations

import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jira_enhancer.bootstrap import build_orchestrator


ISSUE_KEY = "DEMO-18324"


class SourceBoundCausalTransactionTest(unittest.TestCase):
    def _build_run(self, tmpdir: str):
        orchestrator = build_orchestrator(Path(tmpdir))
        created = orchestrator.create_run("issue", ISSUE_KEY, "inventor@example.org")
        return orchestrator, created["runId"]

    @staticmethod
    def _approve(orchestrator, run_id: str) -> dict:
        return orchestrator.record_approval(
            run_id,
            ISSUE_KEY,
            reviewer="reviewer@example.org",
            decision="approve",
            reviewer_notes="Approved against the displayed source and draft.",
        )

    def test_writeback_without_bound_approval_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"},
            clear=False,
        ):
            orchestrator, run_id = self._build_run(tmpdir)
            before = orchestrator.writeback_service.jira.get_issue(ISSUE_KEY)["description"]

            result = orchestrator.writeback(run_id, ISSUE_KEY)

            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["reason"], "approval_envelope_missing")
            self.assertEqual(orchestrator.writeback_service.jira.get_issue(ISSUE_KEY)["description"], before)

    def test_source_edit_after_approval_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"},
            clear=False,
        ):
            orchestrator, run_id = self._build_run(tmpdir)
            self._approve(orchestrator, run_id)
            jira = orchestrator.writeback_service.jira
            jira.issues[ISSUE_KEY]["summary"] += " changed after approval"
            jira.issues[ISSUE_KEY]["_revision"] += 1

            result = orchestrator.writeback(run_id, ISSUE_KEY)

            self.assertEqual(result["status"], "conflict")
            self.assertIn("approved_source_hash_mismatch", result["validation_errors"])
            self.assertNotIn("[AI_UPDATE_BEGIN]", jira.get_issue(ISSUE_KEY)["description"])

    def test_aba_revision_change_is_rejected_even_when_content_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"},
            clear=False,
        ):
            orchestrator, run_id = self._build_run(tmpdir)
            self._approve(orchestrator, run_id)
            jira = orchestrator.writeback_service.jira
            original_summary = jira.issues[ISSUE_KEY]["summary"]
            jira.issues[ISSUE_KEY]["summary"] = f"{original_summary} temporary edit"
            jira.issues[ISSUE_KEY]["_revision"] += 1
            jira.issues[ISSUE_KEY]["summary"] = original_summary
            jira.issues[ISSUE_KEY]["_revision"] += 1

            result = orchestrator.writeback(run_id, ISSUE_KEY)

            self.assertEqual(result["status"], "conflict")
            self.assertIn("approved_source_revision_mismatch", result["validation_errors"])

    def test_draft_regeneration_after_approval_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"},
            clear=False,
        ):
            orchestrator, run_id = self._build_run(tmpdir)
            self._approve(orchestrator, run_id)
            latest = orchestrator.store.get_latest_draft(run_id, ISSUE_KEY)
            changed = copy.deepcopy(latest)
            changed["draft_version"] = int(latest["draft_version"]) + 1
            changed["payload"]["summary"] += " unapproved revision"
            orchestrator.store.save_draft(run_id, ISSUE_KEY, changed["draft_version"], changed)

            result = orchestrator.writeback(run_id, ISSUE_KEY)

            self.assertEqual(result["status"], "blocked")
            self.assertIn("approved_draft_version_mismatch", result["validation_errors"])
            self.assertIn("approved_draft_hash_mismatch", result["validation_errors"])

    def test_policy_change_after_approval_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"},
            clear=False,
        ):
            orchestrator, run_id = self._build_run(tmpdir)
            self._approve(orchestrator, run_id)
            orchestrator.policy_service.policy_version = "jira-writeback-policy-v2"

            result = orchestrator.writeback(run_id, ISSUE_KEY)

            self.assertEqual(result["status"], "blocked")
            self.assertIn("approved_policy_version_mismatch", result["validation_errors"])

    def test_completed_writeback_retry_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"},
            clear=False,
        ):
            orchestrator, run_id = self._build_run(tmpdir)
            approval = self._approve(orchestrator, run_id)
            self.assertTrue(approval["causal_envelope"]["idempotency_key"])
            jira = orchestrator.writeback_service.jira
            original_write = jira.write_description
            calls = 0

            def counted_write(issue_key: str, description: str) -> dict:
                nonlocal calls
                calls += 1
                return original_write(issue_key, description)

            jira.write_description = counted_write
            first = orchestrator.writeback(run_id, ISSUE_KEY)
            second = orchestrator.writeback(run_id, ISSUE_KEY)

            self.assertEqual(first["status"], "written")
            self.assertEqual(second["status"], "written")
            self.assertTrue(second["replayed"])
            self.assertEqual(second["reason"], "idempotent_replay")
            self.assertEqual(calls, 1)

    def test_retry_recovers_when_remote_commit_response_is_lost(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"},
            clear=False,
        ):
            orchestrator, run_id = self._build_run(tmpdir)
            self._approve(orchestrator, run_id)
            jira = orchestrator.writeback_service.jira
            original_write = jira.write_description
            calls = 0

            def commit_then_timeout(issue_key: str, description: str) -> dict:
                nonlocal calls
                calls += 1
                result = original_write(issue_key, description)
                raise TimeoutError(f"Response lost after {result['jira_update_id']}")

            jira.write_description = commit_then_timeout
            first = orchestrator.writeback(run_id, ISSUE_KEY)
            second = orchestrator.writeback(run_id, ISSUE_KEY)

            self.assertEqual(first["status"], "indeterminate")
            self.assertEqual(second["status"], "written")
            self.assertEqual(second["reason"], "recovered_after_ambiguous_commit")
            self.assertTrue(second["replayed"])
            self.assertEqual(calls, 1)


if __name__ == "__main__":
    unittest.main()
