from __future__ import annotations

import json
import math
import re
from pathlib import Path
from uuid import uuid4

from jira_enhancer.models import utc_now

from .policy_bandit import BanditState, PolicySelection, build_policy
from .prompt_library import PromptLibrary
from .retriever import PromptRetriever
from .schemas import PromptDecision, PromptGovernanceContext, PromptOutcome, PromptTemplate


_ALLOWED_PROMPT_WORKFLOW_STATES = frozenset({"queued", "running", "refining", "awaiting_input"})
_LOW_CONTEXT_ARM_TAGS = frozenset({"fallback", "low_context_fallback"})
_MAX_SAFE_PROMPT_CANDIDATES = 3
_ISSUE_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")


def _reward_from_outcome(outcome: PromptOutcome) -> float:
    reward = (outcome.confidence_after - outcome.confidence_before) / 10.0
    if outcome.decision == "approve":
        reward += 0.2
    if outcome.decision in {"reject", "regenerate"}:
        reward -= 0.2
    if outcome.writeback_status == "written":
        reward += 0.1
    if outcome.writeback_status == "conflict":
        reward -= 0.1
    return max(-1.0, min(1.0, reward))


def _target_confidence(outcome: PromptOutcome) -> int:
    target = outcome.metadata.get("target_confidence", 9) if isinstance(outcome.metadata, dict) else 9
    try:
        return max(1, min(10, int(target)))
    except (TypeError, ValueError):
        return 9


def _loss_metrics(outcome: PromptOutcome) -> dict[str, float]:
    target = float(_target_confidence(outcome))
    lag_before = max(0.0, target - float(outcome.confidence_before))
    lag_after = max(0.0, target - float(outcome.confidence_after))
    epsilon = 1e-6
    kappa_hat = max(0.0, math.log((lag_before + epsilon) / (lag_after + epsilon)))
    return {
        "target_confidence": target,
        "lag_before": lag_before,
        "lag_after": lag_after,
        "loss_before": lag_before / target,
        "loss_after": lag_after / target,
        "loss_delta": (lag_before - lag_after) / target,
        "kappa_hat": kappa_hat,
    }


