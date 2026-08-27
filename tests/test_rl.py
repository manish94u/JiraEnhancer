from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jira_enhancer.rl.policy_bandit import (
    BanditState,
    ThompsonExploitationBandit,
    ThompsonSamplingBandit,
)
from jira_enhancer.rl.prompt_library import DEFAULT_PROMPTS
from jira_enhancer.rl.service import RlPromptService
from jira_enhancer.rl.schemas import PromptGovernanceContext


def _safe_governance_context() -> PromptGovernanceContext:
    return PromptGovernanceContext(
        has_sufficient_evidence=True,
        context_available=True,
        workflow_status="running",
        write_target="description_top_block",
        allowed_write_targets=("description_top_block",),
        evidence_count=1,
    )


class RlPromptServiceTest(unittest.TestCase):
    def test_select_and_record_outcome_updates_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service = RlPromptService(Path(tmpdir))
            decision = service.select_prompt(
                "run-1",
                "DEMO-18325",
                "Missing acceptance criteria and dependency sequencing details",
                governance_context=_safe_governance_context(),
            )
            self.assertIsNotNone(decision)
            assert decision is not None
            self.assertTrue((Path(tmpdir) / "data" / "decisions" / f"{decision.decision_id}.json").exists())
            governance_events = list((Path(tmpdir) / "data" / "governance_events").glob("*.json"))
            self.assertEqual(len(governance_events), 1)

            from jira_enhancer.rl.schemas import PromptOutcome

            service.record_outcome(
                PromptOutcome(
                    decision_id=decision.decision_id,
                    prompt_id=decision.prompt_id,
                    run_id="run-1",
                    issue_key="DEMO-18325",
                    decision="approve",
                    confidence_before=6,
                    confidence_after=8,
                )
            )
            outcome_path = Path(tmpdir) / "data" / "outcomes" / f"{decision.decision_id}.json"
            self.assertTrue(outcome_path.exists())
            payload = json.loads(outcome_path.read_text(encoding="utf-8"))
            self.assertIn("reward", payload)
            self.assertIn("loss", payload)
            self.assertIn("lag_before", payload)
            self.assertIn("lag_after", payload)
            self.assertIn("kappa_hat", payload)
            self.assertIn("bandit_stats_before", payload)
            self.assertIn("bandit_stats_after", payload)
            self.assertTrue((Path(tmpdir) / "data" / "bandit_state.json").exists())

    def test_migrates_legacy_rl_storage_into_data_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "decisions").mkdir(parents=True, exist_ok=True)
            (root / "outcomes").mkdir(parents=True, exist_ok=True)
            (root / "decisions" / "legacy.json").write_text('{"ok":true}', encoding="utf-8")
            (root / "outcomes" / "legacy.json").write_text('{"ok":true}', encoding="utf-8")
            (root / "bandit_state.json").write_text('{"prompt_stats": {"p1": {"count": 1, "mean_reward": 0.4}}}', encoding="utf-8")

            _ = RlPromptService(root)
            self.assertTrue((root / "data" / "decisions" / "legacy.json").exists())
            self.assertTrue((root / "data" / "outcomes" / "legacy.json").exists())
            self.assertTrue((root / "data" / "bandit_state.json").exists())


