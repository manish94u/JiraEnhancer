from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PromptTemplate:
    prompt_id: str
    title: str
    body: str
    tags: tuple[str, ...] = ()
    scope_types: tuple[str, ...] = ()
    version: int = 1


@dataclass(frozen=True)
class PromptGovernanceContext:
    """Runtime state used to decide which prompt arms are safe to sample."""

    has_sufficient_evidence: bool
    context_available: bool
    workflow_status: str
    write_target: str
    allowed_write_targets: tuple[str, ...]
    evidence_count: int = 0
    scope_type: str = "issue"


@dataclass(frozen=True)
class PromptDecision:
    decision_id: str
    prompt_id: str
    issue_key: str
    run_id: str
    score: float
    context_summary: str
    selected_at: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PromptOutcome:
    decision_id: str
    prompt_id: str
    run_id: str
    issue_key: str
    decision: str
    confidence_before: int
    confidence_after: int
    writeback_status: str = ""
    reward: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