class RlPromptService:
    def __init__(self, root: Path, prompt_library_path: Path | None = None, policy_name: str = "thompson") -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.data_root = self.root / "data"
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.decisions_dir = self.data_root / "decisions"
        self.outcomes_dir = self.data_root / "outcomes"
        self.governance_events_dir = self.data_root / "governance_events"
        self.decisions_dir.mkdir(parents=True, exist_ok=True)
        self.outcomes_dir.mkdir(parents=True, exist_ok=True)
        self.governance_events_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.data_root / "bandit_state.json"
        self._migrate_legacy_storage_if_needed()
        self.library = PromptLibrary(prompt_library_path)
        self.retriever = PromptRetriever(self.library)
        self.policy_name = policy_name
        self.bandit = build_policy(self._normalize_policy_name(policy_name), self._load_state())

    def select_prompt(
        self,
        run_id: str,
        issue_key: str,
        context_text: str,
        *,
        governance_context: PromptGovernanceContext,
        excluded_prompt_ids: tuple[str, ...] = (),
    ) -> PromptDecision | None:
        prompt_count = max(1, len(self.library.load()))
        retrieved = self.retriever.retrieve(context_text, top_k=prompt_count)
        if not retrieved:
            return None

        evaluations = [
            self._evaluate_governance_conditions(prompt, issue_key, governance_context)
            for prompt, _ in retrieved
        ]
        evaluation_by_prompt = {entry["prompt_id"]: entry for entry in evaluations}
        governance_safe_retrieved = [
            (prompt, score)
            for prompt, score in retrieved
            if bool(evaluation_by_prompt[prompt.prompt_id]["allowed"])
        ]
        normalized_exclusions = {prompt_id.strip() for prompt_id in excluded_prompt_ids if prompt_id.strip()}
        untried_safe_retrieved = [
            (prompt, score)
            for prompt, score in governance_safe_retrieved
            if prompt.prompt_id not in normalized_exclusions
        ]
        exclusion_cycle_reset = bool(
            governance_safe_retrieved and normalized_exclusions and not untried_safe_retrieved
        )
        candidate_pool = governance_safe_retrieved if exclusion_cycle_reset else untried_safe_retrieved
        safe_retrieved = candidate_pool[:_MAX_SAFE_PROMPT_CANDIDATES]
        governance_event_id = f"rl-governance-{uuid4().hex[:10]}"
        safe_prompt_ids = [prompt.prompt_id for prompt, _ in safe_retrieved]
        governance_safe_prompt_ids = [prompt.prompt_id for prompt, _ in governance_safe_retrieved]
        governance_filter = {
            "event_id": governance_event_id,
            "mode": "explicit",
            "predicate_version": "prompt-governance-v2",
            "context": self._governance_context_payload(governance_context),
            "condition_count": len(evaluations[0]["conditions"]) if evaluations else 0,
            "evaluations": evaluations,
            "max_safe_candidates": _MAX_SAFE_PROMPT_CANDIDATES,
            "governance_safe_prompt_ids": governance_safe_prompt_ids,
            "safe_prompt_ids": safe_prompt_ids,
            "excluded_prompt_ids": sorted(normalized_exclusions),
            "exclusion_cycle_reset": exclusion_cycle_reset,
            "deferred_prompt_ids": [
                prompt.prompt_id
                for prompt, _ in governance_safe_retrieved
                if prompt.prompt_id not in safe_prompt_ids
            ],
            "rejected_prompt_ids": [
                prompt.prompt_id
                for prompt, _ in retrieved
                if not bool(evaluation_by_prompt[prompt.prompt_id]["allowed"])
            ],
        }
        if not safe_retrieved:
            self._save_governance_event(
                governance_filter,
                run_id=run_id,
                issue_key=issue_key,
                selected_prompt_id="",
                decision_id="",
            )
            return None

        candidates = [(prompt.prompt_id, score) for prompt, score in safe_retrieved]
        selected: PolicySelection = self.bandit.select_with_metadata(candidates)
        prompt_id, score = selected.prompt_id, selected.retrieval_score
        selected_prompt = next(
            (prompt for prompt, _ in safe_retrieved if prompt.prompt_id == prompt_id),
            safe_retrieved[0][0],
        )
        candidate_beta_distribution = []
        for prompt, _ in safe_retrieved:
            stats = self.bandit.state.prompt_stats.get(prompt.prompt_id, {})
            alpha = float(stats.get("alpha", 1.0))
            beta = float(stats.get("beta", 1.0))
            expected = alpha / (alpha + beta) if (alpha + beta) > 0 else 0.5
            candidate_beta_distribution.append(
                {
                    "prompt_id": prompt.prompt_id,
                    "alpha": alpha,
                    "beta": beta,
                    "expected": round(expected, 4),
                }
            )
        retrieved_candidates = [
            {
                "rank": index,
                "prompt_id": prompt.prompt_id,
                "title": prompt.title,
                "score": float(candidate_score),
            }
            for index, (prompt, candidate_score) in enumerate(retrieved, start=1)
        ]
        safe_ranked_candidates = [
            {**entry, "rank": safe_rank, "retrieval_rank": entry["rank"]}
            for safe_rank, entry in enumerate(
                (
                    entry
                    for entry in retrieved_candidates
                    if entry["prompt_id"] in safe_prompt_ids
                ),
                start=1,
            )
        ]
        decision = PromptDecision(
            decision_id=f"rl-decision-{uuid4().hex[:10]}",
            prompt_id=prompt_id,
            run_id=run_id,
            issue_key=issue_key,
            score=float(score),
            context_summary=context_text[:280],
            selected_at=utc_now(),
            metadata={
                "policy": self.policy_name,
                "policy_selection": selected.policy_metadata,
                "sampled_value": float(selected.sampled_value),
                "prompt_title": selected_prompt.title,
                "prompt_body": selected_prompt.body,
                "selected_prompt_pack": {
                    "prompt_id": selected_prompt.prompt_id,
                    "title": selected_prompt.title,
                    "body": selected_prompt.body,
                    "tags": list(selected_prompt.tags),
                    "version": selected_prompt.version,
                    "rationale": (
                        f"Selected from the governance-safe arms by {self.policy_name} "
                        f"with sampled score {round(selected.sampled_value, 4)}"
                    ),
                },
                "candidate_beta_distribution": candidate_beta_distribution,
                "candidate_ranking": safe_ranked_candidates,
                "retrieved_candidate_ranking": retrieved_candidates,
                "candidates": [{"prompt_id": pid, "score": s} for pid, s in candidates],
                "governance_filter": governance_filter,
            },
        )
        self._save_json(self.decisions_dir / f"{decision.decision_id}.json", {
            "decision_id": decision.decision_id,
            "prompt_id": decision.prompt_id,
            "run_id": decision.run_id,
            "issue_key": decision.issue_key,
            "score": decision.score,
            "context_summary": decision.context_summary,
            "selected_at": decision.selected_at,
            "metadata": decision.metadata,
        })
        self._save_governance_event(
            governance_filter,
            run_id=run_id,
            issue_key=issue_key,
            selected_prompt_id=decision.prompt_id,
            decision_id=decision.decision_id,
        )
        return decision

    @staticmethod
    def _evaluate_governance_conditions(
        prompt: PromptTemplate,
        issue_key: str,
        context: PromptGovernanceContext,
    ) -> dict[str, object]:
        normalized_tags = {tag.strip().lower() for tag in prompt.tags}
        is_low_context_arm = (
            prompt.prompt_id == "low_external_context"
            or bool(normalized_tags & _LOW_CONTEXT_ARM_TAGS)
        )
        allowed_scope_types = {scope.strip().lower() for scope in prompt.scope_types if scope.strip()}
        conditions = {
            "context_available": context.context_available,
            "evidence_mode_matches_arm": (
                (context.has_sufficient_evidence and not is_low_context_arm)
                or (not context.has_sufficient_evidence and is_low_context_arm)
            ),
            "workflow_state_allowed": context.workflow_status.strip().lower() in _ALLOWED_PROMPT_WORKFLOW_STATES,
            "write_target_valid": (
                bool(context.write_target.strip())
                and context.write_target in context.allowed_write_targets
                and bool(_ISSUE_KEY_PATTERN.fullmatch(issue_key.strip()))
            ),
            "scope_allowed": (
                not allowed_scope_types
                or context.scope_type.strip().lower() in allowed_scope_types
            ),
        }
        failed_conditions = [name for name, passed in conditions.items() if not passed]
        return {
            "prompt_id": prompt.prompt_id,
            "allowed": not failed_conditions,
            "conditions": {name: int(passed) for name, passed in conditions.items()},
            "failed_conditions": failed_conditions,
        }

    @staticmethod
    def _governance_context_payload(context: PromptGovernanceContext) -> dict[str, object]:
        return {
            "has_sufficient_evidence": context.has_sufficient_evidence,
            "context_available": context.context_available,
            "workflow_status": context.workflow_status,
            "write_target": context.write_target,
            "allowed_write_targets": list(context.allowed_write_targets),
            "evidence_count": context.evidence_count,
            "scope_type": context.scope_type,
        }

    def _save_governance_event(
        self,
        governance_filter: dict[str, object],
        *,
        run_id: str,
        issue_key: str,
        selected_prompt_id: str,
        decision_id: str,
    ) -> None:
        payload = {
            **governance_filter,
            "run_id": run_id,
            "issue_key": issue_key,
            "selected_prompt_id": selected_prompt_id,
            "decision_id": decision_id,
            "evaluated_at": utc_now(),
        }
        event_id = str(governance_filter["event_id"])
        self._save_json(self.governance_events_dir / f"{event_id}.json", payload)

    def record_outcome(self, outcome: PromptOutcome) -> None:
        reward = outcome.reward if outcome.reward else _reward_from_outcome(outcome)
        bounded_reward = max(-1.0, min(1.0, reward))
        loss_metrics = _loss_metrics(outcome)
        pre_update_stats = self._prompt_stats_entry(outcome.prompt_id)
        self.bandit.update(outcome.prompt_id, reward)
        post_update_stats = self._prompt_stats_entry(outcome.prompt_id)
        self._save_state()
        payload = {
            "decision_id": outcome.decision_id,
            "prompt_id": outcome.prompt_id,
            "run_id": outcome.run_id,
            "issue_key": outcome.issue_key,
            "decision": outcome.decision,
            "confidence_before": outcome.confidence_before,
            "confidence_after": outcome.confidence_after,
            "confidence_delta": outcome.confidence_after - outcome.confidence_before,
            "writeback_status": outcome.writeback_status,
            "reward": bounded_reward,
            "loss": round(loss_metrics["loss_after"], 6),
            "loss_before": round(loss_metrics["loss_before"], 6),
            "loss_delta": round(loss_metrics["loss_delta"], 6),
            "lag_before": round(loss_metrics["lag_before"], 6),
            "lag_after": round(loss_metrics["lag_after"], 6),
            "target_confidence": int(loss_metrics["target_confidence"]),
            "kappa_hat": round(loss_metrics["kappa_hat"], 6),
            "reward_components": {
                "confidence_delta": round((outcome.confidence_after - outcome.confidence_before) / 10.0, 4),
                "decision": outcome.decision,
                "writeback_status": outcome.writeback_status,
                "decision_bonus": 0.2 if outcome.decision == "approve" else (-0.2 if outcome.decision in {"reject", "regenerate"} else 0.0),
                "writeback_bonus": 0.1 if outcome.writeback_status == "written" else (-0.1 if outcome.writeback_status == "conflict" else 0.0),
            },
            "bandit_stats_before": pre_update_stats,
            "bandit_stats_after": post_update_stats,
            "metadata": outcome.metadata,
            "recorded_at": utc_now(),
        }
        self._save_json(self.outcomes_dir / f"{outcome.decision_id}.json", payload)

    def _load_state(self) -> BanditState:
        if not self.state_path.exists():
            return BanditState()
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        return BanditState(
            prompt_stats=dict(payload.get("prompt_stats", {})),
            epsilon=float(payload.get("epsilon", 0.15)),
        )

    def _save_state(self) -> None:
        payload = {
            "prompt_stats": self.bandit.state.prompt_stats,
            "epsilon": self.bandit.state.epsilon,
            "policy": self.policy_name,
        }
        self._save_json(self.state_path, payload)

    def get_prompt_stats_snapshot(self, top_k: int = 5) -> list[dict[str, float | str]]:
        ranked = sorted(
            self.bandit.state.prompt_stats.items(),
            key=lambda item: float(item[1].get("count", 0.0)),
            reverse=True,
        )
        snapshot: list[dict[str, float | str]] = []
        for prompt_id, stats in ranked[: max(1, top_k)]:
            snapshot.append(
                {
                    "prompt_id": prompt_id,
                    "count": float(stats.get("count", 0.0)),
                    "mean_reward": float(stats.get("mean_reward", 0.0)),
                    "alpha": float(stats.get("alpha", 1.0)),
                    "beta": float(stats.get("beta", 1.0)),
                }
            )
        return snapshot

    def _prompt_stats_entry(self, prompt_id: str) -> dict[str, float]:
        stats = self.bandit.state.prompt_stats.get(prompt_id, {})
        return {
            "count": float(stats.get("count", 0.0)),
            "mean_reward": float(stats.get("mean_reward", 0.0)),
            "alpha": float(stats.get("alpha", 1.0)),
            "beta": float(stats.get("beta", 1.0)),
        }

    def _migrate_legacy_storage_if_needed(self) -> None:
        legacy_state = self.root / "bandit_state.json"
        legacy_decisions = self.root / "decisions"
        legacy_outcomes = self.root / "outcomes"

        if legacy_state.exists() and not self.state_path.exists():
            self.state_path.write_text(legacy_state.read_text(encoding="utf-8"), encoding="utf-8")

        if legacy_decisions.exists():
            for legacy_file in legacy_decisions.glob("*.json"):
                target = self.decisions_dir / legacy_file.name
                if not target.exists():
                    target.write_text(legacy_file.read_text(encoding="utf-8"), encoding="utf-8")

        if legacy_outcomes.exists():
            for legacy_file in legacy_outcomes.glob("*.json"):
                target = self.outcomes_dir / legacy_file.name
                if not target.exists():
                    target.write_text(legacy_file.read_text(encoding="utf-8"), encoding="utf-8")

    @staticmethod
    def _normalize_policy_name(name: str) -> str:
        if name in {"thompson_exploit", "posterior_mean", "exploit"}:
            return "thompson_exploit"
        if name in {"thompson", "thompson_sampling"}:
            return "thompson"
        return "epsilon_greedy"

    @staticmethod
    def _save_json(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
