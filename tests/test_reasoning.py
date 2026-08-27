from __future__ import annotations

import json
import unittest

from jira_enhancer.models import EvidenceRecord
import os
import tempfile
from pathlib import Path

from jira_enhancer.bootstrap import build_orchestrator
from jira_enhancer.services import (
    CodexBrokerSemanticReasoner,
    CodexExecSemanticReasoner,
    ModelSemanticReasoner,
    _normalize_planned_epic_story_candidates,
    _story_plan_response_schema,
)


class ModelReasoningTest(unittest.TestCase):
    def setUp(self) -> None:
        self.issue = {
            "issue_key": "AES-100",
            "summary": "AES CP: CRUD kafka subscriptions",
            "description": "",
            "acceptance_criteria": ["Create, update, list, and delete subscription definitions."],
            "dependencies": ["Kafka subscription service"],
            "edge_cases": ["Invalid topic mapping"],
        }
        self.evidence = [
            EvidenceRecord(
                run_id="run-1",
                issue_key="AES-100",
                source_type="confluence",
                source_ref="30000000001",
                relevance_score=0.95,
                selected_for_draft=True,
                content={
                    "id": "30000000001",
                    "title": "DEMO Kafka Subscriptions",
                    "summary": "Kafka subscriptions API supports CRUD operations for subscription definitions.",
                },
            )
        ]

    def test_epic_story_plan_schema_requires_every_story_property(self) -> None:
        story_schema = _story_plan_response_schema()["properties"]["stories"]["items"]
        self.assertEqual(set(story_schema["required"]), set(story_schema["properties"]))

    def test_epic_story_plan_skips_unavailable_placeholder_candidate(self) -> None:
        candidates = _normalize_planned_epic_story_candidates(
            [
                {
                    "summary": "Confluence evidence unavailable for story planning",
                    "description": "Confluence evidence unavailable for story planning.",
                    "acceptance_criteria": ["Evidence must be loaded before planning."],
                    "dependencies": [],
                    "edge_cases": [],
                    "story_points": 1,
                    "planner": "codex",
                    "evidence_refs": [],
                }
            ],
            "OQS Rebalancing",
        )
        self.assertEqual(candidates, [])

    def test_model_reasoner_uses_structured_model_output(self) -> None:
        def fake_request_json(method: str, url: str, headers: dict[str, str], payload: dict | None = None) -> dict:
            self.assertEqual(method, "POST")
            self.assertIn("/v1/chat/completions", url)
            self.assertEqual(payload["model"], "test-model")
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "suggested_description": "CRUD kafka subscriptions should expose create, update, list, and delete flows backed by the shared subscription design.",
                                    "guidance": [
                                        "Keep the implementation focused on CRUD lifecycle behavior for subscription definitions.",
                                        "Preserve the Kafka subscription validation rules from the shared design.",
                                    ],
                                    "out_of_scope": ["Schema redesign outside the Kafka subscription CRUD flow."],
                                    "error_handling": ["Retry transient Kafka admin failures and surface validation errors clearly."],
                                    "nfrs": ["Perf budget: subscription CRUD should remain responsive under normal control-plane load."],
                                    "open_questions": ["Confirm whether delete requires soft-delete semantics."],
                                    "recommended_next_step": "Review the CRUD lifecycle coverage and confirm delete semantics before writeback.",
                                    "risks": ["Subscription-definition drift can cause runtime mismatch if stale data is used."],
                                }
                            )
                        }
                    }
                ]
            }

        reasoner = ModelSemanticReasoner(
            base_url="https://llm.example.com",
            api_key="secret",
            model="test-model",
            request_json=fake_request_json,
        )
        result = reasoner.analyze_issue(self.issue, self.evidence, "Use the confluence design.")
        self.assertEqual(result["reasoning_mode"], "model")
        self.assertIn("CRUD kafka subscriptions", result["suggested_description"])
        self.assertEqual(len(result["guidance"]), 2)
        self.assertEqual(result["recommended_next_step"], "Review the CRUD lifecycle coverage and confirm delete semantics before writeback.")
        self.assertIn("Schema redesign outside the Kafka subscription CRUD flow.", result["out_of_scope"])
        self.assertIn("Retry transient Kafka admin failures and surface validation errors clearly.", result["error_handling"])

    def test_model_reasoner_falls_back_when_model_call_fails(self) -> None:
        def failing_request_json(method: str, url: str, headers: dict[str, str], payload: dict | None = None) -> dict:
            raise RuntimeError("model unavailable")

        reasoner = ModelSemanticReasoner(
            base_url="https://llm.example.com",
            api_key="secret",
            model="test-model",
            request_json=failing_request_json,
        )
        result = reasoner.analyze_issue(self.issue, self.evidence, "Use the confluence design.")
        self.assertEqual(result["reasoning_mode"], "heuristic_fallback")
        self.assertIn("CRUD kafka subscriptions", result["suggested_description"])
        self.assertTrue(result["recommended_next_step"])

    def test_codex_broker_reasoner_uses_broker_response(self) -> None:
        def fake_request_json(method: str, url: str, headers: dict[str, str], payload: dict | None = None) -> dict:
            self.assertEqual(method, "POST")
            self.assertEqual(url, "http://127.0.0.1:9000/api/v1/analysis/analyze-issue")
            self.assertEqual(payload["issue"]["issue_key"], "AES-100")
            return {
                "suggested_description": "CRUD kafka subscriptions should align with the Codex-reviewed implementation design.",
                "guidance": ["Keep CRUD behavior specific to this story."],
                "out_of_scope": ["Cross-cluster Kafka redesign."],
                "error_handling": ["Surface invalid topic mappings as validation errors."],
                "nfrs": ["Perf budget: keep CRUD operations responsive."],
                "open_questions": ["Confirm delete retention rules."],
                "recommended_next_step": "Confirm delete retention rules before writeback.",
                "risks": ["Broker-sourced design may be stale if Jira scope changed."],
            }

        reasoner = CodexBrokerSemanticReasoner(
            base_url="http://127.0.0.1:9000",
            request_json=fake_request_json,
        )
        result = reasoner.analyze_issue(self.issue, self.evidence, "Use the confluence design.")
        self.assertEqual(result["reasoning_mode"], "codex_broker")
        self.assertIn("Codex-reviewed", result["suggested_description"])
        self.assertEqual(result["recommended_next_step"], "Confirm delete retention rules before writeback.")

    def test_codex_exec_reasoner_uses_subprocess_output(self) -> None:
        def fake_runner(cmd, input=None, text=None, capture_output=None, cwd=None, timeout=None, check=None):
            output_index = cmd.index("-o") + 1
            with open(cmd[output_index], "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "suggested_description": "Codex exec analyzed the Jira and linked systems.",
                        "guidance": ["Use story-specific evidence only."],
                        "out_of_scope": ["Broad platform redesign."],
                        "error_handling": ["Retry transient connector failures."],
                        "nfrs": ["Perf budget: keep enrichment responsive."],
                        "open_questions": ["Confirm retention semantics."],
                        "recommended_next_step": "Review Codex findings before writeback.",
                        "risks": ["Linked evidence may be stale."],
                    },
                    handle,
                )
            return None

        reasoner = CodexExecSemanticReasoner(
            codex_command="codex",
            runner=fake_runner,
            workdir="/tmp",
        )
        result = reasoner.analyze_issue(self.issue, self.evidence, "Use the confluence design.")
        self.assertEqual(result["reasoning_mode"], "codex_exec")
        self.assertIn("Codex exec analyzed", result["suggested_description"])

    def test_build_orchestrator_skips_external_fetch_when_codex_exec_enabled(self) -> None:
        previous = dict(os.environ)
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                os.environ["JIRA_ENHANCER_CONNECTOR_MODE"] = "mock"
                os.environ["JIRA_ENHANCER_USE_CODEX_EXEC"] = "true"
                orchestrator = build_orchestrator(Path(tmpdir))
                self.assertTrue(orchestrator.evidence_resolver.skip_external_fetch)
        finally:
            os.environ.clear()
            os.environ.update(previous)

    def test_build_orchestrator_does_not_inject_broker_when_codex_exec_enabled(self) -> None:
        previous = dict(os.environ)
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                os.environ["JIRA_ENHANCER_CONNECTOR_MODE"] = "live"
                os.environ["JIRA_ENHANCER_USE_CODEX_EXEC"] = "true"
                os.environ["JIRA_BASE_URL"] = "https://jira.example.com"
                os.environ["JIRA_TOKEN"] = "jira-token"
                os.environ["CONFLUENCE_BASE_URL"] = "https://confluence.example.com"
                os.environ["CONFLUENCE_MCP_COMMAND"] = "/opt/homebrew/bin/uvx"
                os.environ["BITBUCKET_BASE_URL"] = "https://bitbucket.example.com"
                os.environ["BITBUCKET_TOKEN"] = "bb-token"
                orchestrator = build_orchestrator(Path(tmpdir), broker_base_url="http://127.0.0.1:8090")
                self.assertEqual(orchestrator.store.config.codex_broker_base_url, "")
        finally:
            os.environ.clear()
            os.environ.update(previous)


if __name__ == "__main__":
    unittest.main()
