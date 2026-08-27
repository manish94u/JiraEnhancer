from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class RunRecord:
    run_id: str
    scope_type: str
    scope_value: str
    requestor: str
    status: str
    created_at: str
    updated_at: str
    idempotency_key: str
    items: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunItemRecord:
    run_id: str
    issue_key: str
    jira_summary: str
    status: str
    source_hash: str
    readiness_score: float
    confidence: float
    policy_flags: list[str]
    approval_required: bool
    created_at: str
    updated_at: str
    source_revision: str = ""
    warnings: list[dict[str, str]] = field(default_factory=list)
    agent_state: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceRecord:
    run_id: str
    issue_key: str
    source_type: str
    source_ref: str
    relevance_score: float
    selected_for_draft: bool
    content: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DraftRecord:
    run_id: str
    issue_key: str
    draft_version: int
    confidence: float
    status: str
    payload: dict[str, Any]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ApprovalRecord:
    run_id: str
    issue_key: str
    reviewer: str
    decision: str
    reviewer_notes: str
    user_input: dict[str, Any]
    reviewed_at: str
    causal_envelope: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WritebackRecord:
    run_id: str
    issue_key: str
    status: str
    jira_update_id: str
    write_targets: dict[str, Any]
    before_hash: str
    after_hash: str
    written_at: str
    causal_transaction_id: str = ""
    idempotency_key: str = ""
    replayed: bool = False
    reason: str = ""
    validation_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
