from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jira_enhancer.__main__ import build_parser
from jira_enhancer.api import _html_response, _json_response, make_handler
from jira_enhancer.ui import render_index_page, render_rl_page


class ApiSurfaceTest(unittest.TestCase):
    def test_json_response_ignores_client_disconnect(self) -> None:
        class _BrokenWriter:
            def write(self, data: bytes) -> int:
                raise BrokenPipeError("client disconnected")

        class _FakeHandler:
            def __init__(self) -> None:
                self.wfile = _BrokenWriter()

            def send_response(self, status: int) -> None:
                self.status = status

            def send_header(self, key: str, value: str) -> None:
                return None

            def end_headers(self) -> None:
                return None

        _json_response(_FakeHandler(), 200, {"ok": True})

    def test_html_response_ignores_client_disconnect(self) -> None:
        class _BrokenWriter:
            def write(self, data: bytes) -> int:
                raise BrokenPipeError("client disconnected")

        class _FakeHandler:
            def __init__(self) -> None:
                self.wfile = _BrokenWriter()

            def send_response(self, status: int) -> None:
                self.status = status

            def send_header(self, key: str, value: str) -> None:
                return None

            def end_headers(self) -> None:
                return None

        _html_response(_FakeHandler(), 200, "ok")

    def test_handler_factory_returns_request_handler(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"JIRA_ENHANCER_CONNECTOR_MODE": "mock"}, clear=False):
            handler = make_handler(str(Path(tmpdir)))
            self.assertTrue(hasattr(handler, "do_GET"))
            self.assertTrue(hasattr(handler, "do_POST"))

    def test_cli_parser_understands_run_command(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "--storage-root",
                "./runtime",
                "run",
                "--scope-type",
                "epic",
                "--scope-value",
                "DEMO-18324",
                "--requestor",
                "reviewer@example.org",
            ]
        )
        self.assertEqual(args.command, "run")
        self.assertEqual(args.scope_type, "epic")
        self.assertEqual(args.scope_value, "DEMO-18324")

    def test_cli_parser_understands_codex_orchestrator_command(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "--storage-root",
                "./runtime",
                "serve-codex-orchestrator",
                "--host",
                "127.0.0.1",
                "--port",
                "8090",
            ]
        )
        self.assertEqual(args.command, "serve-codex-orchestrator")
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 8090)

    def test_ui_page_contains_controls(self) -> None:
        html = render_index_page("https://confluence.example.com")
        self.assertIn("Jira Enhancer", html)
        self.assertIn("Create Run", html)
        self.assertIn("Write Back", html)
        self.assertIn("User Input", html)
        self.assertIn("Prompt", html)
        self.assertIn("Generate Prompt", html)
        self.assertIn("Generate Jira-specific Codex input", html)
        self.assertIn("generatedPrompt", html)
        self.assertIn("promptInput", html)
        self.assertIn("Apply to all Jiras in run", html)
        self.assertIn("runWarnings", html)
        self.assertIn("itemWarnings", html)
        self.assertIn("Warnings", html)
        self.assertIn("API_TIMEOUT_MS = 30000", html)
        self.assertIn("Leave it unchecked to target only the selected Jira", html)
        self.assertIn("const applyAll = els.applyAllItems.checked;", html)
        self.assertIn('<option value="prompt">Prompt</option>', html)
        self.assertIn('formaction="/ui/writeback"', html)
        self.assertNotIn("Codex Prompt For Selected Jira", html)
        self.assertNotIn("Interactive Workflow", html)
        self.assertNotIn("interaction-answer", html)
        self.assertIn("authModal", html)
        self.assertIn("Confluence Authentication Required", html)
        self.assertIn('const CONFLUENCE_BASE_URL = "https://confluence.example.com"', html)
        self.assertIn('href="/ui/rl"', html)
        self.assertIn('role="tablist" aria-label="Workflow"', html)
        self.assertIn('role="tab" aria-selected="true" aria-controls="enrichmentFlowPanel"', html)
        self.assertIn('role="tabpanel" aria-labelledby="enrichmentFlowTab"', html)
        self.assertIn('<ol id="enrichWorkflowSteps" class="workflow-steps" aria-label="Enrich workflow steps">', html)
        self.assertIn('aria-current="step"', html)
        self.assertIn('role="status" aria-live="polite" aria-atomic="true"', html)
        self.assertIn('candidate.setAttribute("aria-selected", "false")', html)
        self.assertIn('step.setAttribute("aria-current", "step")', html)

    def test_rl_dashboard_page_renders_stats_and_decisions(self) -> None:
        html = render_rl_page(
            rl_enabled=True,
            policy_name="thompson",
            stats=[
                {"prompt_id": "prompt-risk-assessor", "count": 5, "mean_reward": 0.34, "alpha": 4, "beta": 2},
            ],
            decisions=[
                {
                    "issue_key": "DEMO-18323",
                    "prompt_id": "prompt-older",
                    "selected_at": "2026-04-14T12:00:00Z",
                    "score": 0.75,
                    "metadata": {"selected_prompt_pack": {"rationale": "Older decision"}},
                },
                {
                    "issue_key": "DEMO-18324",
                    "prompt_id": "prompt-risk-assessor",
                    "selected_at": "2026-04-15T12:00:00Z",
                    "score": 0.24806946917841693,
                    "metadata": {"selected_prompt_pack": {"rationale": "Selected by thompson"}},
                }
            ],
        )
        self.assertIn("RL Dashboard", html)
        self.assertIn("Prompt Statistics (Beta Distribution)", html)
        self.assertIn("Beta Distribution Graph", html)
        self.assertIn("Recent Prompt Decisions", html)
        self.assertIn("prompt-risk-assessor", html)
        self.assertIn("Selected by thompson", html)
        self.assertIn("E[p]=", html)
        self.assertIn("Beta distribution bell curve", html)
        self.assertIn(">0.248</td>", html)
        self.assertNotIn(">0.24806946917841693</td>", html)
        self.assertIn("Apr 15, 2026", html)
        self.assertNotIn(">2026-04-15T12:00:00Z</td>", html)
        self.assertLess(html.index("DEMO-18324"), html.index("DEMO-18323"))

    def test_rl_dashboard_infers_stats_from_decisions_when_stats_empty(self) -> None:
        html = render_rl_page(
            rl_enabled=True,
            policy_name="thompson",
            stats=[],
            decisions=[
                {
                    "issue_key": "DEMO-18324",
                    "prompt_id": "prompt-risk-assessor",
                    "selected_at": "2026-04-15T12:00:00Z",
                    "score": 0.91,
                    "metadata": {
                        "candidate_beta_distribution": [
                            {"prompt_id": "prompt-risk-assessor", "alpha": 4, "beta": 2},
                            {"prompt_id": "prompt-ac-gap", "alpha": 2, "beta": 3},
                        ],
                        "selected_prompt_pack": {"rationale": "Selected by thompson"},
                    },
                }
            ],
        )
        self.assertNotIn("No prompt stats yet.", html)
        self.assertIn("prompt-risk-assessor", html)
        self.assertIn("prompt-ac-gap", html)

    def test_ui_page_renders_rl_prompt_statistics(self) -> None:
        html = render_index_page(
            "https://confluence.example.com",
            initial_item={
                "issue_key": "DEMO-18324",
                "rl_prompt_decision": {
                    "prompt_id": "prompt-risk-assessor",
                    "metadata": {
                        "selected_prompt_pack": {
                            "title": "Risk-Assessor Prompt",
                            "rationale": "Selected by thompson",
                        },
                        "candidate_ranking": [
                            {"rank": 1, "prompt_id": "prompt-risk-assessor", "score": 0.91},
                            {"rank": 2, "prompt_id": "prompt-ac-gap", "score": 0.73},
                        ],
                        "candidate_beta_distribution": [
                            {"prompt_id": "prompt-risk-assessor", "alpha": 3.0, "beta": 2.0, "expected": 0.6},
                            {"prompt_id": "prompt-ac-gap", "alpha": 2.0, "beta": 3.0, "expected": 0.4},
                        ],
                        "confidence_trajectory": [
                            {"attempt": 1, "confidence": 7, "lag": 2},
                            {"attempt": 2, "confidence": 9, "lag": 0},
                        ],
                    },
                },
                "draft": {"payload": {}},
            },
        )
        self.assertIn("RL Prompt Selected", html)
        self.assertIn("RL Candidate Ranking", html)
        self.assertIn("RL Beta Distribution", html)
        self.assertIn("RL Beta Graph", html)
        self.assertIn("RL Confidence Trajectory", html)
        self.assertIn("function renderEpicFailureStatus(message, storiesToPreserve = null)", html)
        self.assertIn("renderEpicFailureStatus(error.message, stories)", html)
        self.assertIn("The selected stories are still loaded", html)

    def test_generated_prompt_does_not_prefill_user_input_or_prompt(self) -> None:
        html = render_index_page(
            "https://confluence.example.com",
            initial_item={
                "issue_key": "DEMO-94831",
                "draft": {
                    "payload": {
                        "codex_user_input_prompt": "Refine the acceptance criteria for DEMO-94831.",
                    }
                },
            },
        )
        self.assertIn(
            '<textarea id="generatedPrompt" class="hidden">Refine the acceptance criteria for DEMO-94831.</textarea>',
            html,
        )
        self.assertIn(
            '<textarea id="userInput" name="userInput" placeholder="Provide detailed user input. You can paste Confluence page links/ids and Bitbucket PR links/refs here. For epic runs, this can be applied to every Jira in the run."></textarea>',
            html,
        )
        self.assertIn(
            '<textarea id="promptInput" name="promptInput" placeholder="Generate or enter a Jira-specific prompt here. Submit Decision with `Prompt` selected to execute it."></textarea>',
            html,
        )

    def test_ui_does_not_double_bind_inline_control_handlers(self) -> None:
        html = render_index_page("https://confluence.example.com")
        self.assertNotIn('document.getElementById("createRunBtn").addEventListener("click", createRun);', html)
        self.assertNotIn('document.getElementById("refreshRunBtn").addEventListener("click", refreshRun);', html)
        self.assertNotIn('document.getElementById("approveBtn").addEventListener("click", submitDecision);', html)
        self.assertNotIn('document.getElementById("writebackBtn").addEventListener("click", writeback);', html)
        self.assertNotIn('document.getElementById("replayBtn").addEventListener("click", replayRun);', html)
        self.assertNotIn('document.getElementById("itemSelect").addEventListener("change", handleItemSelectionChange);', html)
        self.assertNotIn('els.generatePromptBtn.addEventListener("click", generatePromptFromSelection);', html)

    def test_ui_guards_enrich_item_loading_against_story_indexes(self) -> None:
        html = render_index_page("https://confluence.example.com")
        self.assertIn("function looksLikeJiraKey(value)", html)
        self.assertIn("function currentEnrichIssueKey()", html)
        self.assertIn('if (!looksLikeJiraKey(els.itemSelect.value))', html)
        self.assertNotIn("els.issueKey.value = els.itemSelect.value.trim();\n          await loadItem();", html)

    def test_ui_forces_enrich_panel_rerender_after_epic_tab(self) -> None:
        html = render_index_page("https://confluence.example.com")
        self.assertIn('lastRenderedRunSignature = "";\n            lastRenderedItemSignature = "";', html)


if __name__ == "__main__":
    unittest.main()
