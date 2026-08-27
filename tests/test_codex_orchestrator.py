from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jira_enhancer.codex_orchestrator import CodexOrchestratorBackend, make_codex_orchestrator_handler
from jira_enhancer.services import CodexExecSemanticReasoner


class CodexOrchestratorTest(unittest.TestCase):
    def test_backend_fetches_mock_confluence_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"}, clear=False):
            backend = CodexOrchestratorBackend(Path(tmpdir))
            page = backend.get_confluence_page("20026531262")
            self.assertEqual(page["id"], "20026531262")
            self.assertTrue(page["title"])

    def test_backend_analyzes_issue_with_heuristic_reasoner(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"}, clear=False):
            backend = CodexOrchestratorBackend(Path(tmpdir))
            result = backend.analyze_issue(
                {
                    "issue_key": "AES-100",
                    "summary": "AES CP: CRUD kafka subscriptions",
                    "description": "",
                    "acceptance_criteria": ["Create, update, list, and delete subscription definitions."],
                    "dependencies": ["Kafka subscription service"],
                    "edge_cases": ["Invalid topic mapping"],
                },
                [
                    {
                        "source_type": "confluence",
                        "source_ref": "30000000001",
                        "title": "DEMO Kafka Subscriptions",
                        "summary": "Kafka subscriptions API supports CRUD operations for subscription definitions.",
                    }
                ],
                "Use the confluence design.",
            )
            self.assertTrue(result["suggested_description"])
            self.assertTrue(result["reasoning_mode"])

    def test_backend_uses_codex_exec_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {
                "JIRA_ENHANCER_CONNECTOR_MODE": "mock",
                "JIRA_ENHANCER_USE_CODEX_EXEC": "true",
            },
            clear=False,
        ):
            backend = CodexOrchestratorBackend(Path(tmpdir))
            self.assertIsInstance(backend.reasoner, CodexExecSemanticReasoner)

    def test_handler_factory_returns_request_handler(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"}, clear=False):
            handler = make_codex_orchestrator_handler(str(Path(tmpdir)))
            self.assertTrue(hasattr(handler, "do_GET"))
            self.assertTrue(hasattr(handler, "do_POST"))


if __name__ == "__main__":
    unittest.main()