class RlPolicyDefaultsTest(unittest.TestCase):
    def test_default_library_has_specialized_bounded_arms(self) -> None:
        prompt_ids = {prompt.prompt_id for prompt in DEFAULT_PROMPTS}
        self.assertGreaterEqual(len(prompt_ids), 9)
        self.assertTrue(
            {
                "failure_recovery_paths",
                "nonfunctional_constraints",
                "interface_data_contracts",
                "operational_readiness",
                "ambiguity_resolution",
                "security_privacy_boundaries",
            }.issubset(prompt_ids)
        )

    def test_default_policy_is_thompson(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service = RlPromptService(Path(tmpdir))
            self.assertEqual(service.policy_name, "thompson")

    def test_decision_contains_selected_prompt_pack_and_policy_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service = RlPromptService(Path(tmpdir), policy_name="thompson")
            decision = service.select_prompt(
                "run-1",
                "DEMO-18325",
                "Missing acceptance criteria and dependency sequencing details",
                governance_context=_safe_governance_context(),
            )
            assert decision is not None
            self.assertIn("selected_prompt_pack", decision.metadata)
            self.assertIn("candidate_ranking", decision.metadata)
            self.assertIn("policy_selection", decision.metadata)
            self.assertIn("governance_filter", decision.metadata)
            governance_filter = decision.metadata["governance_filter"]
            self.assertEqual(governance_filter["predicate_version"], "prompt-governance-v2")
            self.assertIn(decision.prompt_id, governance_filter["safe_prompt_ids"])
            self.assertTrue(
                all(
                    evaluation["allowed"]
                    for evaluation in governance_filter["evaluations"]
                    if evaluation["prompt_id"] == decision.prompt_id
                )
            )

    def test_sparse_evidence_allows_only_low_context_fallback_arm(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service = RlPromptService(Path(tmpdir), policy_name="thompson")
            context = PromptGovernanceContext(
                has_sufficient_evidence=False,
                context_available=True,
                workflow_status="running",
                write_target="description_top_block",
                allowed_write_targets=("description_top_block",),
                evidence_count=0,
            )

            decision = service.select_prompt(
                "run-1",
                "DEMO-18325",
                "Sparse Jira summary without supporting evidence",
                governance_context=context,
            )

            assert decision is not None
            self.assertEqual(decision.prompt_id, "low_external_context")
            governance_filter = decision.metadata["governance_filter"]
            self.assertEqual(governance_filter["safe_prompt_ids"], ["low_external_context"])
            self.assertCountEqual(
                governance_filter["rejected_prompt_ids"],
                [
                    prompt.prompt_id
                    for prompt in DEFAULT_PROMPTS
                    if prompt.prompt_id != "low_external_context"
                ],
            )
            self.assertEqual(set(service.bandit.state.prompt_stats), {"low_external_context"})

    def test_filter_considers_every_arm_before_bandit_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            library_path = root / "prompts.json"
            library_path.write_text(
                json.dumps(
                    [
                        {
                            "prompt_id": f"evidence_arm_{index}",
                            "title": f"Evidence arm {index}",
                            "body": "Acceptance criteria dependencies and implementation details.",
                            "tags": ["quality"],
                        }
                        for index in range(1, 4)
                    ]
                    + [
                        {
                            "prompt_id": "fourth_fallback",
                            "title": "Sparse evidence fallback",
                            "body": "Ask for missing context.",
                            "tags": ["fallback"],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            service = RlPromptService(root / "rl", prompt_library_path=library_path, policy_name="thompson")
            context = PromptGovernanceContext(
                has_sufficient_evidence=False,
                context_available=True,
                workflow_status="running",
                write_target="description_top_block",
                allowed_write_targets=("description_top_block",),
                evidence_count=0,
            )

            with patch.object(service.retriever, "retrieve", wraps=service.retriever.retrieve) as retrieve:
                decision = service.select_prompt(
                    "run-1",
                    "DEMO-18325",
                    "Acceptance criteria dependencies and implementation details",
                    governance_context=context,
                )

            assert decision is not None
            self.assertEqual(retrieve.call_args.kwargs["top_k"], 4)
            self.assertEqual(decision.prompt_id, "fourth_fallback")
            self.assertEqual(
                decision.metadata["governance_filter"]["safe_prompt_ids"],
                ["fourth_fallback"],
            )

    def test_questions_tag_does_not_make_an_arm_a_low_context_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            library_path = root / "prompts.json"
            library_path.write_text(
                json.dumps(
                    [
                        {
                            "prompt_id": "ordinary_questions",
                            "title": "Ask refinement questions",
                            "body": "Ask focused questions before drafting.",
                            "tags": ["questions"],
                            "scope_types": ["issue"],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            service = RlPromptService(root / "rl", prompt_library_path=library_path, policy_name="thompson")
            context = PromptGovernanceContext(
                has_sufficient_evidence=False,
                context_available=True,
                workflow_status="running",
                write_target="description_top_block",
                allowed_write_targets=("description_top_block",),
                evidence_count=0,
            )

            decision = service.select_prompt(
                "run-1",
                "DEMO-18325",
                "Sparse Jira summary",
                governance_context=context,
            )

            self.assertIsNone(decision)
            event_file = next((root / "rl" / "data" / "governance_events").glob("*.json"))
            payload = json.loads(event_file.read_text(encoding="utf-8"))
            self.assertEqual(
                payload["evaluations"][0]["failed_conditions"],
                ["evidence_mode_matches_arm"],
            )

    def test_sufficient_evidence_excludes_fallback_and_bounds_candidate_pool(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service = RlPromptService(Path(tmpdir), policy_name="thompson")
            decision = service.select_prompt(
                "run-1",
                "DEMO-18325",
                "API contract retries capacity authentication logs metrics and rollback",
                governance_context=_safe_governance_context(),
            )

            assert decision is not None
            governance_filter = decision.metadata["governance_filter"]
            self.assertNotIn("low_external_context", governance_filter["governance_safe_prompt_ids"])
            self.assertLessEqual(len(governance_filter["safe_prompt_ids"]), 3)
            fallback_evaluation = next(
                item
                for item in governance_filter["evaluations"]
                if item["prompt_id"] == "low_external_context"
            )
            self.assertEqual(fallback_evaluation["failed_conditions"], ["evidence_mode_matches_arm"])

    def test_excluded_prompt_is_not_repeated_while_other_safe_arms_remain(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service = RlPromptService(Path(tmpdir), policy_name="thompson")
            first = service.select_prompt(
                "run-1",
                "DEMO-18325",
                "API contract retries capacity authentication logs metrics and rollback",
                governance_context=_safe_governance_context(),
            )
            assert first is not None
            second = service.select_prompt(
                "run-1",
                "DEMO-18325",
                "API contract retries capacity authentication logs metrics and rollback",
                governance_context=_safe_governance_context(),
                excluded_prompt_ids=(first.prompt_id,),
            )

            assert second is not None
            self.assertNotEqual(first.prompt_id, second.prompt_id)
            self.assertIn(first.prompt_id, second.metadata["governance_filter"]["excluded_prompt_ids"])

    def test_terminal_workflow_state_blocks_all_arms_without_sampling(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service = RlPromptService(root, policy_name="thompson")
            context = PromptGovernanceContext(
                has_sufficient_evidence=True,
                context_available=True,
                workflow_status="written",
                write_target="description_top_block",
                allowed_write_targets=("description_top_block",),
                evidence_count=2,
            )

            decision = service.select_prompt(
                "run-1",
                "DEMO-18325",
                "Complete issue context",
                governance_context=context,
            )

            self.assertIsNone(decision)
            self.assertEqual(service.bandit.state.prompt_stats, {})
            event_files = list((root / "data" / "governance_events").glob("*.json"))
            self.assertEqual(len(event_files), 1)
            payload = json.loads(event_files[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["safe_prompt_ids"], [])
            self.assertTrue(
                all(
                    evaluation["conditions"]["workflow_state_allowed"] == 0
                    for evaluation in payload["evaluations"]
                )
            )

    def test_invalid_write_target_blocks_all_arms(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service = RlPromptService(Path(tmpdir), policy_name="thompson")
            context = PromptGovernanceContext(
                has_sufficient_evidence=True,
                context_available=True,
                workflow_status="running",
                write_target="status",
                allowed_write_targets=("description_top_block",),
                evidence_count=1,
            )

            decision = service.select_prompt(
                "run-1",
                "DEMO-18325",
                "Complete issue context",
                governance_context=context,
            )

            self.assertIsNone(decision)
            self.assertEqual(service.bandit.state.prompt_stats, {})

    def test_scope_mismatch_rejects_custom_arm(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            library_path = root / "prompts.json"
            library_path.write_text(
                json.dumps(
                    [
                        {
                            "prompt_id": "epic_only",
                            "title": "Epic-only prompt",
                            "body": "Decompose an epic.",
                            "tags": ["planning"],
                            "scope_types": ["epic"],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            service = RlPromptService(root / "rl", prompt_library_path=library_path, policy_name="thompson")
            context = PromptGovernanceContext(
                has_sufficient_evidence=True,
                context_available=True,
                workflow_status="running",
                write_target="description_top_block",
                allowed_write_targets=("description_top_block",),
                evidence_count=1,
                scope_type="issue",
            )

            decision = service.select_prompt(
                "run-1",
                "DEMO-18325",
                "Issue context",
                governance_context=context,
            )

            self.assertIsNone(decision)
            event_files = list((root / "rl" / "data" / "governance_events").glob("*.json"))
            payload = json.loads(event_files[0].read_text(encoding="utf-8"))
            evaluation = payload["evaluations"][0]
            self.assertEqual(evaluation["failed_conditions"], ["scope_allowed"])

    def test_duplicate_prompt_ids_are_rejected_before_sampling(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            library_path = root / "prompts.json"
            library_path.write_text(
                json.dumps(
                    [
                        {"prompt_id": "duplicate", "title": "First", "body": "First body"},
                        {"prompt_id": "duplicate", "title": "Second", "body": "Second body"},
                    ]
                ),
                encoding="utf-8",
            )
            service = RlPromptService(root / "rl", prompt_library_path=library_path, policy_name="thompson")

            with self.assertRaisesRegex(ValueError, "Duplicate prompt_id"):
                service.select_prompt(
                    "run-1",
                    "DEMO-18325",
                    "Issue context",
                    governance_context=_safe_governance_context(),
                )

            self.assertEqual(service.bandit.state.prompt_stats, {})

    def test_thompson_selection_uses_posterior_sample_not_retrieval_score(self) -> None:
        policy = ThompsonSamplingBandit(BanditState())
        with patch(
            "jira_enhancer.rl.policy_bandit.random.betavariate",
            side_effect=[0.10, 0.90],
        ):
            selected = policy.select_with_metadata(
                [("retrieval_favorite", 0.99), ("posterior_favorite", 0.0)]
            )

        self.assertEqual(selected.prompt_id, "posterior_favorite")
        self.assertEqual(selected.sampled_value, 0.90)
        self.assertEqual(selected.policy_metadata["posterior_sample"], 0.90)

    def test_thompson_exploitation_uses_learned_posterior_mean(self) -> None:
        policy = ThompsonExploitationBandit(
            BanditState(
                prompt_stats={
                    "learned_winner": {"alpha": 18.0, "beta": 2.0, "count": 18.0},
                    "retrieval_winner": {"alpha": 6.0, "beta": 4.0, "count": 8.0},
                }
            )
        )

        selected = policy.select_with_metadata(
            [("retrieval_winner", 0.99), ("learned_winner", 0.10)]
        )

        self.assertEqual(selected.prompt_id, "learned_winner")
        self.assertEqual(selected.sampled_value, 0.9)
        self.assertEqual(selected.policy_metadata["mode"], "exploit")
        self.assertEqual(selected.policy_metadata["posterior_mean"], 0.9)


if __name__ == "__main__":
    unittest.main()
