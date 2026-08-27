from __future__ import annotations

from typing import Any

from .utils import stable_hash


CAUSAL_ENVELOPE_VERSION = "source-bound-causal-v1"
DEFAULT_POLICY_VERSION = "jira-writeback-policy-v1"


def source_revision(issue: dict[str, Any]) -> str:
    """Return the strongest revision marker exposed by the Jira connector."""
    for key in ("source_revision", "revision", "_revision", "version", "updated_at", "updated"):
        value = issue.get(key)
        if value not in (None, ""):
            return str(value)
    fields = issue.get("fields")
    if isinstance(fields, dict):
        for key in ("updated", "version"):
            value = fields.get(key)
            if value not in (None, ""):
                return str(value)
    return ""


def _canonical_targets(values: list[str] | tuple[str, ...]) -> list[str]:
    return sorted({str(value).strip() for value in values if str(value).strip()})


def _envelope_core(envelope: dict[str, Any]) -> dict[str, Any]:
    return {
        "envelope_version": envelope.get("envelope_version", ""),
        "run_id": envelope.get("run_id", ""),
        "issue_key": envelope.get("issue_key", ""),
        "source_hash": envelope.get("source_hash", ""),
        "source_revision": envelope.get("source_revision", ""),
        "draft_version": int(envelope.get("draft_version", 0)),
        "draft_hash": envelope.get("draft_hash", ""),
        "policy_version": envelope.get("policy_version", ""),
        "policy_snapshot_hash": envelope.get("policy_snapshot_hash", ""),
        "allowed_write_targets": _canonical_targets(envelope.get("allowed_write_targets", [])),
        "decision": envelope.get("decision", ""),
        "reviewer": envelope.get("reviewer", ""),
        "reviewed_at": envelope.get("reviewed_at", ""),
    }


def build_causal_envelope(
    *,
    run_id: str,
    issue_key: str,
    source_hash: str,
    source_revision_value: str,
    draft_version: int,
    draft_payload: dict[str, Any],
    policy_version: str,
    policy_snapshot: dict[str, Any],
    allowed_write_targets: list[str],
    decision: str,
    reviewer: str,
    reviewed_at: str,
) -> dict[str, Any]:
    envelope = {
        "envelope_version": CAUSAL_ENVELOPE_VERSION,
        "run_id": run_id,
        "issue_key": issue_key,
        "source_hash": source_hash,
        "source_revision": source_revision_value,
        "draft_version": int(draft_version),
        "draft_hash": stable_hash(draft_payload),
        "policy_version": policy_version,
        "policy_snapshot_hash": stable_hash(policy_snapshot),
        "allowed_write_targets": _canonical_targets(allowed_write_targets),
        "decision": decision,
        "reviewer": reviewer,
        "reviewed_at": reviewed_at,
    }
    transaction_digest = stable_hash(_envelope_core(envelope))
    envelope["causal_transaction_id"] = f"ctx-{transaction_digest[:32]}"
    envelope["idempotency_key"] = f"wb-{stable_hash({'transaction': transaction_digest, 'issue_key': issue_key})[:32]}"
    return envelope


def validate_causal_envelope(
    envelope: dict[str, Any],
    *,
    run_id: str,
    issue_key: str,
    draft_version: int,
    draft_payload: dict[str, Any],
    policy_version: str,
    policy_snapshot: dict[str, Any],
    allowed_write_targets: list[str],
    current_source_hash: str | None = None,
    current_source_revision: str | None = None,
) -> list[str]:
    errors: list[str] = []
    if not envelope:
        return ["approval_envelope_missing"]
    if envelope.get("envelope_version") != CAUSAL_ENVELOPE_VERSION:
        errors.append("approval_envelope_version_mismatch")
    if envelope.get("decision") != "approve":
        errors.append("approval_decision_not_approve")
    if envelope.get("run_id") != run_id:
        errors.append("approval_run_mismatch")
    if envelope.get("issue_key") != issue_key:
        errors.append("approval_issue_mismatch")
    if int(envelope.get("draft_version", 0)) != int(draft_version):
        errors.append("approved_draft_version_mismatch")
    if envelope.get("draft_hash") != stable_hash(draft_payload):
        errors.append("approved_draft_hash_mismatch")
    if envelope.get("policy_version") != policy_version:
        errors.append("approved_policy_version_mismatch")
    if envelope.get("policy_snapshot_hash") != stable_hash(policy_snapshot):
        errors.append("approved_policy_snapshot_mismatch")
    if _canonical_targets(envelope.get("allowed_write_targets", [])) != _canonical_targets(allowed_write_targets):
        errors.append("approved_write_targets_mismatch")
    if current_source_hash is not None and envelope.get("source_hash") != current_source_hash:
        errors.append("approved_source_hash_mismatch")
    expected_revision = str(envelope.get("source_revision", ""))
    if current_source_revision is not None and expected_revision and expected_revision != str(current_source_revision):
        errors.append("approved_source_revision_mismatch")

    transaction_digest = stable_hash(_envelope_core(envelope))
    expected_transaction_id = f"ctx-{transaction_digest[:32]}"
    expected_idempotency_key = f"wb-{stable_hash({'transaction': transaction_digest, 'issue_key': issue_key})[:32]}"
    if envelope.get("causal_transaction_id") != expected_transaction_id:
        errors.append("causal_transaction_id_invalid")
    if envelope.get("idempotency_key") != expected_idempotency_key:
        errors.append("writeback_idempotency_key_invalid")
    return errors
