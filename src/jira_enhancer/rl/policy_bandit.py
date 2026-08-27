from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class BanditState:
    prompt_stats: dict[str, dict[str, float]] = field(default_factory=dict)
    epsilon: float = 0.15


@dataclass(frozen=True)
class PolicySelection:
    prompt_id: str
    retrieval_score: float
    sampled_value: float
    policy_metadata: dict[str, float | str]


class PromptPolicy(Protocol):
    state: BanditState

    def select(self, candidates: list[tuple[str, float]]) -> tuple[str, float]:
        ...

    def select_with_metadata(self, candidates: list[tuple[str, float]]) -> PolicySelection:
        ...

    def update(self, prompt_id: str, reward: float) -> None:
        ...


class EpsilonGreedyBandit:
    def __init__(self, state: BanditState | None = None) -> None:
        self.state = state or BanditState()

    def select(self, candidates: list[tuple[str, float]]) -> tuple[str, float]:
        selected = self.select_with_metadata(candidates)
        return selected.prompt_id, selected.retrieval_score

    def select_with_metadata(self, candidates: list[tuple[str, float]]) -> PolicySelection:
        if not candidates:
            raise ValueError("No prompt candidates provided")
        roll = random.random()
        if roll < self.state.epsilon:
            chosen_prompt_id, chosen_score = random.choice(candidates)
            return PolicySelection(
                prompt_id=chosen_prompt_id,
                retrieval_score=chosen_score,
                sampled_value=self._value(chosen_prompt_id) + chosen_score,
                policy_metadata={
                    "policy": "epsilon_greedy",
                    "mode": "explore",
                    "epsilon": float(self.state.epsilon),
                    "roll": roll,
                },
            )
        best_prompt = candidates[0]
        best_value = self._value(best_prompt[0]) + best_prompt[1]
        for prompt_id, score in candidates[1:]:
            value = self._value(prompt_id) + score
            if value > best_value:
                best_prompt = (prompt_id, score)
                best_value = value
        return PolicySelection(
            prompt_id=best_prompt[0],
            retrieval_score=best_prompt[1],
            sampled_value=best_value,
            policy_metadata={
                "policy": "epsilon_greedy",
                "mode": "exploit",
                "epsilon": float(self.state.epsilon),
                "roll": roll,
            },
        )

    def update(self, prompt_id: str, reward: float) -> None:
        entry = self.state.prompt_stats.setdefault(prompt_id, {"count": 0.0, "mean_reward": 0.0})
        entry["count"] += 1.0
        count = entry["count"]
        previous = entry["mean_reward"]
        entry["mean_reward"] = previous + ((reward - previous) / count)

    def _value(self, prompt_id: str) -> float:
        entry = self.state.prompt_stats.get(prompt_id)
        if not entry:
            return 0.0
        return float(entry.get("mean_reward", 0.0))


class ThompsonSamplingBandit:
    def __init__(self, state: BanditState | None = None) -> None:
        self.state = state or BanditState()

    def select(self, candidates: list[tuple[str, float]]) -> tuple[str, float]:
        selected = self.select_with_metadata(candidates)
        return selected.prompt_id, selected.retrieval_score

    def select_with_metadata(self, candidates: list[tuple[str, float]]) -> PolicySelection:
        if not candidates:
            raise ValueError("No prompt candidates provided")
        best = candidates[0]
        best_value = self._sample(best[0])
        for prompt_id, retrieval_score in candidates[1:]:
            sample = self._sample(prompt_id)
            if sample > best_value:
                best = (prompt_id, retrieval_score)
                best_value = sample
        entry = self.state.prompt_stats.setdefault(
            best[0], {"alpha": 1.0, "beta": 1.0, "count": 0.0, "mean_reward": 0.0}
        )
        return PolicySelection(
            prompt_id=best[0],
            retrieval_score=best[1],
            sampled_value=best_value,
            policy_metadata={
                "policy": "thompson",
                "alpha": float(entry.get("alpha", 1.0)),
                "beta": float(entry.get("beta", 1.0)),
                "posterior_sample": float(best_value),
            },
        )

    def update(self, prompt_id: str, reward: float) -> None:
        bounded = max(-1.0, min(1.0, reward))
        success = (bounded + 1.0) / 2.0
        entry = self.state.prompt_stats.setdefault(prompt_id, {"alpha": 1.0, "beta": 1.0, "count": 0.0, "mean_reward": 0.0})
        entry["alpha"] = float(entry.get("alpha", 1.0)) + success
        entry["beta"] = float(entry.get("beta", 1.0)) + (1.0 - success)
        entry["count"] = float(entry.get("count", 0.0)) + 1.0
        count = entry["count"]
        prev = float(entry.get("mean_reward", 0.0))
        entry["mean_reward"] = prev + ((bounded - prev) / count)

    def _sample(self, prompt_id: str) -> float:
        entry = self.state.prompt_stats.setdefault(prompt_id, {"alpha": 1.0, "beta": 1.0, "count": 0.0, "mean_reward": 0.0})
        alpha = float(entry.get("alpha", 1.0))
        beta = float(entry.get("beta", 1.0))
        return random.betavariate(alpha, beta)


class ThompsonExploitationBandit(ThompsonSamplingBandit):
    """Select the highest learned posterior mean without exploratory sampling."""

    def select_with_metadata(self, candidates: list[tuple[str, float]]) -> PolicySelection:
        if not candidates:
            raise ValueError("No prompt candidates provided")

        def posterior_mean(prompt_id: str) -> float:
            entry = self.state.prompt_stats.get(prompt_id, {})
            alpha = float(entry.get("alpha", 1.0))
            beta = float(entry.get("beta", 1.0))
            return alpha / (alpha + beta) if alpha + beta > 0 else 0.5

        prompt_id, retrieval_score = max(
            candidates,
            key=lambda candidate: (posterior_mean(candidate[0]), candidate[1]),
        )
        entry = self.state.prompt_stats.setdefault(
            prompt_id,
            {"alpha": 1.0, "beta": 1.0, "count": 0.0, "mean_reward": 0.0},
        )
        expected = posterior_mean(prompt_id)
        return PolicySelection(
            prompt_id=prompt_id,
            retrieval_score=retrieval_score,
            sampled_value=expected,
            policy_metadata={
                "policy": "thompson_exploit",
                "mode": "exploit",
                "alpha": float(entry.get("alpha", 1.0)),
                "beta": float(entry.get("beta", 1.0)),
                "posterior_mean": expected,
            },
        )


def build_policy(policy_name: str, state: BanditState) -> PromptPolicy:
    if policy_name == "thompson":
        return ThompsonSamplingBandit(state)
    if policy_name == "thompson_exploit":
        return ThompsonExploitationBandit(state)
    return EpsilonGreedyBandit(state)
