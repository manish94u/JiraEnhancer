from __future__ import annotations

import json
from pathlib import Path

from .schemas import PromptTemplate


DEFAULT_PROMPTS: list[PromptTemplate] = [
    PromptTemplate(
        prompt_id="missing_acceptance_criteria",
        title="Fill missing acceptance criteria",
        body=(
            "Focus on producing concise, testable acceptance criteria tied to the Jira summary and linked evidence. "
            "Do not add generic criteria; make each criterion verifiable and scoped to this Jira only."
        ),
        tags=("acceptance", "quality"),
    ),
    PromptTemplate(
        prompt_id="dependency_heavy",
        title="Dependency-first draft",
        body=(
            "Prioritize dependency sequencing and integration constraints. "
            "Call out blockers, prerequisites, and validation checkpoints before writeback."
        ),
        tags=("dependencies", "risk"),
    ),
    PromptTemplate(
        prompt_id="low_external_context",
        title="Low context fallback",
        body=(
            "When external evidence is sparse, produce targeted reviewer questions and avoid over-confident assumptions. "
            "Keep scope narrow and explicitly list unknowns."
        ),
        tags=("fallback", "questions"),
    ),
    PromptTemplate(
        prompt_id="failure_recovery_paths",
        title="Failure and recovery paths",
        body=(
            "Trace the failure modes stated or implied by the linked evidence. Cover timeout, retry, partial failure, "
            "rollback, idempotency, duplicate handling, and recovery only where relevant. Turn each supported path "
            "into a measurable acceptance check. Label any missing recovery decision as an open question instead of "
            "inventing a requirement."
        ),
        tags=("failure", "recovery", "idempotency", "quality"),
    ),
    PromptTemplate(
        prompt_id="nonfunctional_constraints",
        title="Non-functional constraint coverage",
        body=(
            "Extract measurable non-functional constraints from the Jira text and linked evidence, including latency, "
            "capacity, availability, compatibility, and resource limits when present. Keep stated limits separate from "
            "assumptions. Add testable acceptance checks for supported constraints and ask a focused question when a "
            "required threshold is missing."
        ),
        tags=("nonfunctional", "performance", "capacity", "compatibility"),
    ),
    PromptTemplate(
        prompt_id="interface_data_contracts",
        title="Interface and data-contract boundaries",
        body=(
            "Identify the interfaces, headers, schemas, precedence rules, persistence fields, and compatibility "
            "boundaries supported by the evidence. Split work at clear contract boundaries. Preserve exact defaults "
            "and validation rules, and add contract tests without creating fields or protocols that are not in the "
            "source material."
        ),
        tags=("interfaces", "contracts", "schema", "data", "compatibility"),
    ),
    PromptTemplate(
        prompt_id="operational_readiness",
        title="Operational readiness and rollout",
        body=(
            "Cover evidence-backed telemetry, logs, metrics, alarms, feature flags, rollout, rollback, and runbook needs. "
            "Separate implementation work from operational validation. Make observability criteria measurable and do "
            "not claim an SLO, dashboard, or deployment policy unless the source requires it."
        ),
        tags=("operations", "observability", "rollout", "metrics", "alarms"),
    ),
    PromptTemplate(
        prompt_id="ambiguity_resolution",
        title="Resolve ambiguity without invention",
        body=(
            "Find contradictions, unresolved choices, unclear ownership, and missing thresholds before finalizing the "
            "story set. Preserve the known requirement, state the effect of each uncertainty, and produce a small set "
            "of answerable reviewer questions. Do not silently choose a design that the evidence leaves open."
        ),
        tags=("ambiguity", "questions", "ownership", "risk"),
    ),
    PromptTemplate(
        prompt_id="security_privacy_boundaries",
        title="Security and privacy boundaries",
        body=(
            "Extract only evidence-backed authentication, authorization, tenancy, TLS, secret-handling, audit, and "
            "sensitive-data requirements. Add negative and boundary tests for the supported controls. Mark missing "
            "security decisions for human review rather than adding generic controls or unsupported claims."
        ),
        tags=("security", "privacy", "authentication", "authorization", "tenancy"),
    ),
]


class PromptLibrary:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path

    def load(self) -> list[PromptTemplate]:
        if self.path is None or not self.path.exists():
            return self._validate_unique_prompt_ids(list(DEFAULT_PROMPTS))
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        prompts: list[PromptTemplate] = []
        for item in payload:
            prompts.append(
                PromptTemplate(
                    prompt_id=str(item["prompt_id"]),
                    title=str(item.get("title", "")),
                    body=str(item.get("body", "")),
                    tags=tuple(item.get("tags", [])),
                    scope_types=tuple(item.get("scope_types", [])),
                    version=int(item.get("version", 1)),
                )
            )
        return self._validate_unique_prompt_ids(prompts or list(DEFAULT_PROMPTS))

    @staticmethod
    def _validate_unique_prompt_ids(prompts: list[PromptTemplate]) -> list[PromptTemplate]:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for prompt in prompts:
            if prompt.prompt_id in seen:
                duplicates.add(prompt.prompt_id)
            seen.add(prompt.prompt_id)
        if duplicates:
            duplicate_list = ", ".join(sorted(duplicates))
            raise ValueError(f"Duplicate prompt_id values are not allowed: {duplicate_list}")
        return prompts
