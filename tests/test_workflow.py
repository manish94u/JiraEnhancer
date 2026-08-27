from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jira_enhancer.bootstrap import build_orchestrator


class WorkflowTest(unittest.TestCase):
    class _ImmediateThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self) -> None:
            if self._target:
                self._target(*self._args, **self._kwargs)

    def test_start_run_initializes_background_ready_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"}, clear=False):
            orchestrator = build_orchestrator(Path(tmpdir))
            started = orchestrator.start_run("issue", "DEMO-18324", "reviewer@example.org")
            self.assertEqual(started["status"], "running")
            run_state = orchestrator.get_run(started["runId"])
            self.assertEqual(run_state["status"], "running")
            self.assertEqual(run_state["status_payload"]["status"], "running")
            self.assertEqual(run_state["items"], ["DEMO-18324"])
            self.assertEqual(run_state["status_payload"]["items"][0]["issue_key"], "DEMO-18324")
            self.assertEqual(run_state["status_payload"]["items"][0]["status"], "queued")

            completed = orchestrator.continue_run(started["runId"])
            self.assertIn(completed["status"], {"awaiting_approval", "completed"})
            final_state = orchestrator.get_run(started["runId"])
            self.assertIn(final_state["status"], {"awaiting_approval", "completed"})
            self.assertTrue(final_state["status_payload"]["items"])

    def test_get_run_item_returns_pending_placeholder_before_analysis_finishes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"}, clear=False):
            orchestrator = build_orchestrator(Path(tmpdir))
            started = orchestrator.start_run("epic", "DEMO-18000", "reviewer@example.org")
            pending = orchestrator.get_run_item(started["runId"], "DEMO-18324")
            self.assertEqual(pending["issue_key"], "DEMO-18324")
            self.assertEqual(pending["status"], "queued")
            self.assertEqual(pending["draft"]["status"], "pending")

    def test_single_issue_run_approval_and_writeback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"}, clear=False):
            orchestrator = build_orchestrator(Path(tmpdir))
            created = orchestrator.create_run("issue", "DEMO-18324", "reviewer@example.org")
            run_id = created["runId"]

            run_state = orchestrator.get_run(run_id)
            self.assertEqual(run_state["scope_type"], "issue")
            self.assertEqual(run_state["status_payload"]["items"][0]["issue_key"], "DEMO-18324")

            item = orchestrator.get_run_item(run_id, "DEMO-18324")
            self.assertIn(item["status"], ["awaiting_approval", "draft_ready"])
            self.assertIn("implementation_outline", item["draft"]["payload"])
            self.assertTrue(item["draft"]["payload"]["summary"].startswith("DEMO-18324:"))

            approval = orchestrator.record_approval(
                run_id,
                "DEMO-18324",
                reviewer="approver@example.org",
                decision="approve",
                reviewer_notes="Looks bounded",
            )
            self.assertEqual(approval["decision"], "approve")
            run_after_approval = orchestrator.get_run(run_id)
            self.assertEqual(run_after_approval["status_payload"]["items"][0]["status"], "approved")

            writeback = orchestrator.writeback(run_id, "DEMO-18324")
            self.assertEqual(writeback["status"], "written")
            self.assertIn("[AI_UPDATE_BEGIN]", writeback["write_targets"]["description"])
            self.assertIn("version: 1", writeback["write_targets"]["description"])
            self.assertIn("approval_state: approved_for_writeback", writeback["write_targets"]["description"])
            self.assertIn("Summary:\nDEMO-18324:", writeback["write_targets"]["description"])
            self.assertIn("Implementation Outline:", writeback["write_targets"]["description"])
            self.assertIn("Recommended Next Step:", writeback["write_targets"]["description"])
            refreshed = orchestrator.writeback_service.jira.get_issue("DEMO-18324")
            self.assertIn("[AI_UPDATE_BEGIN]", refreshed["description"])
            self.assertIn("Allow the enrichment pipeline", refreshed["description"])
            run_after_writeback = orchestrator.get_run(run_id)
            self.assertEqual(run_after_writeback["status_payload"]["items"][0]["status"], "written")

    def test_confluence_failure_becomes_structured_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"}, clear=False):
            orchestrator = build_orchestrator(Path(tmpdir))

            def fail_confluence(page_id: str) -> dict:
                raise RuntimeError("Confluence MCP server unavailable")

            orchestrator.evidence_resolver.confluence.get_page = fail_confluence
            created = orchestrator.create_run("issue", "DEMO-18324", "reviewer@example.org")
            run_id = created["runId"]

            run_state = orchestrator.get_run(run_id)
            item = orchestrator.get_run_item(run_id, "DEMO-18324")

            self.assertTrue(run_state["status"] in {"awaiting_approval", "completed"})
            self.assertTrue(item["warnings"])
            self.assertEqual(item["warnings"][0]["code"], "confluence_mcp_unavailable")
            self.assertEqual(item["warnings"][0]["source"], "confluence")
            self.assertTrue(run_state["status_payload"]["warnings"])
            self.assertEqual(run_state["status_payload"]["warnings"][0]["code"], "confluence_mcp_unavailable")
            self.assertEqual(item["draft"]["payload"]["warnings"][0]["code"], "confluence_mcp_unavailable")

    def test_confluence_auth_like_failure_requests_auth(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"}, clear=False):
            orchestrator = build_orchestrator(Path(tmpdir))
            orchestrator.evidence_resolver.confluence_use_web_session = True
            orchestrator.evidence_resolver.confluence_base_url = "https://confluence.example.com"

            def fail_confluence(page_id: str) -> dict:
                raise RuntimeError("Timed out waiting for MCP server response during initialize")

            orchestrator.evidence_resolver.confluence.get_page = fail_confluence
            created = orchestrator.create_run("issue", "DEMO-18324", "reviewer@example.org")
            item = orchestrator.get_run_item(created["runId"], "DEMO-18324")

            self.assertEqual(item["warnings"][0]["code"], "confluence_auth_required")
            self.assertEqual(item["warnings"][0]["auth_url"], "https://confluence.example.com")
            self.assertIn("Complete browser sign-in", item["warnings"][0]["message"])

    def test_batch_scope_is_isolated_per_issue(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"}, clear=False):
            orchestrator = build_orchestrator(Path(tmpdir))
            created = orchestrator.create_run("epic", "DEMO-18000", "reviewer@example.org")
            run_id = created["runId"]
            first = orchestrator.get_run_item(run_id, "DEMO-18324")
            second = orchestrator.get_run_item(run_id, "DEMO-18325")
            self.assertEqual(first["issue_key"], "DEMO-18324")
            self.assertEqual(second["issue_key"], "DEMO-18325")
            self.assertNotEqual(first["draft"]["payload"]["issue_key"], second["draft"]["payload"]["issue_key"])

    def test_rl_safe_arm_filter_uses_each_item_state_in_multi_issue_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {
                "JIRA_ENHANCER_CONNECTOR_MODE": "mock",
                "JIRA_ENHANCER_RL_ENABLED": "true",
                "JIRA_ENHANCER_RL_POLICY": "thompson",
            },
            clear=False,
        ):
            orchestrator = build_orchestrator(Path(tmpdir))
            created = orchestrator.create_run("epic", "DEMO-18000", "reviewer@example.org")
            run_id = created["runId"]

            first = orchestrator.get_run_item(run_id, "DEMO-18324")
            second = orchestrator.get_run_item(run_id, "DEMO-18325")
            self.assertIn("rl_prompt_decision", first)
            self.assertIn("rl_prompt_decision", second)
            self.assertEqual(
                first["rl_prompt_decision"]["metadata"]["governance_filter"]["context"]["workflow_status"],
                "running",
            )
            self.assertEqual(
                second["rl_prompt_decision"]["metadata"]["governance_filter"]["context"]["workflow_status"],
                "running",
            )

    def test_rl_safe_arm_filter_fails_closed_when_item_state_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {
                "JIRA_ENHANCER_CONNECTOR_MODE": "mock",
                "JIRA_ENHANCER_RL_ENABLED": "true",
                "JIRA_ENHANCER_RL_POLICY": "thompson",
            },
            clear=False,
        ):
            orchestrator = build_orchestrator(Path(tmpdir))
            started = orchestrator.start_run("issue", "DEMO-18324", "reviewer@example.org")
            run_id = started["runId"]
            status = orchestrator.store.get_status(run_id)
            status["items"] = []
            orchestrator.store.save_status(run_id, status)

            orchestrator._process_issue(run_id, "DEMO-18324")

            item = orchestrator.get_run_item(run_id, "DEMO-18324")
            self.assertNotIn("rl_prompt_decision", item)
            event_file = next(
                orchestrator.rl_prompt_service.governance_events_dir.glob("*.json")
            )
            event = json.loads(event_file.read_text(encoding="utf-8"))
            self.assertEqual(event["context"]["workflow_status"], "unknown")
            self.assertEqual(event["safe_prompt_ids"], [])
            self.assertTrue(
                all(
                    evaluation["conditions"]["workflow_state_allowed"] == 0
                    for evaluation in event["evaluations"]
                )
            )

    def test_initial_draft_can_infer_description_from_linked_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"}, clear=False):
            orchestrator = build_orchestrator(Path(tmpdir))
            orchestrator.scanner.jira.issues["DEMO-18326"]["description"] = ""
            created = orchestrator.create_run("issue", "DEMO-18326", "reviewer@example.org")
            run_id = created["runId"]

            item = orchestrator.get_run_item(run_id, "DEMO-18326")
            self.assertIn("Persist run state in MVP filesystem schema", item["draft"]["payload"]["suggested_description"])
            self.assertIn("Architecture", item["draft"]["payload"]["suggested_description"])
            self.assertIn("filesystem storage adapter", item["draft"]["payload"]["suggested_description"])
            self.assertGreaterEqual(item["confidence"], 6)
            self.assertGreaterEqual(item["readiness_score"], 6)
            self.assertLessEqual(item["confidence"], 9)
            self.assertLessEqual(item["readiness_score"], 9)

    def test_create_run_uses_bounded_autonomous_agent_loop(self) -> None:
        class FakeReasoner:
            def __init__(self) -> None:
                self.calls = 0

            def analyze_issue(self, normalized_issue, evidence, user_input_text=""):
                self.calls += 1
                if self.calls == 1:
                    return {
                        "problem_statement": "The Jira needs clearer scope and completion conditions.",
                        "suggested_description": "Clarify the requested change for DEMO-18325.",
                        "acceptance_criteria": ["Completion criteria are still unclear."],
                        "guidance": [],
                        "out_of_scope": [],
                        "error_handling": [],
                        "nfrs": [],
                        "open_questions": ["What concrete behavior proves this Jira is complete?"],
                        "recommended_next_step": "Refine the Jira before requesting approval.",
                        "risks": [],
                        "reasoning_mode": "codex_exec",
                    }
                return {
                    "problem_statement": "The Jira exists to ensure the targeted user-input fallback stays story-specific and reviewable.",
                    "suggested_description": "Add targeted user input fallback that captures reviewer context, keeps the flow story-specific, and re-runs enrichment with explicit dependency notes.",
                    "acceptance_criteria": [
                        "The fallback asks Jira-specific questions instead of a generic free-form prompt.",
                        "Reviewer identity and timestamps are preserved through the rerun flow.",
                        "The regenerated brief explicitly calls out dependency expectations for the story.",
                    ],
                    "guidance": ["Keep the flow story-specific and reviewer-auditable."],
                    "out_of_scope": ["Epic-wide redesign outside the story fallback flow."],
                    "error_handling": ["Reject empty reviewer input and preserve the prior draft."],
                    "nfrs": ["Perf budget: keep autonomous refinement bounded."],
                    "open_questions": [],
                    "recommended_next_step": "Review the refined fallback flow and approve it if the story scope is clear.",
                    "risks": ["Reviewer guidance may still be needed for edge-case policy."],
                    "reasoning_mode": "codex_exec",
                }

        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"}, clear=False):
            orchestrator = build_orchestrator(Path(tmpdir))
            orchestrator.semantic_reasoner = FakeReasoner()
            issue = orchestrator.scanner.jira.issues["DEMO-18325"]
            issue["description"] = ""
            issue["acceptance_criteria"] = []
            issue["dependencies"] = []
            issue["edge_cases"] = []
            issue["comments"] = []
            issue["worklogs"] = []
            issue["links"] = {"confluence_pages": [], "bitbucket_prs": []}

            created = orchestrator.create_run("issue", "DEMO-18325", "reviewer@example.org")
            item = orchestrator.get_run_item(created["runId"], "DEMO-18325")
            agent_state = item["agent_state"]

            self.assertGreaterEqual(orchestrator.semantic_reasoner.calls, 2)
            self.assertGreaterEqual(agent_state["iterations_completed"], 1)
            self.assertTrue(agent_state["history"])
            self.assertEqual(agent_state["history"][0]["action"], "self_refine")
            self.assertGreaterEqual(item["draft"]["draft_version"], 2)
            self.assertNotEqual(agent_state["next_action"], "self_refine")

    def test_user_input_regenerates_draft_with_detailed_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"}, clear=False):
            orchestrator = build_orchestrator(Path(tmpdir))
            created = orchestrator.create_run("issue", "DEMO-18325", "reviewer@example.org")
            run_id = created["runId"]

            approval = orchestrator.record_approval(
                run_id,
                "DEMO-18325",
                reviewer="approver@example.org",
                decision="user_input",
                reviewer_notes="Focus on story-level work only.",
                user_input={
                    "details": "Read the Jira description carefully.\n- split implementation by story\n- keep epic-level guidance out of story writeback\n- call out missing dependencies explicitly"
                },
            )

            self.assertEqual(approval["decision"], "user_input")
            self.assertEqual(approval["item_status"], "awaiting_approval")
            self.assertEqual(approval["draft"]["draft_version"], 2)
            self.assertGreater(approval["draft"]["payload"]["confidence"], 0.0)
            self.assertTrue(
                any(
                    "story intent" in line.lower() or "dependencies and sequencing" in line.lower()
                    for line in approval["draft"]["payload"]["user_input_details"]
                )
            )

            item = orchestrator.get_run_item(run_id, "DEMO-18325")
            self.assertEqual(item["draft"]["draft_version"], 2)
            self.assertEqual(item["status"], "awaiting_approval")
            self.assertGreater(item["confidence"], 0.0)
            self.assertTrue(
                any(
                    "story intent" in step or "dependencies and sequencing" in step
                    for step in item["draft"]["payload"]["implementation_outline"]
                )
            )

    def test_prompt_decision_regenerates_draft_with_prompt_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"}, clear=False):
            orchestrator = build_orchestrator(Path(tmpdir))
            created = orchestrator.create_run("issue", "DEMO-18325", "reviewer@example.org")
            run_id = created["runId"]

            approval = orchestrator.record_approval(
                run_id,
                "DEMO-18325",
                reviewer="approver@example.org",
                decision="prompt",
                reviewer_notes="Execute the generated prompt.",
                user_input={
                    "details": "Refine the Jira so the acceptance criteria are concrete, story-specific, and explicitly capture dependency sequencing."
                },
            )

            self.assertEqual(approval["decision"], "prompt")
            self.assertEqual(approval["item_status"], "awaiting_approval")
            self.assertGreaterEqual(approval["draft"]["draft_version"], 2)
            self.assertTrue(
                any(
                    "story" in line.lower() or "dependenc" in line.lower()
                    for line in approval["draft"]["payload"]["user_input_details"]
                )
            )

    def test_user_input_updates_confidence_written_into_ai_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"}, clear=False):
            orchestrator = build_orchestrator(Path(tmpdir))
            orchestrator.scanner.jira.issues["DEMO-18325"]["description"] = ""
            created = orchestrator.create_run("issue", "DEMO-18325", "reviewer@example.org")
            run_id = created["runId"]

            approval = orchestrator.record_approval(
                run_id,
                "DEMO-18325",
                reviewer="approver@example.org",
                decision="user_input",
                reviewer_notes="Use reviewer input to tighten the story scope.",
                user_input={"details": "Implement the story-specific writeback with explicit dependency notes."},
            )
            self.assertGreater(approval["draft"]["payload"]["confidence"], 0.0)

            orchestrator.record_approval(
                run_id,
                "DEMO-18325",
                reviewer="approver@example.org",
                decision="approve",
                reviewer_notes="Updated draft looks good.",
            )
            writeback = orchestrator.writeback(run_id, "DEMO-18325")

            self.assertNotIn("confidence: 0.0", writeback["write_targets"]["description"])
            self.assertNotIn("confidence: 0.", writeback["write_targets"]["description"])
            self.assertIn(f"confidence: {approval['draft']['payload']['confidence']}", writeback["write_targets"]["description"])

    def test_user_input_triggers_background_codex_refinement_until_confident(self) -> None:
        class FakeReasoner:
            def __init__(self) -> None:
                self.calls = 0

            def analyze_issue(self, normalized_issue, evidence, user_input_text=""):
                self.calls += 1
                if self.calls < 3:
                    return {
                        "suggested_description": "Clarify the requested change for DEMO-18325.",
                        "guidance": [],
                        "out_of_scope": [],
                        "error_handling": [],
                        "nfrs": [],
                        "open_questions": [],
                        "recommended_next_step": "",
                        "risks": [],
                        "reasoning_mode": "codex_exec",
                    }
                return {
                    "suggested_description": "Add explicit user-input fallback handling so low-context Jira tickets can be refined with Codex-backed story details before writeback.",
                    "guidance": [
                        "Keep the writeback scoped to the story intent in Add targeted user input fallback.",
                        "Call out the dependencies and sequencing constraints for Add targeted user input fallback.",
                    ],
                    "out_of_scope": ["Unrelated platform redesign outside the fallback flow."],
                    "error_handling": ["Return a clear validation error when required user input context is missing."],
                    "nfrs": ["Perf budget: keep iterative refinement bounded in the background."],
                    "open_questions": ["Confirm whether retry count should be configurable."],
                    "recommended_next_step": "Review the refined draft and approve the updated writeback.",
                    "risks": ["Missing reviewer context can still leave the story underspecified."],
                    "reasoning_mode": "codex_exec",
                }

        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"}, clear=False):
            with patch("jira_enhancer.services.Thread", self._ImmediateThread):
                orchestrator = build_orchestrator(Path(tmpdir))
                orchestrator.semantic_reasoner = FakeReasoner()
                issue = orchestrator.scanner.jira.issues["DEMO-18325"]
                issue["description"] = ""
                issue["acceptance_criteria"] = []
                issue["dependencies"] = []
                issue["edge_cases"] = []
                issue["comments"] = []
                issue["worklogs"] = []
                issue["links"] = {"confluence_pages": [], "bitbucket_prs": []}

                created = orchestrator.create_run("issue", "DEMO-18325", "reviewer@example.org")
                approval = orchestrator.record_approval(
                    created["runId"],
                    "DEMO-18325",
                    reviewer="approver@example.org",
                    decision="user_input",
                    reviewer_notes="Use Codex refinement when the score is still low.",
                    user_input={"details": "Refine the Jira description until the confidence score is at least eight."},
                )

                self.assertIn(approval["item_status"], {"refining", "awaiting_approval"})
                item = orchestrator.get_run_item(created["runId"], "DEMO-18325")
                self.assertGreaterEqual(item["confidence"], 8)
                self.assertEqual(item["status"], "awaiting_approval")
                self.assertGreaterEqual(item["draft"]["draft_version"], 3)

    def test_epic_run_can_apply_bulk_user_input_and_writeback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"}, clear=False):
            orchestrator = build_orchestrator(Path(tmpdir))
            created = orchestrator.create_run("epic", "DEMO-18000", "reviewer@example.org")
            run_id = created["runId"]

            approval = orchestrator.record_approval_for_run(
                run_id,
                reviewer="approver@example.org",
                decision="user_input",
                reviewer_notes="Use detailed user input for every story in this epic.",
                user_input={"details": "Read each story in detail and tailor the draft to that story only."},
            )
            self.assertEqual(approval["item_count"], 3)
            self.assertTrue(all(result["decision"] == "user_input" for result in approval["results"]))

            orchestrator.record_approval_for_run(
                run_id,
                reviewer="approver@example.org",
                decision="approve",
                reviewer_notes="All regenerated drafts look good.",
            )
            writeback = orchestrator.writeback_run(run_id)

            self.assertEqual(writeback["item_count"], 3)
            self.assertTrue(all(result["status"] == "written" for result in writeback["results"]))
            refreshed = orchestrator.writeback_service.jira.get_issue("DEMO-18324")
            self.assertIn("[AI_UPDATE_BEGIN]", refreshed["description"])

    def test_user_input_can_attach_confluence_and_bitbucket_context_for_description(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"}, clear=False):
            orchestrator = build_orchestrator(Path(tmpdir))
            orchestrator.scanner.jira.issues["DEMO-18325"]["description"] = ""
            created = orchestrator.create_run("issue", "DEMO-18325", "reviewer@example.org")
            run_id = created["runId"]

            approval = orchestrator.record_approval(
                run_id,
                "DEMO-18325",
                reviewer="approver@example.org",
                decision="user_input",
                reviewer_notes="Use provided implementation references.",
                user_input={
                    "details": (
                        "Confluence: https://confluence.example.com/confluence/pages/viewpage.action?pageId=20026531262\n"
                        "Bitbucket: https://bitbucket.example.com/projects/DEMO/repos/demo-project/pull-requests/18822\n"
                        "Derive a stronger Jira description from these sources."
                    )
                },
            )

            suggested = approval["draft"]["payload"]["suggested_description"]
            self.assertIn("Add targeted user input fallback", suggested)
            self.assertIn("Architecture", suggested)
            self.assertIn("filesystem storage adapter", suggested)
            self.assertIn("confluence:20026531262", approval["draft"]["payload"]["evidence_refs"])
            self.assertIn("bitbucket:DEMO/demo-project/18822", approval["draft"]["payload"]["evidence_refs"])
            self.assertNotIn("Derive a stronger Jira description from these sources.", "\n".join(approval["draft"]["payload"]["implementation_outline"]))

    def test_missing_acceptance_criteria_generates_summary_and_design_specific_questions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"}, clear=False):
            orchestrator = build_orchestrator(Path(tmpdir))
            issue = orchestrator.scanner.jira.issues["DEMO-18326"]
            issue["acceptance_criteria"] = []
            issue["description"] = ""

            created = orchestrator.create_run("issue", "DEMO-18326", "reviewer@example.org")
            item = orchestrator.get_run_item(created["runId"], "DEMO-18326")
            questions = item["draft"]["payload"]["fallback_questions"]
            context_brief = item["draft"]["payload"]["context_brief"]

            self.assertTrue(any("acceptance criteria" in question.lower() for question in questions))
            self.assertTrue(any("Persist run state in MVP filesystem schema" in question for question in questions))
            self.assertTrue(any("Architecture" in question or "filesystem MVP" in question for question in questions))
            self.assertIn("Codex is improving Jira DEMO-18326", item["draft"]["payload"]["codex_user_input_prompt"])
            self.assertIn("Problem statement", item["draft"]["payload"]["codex_user_input_prompt"])
            self.assertIn("Problem to solve:", context_brief["problem_statement"])
            self.assertIn("Completion for Persist run state in MVP filesystem schema is reviewable", context_brief["acceptance_focus"])
            self.assertNotEqual(context_brief["problem_statement"], item["draft"]["payload"]["suggested_description"])
            self.assertNotEqual(context_brief["acceptance_focus"], item["draft"]["payload"]["suggested_description"])
            self.assertTrue(any("Completion for Persist run state in MVP filesystem schema is reviewable" in criterion for criterion in item["draft"]["payload"]["acceptance_criteria"]))

    def test_user_input_refreshes_codex_prompt_acceptance_focus(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"}, clear=False):
            orchestrator = build_orchestrator(Path(tmpdir))
            issue = orchestrator.scanner.jira.issues["DEMO-18325"]
            issue["acceptance_criteria"] = []
            issue["description"] = ""

            created = orchestrator.create_run("issue", "DEMO-18325", "reviewer@example.org")
            approval = orchestrator.record_approval(
                created["runId"],
                "DEMO-18325",
                reviewer="approver@example.org",
                decision="user_input",
                reviewer_notes="Keep the flow targeted to the story and make the fallback behavior explicit.",
                user_input={"details": "Clarify the story-specific fallback behavior, the reviewer capture requirement, and the dependency on the approval UI."},
            )

            acceptance_focus = approval["draft"]["payload"]["context_brief"]["acceptance_focus"]
            problem_statement = approval["draft"]["payload"]["context_brief"]["problem_statement"]
            suggested_description = approval["draft"]["payload"]["suggested_description"]
            prompt = approval["draft"]["payload"]["codex_user_input_prompt"]

            self.assertNotEqual(acceptance_focus, "Acceptance criteria still need to be confirmed by the reviewer.")
            self.assertIn("Completion for Add targeted user input fallback is reviewable", acceptance_focus)
            self.assertIn("Problem to solve:", problem_statement)
            self.assertNotEqual(problem_statement, suggested_description)
            self.assertNotEqual(acceptance_focus, suggested_description)
            self.assertIn("Current acceptance focus", prompt)
            self.assertIn(acceptance_focus, prompt)

    def test_submit_interaction_answer_advances_to_next_question(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"}, clear=False):
            orchestrator = build_orchestrator(Path(tmpdir))
            issue = orchestrator.scanner.jira.issues["DEMO-18326"]
            issue["acceptance_criteria"] = []
            issue["description"] = ""

            created = orchestrator.create_run("issue", "DEMO-18326", "reviewer@example.org")
            before = orchestrator.get_run_item(created["runId"], "DEMO-18326")
            current_question = before["draft"]["payload"]["interaction"]["current_question"]
            self.assertTrue(current_question)

            response = orchestrator.submit_interaction_answer(
                created["runId"],
                "DEMO-18326",
                reviewer="approver@example.org",
                answer="Persisted state must survive restart and keep replay support explicit in the Jira brief.",
            )

            interaction = response["interaction"]
            self.assertEqual(len(interaction["answers"]), 1)
            self.assertEqual(interaction["answers"][0]["question"], current_question)
            self.assertTrue(interaction["answers"][0]["answer"].startswith("Persisted state must survive restart"))
            self.assertIn("Question 1:", interaction["transcript"])
            self.assertNotEqual(interaction["current_question"], current_question)
            item = orchestrator.get_run_item(created["runId"], "DEMO-18326")
            self.assertEqual(item["draft"]["payload"]["interaction"]["answers"][0]["question"], current_question)

    def test_shared_sources_generate_different_text_per_jira_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"}, clear=False):
            orchestrator = build_orchestrator(Path(tmpdir))
            orchestrator.scanner.jira.issues["AES-100"] = {
                "key": "AES-100",
                "summary": "AES CP: CRUD kafka subscriptions",
                "description": "",
                "acceptance_criteria": ["Create, update, list, and delete subscription definitions."],
                "dependencies": ["Kafka subscription service"],
                "edge_cases": ["Invalid topic mapping"],
                "comments": [],
                "worklogs": [],
                "links": {"confluence_pages": [], "bitbucket_prs": []},
            }
            orchestrator.scanner.jira.issues["AES-101"] = {
                "key": "AES-101",
                "summary": "AES CP: rebalance kafka consumers",
                "description": "",
                "acceptance_criteria": ["Consumer rebalance should resume without dropping subscriptions."],
                "dependencies": ["Kafka consumer worker"],
                "edge_cases": ["Duplicate rebalance events"],
                "comments": [],
                "worklogs": [],
                "links": {"confluence_pages": [], "bitbucket_prs": []},
            }
            orchestrator.scope_resolver.jira.scopes["epics"]["AES-EPIC"] = ["AES-100", "AES-101"]
            orchestrator.evidence_resolver.confluence.pages["30000000001"] = {
                "id": "30000000001",
                "title": "DEMO Kafka Subscriptions",
                "summary": (
                    "Kafka subscriptions API supports CRUD operations for subscription definitions. "
                    "Consumer rebalance flow coordinates worker resubscription after topology changes. "
                    "Dead-letter and retry policies handle failed delivery."
                ),
            }
            orchestrator.evidence_resolver.bitbucket.prs["19999"] = {
                "id": "19999",
                "title": "Implement kafka subscription CRUD and rebalance handling",
                "repository": "demo-project",
                "branch": "feature/kafka-subscriptions",
                "changed_files": [
                    "src/kafka/subscriptions.py",
                    "src/kafka/consumer_rebalance.py",
                ],
                "reviewers": ["tech.lead"],
            }

            created = orchestrator.create_run("epic", "AES-EPIC", "reviewer@example.org")
            run_id = created["runId"]
            orchestrator.record_approval_for_run(
                run_id,
                reviewer="approver@example.org",
                decision="user_input",
                reviewer_notes="Use the provided Kafka subscription design and implementation references.",
                user_input={
                    "details": (
                        "Confluence page: https://confluence.example.com/confluence/display/DEMO/DEMO+Kafka+Subscriptions\n"
                        "Bitbucket PR: https://bitbucket.example.com/projects/DEMO/repos/demo-project/pull-requests/19999\n"
                        "Use those sources to generate the right text for each Jira based on its summary."
                    )
                },
            )

            crud_item = orchestrator.get_run_item(run_id, "AES-100")
            rebalance_item = orchestrator.get_run_item(run_id, "AES-101")
            crud_text = crud_item["draft"]["payload"]["suggested_description"]
            rebalance_text = rebalance_item["draft"]["payload"]["suggested_description"]

            self.assertIn("CRUD kafka subscriptions", crud_text)
            self.assertIn("CRUD operations for subscription definitions", crud_text)
            self.assertIn("rebalance kafka consumers", rebalance_text)
            self.assertIn("Consumer rebalance flow coordinates worker resubscription", rebalance_text)
            self.assertNotEqual(crud_text, rebalance_text)
            crud_questions = crud_item["draft"]["payload"]["fallback_questions"]
            rebalance_questions = rebalance_item["draft"]["payload"]["fallback_questions"]
            self.assertTrue(any("acceptance criteria" in question.lower() for question in crud_questions))
            self.assertTrue(any("CRUD kafka subscriptions" in question for question in crud_questions))
            self.assertTrue(any("rebalance kafka consumers" in question for question in rebalance_questions))
            self.assertNotEqual(crud_questions[0], rebalance_questions[0])

    def test_rl_flag_enables_service_and_records_prompt_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {
                "JIRA_ENHANCER_CONNECTOR_MODE": "mock",
                "JIRA_ENHANCER_RL_ENABLED": "true",
                "JIRA_ENHANCER_RL_POLICY": "thompson",
            },
            clear=False,
        ):
            orchestrator = build_orchestrator(Path(tmpdir))
            self.assertIsNotNone(orchestrator.rl_prompt_service)

            created = orchestrator.create_run("issue", "DEMO-18324", "reviewer@example.org")
            item = orchestrator.get_run_item(created["runId"], "DEMO-18324")
            self.assertIn("rl_prompt_decision", item)
            self.assertIn("metadata", item["rl_prompt_decision"])
            self.assertIn("selected_prompt_pack", item["rl_prompt_decision"]["metadata"])
            self.assertIn("policy_selection", item["rl_prompt_decision"]["metadata"])
            governance_filter = item["rl_prompt_decision"]["metadata"].get("governance_filter", {})
            self.assertEqual(governance_filter.get("mode"), "explicit")
            self.assertEqual(governance_filter.get("predicate_version"), "prompt-governance-v2")
            self.assertEqual(governance_filter.get("context", {}).get("workflow_status"), "running")
            self.assertEqual(governance_filter.get("context", {}).get("write_target"), "description_top_block")
            self.assertIn(item["rl_prompt_decision"]["prompt_id"], governance_filter.get("safe_prompt_ids", []))
            self.assertLess(item["confidence"], 9)
            self.assertIn("test_case_gap", item["policy_flags"])
            self.assertIn("missing_unit_test_case_sample", item["policy_flags"])
            self.assertIn("missing_integration_test_case_sample", item["policy_flags"])
            gap_analysis = item["rl_prompt_decision"]["metadata"].get("test_case_gap_analysis", {})
            self.assertEqual(gap_analysis.get("missing"), ["unit", "integration"])
            self.assertIn("sample unit and integration test cases", item["rl_prompt_decision"]["metadata"].get("test_case_gap_prompt", ""))
            self.assertIn("Test coverage gap", item["draft"]["payload"]["test_case_gap_analysis"].get("gap_prompt", ""))

    def test_rl_retry_trajectory_and_outcome_audit_are_persisted(self) -> None:
        class FakeReasoner:
            def analyze_issue(self, normalized_issue, evidence, user_input_text=""):
                return {
                    "problem_statement": "Problem",
                    "suggested_description": "Suggested description",
                    "acceptance_criteria": ["AC1"],
                    "guidance": ["Guidance"],
                    "out_of_scope": ["Out"],
                    "error_handling": ["Handle"],
                    "nfrs": ["NFR"],
                    "open_questions": [],
                    "recommended_next_step": "Next",
                    "risks": ["Risk"],
                    "reasoning_mode": "heuristic",
                }

        class FakeScorer:
            def __init__(self) -> None:
                self.calls = 0

            def score(self, normalized_issue, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return 6, 6, ["missing_acceptance_criteria"]
                return 9, 9, []

        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {
                "JIRA_ENHANCER_CONNECTOR_MODE": "mock",
                "JIRA_ENHANCER_RL_ENABLED": "true",
                "JIRA_ENHANCER_RL_POLICY": "thompson",
                "JIRA_ENHANCER_RL_MAX_PROMPT_RETRIES": "3",
                "JIRA_ENHANCER_RL_TARGET_CONFIDENCE": "9",
            },
            clear=False,
        ):
            orchestrator = build_orchestrator(Path(tmpdir))
            orchestrator.semantic_reasoner = FakeReasoner()
            orchestrator.scorer = FakeScorer()

            created = orchestrator.create_run("issue", "DEMO-18324", "reviewer@example.org")
            run_id = created["runId"]
            item = orchestrator.get_run_item(run_id, "DEMO-18324")
            meta = item["rl_prompt_decision"]["metadata"]
            self.assertIn("confidence_trajectory", meta)
            self.assertGreaterEqual(len(meta["confidence_trajectory"]), 2)
            self.assertEqual(meta["target_confidence"], 9)

            orchestrator.record_approval(
                run_id,
                "DEMO-18324",
                reviewer="approver@example.org",
                decision="approve",
                reviewer_notes="Looks good",
            )
            outcomes = sorted((orchestrator.rl_prompt_service.root / "data" / "outcomes").glob("*.json"))
            self.assertTrue(outcomes)
            payload = json.loads(outcomes[-1].read_text(encoding="utf-8"))
            self.assertIn("metadata", payload)
            self.assertIn("stats_snapshot", payload["metadata"])
            self.assertIn("confidence_trajectory", payload["metadata"])

    def test_rl_codex_guardrail_metadata_when_codex_exec_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {
                "JIRA_ENHANCER_CONNECTOR_MODE": "mock",
                "JIRA_ENHANCER_RL_ENABLED": "true",
                "JIRA_ENHANCER_RL_FORCE_CODEX": "true",
                "JIRA_ENHANCER_USE_CODEX_EXEC": "true",
            },
            clear=False,
        ):
            orchestrator = build_orchestrator(Path(tmpdir))
            created = orchestrator.create_run("issue", "DEMO-18324", "reviewer@example.org")
            item = orchestrator.get_run_item(created["runId"], "DEMO-18324")
            guardrail = item["rl_prompt_decision"]["metadata"].get("codex_guardrail", {})
            self.assertTrue(guardrail.get("enabled"))
            self.assertTrue(guardrail.get("codex_reasoner_active"))
            self.assertTrue(guardrail.get("enforced"))

    def test_suggest_epic_stories_handles_unknown_mock_epic(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"}, clear=False):
            orchestrator = build_orchestrator(Path(tmpdir))
            result = orchestrator.suggest_epic_stories(
                "DEMO-94828",
                project_key="OIC",
                requestor="author@example.com",
            )
            self.assertEqual(result["epic_key"], "DEMO-94828")
            self.assertGreaterEqual(len(result["suggested_stories"]), 1)
            self.assertTrue(all(str(item.get("summary", "")).strip() for item in result["suggested_stories"]))
            self.assertTrue(all("Test Case Details:" in str(item.get("description", "")) for item in result["suggested_stories"]))
            self.assertTrue(all("Unit test case:" in str(item.get("description", "")) for item in result["suggested_stories"]))
            self.assertTrue(all("Integration test case:" in str(item.get("description", "")) for item in result["suggested_stories"]))
            self.assertTrue(
                all(
                    any("Unit test case:" in str(value) for value in item.get("acceptance_criteria", []))
                    and any("Integration test case:" in str(value) for value in item.get("acceptance_criteria", []))
                    for item in result["suggested_stories"]
                )
            )

    def test_suggest_epic_stories_uses_loaded_epic_evidence_for_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"}, clear=False):
            orchestrator = build_orchestrator(Path(tmpdir))
            orchestrator.scanner.jira.issues["DEMO-96210"] = {
                "key": "DEMO-96210",
                "summary": "OQS Rebalancing Subham Test - 1",
                "description": "Read https://confluence.example.com/pages/viewpage.action?pageId=18807043383 for OQS rebalancing.",
                "acceptance_criteria": [],
                "dependencies": [],
                "edge_cases": [],
                "comments": [],
                "worklogs": [],
                "links": {"confluence_pages": [], "bitbucket_prs": []},
            }
            orchestrator.evidence_resolver.confluence.pages["18807043383"] = {
                "id": "18807043383",
                "title": "Scaling of Connection(OQSClient and AESClient) based on backlog on OQS Channel",
                "summary": (
                    "Scaling Rule for Connection in OQS Client: scaling based on Subscription Count creates 1 connection "
                    "for every 10 subscriptions with max 15. Scaling based on Backlog Count uses RequiredConnections = "
                    "min(BCSn/500, 15) when AESClient execution avg is less than 1000ms. Scaling Rule for Connection "
                    "in AES Client: for every 200 subscriptions create 1 connection, maximum AES connections 5. "
                    "AES semaphore backlog rule sets SemaphoreCount = min(BCSn/500, 15) * 10. De-scaling Rule for "
                    "Connection in OQS uses low backlog for 4 consecutive intervals. De-scaling Rule for Semaphore "
                    "reduces towards baseline by 10%. Post-scale Guardrail uses execution-time breach, LastSafeSemaphore, "
                    "cooldown, and scale statistics metrics."
                ),
            }
            result = orchestrator.suggest_epic_stories(
                "DEMO-96210",
                project_key="OIC",
                requestor="author@example.com",
            )

            summaries = [item["summary"] for item in result["suggested_stories"]]
            summary_text = " ".join(summaries).lower()
            self.assertIn("18807043383", result["evidence_refs"])
            self.assertIn("oqs client", summary_text)
            self.assertIn("backlog count", summary_text)
            self.assertIn("aes client", summary_text)
            self.assertIn("semaphore", summary_text)
            self.assertNotIn("OQS Rebalancing Subham Test - 1: UI flow and interaction handling", summaries)
            self.assertTrue(all("evidence_refs" in item for item in result["suggested_stories"]))
            self.assertTrue(all("Test Case Details:" in str(item.get("description", "")) for item in result["suggested_stories"]))

    def test_suggest_epic_stories_derives_candidates_for_non_oqs_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"}, clear=False):
            orchestrator = build_orchestrator(Path(tmpdir))
            orchestrator.scanner.jira.issues["DEMO-97000"] = {
                "key": "DEMO-97000",
                "summary": "Manual wallet rotation setup",
                "description": "Read https://confluence.example.com/pages/viewpage.action?pageId=18807049999 for wallet rotation design.",
                "acceptance_criteria": [],
                "dependencies": [],
                "edge_cases": [],
                "comments": [],
                "worklogs": [],
                "links": {"confluence_pages": [], "bitbucket_prs": []},
            }
            orchestrator.evidence_resolver.confluence.pages["18807049999"] = {
                "id": "18807049999",
                "title": "Manual Wallet Rotation Design",
                "summary": (
                    "Wallet upload workflow should validate the uploaded wallet archive before it is staged. "
                    "Rotation audit API persists requestor, environment, previous wallet version, new wallet version, and status. "
                    "Rollback guardrail must restore the previous wallet version when validation or smoke verification fails."
                ),
            }

            result = orchestrator.suggest_epic_stories(
                "DEMO-97000",
                project_key="OIC",
                requestor="author@example.com",
            )

            summaries = [item["summary"] for item in result["suggested_stories"]]
            summary_text = " ".join(summaries).lower()
            self.assertIn("wallet upload workflow", summary_text)
            self.assertIn("rotation audit api", summary_text)
            self.assertIn("rollback guardrail", summary_text)
            self.assertNotIn("oqsclient", summary_text)
            self.assertNotIn("aesclient", summary_text)

    def test_suggest_epic_stories_prefers_codex_planner_candidates(self) -> None:
        class FakePlannerReasoner:
            def __init__(self) -> None:
                self.plan_calls = 0
                self.evidence_counts: list[int] = []
                self.confluence_refs: list[list[str]] = []

            def plan_epic_stories(self, epic_key, epic_summary, normalized_epic, evidence, existing_summaries):
                self.plan_calls += 1
                self.evidence_counts.append(len(evidence))
                self.confluence_refs.append(list(normalized_epic.get("links", {}).get("confluence_pages", [])))
                return [
                    {
                        "summary": "Implement planner-derived wallet validation",
                        "description": "Use the Confluence design to validate wallet archives before staging.",
                        "acceptance_criteria": [
                            "Wallet archive validation rejects malformed archives before staging.",
                            "Validation failures are returned with actionable error messages.",
                        ],
                        "dependencies": ["Wallet validation service"],
                        "edge_cases": ["Malformed archive", "Missing wallet metadata"],
                        "story_points": 3,
                    }
                ]

            def analyze_issue(self, normalized_issue, evidence, user_input_text=""):
                return {
                    "problem_statement": f"{normalized_issue['issue_key']}: {normalized_issue['summary']}. Problem to solve: create the planner-derived story.",
                    "suggested_description": normalized_issue["description"],
                    "acceptance_criteria": normalized_issue["acceptance_criteria"],
                    "guidance": ["Keep this story aligned with planner evidence."],
                    "out_of_scope": ["Unrelated wallet flows."],
                    "error_handling": ["Return validation errors clearly."],
                    "nfrs": ["Validation should complete within the expected request budget."],
                    "open_questions": [],
                    "recommended_next_step": "Review and approve the planner-derived story.",
                    "risks": ["Validation rules can drift from design."],
                    "reasoning_mode": "fake_planner",
                }

        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"}, clear=False):
            orchestrator = build_orchestrator(Path(tmpdir))
            fake_reasoner = FakePlannerReasoner()
            orchestrator.semantic_reasoner = fake_reasoner
            orchestrator.evidence_resolver.skip_external_fetch = True
            orchestrator.scanner.jira.issues["DEMO-97100"] = {
                "key": "DEMO-97100",
                "summary": "Wallet validation epic",
                "description": "Read https://confluence.example.com/pages/viewpage.action?pageId=18807050000 for wallet validation.",
                "acceptance_criteria": [],
                "dependencies": [],
                "edge_cases": [],
                "comments": [],
                "worklogs": [],
                "links": {"confluence_pages": [], "bitbucket_prs": []},
            }
            orchestrator.evidence_resolver.confluence.pages["18807050000"] = {
                "id": "18807050000",
                "title": "Wallet Validation Design",
                "summary": "Wallet upload workflow should validate wallet archives before staging.",
            }

            result = orchestrator.suggest_epic_stories(
                "DEMO-97100",
                project_key="OIC",
                requestor="author@example.com",
            )

            self.assertEqual(fake_reasoner.plan_calls, 1)
            self.assertEqual(fake_reasoner.evidence_counts, [0])
            self.assertEqual(fake_reasoner.confluence_refs, [["18807050000"]])
            self.assertEqual(result["evidence_refs"], [])
            summaries = [item["summary"] for item in result["suggested_stories"]]
            self.assertEqual(summaries, ["Implement planner-derived wallet validation"])
            self.assertTrue(result["suggested_stories"][0]["description"].startswith("Use the Confluence design"))

    def test_suggest_epic_stories_does_not_create_gap_story_when_codex_linked_context_returns_empty(self) -> None:
        class EmptyPlannerReasoner:
            def plan_epic_stories(self, epic_key, epic_summary, normalized_epic, evidence, existing_summaries):
                return []

            def analyze_issue(self, normalized_issue, evidence, user_input_text=""):
                raise AssertionError("No candidate should be analyzed when linked context planning returns no stories")

        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"}, clear=False):
            orchestrator = build_orchestrator(Path(tmpdir))
            orchestrator.semantic_reasoner = EmptyPlannerReasoner()
            orchestrator.evidence_resolver.skip_external_fetch = True
            orchestrator.scanner.jira.issues["DEMO-97101"] = {
                "key": "DEMO-97101",
                "summary": "Wallet validation epic",
                "description": "Read https://confluence.example.com/pages/viewpage.action?pageId=18807050001 for wallet validation.",
                "acceptance_criteria": [],
                "dependencies": [],
                "edge_cases": [],
                "comments": [],
                "worklogs": [],
                "links": {"confluence_pages": [], "bitbucket_prs": []},
            }

            result = orchestrator.suggest_epic_stories(
                "DEMO-97101",
                project_key="OIC",
                requestor="author@example.com",
            )

            self.assertEqual(result["suggested_stories"], [])
            warning_codes = [warning["code"] for warning in result["warnings"]]
            self.assertIn("epic_breakdown_codex_context_unavailable", warning_codes)

    def test_suggest_epic_stories_uses_embedded_epic_context_when_codex_returns_empty(self) -> None:
        class EmptyPlannerWithAnalysis:
            def plan_epic_stories(self, epic_key, epic_summary, normalized_epic, evidence, existing_summaries):
                return []

            def analyze_issue(self, normalized_issue, evidence, user_input_text=""):
                return {
                    "problem_statement": normalized_issue["summary"],
                    "suggested_description": normalized_issue["description"],
                    "acceptance_criteria": normalized_issue["acceptance_criteria"],
                    "guidance": [],
                    "out_of_scope": [],
                    "error_handling": [],
                    "nfrs": [],
                    "open_questions": [],
                    "recommended_next_step": "Review the embedded-context story.",
                    "risks": [],
                    "reasoning_mode": "fake_empty_planner",
                }

        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"}, clear=False):
            orchestrator = build_orchestrator(Path(tmpdir))
            orchestrator.semantic_reasoner = EmptyPlannerWithAnalysis()
            orchestrator.evidence_resolver.skip_external_fetch = True
            orchestrator.scanner.jira.issues["DEMO-97102"] = {
                "key": "DEMO-97102",
                "summary": "Wallet validation epic",
                "description": (
                    "Read https://confluence.example.com/pages/viewpage.action?pageId=18807050002. "
                    "Wallet upload workflow should validate the uploaded wallet archive before staging and return actionable validation errors. "
                    "Rotation audit API persists requestor, environment, previous wallet version, new wallet version, status, and timestamps. "
                    "Rollback guardrail must restore the previous wallet version when validation or smoke verification fails. "
                    "Cooldown and retry rules prevent repeated rotations while a previous rollback or verification is still running. "
                    "Integration tests should cover archive validation, audit persistence, rollback behavior, and failed smoke verification."
                ),
                "acceptance_criteria": [],
                "dependencies": [],
                "edge_cases": [],
                "comments": [],
                "worklogs": [],
                "links": {"confluence_pages": [], "bitbucket_prs": []},
            }

            result = orchestrator.suggest_epic_stories(
                "DEMO-97102",
                project_key="OIC",
                requestor="author@example.com",
            )

            self.assertGreaterEqual(len(result["suggested_stories"]), 1)
            warning_codes = [warning["code"] for warning in result["warnings"]]
            self.assertNotIn("epic_breakdown_codex_context_unavailable", warning_codes)

    def test_suggest_epic_stories_treats_embedded_design_as_evidence(self) -> None:
        class EmptyPlannerWithRichAnalysis:
            def plan_epic_stories(self, epic_key, epic_summary, normalized_epic, evidence, existing_summaries):
                return []

            def analyze_issue(self, normalized_issue, evidence, user_input_text=""):
                return {
                    "problem_statement": f"{normalized_issue['summary']} needs implementation from embedded design context.",
                    "suggested_description": normalized_issue["description"],
                    "acceptance_criteria": normalized_issue["acceptance_criteria"],
                    "guidance": ["Implement the formula and guardrail exactly as specified."],
                    "out_of_scope": ["Unrelated scaling dimensions."],
                    "error_handling": ["Reject invalid backlog metrics and keep the previous safe value."],
                    "nfrs": ["Scaling decisions should complete inside the subscriber polling interval."],
                    "open_questions": [],
                    "recommended_next_step": "Review and approve the embedded-design story.",
                    "risks": ["Incorrect thresholds can over-scale AES concurrency."],
                    "reasoning_mode": "fake_embedded_planner",
                }

        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"}, clear=False):
            orchestrator = build_orchestrator(Path(tmpdir))
            orchestrator.semantic_reasoner = EmptyPlannerWithRichAnalysis()
            orchestrator.evidence_resolver.skip_external_fetch = True
            orchestrator.scanner.jira.issues["DEMO-97103"] = {
                "key": "DEMO-97103",
                "summary": "OQS backlog scaling epic",
                "description": (
                    "Problem Description\n"
                    "Current subscription count scaling does not handle one subscription with high OQS backlog.\n\n"
                    "Solution Design\n"
                    "Scaling Rule for Connection in OQS Client: if AESClient execution avg < 1000ms and "
                    "(BCSn / 500) > 1, calculate RequiredConnections = min(BCSn / 500, 15). "
                    "Subscribe the heavy subscription across the lowest loaded connections or create connections up to that value. "
                    "Scaling Rule for AES Client: for every 200 subscriptions create 1 connection, max 5. "
                    "SemaphoreCount = min(BCSn / 500, 15) * 10. "
                    "Post-scale Guardrail: if AESClient avg_exec_time >= 1000ms, rollback to LastSafeSemaphore, throttle OQS intake, "
                    "and enforce no scaling up for 4 minutes. Never kill connections aggressively."
                ),
                "acceptance_criteria": [],
                "dependencies": [],
                "edge_cases": [],
                "comments": [],
                "worklogs": [],
                "links": {"confluence_pages": [], "bitbucket_prs": []},
            }

            result = orchestrator.suggest_epic_stories(
                "DEMO-97103",
                project_key="OIC",
                requestor="author@example.com",
            )

            self.assertIn("DEMO-97103:description", result["evidence_refs"])
            self.assertGreaterEqual(len(result["suggested_stories"]), 1)
            self.assertTrue(all(item["confidence"] >= 9 for item in result["suggested_stories"]))
            self.assertTrue(all(item["meets_threshold"] for item in result["suggested_stories"]))

    def test_create_epic_stories_passes_assignee_epic_and_story_points(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"}, clear=False):
            orchestrator = build_orchestrator(Path(tmpdir))
            create_calls: list[dict] = []

            def fake_create_issue(**kwargs):
                create_calls.append(kwargs)
                return {"jira_update_id": "mock-create-demo-123", "issue": {"key": "DEMO-123"}}

            orchestrator.scanner.jira.create_issue = fake_create_issue
            result = orchestrator.create_epic_stories_on_approval(
                "DEMO-999",
                project_key="OIC",
                requestor="author@example.com",
                stories=[
                    {
                        "summary": "Created story",
                        "description": "Created story description",
                        "story_points": 5,
                        "confidence": 9,
                        "approved": True,
                    }
                ],
            )

            self.assertEqual(result["created_count"], 1)
            self.assertEqual(len(create_calls), 1)
            self.assertEqual(
                create_calls[0]["additional_fields"],
                {
                    "epic": "DEMO-999",
                    "assignee": "author@example.com",
                    "story_points": 5,
                    "acceptance_criteria": [],
                },
            )

    def test_create_epic_stories_reports_per_story_create_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"}, clear=False):
            orchestrator = build_orchestrator(Path(tmpdir))

            def fake_create_issue(**kwargs):
                raise RuntimeError("POST /rest/api/2/issue timed out after 20s waiting for response")

            orchestrator.scanner.jira.create_issue = fake_create_issue
            result = orchestrator.create_epic_stories_on_approval(
                "DEMO-999",
                project_key="OIC",
                requestor="author@example.com",
                stories=[
                    {
                        "summary": "Timeout story",
                        "description": "Created story description",
                        "story_points": 5,
                        "confidence": 9,
                        "approved": True,
                    }
                ],
            )

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["created_count"], 0)
            self.assertEqual(result["skipped_count"], 1)
            self.assertEqual(result["skipped"][0]["reason"], "create_failed")
            self.assertIn("timed out", result["skipped"][0]["error"])

    def test_create_epic_stories_skips_existing_summary_and_creates_rest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"}, clear=False):
            orchestrator = build_orchestrator(Path(tmpdir))
            create_calls: list[dict] = []

            def fake_create_issue(**kwargs):
                create_calls.append(kwargs)
                return {"jira_update_id": "mock-create-demo-999", "issue": {"key": "DEMO-999"}}

            orchestrator.scanner.jira.create_issue = fake_create_issue
            result = orchestrator.create_epic_stories_on_approval(
                "DEMO-18000",
                project_key="DEMO",
                requestor="author@example.com",
                stories=[
                    {
                        "summary": "Add AI-owned enrichment summary field writeback",
                        "description": "Duplicate of an existing story.",
                        "story_points": 5,
                        "confidence": 9,
                        "approved": True,
                    },
                    {
                        "summary": "Create missing epic breakdown retry handling",
                        "description": "New story that should be created.",
                        "story_points": 3,
                        "confidence": 9,
                        "approved": True,
                    },
                ],
            )

            self.assertEqual(result["status"], "created")
            self.assertEqual(result["created_count"], 1)
            self.assertEqual(result["skipped_count"], 1)
            self.assertEqual(result["skipped"][0]["reason"], "already_exists")
            self.assertEqual(result["skipped"][0]["existing_issue_key"], "DEMO-18324")
            self.assertEqual(create_calls[0]["summary"], "Create missing epic breakdown retry handling")
            self.assertEqual(result["existing_story_count"], 3)

    def test_create_epic_stories_reports_existing_stories(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"}, clear=False):
            orchestrator = build_orchestrator(Path(tmpdir))
            result = orchestrator.create_epic_stories_on_approval(
                "DEMO-18000",
                project_key="DEMO",
                requestor="author@example.com",
                stories=[],
            )
            self.assertEqual(result["status"], "already_exists")
            self.assertGreater(result["existing_story_count"], 0)
            self.assertTrue(result["existing_stories"])
            self.assertEqual(result["created_count"], 0)


if __name__ == "__main__":
    unittest.main()
