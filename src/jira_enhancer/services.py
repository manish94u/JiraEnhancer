from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Thread
from typing import Any, Protocol
from uuid import uuid4

from .causal_transaction import (
    DEFAULT_POLICY_VERSION,
    build_causal_envelope,
    source_revision,
    validate_causal_envelope,
)
from .models import ApprovalRecord, DraftRecord, EvidenceRecord, RunItemRecord, RunRecord, WritebackRecord, utc_now
from .rl.schemas import PromptGovernanceContext, PromptOutcome
from .sources import BitbucketSource, ConfluenceSource, JiraSource, _default_request_json
from .store import FilesystemStore
from .utils import stable_hash

logger = logging.getLogger(__name__)


AI_WRITE_TARGETS = ["description_top_block"]
AI_BLOCK_BEGIN = "[AI_UPDATE_BEGIN]"
AI_BLOCK_END = "[AI_UPDATE_END]"
TARGET_CONFIDENCE_SCORE = 8
EPIC_EVIDENCE_FETCH_TIMEOUT_SEC = 30.0
MAX_BACKGROUND_REFINEMENT_ATTEMPTS = 3
MAX_AUTONOMOUS_AGENT_STEPS = 3
QUALITY_CRITIC_MAX_PASSES = 2
QUALITY_CRITIC_MIN_TOTAL_SCORE = 18
AI_BLOCK_PATTERN = re.compile(
    r"(?:\[AI_UPDATE_BEGIN\].*?\[AI_UPDATE_END\]|\[AI_ENRICHMENT_BEGIN\].*?\[AI_ENRICHMENT_END\])\n*",
    re.DOTALL,
)

GUIDANCE_DECISIONS = {"user_input", "prompt"}


def strip_ai_block(description: str) -> str:
    return AI_BLOCK_PATTERN.sub("", description or "").strip()


def normalize_jira_source(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        "issue_key": issue.get("key"),
        "summary": issue.get("summary"),
        "description": strip_ai_block(issue.get("description", "")),
        "acceptance_criteria": issue.get("acceptance_criteria", []),
        "dependencies": issue.get("dependencies", []),
        "edge_cases": issue.get("edge_cases", []),
        "comments": issue.get("comments", []),
        "worklogs": issue.get("worklogs", []),
        "links": issue.get("links", {}),
    }


def _render_list_section(title: str, values: list[str]) -> str:
    lines = [f"{title}:"]
    lines.extend(f"- {value}" for value in values)
    return "\n".join(lines)


def build_ai_description_block(run_id: str, draft_payload: dict[str, Any], generated_at: str) -> str:
    outline = draft_payload.get("implementation_outline", [])
    outline_lines = ["Implementation Outline:"]
    outline_lines.extend(f"{index}. {value}" for index, value in enumerate(outline, start=1))
    nfr_values = draft_payload.get("nfrs", ["Perf budget: not specified", "Security notes: standard Jira writeback safeguards"])
    block_parts = [
        AI_BLOCK_BEGIN,
        "version: 1",
        f"last_generated_at: {generated_at}",
        f"run_id: {run_id}",
        f"confidence: {draft_payload.get('confidence', draft_payload.get('readiness_score', ''))}",
        "approval_state: approved_for_writeback",
        f"user_input_used: {'true' if bool(draft_payload.get('user_input_details')) else 'false'}",
        "Summary:",
        draft_payload.get("summary", ""),
        *outline_lines,
        _render_list_section("Out of Scope", draft_payload.get("out_of_scope", ["No additional out-of-scope items were identified."])),
        _render_list_section("Dependencies", draft_payload.get("dependencies", ["No explicit dependency is recorded in Jira yet."])),
        _render_list_section("Risks", draft_payload.get("risks", ["No additional risk captured."])),
        _render_list_section("Edge Cases", draft_payload.get("edge_cases", ["No explicit edge case is recorded in Jira yet."])),
        _render_list_section("Error Handling", draft_payload.get("error_handling", ["Re-read Jira before writeback and require rerun on material conflict."])),
        _render_list_section("NFRs", nfr_values),
        _render_list_section("Open Questions", draft_payload.get("fallback_questions", ["No open questions remain."])),
        "Recommended Next Step:",
        draft_payload.get("recommended_next_step", "Review the AI update and proceed with the next implementation step."),
        AI_BLOCK_END,
    ]
    return "\n".join(block_parts)


def merge_ai_block_into_description(existing_description: str, run_id: str, draft_payload: dict[str, Any], generated_at: str) -> str:
    ai_block = build_ai_description_block(run_id, draft_payload, generated_at)
    cleaned = strip_ai_block(existing_description)
    if cleaned:
        return f"{ai_block}\n\n{cleaned}"
    return ai_block


def _sentence_or_default(text: str, default: str) -> str:
    cleaned = " ".join(text.split()).strip()
    if not cleaned:
        return default
    return cleaned[:220]


def _bullet_join(values: list[str], default: str) -> list[str]:
    cleaned = [value.strip() for value in values if value and value.strip()]
    return cleaned or [default]


def _dedupe_text(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        cleaned = " ".join((value or "").split()).strip()
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        deduped.append(cleaned)
    return deduped


def _detail_lines(user_input_text: str) -> list[str]:
    lines = []
    for raw_line in user_input_text.splitlines():
        cleaned = raw_line.strip().lstrip("-*").strip()
        if cleaned:
            lines.append(cleaned)
    return lines


def _score_to_ten_scale(value: float) -> int:
    if value <= 0:
        return 1
    return max(1, min(10, int(round(value * 10))))


_STOPWORDS = {
    "aes",
    "demo",
    "cp",
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "about",
    "should",
    "would",
    "could",
    "have",
    "will",
    "just",
    "only",
    "your",
    "their",
    "them",
    "then",
    "than",
    "each",
    "every",
    "also",
    "need",
    "needs",
    "using",
    "use",
    "used",
    "read",
    "detail",
    "details",
    "story",
    "stories",
    "jira",
}


def _extract_user_input_text(user_input: dict[str, Any] | None) -> str:
    if not user_input:
        return ""
    if isinstance(user_input, dict):
        for key in ("details", "text", "value"):
            value = user_input.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _has_values(values: Any) -> bool:
    return any(str(value).strip() for value in (values or []))


def _normalize_summary(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _flatten_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        parts: list[str] = []
        for nested in value.values():
            parts.extend(_flatten_text(nested))
        return parts
    if isinstance(value, (list, tuple, set)):
        parts = []
        for nested in value:
            parts.extend(_flatten_text(nested))
        return parts
    return [str(value)]


def _test_case_context_text(
    normalized_issue: dict[str, Any],
    analysis: dict[str, Any] | None = None,
    evidence: list[EvidenceRecord] | None = None,
) -> str:
    parts: list[str] = []
    for key in ("summary", "description", "acceptance_criteria", "dependencies", "edge_cases", "comments", "worklogs"):
        parts.extend(_flatten_text(normalized_issue.get(key)))
    if analysis:
        for key in (
            "suggested_description",
            "acceptance_criteria",
            "guidance",
            "recommended_next_step",
            "open_questions",
            "risks",
            "error_handling",
            "nfrs",
        ):
            parts.extend(_flatten_text(analysis.get(key)))
        gap_analysis = analysis.get("test_case_gap_analysis")
        if isinstance(gap_analysis, dict):
            parts.extend(_flatten_text(gap_analysis.get("available_samples")))
    for record in evidence or []:
        parts.extend(_flatten_text(record.content))
    return "\n".join(part for part in parts if str(part).strip()).lower()


def _has_unit_test_sample(text: str) -> bool:
    patterns = [
        r"\bunit\s+test(?:\s+case|\s+sample|s)?\b",
        r"\b(?:pytest|unittest|jest|junit|rspec)\b.{0,120}\b(?:assert|expect|should|verify)\b",
        r"\btest_[a-z0-9_]+\b.{0,120}\b(?:assert|expect|should|verify)\b",
    ]
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _has_integration_test_sample(text: str) -> bool:
    patterns = [
        r"\bintegration\s+test(?:\s+case|\s+sample|s)?\b",
        r"\b(?:e2e|end[-\s]?to[-\s]?end|contract)\s+test(?:\s+case|\s+sample|s)?\b",
        r"\btests?/integration/.{0,120}\b(?:assert|expect|should|verify)\b",
    ]
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _build_test_case_gap_prompt(issue_key: str, missing: list[str]) -> str:
    missing_label = " and ".join(missing)
    return (
        f"Test coverage gap for {issue_key}: provide sample {missing_label} test cases before marking this Jira ready. "
        "For each missing test type include test name, setup, action, assertions, and the expected failure/edge path."
    )


def _analyze_test_case_gaps(
    normalized_issue: dict[str, Any],
    analysis: dict[str, Any] | None = None,
    evidence: list[EvidenceRecord] | None = None,
) -> dict[str, Any]:
    text = _test_case_context_text(normalized_issue, analysis, evidence)
    has_unit = _has_unit_test_sample(text)
    has_integration = _has_integration_test_sample(text)
    missing = []
    if not has_unit:
        missing.append("unit")
    if not has_integration:
        missing.append("integration")
    return {
        "has_unit_test_sample": has_unit,
        "has_integration_test_sample": has_integration,
        "missing": missing,
        "gap_prompt": _build_test_case_gap_prompt(str(normalized_issue.get("issue_key", "this Jira")), missing) if missing else "",
    }


def _attach_test_case_gap_analysis(
    normalized_issue: dict[str, Any],
    evidence: list[EvidenceRecord],
    analysis: dict[str, Any],
) -> dict[str, Any]:
    enriched = dict(analysis or {})
    gap_analysis = _analyze_test_case_gaps(normalized_issue, enriched, evidence)
    enriched["test_case_gap_analysis"] = gap_analysis
    gap_prompt = str(gap_analysis.get("gap_prompt", "")).strip()
    if gap_prompt:
        enriched["open_questions"] = _dedupe_text(list(enriched.get("open_questions") or []) + [gap_prompt])[:4]
        enriched["guidance"] = _dedupe_text(list(enriched.get("guidance") or []) + [gap_prompt])[:4]
    return enriched


def _build_epic_story_test_case_details(summary: str) -> list[str]:
    story_summary = str(summary or "this story").strip()
    return [
        f"Unit test case: verify {story_summary} handles the primary success path with mocked collaborators and asserts the expected output/state.",
        f"Integration test case: verify {story_summary} works through the relevant API/UI flow and persists or returns the expected Jira-facing result.",
    ]


def _append_epic_story_test_case_details(description: str, summary: str) -> str:
    cleaned = str(description or "").strip()
    lowered = cleaned.lower()
    needs_unit = "unit test case" not in lowered
    needs_integration = "integration test case" not in lowered
    missing_details = []
    for detail in _build_epic_story_test_case_details(summary):
        detail_lower = detail.lower()
        if ("unit test case" in detail_lower and needs_unit) or ("integration test case" in detail_lower and needs_integration):
            missing_details.append(detail)
    if not missing_details:
        return cleaned
    section = "Test Case Details:\n" + "\n".join(f"- {detail}" for detail in missing_details)
    if not cleaned:
        return section
    return f"{cleaned}\n\n{section}"


def _ensure_epic_story_analysis_has_test_cases(summary: str, analysis: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(analysis or {})
    enriched["suggested_description"] = _append_epic_story_test_case_details(
        str(enriched.get("suggested_description", "")).strip(),
        summary,
    )
    acceptance = [str(item).strip() for item in enriched.get("acceptance_criteria", []) if str(item).strip()]
    existing_blob = "\n".join(acceptance).lower()
    for detail in _build_epic_story_test_case_details(summary):
        if "unit test case" in detail.lower() and "unit test case" in existing_blob:
            continue
        if "integration test case" in detail.lower() and "integration test case" in existing_blob:
            continue
        acceptance.append(detail)
    enriched["acceptance_criteria"] = acceptance[:5]
    return enriched


def _score_candidate_match(text: str, keywords: list[str]) -> int:
    lowered = (text or "").lower()
    return sum(1 for keyword in keywords if keyword.lower() in lowered)


def _clean_heading_text(value: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", value or "")
    cleaned = cleaned.strip().strip("#").strip()
    cleaned = re.sub(r"^[0-9]+[.)]\s*", "", cleaned)
    cleaned = re.sub(r"^\*+|\*+$", "", cleaned).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" :-")


def _looks_like_story_heading(value: str) -> bool:
    cleaned = _clean_heading_text(value)
    if not cleaned or len(cleaned) > 140:
        return False
    lowered = cleaned.lower()
    weak_titles = {
        "goal",
        "rule",
        "example",
        "note",
        "then",
        "condition",
        "solution design",
        "problem description",
        "jira details",
        "targeted for release",
    }
    if lowered in weak_titles:
        return False
    if len(cleaned.split()) <= 2 and not any(token in lowered for token in ("api", "client", "service", "flow", "guardrail")):
        return False
    heading_terms = (
        "scale",
        "scaling",
        "descale",
        "de-scale",
        "guardrail",
        "cooldown",
        "api",
        "service",
        "client",
        "workflow",
        "flow",
        "rule",
        "connection",
        "semaphore",
        "validation",
        "observability",
        "metric",
        "writeback",
        "persistence",
        "integration",
        "migration",
        "security",
        "performance",
        "setup",
        "rotation",
    )
    return any(term in lowered for term in heading_terms)


def _story_heading_from_line(line: str, next_line: str = "") -> tuple[str, bool]:
    stripped = line.strip()
    markdown = re.match(r"^#{1,6}\s+(.+)$", stripped)
    if markdown:
        title = _clean_heading_text(markdown.group(1))
        return title, _looks_like_story_heading(title)
    if next_line.strip() and re.fullmatch(r"[=-]{3,}", next_line.strip()):
        title = _clean_heading_text(stripped)
        return title, _looks_like_story_heading(title)
    if re.match(r"^(?:\*+)?(?:[0-9]+[.)]\s*)?[A-Z][A-Za-z0-9 /()_-]{8,120}:?(?:\*+)?$", stripped):
        title = _clean_heading_text(stripped)
        return title, _looks_like_story_heading(title)
    return "", False


def _build_epic_evidence_text(normalized_epic: dict[str, Any], evidence: list[EvidenceRecord]) -> str:
    parts: list[str] = []
    for key in ("summary", "description", "acceptance_criteria", "dependencies", "edge_cases", "comments", "worklogs"):
        parts.extend(_flatten_text(normalized_epic.get(key)))
    for record in evidence:
        parts.extend(_flatten_text(record.content))
    return "\n".join(str(part) for part in parts if str(part).strip())


def _embedded_epic_design_text(normalized_epic: dict[str, Any]) -> str:
    description = str(normalized_epic.get("description", "") or "").strip()
    if not description:
        return ""
    text_without_links = _strip_links(description)
    tokens = _tokenize(text_without_links)
    if len(tokens) < 45:
        return ""

    lowered = text_without_links.lower()
    heading_hits = sum(
        1
        for pattern in (
            r"\bproblem description\b",
            r"\bsolution design\b",
            r"\bscaling rule\b",
            r"\bde-?scaling rule\b",
            r"\bpost-scale guardrail\b",
            r"\bacceptance criteria\b",
            r"\bimplementation\b",
        )
        if re.search(pattern, lowered)
    )
    formula_hits = len(
        re.findall(
            r"\b(?:requiredconnections|semaphorecount|bc\w*|avg_exec_time|execution avg)\b\s*[=<>]|[=<>]\s*\b(?:requiredconnections|semaphorecount|bc\w*)\b",
            lowered,
        )
    )
    numeric_rule_hits = len(re.findall(r"\b(?:1000\s*ms|500|15|4\s+minutes?|60\s*-\s*120\s*sec|20\s*-\s*30%)\b", lowered))
    design_terms = {
        "api",
        "backlog",
        "connection",
        "cooldown",
        "descale",
        "guardrail",
        "integration",
        "metric",
        "persist",
        "rollback",
        "rule",
        "scaling",
        "semaphore",
        "service",
        "threshold",
        "throttle",
        "validation",
        "workflow",
    }
    design_term_hits = len(tokens & design_terms)
    imperative_hits = len(re.findall(r"\b(?:must|should|never|if|then|when|only if|reduce|increase|calculate|create|persist|validate)\b", lowered))

    signal_score = heading_hits + min(3, formula_hits) + min(3, numeric_rule_hits) + min(5, design_term_hits) + min(4, imperative_hits // 3)
    if signal_score < 6:
        return ""
    return description


def _has_substantive_embedded_epic_context(normalized_epic: dict[str, Any], evidence: list[EvidenceRecord]) -> bool:
    if _embedded_epic_design_text(normalized_epic):
        return True
    text = _strip_links(_build_epic_evidence_text(normalized_epic, evidence))
    tokens = _tokenize(text)
    if len(tokens) < 35:
        return False
    design_terms = {
        "api",
        "backlog",
        "connection",
        "cooldown",
        "descale",
        "guardrail",
        "integration",
        "metric",
        "persist",
        "rule",
        "scaling",
        "semaphore",
        "service",
        "threshold",
        "validation",
        "workflow",
    }
    return bool(tokens & design_terms)


def _embedded_epic_evidence_record(run_id: str, issue_key: str, normalized_issue: dict[str, Any]) -> EvidenceRecord | None:
    embedded_text = _embedded_epic_design_text(normalized_issue)
    if not embedded_text:
        return None
    return EvidenceRecord(
        run_id=run_id,
        issue_key=issue_key,
        source_type="jira_embedded_context",
        source_ref=f"{issue_key}:description",
        relevance_score=0.9,
        selected_for_draft=True,
        content={
            "title": "Embedded Jira epic design context",
            "summary": embedded_text,
            "source": "jira_description",
        },
    )


def _append_embedded_epic_evidence(
    run_id: str,
    issue_key: str,
    normalized_issue: dict[str, Any],
    evidence: list[EvidenceRecord],
) -> list[EvidenceRecord]:
    embedded = _embedded_epic_evidence_record(run_id, issue_key, normalized_issue)
    if not embedded:
        return evidence
    if any(record.source_type == embedded.source_type and record.source_ref == embedded.source_ref for record in evidence):
        return evidence
    logger.info(
        "evidence.resolve embedded_epic_context_added issue_key=%s source_ref=%s chars=%s",
        issue_key,
        embedded.source_ref,
        len(str(embedded.content.get("summary", ""))),
    )
    return [embedded, *evidence]


def _split_evidence_into_story_sections(evidence_text: str) -> list[dict[str, str]]:
    logger.info("epic_breakdown.extract_sections start evidence_chars=%s", len(evidence_text or ""))
    raw_lines = [line.rstrip() for line in (evidence_text or "").splitlines()]
    sections: list[dict[str, str]] = []
    parent_title = ""
    current_title = ""
    current_body: list[str] = []

    def flush() -> None:
        nonlocal current_title, current_body
        body = "\n".join(line for line in current_body if line.strip()).strip()
        if current_title and len(body.split()) >= 12:
            sections.append({"title": current_title, "body": body})
        current_title = ""
        current_body = []

    for index, raw_line in enumerate(raw_lines):
        stripped = raw_line.strip()
        if not stripped or re.fullmatch(r"[=-]{3,}", stripped):
            continue
        next_line = raw_lines[index + 1] if index + 1 < len(raw_lines) else ""
        heading, is_story_heading = _story_heading_from_line(stripped, next_line)
        if heading:
            if any(term in heading.lower() for term in ("client", "service", "rule", "workflow", "flow", "feature")):
                parent_title = heading
            if is_story_heading:
                flush()
                if parent_title and parent_title != heading and heading.lower() not in parent_title.lower():
                    current_title = f"{parent_title}: {heading}"
                else:
                    current_title = heading
                continue
        if current_title:
            current_body.append(stripped)

    flush()
    if len(sections) <= 1:
        fallback_sections = _split_rule_sentences_into_story_sections(evidence_text)
        if len(fallback_sections) > len(sections):
            logger.info(
                "epic_breakdown.extract_sections using_rule_sentence_fallback heading_sections=%s fallback_sections=%s",
                len(sections),
                len(fallback_sections),
            )
            return fallback_sections
    logger.info("epic_breakdown.extract_sections completed section_count=%s", len(sections))
    return sections


def _sentence_candidates(text: str) -> list[str]:
    sentences: list[str] = []
    for line in (text or "").splitlines():
        normalized = re.sub(r"\s+", " ", line).strip()
        if not normalized:
            continue
        sentences.extend(sentence.strip(" -") for sentence in re.split(r"(?<=[.!?])\s+", normalized) if sentence.strip(" -"))
    return sentences


def _split_rule_sentences_into_story_sections(evidence_text: str) -> list[dict[str, str]]:
    logger.info("epic_breakdown.extract_rule_sections start")
    sections: list[dict[str, str]] = []
    for sentence in _sentence_candidates(evidence_text):
        lowered = sentence.lower()
        if not any(
            token in lowered
            for token in (
                "scale",
                "scaling",
                "descale",
                "de-scale",
                "guardrail",
                "cooldown",
                "api",
                "workflow",
                "connection",
                "semaphore",
                "validation",
                "metric",
                "persist",
                "writeback",
            )
        ):
            continue
        if not any(
            token in lowered
            for token in (
                " uses ",
                " creates ",
                " sets ",
                " should ",
                " must ",
                " requires ",
                " calculates ",
                " updates ",
                " persists ",
                " returns ",
                " reduces ",
                " increases ",
                " when ",
                " if ",
                " for every ",
            )
        ):
            continue
        title = sentence
        if ":" in title:
            prefix, suffix = title.split(":", 1)
            suffix_title = re.split(
                r"\b(?:use|uses|create|creates|set|sets|should|must|allow|allows|require|requires|calculate|calculates|update|updates|persist|persists|return|returns|reduce|reduces|increase|increases|restore|restores)\b",
                suffix,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0]
            title = f"{prefix}: {suffix_title}" if _looks_like_story_heading(suffix_title) else prefix
        else:
            title = re.split(
                r"\b(?:use|uses|create|creates|set|sets|should|must|allow|allows|require|requires|calculate|calculates|update|updates|persist|persists|return|returns|reduce|reduces|increase|increases|restore|restores)\b",
                title,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0]
        title = _clean_heading_text(title)
        if not _looks_like_story_heading(title):
            continue
        sections.append({"title": title, "body": sentence})
    logger.info("epic_breakdown.extract_rule_sections completed section_count=%s", len(sections))
    return sections


def _candidate_summary_from_section(title: str) -> str:
    cleaned = _clean_heading_text(title)
    lowered = cleaned.lower()
    if lowered.startswith(("implement ", "add ", "create ", "update ", "migrate ", "validate ", "support ")):
        return cleaned
    if lowered.startswith(("de-scale", "descale")):
        return f"Implement {cleaned}"
    if "guardrail" in lowered or "cooldown" in lowered:
        return f"Add {cleaned}"
    return f"Implement {cleaned}"


def _extract_candidate_acceptance_criteria(body: str) -> list[str]:
    criteria: list[str] = []
    for line in body.splitlines():
        cleaned = re.sub(r"^\s*[-*+]\s*", "", line).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        lowered = cleaned.lower()
        if len(cleaned) < 20 or len(cleaned) > 220:
            continue
        if any(
            token in lowered
            for token in (
                "should",
                "must",
                "only",
                "if ",
                "when ",
                "then",
                "max",
                "minimum",
                "never",
                "calculate",
                "create",
                "reduce",
                "increase",
                "support",
                "persist",
                "return",
                "validate",
            )
        ):
            criteria.append(cleaned.rstrip(".") + ".")
        if len(criteria) >= 5:
            break
    if not criteria:
        for sentence in _sentence_candidates(body)[:3]:
            if len(sentence) >= 20:
                criteria.append(f"Implementation follows evidence rule: {sentence.rstrip('.')}.")
    return _dedupe_text(criteria)[:5]


def _extract_candidate_dependencies(body: str) -> list[str]:
    dependencies: list[str] = []
    for token in re.findall(r"\b[A-Z][A-Za-z0-9]*(?:Client|Service|API|DB|OQS|AES|Jira|Confluence)\b", body or ""):
        dependencies.append(token)
    if any(word in (body or "").lower() for word in ("metric", "statistics", "count", "threshold")):
        dependencies.append("Runtime metrics and threshold configuration")
    return _dedupe_text(dependencies)[:4]


def _extract_candidate_edge_cases(body: str) -> list[str]:
    edge_cases: list[str] = []
    for sentence in _sentence_candidates(body):
        lowered = sentence.lower()
        if any(token in lowered for token in ("max", "minimum", "never", "failure", "fallback", "cooldown", "threshold", "conflict", "empty", "missing")):
            edge_cases.append(sentence.rstrip(".") + ".")
        if len(edge_cases) >= 4:
            break
    return _dedupe_text(edge_cases)[:4]


def _estimate_story_points(section: dict[str, str]) -> int:
    text = f"{section.get('title', '')} {section.get('body', '')}".lower()
    complexity = 0
    for token in ("if ", "when ", "cooldown", "rollback", "threshold", "max", "min", "multiple", "integration", "persist", "concurrent"):
        if token in text:
            complexity += 1
    if complexity >= 4 or len(text.split()) > 160:
        return 5
    if complexity <= 1 and len(text.split()) < 60:
        return 2
    return 3


def _is_existing_story_candidate(candidate: dict[str, Any], existing_summaries: list[str]) -> bool:
    candidate_terms = _tokenize(str(candidate.get("summary", "")))
    if not candidate_terms:
        return False
    for existing in existing_summaries:
        existing_terms = _tokenize(existing)
        if not existing_terms:
            continue
        overlap = len(candidate_terms & existing_terms) / max(1, len(candidate_terms))
        if overlap >= 0.65:
            return True
    return False


def _candidate_from_section(epic_summary: str, section: dict[str, str], index: int) -> dict[str, Any]:
    summary = _candidate_summary_from_section(section["title"])
    body_sentences = _sentence_candidates(section["body"])
    evidence_summary = " ".join(body_sentences[:3]).strip()
    description = (
        f"Implement this story for {epic_summary}. Evidence section: {section['title']}. "
        f"{evidence_summary or section['body'][:500]}"
    ).strip()
    return {
        "id": f"evidence_section_{index}",
        "summary": summary,
        "story_points": _estimate_story_points(section),
        "description": description,
        "acceptance_criteria": _extract_candidate_acceptance_criteria(section["body"]),
        "dependencies": _extract_candidate_dependencies(section["body"]),
        "edge_cases": _extract_candidate_edge_cases(section["body"]),
    }


def _normalize_planned_epic_story_candidates(raw_candidates: Any, epic_summary: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    if not isinstance(raw_candidates, list):
        return candidates
    for index, raw_candidate in enumerate(raw_candidates, start=1):
        if not isinstance(raw_candidate, dict):
            continue
        summary = " ".join(str(raw_candidate.get("summary", "")).split()).strip()
        description = str(raw_candidate.get("description", "")).strip()
        acceptance_criteria = [
            str(item).strip()
            for item in raw_candidate.get("acceptance_criteria", [])
            if str(item).strip()
        ][:5]
        if not summary or not description or not acceptance_criteria:
            continue
        unavailable_text = f"{summary}\n{description}".lower()
        if (
            "evidence unavailable" in unavailable_text
            or "confluence evidence unavailable" in unavailable_text
            or "clarify evidence needed" in unavailable_text
        ):
            logger.info(
                "epic_breakdown.codex_plan skipped_unavailable_candidate epic_summary=%s summary=%s",
                epic_summary,
                summary,
            )
            continue
        try:
            story_points = int(raw_candidate.get("story_points", 3) or 3)
        except (TypeError, ValueError):
            story_points = 3
        story_points = max(1, min(13, story_points))
        candidates.append(
            {
                "id": f"codex_plan_{index}",
                "summary": summary,
                "description": description,
                "acceptance_criteria": acceptance_criteria,
                "dependencies": [str(item).strip() for item in raw_candidate.get("dependencies", []) if str(item).strip()][:5],
                "edge_cases": [str(item).strip() for item in raw_candidate.get("edge_cases", []) if str(item).strip()][:5],
                "story_points": story_points,
                "planner": str(raw_candidate.get("planner", "codex")).strip() or "codex",
                "evidence_refs": [str(item).strip() for item in raw_candidate.get("evidence_refs", []) if str(item).strip()],
            }
        )
        if len(candidates) >= 10:
            break
    logger.info(
        "epic_breakdown.codex_plan normalize_done epic_summary=%s raw_count=%s valid_count=%s",
        epic_summary,
        len(raw_candidates),
        len(candidates),
    )
    return candidates


def _story_plan_response_schema() -> dict[str, Any]:
    string_array = {"type": "array", "items": {"type": "string"}}
    story = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "summary",
            "description",
            "acceptance_criteria",
            "dependencies",
            "edge_cases",
            "story_points",
            "planner",
            "evidence_refs",
        ],
        "properties": {
            "summary": {"type": "string"},
            "description": {"type": "string"},
            "acceptance_criteria": string_array,
            "dependencies": string_array,
            "edge_cases": string_array,
            "story_points": {"type": "integer"},
            "planner": {"type": "string"},
            "evidence_refs": string_array,
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["stories"],
        "properties": {
            "stories": {
                "type": "array",
                "items": story,
            }
        },
    }


def _build_epic_story_planner_payload(
    epic_key: str,
    epic_summary: str,
    normalized_epic: dict[str, Any],
    evidence: list[EvidenceRecord],
    existing_summaries: list[str],
) -> dict[str, Any]:
    evidence_text = _build_epic_evidence_text(normalized_epic, evidence)
    linked_context_refs = list(normalized_epic.get("links", {}).get("confluence_pages", [])) + list(
        normalized_epic.get("links", {}).get("bitbucket_prs", [])
    )
    return {
        "epic_key": epic_key,
        "epic_summary": epic_summary,
        "epic": normalized_epic,
        "codex_context_request": (
            "Inspect the linked Confluence pages and Bitbucket PRs with available Codex tools before planning stories. "
            "Use embedded Jira epic description and evidence_text when they contain concrete design details. "
            "If the epic only contains links, inspect the linked design content as the source of truth and return no stories if the linked context cannot be read."
        ),
        "linked_context_refs": linked_context_refs,
        "existing_story_summaries": existing_summaries,
        "evidence_refs": [record.source_ref for record in evidence],
        "evidence_text": evidence_text[:24000],
        "evidence": [
            {
                "source_type": record.source_type,
                "source_ref": record.source_ref,
                "content": record.content,
            }
            for record in evidence
        ],
    }


def _build_epic_story_planner_prompt(payload: dict[str, Any]) -> str:
    instructions = [
        "You are planning Jira stories under one epic from Jira and Confluence evidence.",
        "Read the epic and evidence as the source of truth.",
        "Plan from embedded Jira epic description and evidence_text when they contain concrete design details, workflows, rules, APIs, guardrails, data changes, or tests.",
        "If the epic links or description mention Confluence pages or Bitbucket PRs, use Codex-accessible tools and configured MCP servers to inspect them before planning stories.",
        "Do not ask the caller to fetch Confluence; you are responsible for inspecting linked context when links are present.",
        "Return an empty stories array only when neither embedded context nor linked context can be read.",
        "Create implementation-ready story candidates from the actual design sections, workflows, rules, APIs, guardrails, data changes, and tests.",
        "Do not use generic buckets such as UI/API/persistence unless those are explicitly present in the evidence.",
        "Do not invent stories that are not grounded in the provided evidence.",
        "Avoid duplicates of existing stories.",
        "Each story must have summary, description, acceptance_criteria, dependencies, edge_cases, story_points, planner, and evidence_refs.",
        "Set planner to the planning strategy name, such as codex.",
        "Set evidence_refs to the source refs used for that story, or an empty array if no evidence was available.",
        "Acceptance criteria must be concrete and testable.",
        "Return only JSON matching the schema.",
    ]
    return "\n".join(instructions) + "\n\nInput:\n" + json.dumps(payload, indent=2)


def _plan_epic_story_candidates_with_reasoner(
    reasoner: Any,
    epic_key: str,
    epic_summary: str,
    normalized_epic: dict[str, Any],
    evidence: list[EvidenceRecord],
    existing_summaries: list[str],
) -> list[dict[str, Any]]:
    planner = getattr(reasoner, "plan_epic_stories", None)
    if not callable(planner):
        logger.info(
            "epic_breakdown.codex_plan unavailable epic_key=%s reasoner=%s",
            epic_key,
            type(reasoner).__name__,
        )
        return []
    logger.info(
        "epic_breakdown.codex_plan start epic_key=%s reasoner=%s evidence_count=%s",
        epic_key,
        type(reasoner).__name__,
        len(evidence),
    )
    try:
        raw_candidates = planner(epic_key, epic_summary, normalized_epic, evidence, existing_summaries)
        candidates = _normalize_planned_epic_story_candidates(raw_candidates, epic_summary)
        logger.info(
            "epic_breakdown.codex_plan done epic_key=%s candidate_count=%s",
            epic_key,
            len(candidates),
        )
        return candidates
    except Exception as exc:  # noqa: BLE001
        logger.warning("epic_breakdown.codex_plan failed epic_key=%s error=%s", epic_key, exc)
        return []


def _build_evidence_grounded_epic_story_candidates(
    epic_summary: str,
    normalized_epic: dict[str, Any],
    evidence: list[EvidenceRecord],
    existing_summaries: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    logger.info(
        "epic_breakdown.build_candidates start epic_summary=%s evidence_count=%s existing_summary_count=%s",
        epic_summary,
        len(evidence),
        len(existing_summaries),
    )
    evidence_text = _build_epic_evidence_text(normalized_epic, evidence)
    candidates: list[dict[str, Any]] = []
    skipped_existing: list[str] = []
    seen_summaries: set[str] = set()
    for index, section in enumerate(_split_evidence_into_story_sections(evidence_text), start=1):
        candidate = _candidate_from_section(epic_summary, section, index)
        summary_key = " ".join(_tokenize(candidate["summary"]))
        if summary_key in seen_summaries:
            logger.info(
                "epic_breakdown.build_candidates skipped_duplicate index=%s summary=%s",
                index,
                candidate["summary"],
            )
            continue
        seen_summaries.add(summary_key)
        if _is_existing_story_candidate(candidate, existing_summaries):
            skipped_existing.append(candidate["summary"])
            logger.info(
                "epic_breakdown.build_candidates skipped_existing index=%s summary=%s",
                index,
                candidate["summary"],
            )
            continue
        candidates.append(candidate)
        logger.info(
            "epic_breakdown.build_candidates added index=%s summary=%s story_points=%s ac_count=%s dependency_count=%s edge_case_count=%s",
            index,
            candidate["summary"],
            candidate["story_points"],
            len(candidate.get("acceptance_criteria", [])),
            len(candidate.get("dependencies", [])),
            len(candidate.get("edge_cases", [])),
        )
        if len(candidates) >= 10:
            logger.info("epic_breakdown.build_candidates reached_limit limit=10")
            break
    logger.info(
        "epic_breakdown.build_candidates completed candidate_count=%s skipped_existing_count=%s",
        len(candidates),
        len(skipped_existing),
    )
    return candidates, skipped_existing


def _build_epic_evidence_gap_candidate(epic_key: str, epic_summary: str) -> dict[str, Any]:
    return {
        "id": "evidence_gap",
        "summary": f"Clarify evidence needed to break down {epic_summary or epic_key}",
        "story_points": 1,
        "description": (
            f"Epic {epic_key} does not have enough loaded Jira or Confluence evidence to create implementation-ready stories. "
            "Attach or fix the design evidence, then rerun story suggestion before approving story creation."
        ),
        "acceptance_criteria": [
            "The epic description contains the authoritative design link or the design content is attached to the epic.",
            "The story breakdown flow can load the design evidence without warnings.",
            "Generated stories are based on concrete feature areas from the loaded evidence rather than generic UI/API/persistence buckets.",
        ],
        "dependencies": ["Authoritative Jira or Confluence evidence"],
        "edge_cases": ["Confluence authentication expired", "Epic references an unrelated or stale feature Jira"],
    }


def _looks_like_placeholder_description(description: str, issue_key: str) -> bool:
    cleaned = " ".join((description or "").split()).strip().lower()
    if not cleaned:
        return True
    generic_patterns = [
        f"clarify the requested change for {issue_key.lower()}",
        "tbd",
        "todo",
        "to be added",
    ]
    return any(pattern in cleaned for pattern in generic_patterns)


def _parse_confluence_refs(text: str) -> list[str]:
    refs = set(re.findall(r"https?://[^\s]+confluence[^\s]*", text or ""))
    refs.update(re.findall(r"pageId=(\d+)", text or ""))
    refs.update(re.findall(r"/pages/(?:viewpage.action\?pageId=)?(\d+)", text or ""))
    refs.update(match for match in re.findall(r"\bconfluence[:#/-]?(\d{6,})\b", text or "", flags=re.IGNORECASE))
    return sorted(refs)


def _canonical_confluence_refs(refs: list[str]) -> list[str]:
    canonical: set[str] = set()
    for ref in refs:
        text = str(ref or "").strip()
        if not text:
            continue
        page_id_match = re.search(r"pageId=(\d+)", text) or re.search(r"/pages/(?:viewpage.action\?pageId=)?(\d+)", text)
        canonical.add(page_id_match.group(1) if page_id_match else text)
    return sorted(canonical)


def _parse_bitbucket_refs(text: str) -> list[str]:
    refs = set()
    for project, repo, pr_id in re.findall(r"/projects/([^/\s]+)/repos/([^/\s]+)/pull-requests/(\d+)", text or ""):
        refs.add(f"{project}/{repo}/{pr_id}")
    for project, repo, pr_id in re.findall(r"\b([A-Z][A-Z0-9_-]+)/([A-Za-z0-9._-]+)/(\d+)\b", text or ""):
        refs.add(f"{project}/{repo}/{pr_id}")
    return sorted(refs)


def _dedupe_evidence(records: list[EvidenceRecord]) -> list[EvidenceRecord]:
    seen: set[tuple[str, str]] = set()
    deduped: list[EvidenceRecord] = []
    for record in records:
        key = (record.source_type, record.source_ref)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped


def _warning(code: str, source: str, message: str, ref: str = "") -> dict[str, str]:
    payload = {
        "code": code,
        "source": source,
        "message": message,
    }
    if ref:
        payload["ref"] = ref
    return payload


def _classify_confluence_warning(
    message: str,
    ref: str,
    confluence_base_url: str,
    confluence_use_web_session: bool,
) -> dict[str, str]:
    lowered = (message or "").lower()
    auth_like = any(
        token in lowered
        for token in (
            "web-session",
            "browser",
            "login",
            "auth",
            "initialize",
            "timed out waiting for mcp server response during initialize",
        )
    )
    if confluence_use_web_session and auth_like:
        warning = _warning(
            "confluence_auth_required",
            "confluence",
            "Confluence authentication is required for the project runtime. Complete browser sign-in, then retry the request.",
            ref=ref,
        )
        if confluence_base_url:
            warning["auth_url"] = confluence_base_url
        warning["detail"] = message
        return warning
    warning = _warning("confluence_mcp_unavailable", "confluence", message, ref=ref)
    if confluence_base_url:
        warning["auth_url"] = confluence_base_url
    return warning


def _dedupe_warnings(warnings: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str, str, str]] = set()
    deduped: list[dict[str, str]] = []
    for warning in warnings:
        key = (
            warning.get("code", ""),
            warning.get("source", ""),
            warning.get("message", ""),
            warning.get("ref", ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(warning)
    return deduped


def _tokenize(text: str) -> set[str]:
    tokens: set[str] = set()
    for token in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if len(token) <= 2 or token in _STOPWORDS or token.isdigit():
            continue
        tokens.add(token)
        if token.endswith("s") and len(token) > 4:
            tokens.add(token[:-1])
    return tokens


def _strip_links(text: str) -> str:
    return re.sub(r"https?://\S+", " ", text or "")


def _issue_terms(normalized_issue: dict[str, Any]) -> set[str]:
    combined = " ".join(
        [
            normalized_issue.get("summary", ""),
            normalized_issue.get("description", ""),
            " ".join(normalized_issue.get("acceptance_criteria", [])),
            " ".join(normalized_issue.get("dependencies", [])),
            " ".join(normalized_issue.get("edge_cases", [])),
        ]
    )
    return _tokenize(combined)


def _summary_terms(normalized_issue: dict[str, Any]) -> set[str]:
    return _tokenize(
        " ".join(
            [
                normalized_issue.get("summary", ""),
                normalized_issue.get("description", ""),
            ]
        )
    )


def _derive_story_guidance(normalized_issue: dict[str, Any], user_input_text: str) -> list[str]:
    issue_terms = _issue_terms(normalized_issue)
    selected_segments: list[str] = []
    scored_segments: list[tuple[int, int, str]] = []
    for index, segment in enumerate(_detail_lines(user_input_text)):
        segment_terms = _tokenize(segment)
        overlap = len(issue_terms & segment_terms)
        score = overlap * 4
        lowered = segment.lower()
        if normalized_issue.get("issue_key", "").lower() in lowered:
            score += 5
        if any(term in lowered for term in ("scope", "dependency", "description", "writeback", "approval", "ui", "api", "test", "storage", "queue")):
            score += 1
        if score > 0:
            scored_segments.append((score, index, segment))
    for _, _, segment in sorted(scored_segments, reverse=True)[:3]:
        selected_segments.append(segment)

    guidance: list[str] = []
    selected_text = " ".join(selected_segments).lower()
    summary = normalized_issue.get("summary", "")
    if "scope" in selected_text or "epic" in selected_text or "story-level" in selected_text:
        guidance.append(f"Keep the writeback scoped to the story intent in {summary}.")
    if "dependency" in selected_text or "blocker" in selected_text:
        guidance.append(f"Call out the dependencies and sequencing constraints for {summary}.")
    if "description" in selected_text or "clarify" in selected_text or "context" in selected_text:
        guidance.append(f"Tighten the Jira description so the implementation intent for {summary} is explicit.")
    if "approval" in selected_text or "review" in selected_text or "audit" in selected_text:
        guidance.append(f"Preserve reviewer approval and auditability requirements for {summary}.")
    if "writeback" in selected_text:
        guidance.append(f"Describe the Jira writeback behavior and content ownership boundaries for {summary}.")
    if "ui" in selected_text or "console" in selected_text or "screen" in selected_text:
        guidance.append(f"Describe the UI interaction changes expected for {summary}.")
    if "api" in selected_text or "contract" in selected_text or "rest" in selected_text or "openapi" in selected_text:
        guidance.append(f"Capture the API or contract changes required for {summary}.")
    if "storage" in selected_text or "filesystem" in selected_text or "file" in selected_text or "db" in selected_text or "queue" in selected_text or "event" in selected_text:
        guidance.append(f"Explain the persistence or eventing implications associated with {summary}.")
    if "test" in selected_text or "coverage" in selected_text or "regression" in selected_text:
        guidance.append(f"Include validation and regression expectations for {summary}.")
    if not guidance and selected_segments:
        guidance.append(f"Refine the implementation brief so it stays aligned with the current story summary: {summary}.")
    return guidance[:3]


def _evidence_passages(record: EvidenceRecord) -> list[str]:
    if record.source_type == "confluence":
        title = str(record.content.get("title", "")).strip()
        summary_text = re.sub(r"<[^>]+>", " ", str(record.content.get("summary", "")))
        sentence_candidates = [
            candidate.strip()
            for candidate in re.split(r"[.\n;]+", summary_text)
            if candidate.strip()
        ]
        candidates = [title] if title else []
        candidates.extend(sentence_candidates[:8])
        return [candidate for candidate in candidates if candidate]
    if record.source_type == "bitbucket":
        title = str(record.content.get("title", "")).strip()
        changed_files = ", ".join(record.content.get("changed_files", []))
        candidates = [
            title,
            f"Changed files: {changed_files}" if changed_files else "",
            f"Repository {record.content.get('repository', '')} branch {record.content.get('branch', '')}".strip(),
        ]
        for file_path in record.content.get("changed_files", [])[:8]:
            cleaned_path = str(file_path).strip()
            if cleaned_path:
                candidates.append(f"Touches file: {cleaned_path}")
        return [candidate.strip() for candidate in candidates if candidate.strip()]
    generic_summary = re.sub(r"<[^>]+>", " ", str(record.content.get("summary", "")))
    generic_candidates = [
        str(record.content.get("title", "")).strip(),
        _sentence_or_default(generic_summary, "").strip(),
    ]
    return [candidate for candidate in generic_candidates if candidate]


def _issue_query_terms(normalized_issue: dict[str, Any], user_input_text: str) -> tuple[set[str], set[str]]:
    issue_terms = _issue_terms(normalized_issue)
    intent_terms = _tokenize(_strip_links(user_input_text))
    return issue_terms, intent_terms


def _select_relevant_source_text(
    normalized_issue: dict[str, Any],
    evidence: list[EvidenceRecord],
    user_input_text: str,
) -> dict[str, str]:
    ranked = _rank_relevant_source_text(normalized_issue, evidence, user_input_text)
    selected: dict[str, str] = {}
    for source_type, passages in ranked.items():
        if passages:
            selected[source_type] = passages[0]
    return selected


def _rank_relevant_source_text(
    normalized_issue: dict[str, Any],
    evidence: list[EvidenceRecord],
    user_input_text: str,
) -> dict[str, list[str]]:
    issue_terms, intent_terms = _issue_query_terms(normalized_issue, user_input_text)
    primary_terms = _summary_terms(normalized_issue)
    scored_by_type: dict[str, list[tuple[int, str]]] = {}
    for record in evidence:
        for passage in _evidence_passages(record):
            passage_terms = _tokenize(passage)
            primary_overlap = passage_terms & primary_terms
            issue_overlap = passage_terms & issue_terms
            intent_overlap = passage_terms & intent_terms
            score = (
                (sum(len(term) for term in primary_overlap) * 5)
                + (sum(len(term) for term in issue_overlap) * 2)
                + sum(len(term) for term in intent_overlap)
            )
            if normalized_issue.get("issue_key", "").lower() in passage.lower():
                score += 4
            if record.source_type == "confluence" and len(passage.split()) <= 4:
                score -= 3
            if record.source_type == "bitbucket" and passage == str(record.content.get("title", "")).strip():
                score += 10
            if record.source_type == "bitbucket" and passage.startswith("Changed files:"):
                score -= 5
            if record.source_type == "bitbucket" and passage.startswith("Repository "):
                score -= 2
            if record.source_type == "bitbucket" and not passage.startswith("Repository ") and not passage.startswith("Changed files:"):
                score += 2
            if score <= 0:
                continue
            scored_by_type.setdefault(record.source_type, []).append((score, passage))

    ranked: dict[str, list[str]] = {}
    for source_type, candidates in scored_by_type.items():
        unique_passages: list[str] = []
        seen: set[str] = set()
        for _, passage in sorted(candidates, key=lambda entry: entry[0], reverse=True):
            normalized = passage.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            unique_passages.append(passage)
            if len(unique_passages) >= 3:
                break
        if unique_passages:
            ranked[source_type] = unique_passages
    return ranked


def _build_context_packet(
    normalized_issue: dict[str, Any],
    evidence: list[EvidenceRecord],
    user_input_text: str = "",
) -> dict[str, Any]:
    ranked = _rank_relevant_source_text(normalized_issue, evidence, user_input_text)
    evidence_refs = [f"{record.source_type}:{record.source_ref}" for record in evidence]
    missing_signals: list[str] = []
    if not normalized_issue.get("description", "").strip():
        missing_signals.append("jira_description_missing_or_placeholder")
    if not normalized_issue.get("acceptance_criteria"):
        missing_signals.append("acceptance_criteria_missing")
    if not normalized_issue.get("dependencies"):
        missing_signals.append("dependencies_missing")
    if not normalized_issue.get("edge_cases"):
        missing_signals.append("edge_cases_missing")
    if not evidence_refs:
        missing_signals.append("external_evidence_missing")

    return {
        "issue_key": normalized_issue.get("issue_key", ""),
        "summary": normalized_issue.get("summary", ""),
        "problem_hint": _build_problem_statement(
            normalized_issue.get("issue_key", ""),
            normalized_issue,
            evidence,
            user_input_text,
        ),
        "acceptance_seed": _build_acceptance_questions(normalized_issue, evidence, user_input_text),
        "confluence_context": ranked.get("confluence", []),
        "bitbucket_context": ranked.get("bitbucket", []),
        "evidence_refs": evidence_refs,
        "missing_signals": missing_signals,
    }


def _compute_quality_rubric(
    normalized_issue: dict[str, Any],
    analysis: dict[str, Any],
    context_packet: dict[str, Any],
) -> dict[str, Any]:
    suggested_description = str(analysis.get("suggested_description", "")).strip()
    problem_statement = str(analysis.get("problem_statement", "")).strip()
    acceptance = [str(item).strip() for item in analysis.get("acceptance_criteria", []) if str(item).strip()]
    next_step = str(analysis.get("recommended_next_step", "")).strip()
    guidance = [str(item).strip() for item in analysis.get("guidance", []) if str(item).strip()]
    evidence_refs = context_packet.get("evidence_refs", [])
    context_terms = _tokenize(
        " ".join(
            context_packet.get("confluence_context", [])[:2]
            + context_packet.get("bitbucket_context", [])[:2]
            + [normalized_issue.get("summary", "")]
        )
    )
    description_terms = _tokenize(suggested_description)
    overlap = len(context_terms & description_terms)

    dimensions = {
        "problem_clarity": min(5, 2 + (1 if len(problem_statement) >= 40 else 0) + (1 if "problem to solve" in problem_statement.lower() else 0) + (1 if normalized_issue.get("summary", "").lower() in (problem_statement + " " + suggested_description).lower() else 0)),
        "acceptance_testability": min(5, len(acceptance) + (1 if any(any(token in criterion.lower() for token in ("verify", "validate", "test", "reviewable", "explicit")) for criterion in acceptance) else 0)),
        "dependency_specificity": min(5, 1 + (2 if normalized_issue.get("dependencies") else 0) + (1 if any("dependenc" in criterion.lower() for criterion in acceptance + guidance) else 0) + (1 if any("sequence" in line.lower() or "order" in line.lower() for line in guidance) else 0)),
        "evidence_grounding": min(5, (2 if evidence_refs else 0) + min(2, overlap) + (1 if bool(context_packet.get("confluence_context") or context_packet.get("bitbucket_context")) else 0)),
        "actionability": min(5, 1 + (1 if len(suggested_description) >= 60 else 0) + (1 if next_step else 0) + (1 if guidance else 0) + (1 if any(any(token in next_step.lower() for token in ("review", "approve", "confirm", "implement", "validate")) for _ in [0]) else 0)),
    }
    total_score = sum(dimensions.values())
    return {
        "dimensions": dimensions,
        "total_score": total_score,
        "max_score": 25,
        "threshold": QUALITY_CRITIC_MIN_TOTAL_SCORE,
    }


def _validate_analysis_consistency(
    normalized_issue: dict[str, Any],
    analysis: dict[str, Any],
    context_packet: dict[str, Any],
) -> tuple[list[str], list[str]]:
    flags: list[str] = []
    notes: list[str] = []
    suggested_description = str(analysis.get("suggested_description", "")).strip()
    acceptance = [str(item).strip() for item in analysis.get("acceptance_criteria", []) if str(item).strip()]
    recommended_next_step = str(analysis.get("recommended_next_step", "")).strip()
    lowered_blob = " ".join([suggested_description, " ".join(acceptance), recommended_next_step]).lower()
    context_terms = _tokenize(
        " ".join(context_packet.get("confluence_context", [])[:2] + context_packet.get("bitbucket_context", [])[:2])
    )
    generated_terms = _tokenize(lowered_blob)
    context_overlap = len(context_terms & generated_terms)

    if _looks_like_placeholder_description(suggested_description, normalized_issue.get("issue_key", "")):
        flags.append("placeholder_description_detected")
        notes.append("Suggested description still looks like a placeholder.")
    if not acceptance:
        flags.append("missing_generated_acceptance_criteria")
        notes.append("Generated analysis is missing testable acceptance criteria.")
    if normalized_issue.get("dependencies") and "dependenc" not in lowered_blob:
        flags.append("dependency_alignment_gap")
        notes.append("Dependencies exist on the Jira but are not reflected in generated guidance.")
    if context_packet.get("evidence_refs") and context_terms and context_overlap == 0:
        flags.append("weak_evidence_grounding")
        notes.append("Linked evidence exists but generated text does not reference design or implementation context.")
    if "out of scope" in lowered_blob and any(token in lowered_blob for token in ("implement everything", "all modules", "entire system rewrite")):
        flags.append("scope_contradiction")
        notes.append("Scope language appears contradictory or too broad for a Jira story.")
    if not recommended_next_step:
        flags.append("missing_recommended_next_step")
        notes.append("Recommended next step is missing.")
    return _dedupe_text(flags), _dedupe_text(notes)


def _apply_quality_critic_loop(
    normalized_issue: dict[str, Any],
    evidence: list[EvidenceRecord],
    analysis: dict[str, Any],
    user_input_text: str = "",
) -> dict[str, Any]:
    enhanced = dict(analysis or {})
    context_packet = _build_context_packet(normalized_issue, evidence, user_input_text)
    passes: list[dict[str, Any]] = []

    for iteration in range(1, QUALITY_CRITIC_MAX_PASSES + 1):
        rubric = _compute_quality_rubric(normalized_issue, enhanced, context_packet)
        consistency_flags, consistency_notes = _validate_analysis_consistency(normalized_issue, enhanced, context_packet)
        passes.append(
            {
                "iteration": iteration,
                "rubric_total_score": rubric["total_score"],
                "consistency_flags": list(consistency_flags),
            }
        )
        if rubric["total_score"] >= QUALITY_CRITIC_MIN_TOTAL_SCORE and not consistency_flags:
            break

        if not _has_text(enhanced.get("problem_statement")):
            enhanced["problem_statement"] = _build_problem_statement(
                normalized_issue.get("issue_key", ""),
                normalized_issue,
                evidence,
                user_input_text,
            )
        if _looks_like_placeholder_description(str(enhanced.get("suggested_description", "")).strip(), normalized_issue.get("issue_key", "")):
            enhanced["suggested_description"] = _build_suggested_description(normalized_issue, evidence, user_input_text)
        if not _has_values(enhanced.get("acceptance_criteria")):
            enhanced["acceptance_criteria"] = _build_effective_acceptance_criteria(normalized_issue, enhanced, evidence)
        if not _has_values(enhanced.get("guidance")):
            guidance = _derive_story_guidance(normalized_issue, user_input_text)
            if not guidance:
                guidance = [
                    f"Keep the scope tightly aligned to {normalized_issue.get('summary', normalized_issue.get('issue_key', 'this Jira'))}.",
                    "Tie each acceptance criterion to a verifiable behavior or validation.",
                ]
            enhanced["guidance"] = guidance[:3]
        if not _has_text(enhanced.get("recommended_next_step")):
            enhanced["recommended_next_step"] = (
                f"Review Jira {normalized_issue.get('issue_key', '')}, confirm acceptance criteria and dependency alignment, then approve writeback."
            )
        if not _has_values(enhanced.get("risks")):
            enhanced["risks"] = [
                "Stale Jira or evidence context can invalidate the generated update.",
                "Missing dependency clarification can cause downstream implementation churn.",
            ]
        if not _has_values(enhanced.get("error_handling")):
            enhanced["error_handling"] = [
                "Block writeback if source hash changed or required context is missing.",
                "Retry evidence fetch idempotently and request reviewer clarification when ambiguity remains.",
            ]
        if not _has_values(enhanced.get("out_of_scope")):
            enhanced["out_of_scope"] = [
                f"Changes outside the scoped Jira intent for {normalized_issue.get('summary', '')}.",
            ]
        if not _has_values(enhanced.get("nfrs")):
            enhanced["nfrs"] = [
                "Perf budget: keep enrichment response latency bounded for reviewer workflows.",
                "Security notes: limit writeback to the AI-owned Jira description block.",
            ]

        rubric = _compute_quality_rubric(normalized_issue, enhanced, context_packet)
        consistency_flags, consistency_notes = _validate_analysis_consistency(normalized_issue, enhanced, context_packet)
        if rubric["total_score"] >= QUALITY_CRITIC_MIN_TOTAL_SCORE and not consistency_flags:
            break

    final_rubric = _compute_quality_rubric(normalized_issue, enhanced, context_packet)
    final_flags, final_notes = _validate_analysis_consistency(normalized_issue, enhanced, context_packet)
    enhanced["quality_rubric"] = final_rubric
    enhanced["consistency_flags"] = final_flags
    enhanced["consistency_notes"] = final_notes
    enhanced["context_packet"] = context_packet
    enhanced["critic"] = {
        "max_passes": QUALITY_CRITIC_MAX_PASSES,
        "passes": passes,
        "finalized_after_passes": len(passes),
        "threshold": QUALITY_CRITIC_MIN_TOTAL_SCORE,
    }
    return enhanced


def _acceptance_context_label(normalized_issue: dict[str, Any], evidence: list[EvidenceRecord], user_input_text: str = "") -> str:
    selected = _select_relevant_source_text(normalized_issue, evidence, user_input_text)
    for source_type in ("confluence", "bitbucket"):
        passage = selected.get(source_type, "").strip()
        if passage:
            return _sentence_or_default(passage, normalized_issue.get("summary", "this Jira"))
    return normalized_issue.get("summary", "this Jira").strip() or "this Jira"


def _build_acceptance_questions(
    normalized_issue: dict[str, Any],
    evidence: list[EvidenceRecord],
    user_input_text: str = "",
) -> list[str]:
    summary = normalized_issue.get("summary", "").strip() or normalized_issue.get("issue_key", "this Jira")
    context_label = _acceptance_context_label(normalized_issue, evidence, user_input_text)
    acceptance = [value.strip() for value in normalized_issue.get("acceptance_criteria", []) if value and value.strip()]
    dependencies = [value.strip() for value in normalized_issue.get("dependencies", []) if value and value.strip()]
    edge_cases = [value.strip() for value in normalized_issue.get("edge_cases", []) if value and value.strip()]

    questions: list[str] = []
    if acceptance:
        questions.append(
            f"Which acceptance criteria should remain mandatory for {summary} based on the design context: {context_label}?"
        )
    else:
        questions.append(
            f"What acceptance criteria should be added for {summary} so completion is testable and reviewable?"
        )
        questions.append(
            f"Based on the linked design context ({context_label}), which behaviors, validations, or failure handling must be explicitly accepted for {summary}?"
        )
    if dependencies:
        questions.append(
            f"Which acceptance criterion should verify dependency alignment for {summary}, especially around {dependencies[0]}?"
        )
    elif edge_cases:
        questions.append(
            f"Which edge case should become explicit acceptance criteria for {summary}, especially {edge_cases[0]}?"
        )
    else:
        questions.append(f"What acceptance evidence would prove {summary} is complete end to end?")
    return _dedupe_text(questions)[:3]


def _build_problem_statement(
    issue_key: str,
    normalized_issue: dict[str, Any],
    evidence: list[EvidenceRecord],
    user_input_text: str = "",
) -> str:
    summary = normalized_issue.get("summary", "").strip() or issue_key
    description = str(normalized_issue.get("description", "")).strip()
    if description and not _looks_like_placeholder_description(description, issue_key):
        return f"{issue_key}: {summary}. Problem to solve: {_sentence_or_default(description, description)}"

    selected = _select_relevant_source_text(normalized_issue, evidence, user_input_text)
    context_bits: list[str] = []
    if selected.get("confluence"):
        context_bits.append(f"Design context: {_sentence_or_default(selected['confluence'], selected['confluence'])}")
    if selected.get("bitbucket"):
        context_bits.append(f"Implementation context: {_sentence_or_default(selected['bitbucket'], selected['bitbucket'])}")
    context_text = " ".join(context_bits).strip()
    if context_text:
        return f"{issue_key}: {summary}. Problem to solve: clarify the requested outcome and constraints for this Jira. {context_text}"
    return f"{issue_key}: {summary}. Problem to solve: clarify the requested outcome, scope boundaries, and completion conditions for this Jira."


def _build_codex_context_brief(
    issue_key: str,
    normalized_issue: dict[str, Any],
    acceptance: list[str],
    evidence_refs: list[str],
    problem_statement: str,
    context_packet: dict[str, Any] | None = None,
) -> dict[str, str]:
    context_packet = context_packet or {}
    dependencies = _bullet_join(
        normalized_issue.get("dependencies", []),
        "No explicit dependency is recorded in Jira yet.",
    )
    edge_cases = _bullet_join(
        normalized_issue.get("edge_cases", []),
        "No explicit edge case is recorded in Jira yet.",
    )
    evidence_line = ", ".join(evidence_refs) if evidence_refs else "No linked Confluence page or Bitbucket PR is attached yet."
    confluence_context = "; ".join(context_packet.get("confluence_context", [])[:2]).strip() or "No Confluence context selected."
    bitbucket_context = "; ".join(context_packet.get("bitbucket_context", [])[:2]).strip() or "No Bitbucket context selected."
    missing_signals = ", ".join(context_packet.get("missing_signals", [])) or "No major context gaps detected."
    return {
        "problem_statement": problem_statement,
        "acceptance_focus": acceptance[0],
        "dependency_focus": dependencies[0],
        "edge_case_focus": edge_cases[0],
        "evidence_context": evidence_line,
        "confluence_context": confluence_context,
        "bitbucket_context": bitbucket_context,
        "missing_signals": missing_signals,
    }


def _build_codex_user_input_prompt(
    issue_key: str,
    context_brief: dict[str, str],
    interaction: dict[str, Any],
    recommended_next_step: str,
) -> str:
    current_question = str(interaction.get("current_question", "")).strip()
    lines = [
        f"Codex is improving Jira {issue_key}. Provide Jira-specific context only.",
        f"Goal: produce a clearer, implementation-ready Jira update for {issue_key} that raises confidence and removes ambiguity.",
        "Focus on missing decisions, concrete scope, dependencies, validation evidence, and completion criteria.",
        "Desired output: a tighter suggested description, stronger acceptance criteria, clearer risks, and a more actionable next step.",
        f"Problem statement: {context_brief['problem_statement']}",
        f"Current acceptance focus: {context_brief['acceptance_focus']}",
        f"Dependency focus: {context_brief['dependency_focus']}",
        f"Edge-case focus: {context_brief['edge_case_focus']}",
        f"Linked evidence: {context_brief['evidence_context']}",
        f"Confluence context: {context_brief.get('confluence_context', '')}",
        f"Bitbucket context: {context_brief.get('bitbucket_context', '')}",
        f"Known context gaps: {context_brief.get('missing_signals', '')}",
    ]
    answers = _normalize_interaction_answers(interaction.get("answers", []))
    if answers:
        lines.append("Answered so far:")
        for index, entry in enumerate(answers, start=1):
            lines.append(f"{index}. Q: {entry['question']}")
            lines.append(f"   A: {entry['answer']}")
    if current_question:
        lines.append("Current question to unblock the Jira:")
        lines.append(current_question)
    else:
        lines.append("All current Codex questions are answered.")
    lines.append("What to provide:")
    if current_question:
        lines.append(f"- Answer the current question directly: {current_question}")
    lines.append("- Add concrete constraints, target environments, dependencies, validation evidence, and edge cases if they are known.")
    lines.append("- Avoid generic guidance that is not specific to this Jira.")
    lines.append(f"Completion target after your input: {recommended_next_step}")
    return "\n".join(lines)


def _normalize_interaction_answers(raw_answers: Any) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for entry in raw_answers or []:
        if not isinstance(entry, dict):
            continue
        question = " ".join(str(entry.get("question", "")).split()).strip()
        answer = " ".join(str(entry.get("answer", "")).split()).strip()
        if not question or not answer:
            continue
        normalized.append({"question": question, "answer": answer})
    return normalized


def _build_interaction_transcript(answers: list[dict[str, str]]) -> str:
    lines: list[str] = []
    for index, entry in enumerate(answers, start=1):
        lines.append(f"Question {index}: {entry['question']}")
        lines.append(f"Answer {index}: {entry['answer']}")
    return "\n".join(lines)


def _build_interaction_state(
    open_questions: list[str],
    existing_interaction: dict[str, Any] | None = None,
) -> dict[str, Any]:
    existing_interaction = existing_interaction or {}
    answers = _normalize_interaction_answers(existing_interaction.get("answers", []))
    merged_questions = _dedupe_text(
        [entry["question"] for entry in answers]
        + list(existing_interaction.get("questions", []))
        + list(open_questions)
    )
    answered_questions = {entry["question"].lower() for entry in answers}
    current_question = ""
    current_index = 0
    for index, question in enumerate(merged_questions, start=1):
        if question.lower() not in answered_questions:
            current_question = question
            current_index = index
            break
    transcript = _build_interaction_transcript(answers)
    remaining = max(0, len(merged_questions) - len(answers))
    return {
        "questions": merged_questions,
        "answers": answers,
        "current_question": current_question,
        "current_index": current_index,
        "remaining_questions": remaining,
        "status": "awaiting_input" if current_question else "complete",
        "transcript": transcript,
    }


def _apply_interaction_payload(payload: dict[str, Any], existing_interaction: dict[str, Any] | None = None) -> dict[str, Any]:
    interaction = _build_interaction_state(payload.get("fallback_questions", []), existing_interaction)
    payload["interaction"] = interaction
    payload["codex_user_input_prompt"] = _build_codex_user_input_prompt(
        str(payload.get("issue_key", "")),
        payload.get("context_brief", {}),
        interaction,
        str(payload.get("recommended_next_step", "")),
    )
    return payload


def _build_agent_state(
    payload: dict[str, Any],
    confidence: int,
    approval_required: bool,
    previous_state: dict[str, Any] | None = None,
    history_entry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    previous_state = previous_state or {}
    history = list(previous_state.get("history", []))
    if history_entry:
        history.append(history_entry)
    iterations_completed = int(previous_state.get("iterations_completed", 0))
    if history_entry and history_entry.get("action") == "self_refine":
        iterations_completed += 1

    interaction = payload.get("interaction", {})
    remaining_questions = int(interaction.get("remaining_questions", 0))
    if confidence < TARGET_CONFIDENCE_SCORE and iterations_completed < MAX_AUTONOMOUS_AGENT_STEPS:
        next_action = "self_refine"
        status = "autonomous_refining"
    elif remaining_questions > 0:
        next_action = "ask_user"
        status = "awaiting_user_input"
    elif approval_required:
        next_action = "await_approval"
        status = "awaiting_approval"
    else:
        next_action = "ready_for_writeback"
        status = "ready"
    return {
        "goal": f"Improve Jira {payload.get('issue_key', '')} until the draft is distinct, reviewable, and sufficiently confident.",
        "iterations_completed": iterations_completed,
        "max_iterations": MAX_AUTONOMOUS_AGENT_STEPS,
        "next_action": next_action,
        "status": status,
        "remaining_questions": remaining_questions,
        "history": history[-10:],
    }


def _autonomous_refinement_input(draft_payload: dict[str, Any], agent_state: dict[str, Any]) -> str:
    interaction = draft_payload.get("interaction", {})
    open_questions = [str(question).strip() for question in interaction.get("questions", []) if str(question).strip()]
    lines = [
        "The autonomous Jira improvement loop is continuing.",
        "Refine this Jira using only Jira content and linked evidence.",
        "Improve the separation and quality of problem_statement, suggested_description, and acceptance_criteria.",
        f"Current confidence: {draft_payload.get('confidence', 0)}/10.",
        f"Autonomous iteration: {int(agent_state.get('iterations_completed', 0)) + 1}/{MAX_AUTONOMOUS_AGENT_STEPS}.",
    ]
    if draft_payload.get("context_brief", {}).get("problem_statement"):
        lines.append(f"Current problem statement: {draft_payload['context_brief']['problem_statement']}")
    if draft_payload.get("suggested_description"):
        lines.append(f"Current suggested description: {draft_payload['suggested_description']}")
    if draft_payload.get("acceptance_criteria"):
        lines.append("Current acceptance criteria:")
        lines.extend(f"- {value}" for value in draft_payload.get("acceptance_criteria", [])[:3])
    if open_questions:
        lines.append("Outstanding questions:")
        lines.extend(f"- {question}" for question in open_questions[:3])
    lines.append("Do not ask the user yet if you can improve the Jira from existing context.")
    return "\n".join(lines)


def _build_effective_acceptance_criteria(
    normalized_issue: dict[str, Any],
    analysis: dict[str, Any],
    evidence: list[EvidenceRecord],
) -> list[str]:
    summary = normalized_issue.get("summary", "").strip() or normalized_issue.get("issue_key", "this Jira")
    acceptance = [value.strip() for value in normalized_issue.get("acceptance_criteria", []) if value and value.strip()]
    guidance = [str(value).strip() for value in analysis.get("guidance", []) if str(value).strip()]
    dependencies = [value.strip() for value in normalized_issue.get("dependencies", []) if value and value.strip()]
    edge_cases = [value.strip() for value in normalized_issue.get("edge_cases", []) if value and value.strip()]
    context_label = _acceptance_context_label(normalized_issue, evidence)

    effective: list[str] = list(acceptance)
    if not effective:
        effective.append(f"Completion for {summary} is reviewable with explicit behavior, validations, and failure handling.")
        effective.append(f"The delivered behavior for {summary} stays aligned with the relevant design context: {context_label}.")
    if guidance:
        effective.append(f"Reviewer-ready scope expectation for {summary}: {guidance[0]}")
    if dependencies:
        effective.append(f"Dependency alignment is explicit and verified for {dependencies[0]}.")
    if edge_cases:
        effective.append(f"Edge-case handling explicitly covers {edge_cases[0]}.")
    if not dependencies and not edge_cases and len(effective) < 2:
        effective.append(f"Required behaviors, validations, and failure handling for {summary} are explicitly captured and testable.")
    return _dedupe_text(effective)[:3]


def _build_suggested_description(
    normalized_issue: dict[str, Any],
    evidence: list[EvidenceRecord],
    user_input_text: str = "",
) -> str:
    issue_key = normalized_issue["issue_key"]
    jira_summary = normalized_issue["summary"]
    jira_description = normalized_issue["description"]
    if jira_description and not _looks_like_placeholder_description(jira_description, issue_key):
        return jira_description

    selected = _select_relevant_source_text(normalized_issue, evidence, user_input_text)
    suggested_parts = [f"{jira_summary}."]
    if "confluence" in selected:
        suggested_parts.append(f"Confluence context indicates: {_sentence_or_default(selected['confluence'], selected['confluence'])}.")
    if "bitbucket" in selected:
        suggested_parts.append(f"Related Bitbucket implementation suggests: {_sentence_or_default(selected['bitbucket'], selected['bitbucket'])}.")
    if len(selected) > 1:
        suggested_parts.append("Additional linked sources can refine the implementation boundaries during review.")
    return " ".join(part.strip() for part in suggested_parts if part.strip())


class SemanticReasoner(Protocol):
    def analyze_issue(
        self,
        normalized_issue: dict[str, Any],
        evidence: list[EvidenceRecord],
        user_input_text: str = "",
    ) -> dict[str, Any]:
        ...


class HeuristicSemanticReasoner:
    def analyze_issue(
        self,
        normalized_issue: dict[str, Any],
        evidence: list[EvidenceRecord],
        user_input_text: str = "",
    ) -> dict[str, Any]:
        summary = normalized_issue.get("summary", "")
        dependencies = _bullet_join(
            normalized_issue.get("dependencies", []),
            "No explicit dependency is recorded in Jira yet.",
        )
        edge_cases = _bullet_join(
            normalized_issue.get("edge_cases", []),
            "No explicit edge case is recorded in Jira yet.",
        )
        guidance = _derive_story_guidance(normalized_issue, user_input_text)
        acceptance_questions = _build_acceptance_questions(normalized_issue, evidence, user_input_text)
        problem_statement = _build_problem_statement(normalized_issue.get("issue_key", ""), normalized_issue, evidence, user_input_text)
        acceptance_criteria = _build_effective_acceptance_criteria(normalized_issue, {"guidance": guidance}, evidence)
        return {
            "problem_statement": problem_statement,
            "suggested_description": _build_suggested_description(normalized_issue, evidence, user_input_text),
            "acceptance_criteria": acceptance_criteria,
            "guidance": guidance,
            "out_of_scope": [
                f"Changes outside the scoped Jira intent for {summary}.",
                "Unapproved workflow transitions or writes outside the AI update block.",
            ],
            "error_handling": [
                f"Re-read source Jira data for {summary} before writeback and invalidate stale approvals on material changes.",
                "Retry external evidence lookup idempotently and fall back to reviewer clarification when evidence is insufficient.",
            ],
            "nfrs": [
                "Perf budget: keep enrichment latency bounded for interactive review flows.",
                "Security notes: restrict writes to the AI update block and avoid expanding Jira edit scope.",
            ],
            "open_questions": acceptance_questions if acceptance_questions else [f"Clarify missing implementation details for {summary} before writeback."],
            "recommended_next_step": (
                f"Review the generated AI update for {normalized_issue.get('issue_key', '')}, confirm dependencies ({dependencies[0]}), and then approve or refine it."
            ),
            "risks": [
                "Stale issue content can invalidate approval.",
                "Missing authoritative source can reduce confidence.",
                f"Key edge case to confirm: {edge_cases[0]}",
            ],
            "reasoning_mode": "heuristic",
        }


class ModelSemanticReasoner:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        fallback: SemanticReasoner | None = None,
        request_json=_default_request_json,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.fallback = fallback or HeuristicSemanticReasoner()
        self.request_json = request_json

    def analyze_issue(
        self,
        normalized_issue: dict[str, Any],
        evidence: list[EvidenceRecord],
        user_input_text: str = "",
    ) -> dict[str, Any]:
        try:
            context_packet = _build_context_packet(normalized_issue, evidence, user_input_text)
            payload = {
                "model": self.model,
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are an implementation analyst for Jira enrichment. "
                            "Use the Jira summary as the primary signal. "
                            "Use provided Confluence and Bitbucket evidence to produce text specific to this Jira only. "
                            "Do not copy raw user input into the result. "
                            "Return JSON with keys problem_statement, suggested_description, acceptance_criteria, guidance, out_of_scope, error_handling, nfrs, open_questions, recommended_next_step, and risks. "
                            "problem_statement must describe why this Jira exists, not implementation steps. "
                            "acceptance_criteria must be an array of concise, testable completion criteria specific to this Jira. "
                            "open_questions must include Jira-specific acceptance-criteria clarifications derived from the summary and linked design evidence when criteria are missing or underspecified. "
                            "guidance must be an array of up to 3 concise actionable strings. "
                            "All array fields must contain concise strings specific to this Jira. "
                            "recommended_next_step must be a single concise string."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "issue": normalized_issue,
                                "user_input_text": user_input_text,
                                "context_packet": context_packet,
                                "evidence": [self._compact_evidence(record) for record in evidence],
                            },
                            indent=2,
                        ),
                    },
                ],
            }
            response = self.request_json(
                "POST",
                f"{self.base_url}/v1/chat/completions",
                {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                payload=payload,
            )
            content = response["choices"][0]["message"]["content"]
            structured = json.loads(content)
            problem_statement = str(structured.get("problem_statement", "")).strip()
            suggested_description = str(structured.get("suggested_description", "")).strip()
            acceptance_criteria = [str(item).strip() for item in structured.get("acceptance_criteria", []) if str(item).strip()]
            guidance = [str(item).strip() for item in structured.get("guidance", []) if str(item).strip()]
            if not suggested_description:
                raise ValueError("Model response missing suggested_description")
            return {
                "problem_statement": problem_statement,
                "suggested_description": suggested_description,
                "acceptance_criteria": acceptance_criteria[:3],
                "guidance": guidance[:3],
                "out_of_scope": [str(item).strip() for item in structured.get("out_of_scope", []) if str(item).strip()][:3],
                "error_handling": [str(item).strip() for item in structured.get("error_handling", []) if str(item).strip()][:3],
                "nfrs": [str(item).strip() for item in structured.get("nfrs", []) if str(item).strip()][:3],
                "open_questions": [str(item).strip() for item in structured.get("open_questions", []) if str(item).strip()][:3],
                "recommended_next_step": str(structured.get("recommended_next_step", "")).strip(),
                "risks": [str(item).strip() for item in structured.get("risks", []) if str(item).strip()][:3],
                "reasoning_mode": "model",
            }
        except Exception:  # noqa: BLE001
            result = self.fallback.analyze_issue(normalized_issue, evidence, user_input_text)
            result["reasoning_mode"] = "heuristic_fallback"
            return result

    def plan_epic_stories(
        self,
        epic_key: str,
        epic_summary: str,
        normalized_epic: dict[str, Any],
        evidence: list[EvidenceRecord],
        existing_summaries: list[str],
    ) -> list[dict[str, Any]]:
        payload = _build_epic_story_planner_payload(epic_key, epic_summary, normalized_epic, evidence, existing_summaries)
        response = self.request_json(
            "POST",
            f"{self.base_url}/v1/chat/completions",
            {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            payload={
                "model": self.model,
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a Jira epic story planner. Return JSON only with key stories. "
                            "Each story must be grounded in the provided Jira and Confluence evidence."
                        ),
                    },
                    {"role": "user", "content": _build_epic_story_planner_prompt(payload)},
                ],
            },
        )
        content = response["choices"][0]["message"]["content"]
        structured = json.loads(content)
        return structured.get("stories", [])

    @staticmethod
    def _compact_evidence(record: EvidenceRecord) -> dict[str, Any]:
        content = record.content
        return {
            "source_type": record.source_type,
            "source_ref": record.source_ref,
            "title": content.get("title", ""),
            "summary": content.get("summary", ""),
            "repository": content.get("repository", ""),
            "branch": content.get("branch", ""),
            "changed_files": content.get("changed_files", []),
        }


class CodexExecSemanticReasoner:
    def __init__(
        self,
        codex_command: str,
        timeout_sec: int = 180,
        workdir: str | None = None,
        fallback: SemanticReasoner | None = None,
        runner=None,
    ) -> None:
        self.codex_command = codex_command
        self.timeout_sec = timeout_sec
        self.workdir = workdir
        self.fallback = fallback or HeuristicSemanticReasoner()
        self.runner = runner or subprocess.run

    def analyze_issue(
        self,
        normalized_issue: dict[str, Any],
        evidence: list[EvidenceRecord],
        user_input_text: str = "",
    ) -> dict[str, Any]:
        try:
            with tempfile.TemporaryDirectory(prefix="jira-enhancer-codex-") as tmpdir:
                schema_path = f"{tmpdir}/schema.json"
                output_path = f"{tmpdir}/output.json"
                with open(schema_path, "w", encoding="utf-8") as handle:
                    json.dump(self._response_schema(), handle, indent=2)
                prompt = self._build_prompt(normalized_issue, evidence, user_input_text)
                self.runner(
                    [
                        self.codex_command,
                        "exec",
                        "--skip-git-repo-check",
                        "--sandbox",
                        "read-only",
                        "--output-schema",
                        schema_path,
                        "-o",
                        output_path,
                        "-",
                    ],
                    input=prompt,
                    text=True,
                    capture_output=True,
                    cwd=self.workdir,
                    timeout=self.timeout_sec,
                    check=True,
                )
                with open(output_path, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
            problem_statement = str(payload.get("problem_statement", "")).strip()
            suggested_description = str(payload.get("suggested_description", "")).strip()
            if not suggested_description:
                raise ValueError("Codex exec response missing suggested_description")
            return {
                "problem_statement": problem_statement,
                "suggested_description": suggested_description,
                "acceptance_criteria": [str(item).strip() for item in payload.get("acceptance_criteria", []) if str(item).strip()][:3],
                "guidance": [str(item).strip() for item in payload.get("guidance", []) if str(item).strip()][:3],
                "out_of_scope": [str(item).strip() for item in payload.get("out_of_scope", []) if str(item).strip()][:3],
                "error_handling": [str(item).strip() for item in payload.get("error_handling", []) if str(item).strip()][:3],
                "nfrs": [str(item).strip() for item in payload.get("nfrs", []) if str(item).strip()][:3],
                "open_questions": [str(item).strip() for item in payload.get("open_questions", []) if str(item).strip()][:3],
                "recommended_next_step": str(payload.get("recommended_next_step", "")).strip(),
                "risks": [str(item).strip() for item in payload.get("risks", []) if str(item).strip()][:3],
                "reasoning_mode": "codex_exec",
            }
        except Exception:  # noqa: BLE001
            result = self.fallback.analyze_issue(normalized_issue, evidence, user_input_text)
            result["reasoning_mode"] = "codex_exec_fallback"
            return result

    def plan_epic_stories(
        self,
        epic_key: str,
        epic_summary: str,
        normalized_epic: dict[str, Any],
        evidence: list[EvidenceRecord],
        existing_summaries: list[str],
    ) -> list[dict[str, Any]]:
        with tempfile.TemporaryDirectory(prefix="jira-enhancer-epic-plan-") as tmpdir:
            schema_path = f"{tmpdir}/schema.json"
            output_path = f"{tmpdir}/output.json"
            with open(schema_path, "w", encoding="utf-8") as handle:
                json.dump(_story_plan_response_schema(), handle, indent=2)
            planner_payload = _build_epic_story_planner_payload(
                epic_key,
                epic_summary,
                normalized_epic,
                evidence,
                existing_summaries,
            )
            planner_prompt = _build_epic_story_planner_prompt(planner_payload)
            logger.info(
                "epic_breakdown.codex_plan prompt epic_key=%s prompt=%s",
                epic_key,
                planner_prompt[-24000:],
            )
            try:
                completed = self.runner(
                    [
                        self.codex_command,
                        "exec",
                        "--skip-git-repo-check",
                        "--sandbox",
                        "read-only",
                        "--output-schema",
                        schema_path,
                        "-o",
                        output_path,
                        "-",
                    ],
                    input=planner_prompt,
                    text=True,
                    capture_output=True,
                    cwd=self.workdir,
                    timeout=self.timeout_sec,
                    check=True,
                )
                logger.info(
                    "epic_breakdown.codex_plan exec_completed epic_key=%s stdout=%s stderr=%s",
                    epic_key,
                    str(getattr(completed, "stdout", "") or "")[-4000:],
                    str(getattr(completed, "stderr", "") or "")[-4000:],
                )
            except subprocess.CalledProcessError as exc:
                logger.warning(
                    "epic_breakdown.codex_plan exec_failed epic_key=%s returncode=%s stdout=%s stderr=%s",
                    epic_key,
                    exc.returncode,
                    str(exc.stdout or "")[-2000:],
                    str(exc.stderr or "")[-2000:],
                )
                raise
            with open(output_path, "r", encoding="utf-8") as handle:
                output_text = handle.read()
            logger.info(
                "epic_breakdown.codex_plan output epic_key=%s output=%s",
                epic_key,
                output_text[-24000:],
            )
            payload = json.loads(output_text)
        return payload.get("stories", [])

    @staticmethod
    def _build_prompt(normalized_issue: dict[str, Any], evidence: list[EvidenceRecord], user_input_text: str) -> str:
        compact_evidence = [ModelSemanticReasoner._compact_evidence(record) for record in evidence]
        context_packet = _build_context_packet(normalized_issue, evidence, user_input_text)
        instructions = [
            "You are generating Jira enrichment content for one Jira issue.",
            "Use the Jira summary as the primary signal.",
            "If the issue links or user input mention Confluence pages or Bitbucket PRs, use Codex-accessible tools and configured MCP servers to inspect them.",
            "Do not copy the raw user input into the output.",
            "Return distinct fields for problem_statement, suggested_description, and acceptance_criteria.",
            "problem_statement explains why the Jira exists.",
            "suggested_description explains what should be implemented.",
            "acceptance_criteria contains only concise, testable completion criteria.",
            "Open questions must ask for Jira-specific acceptance criteria and use linked design evidence when that evidence helps define completion.",
            "Return only JSON matching the provided schema.",
            "The output must be specific to this Jira and suitable for the Jira AI update format.",
        ]
        payload = {
            "issue": normalized_issue,
            "user_input_text": user_input_text,
            "context_packet": context_packet,
            "existing_evidence": compact_evidence,
        }
        return "\n".join(instructions) + "\n\nInput:\n" + json.dumps(payload, indent=2)

    @staticmethod
    def _response_schema() -> dict[str, Any]:
        string_array = {"type": "array", "items": {"type": "string"}}
        return {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "problem_statement",
                "suggested_description",
                "acceptance_criteria",
                "guidance",
                "out_of_scope",
                "error_handling",
                "nfrs",
                "open_questions",
                "recommended_next_step",
                "risks",
            ],
            "properties": {
                "problem_statement": {"type": "string"},
                "suggested_description": {"type": "string"},
                "acceptance_criteria": string_array,
                "guidance": string_array,
                "out_of_scope": string_array,
                "error_handling": string_array,
                "nfrs": string_array,
                "open_questions": string_array,
                "recommended_next_step": {"type": "string"},
                "risks": string_array,
            },
        }


class CodexBrokerSemanticReasoner:
    def __init__(
        self,
        base_url: str,
        fallback: SemanticReasoner | None = None,
        request_json=_default_request_json,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.fallback = fallback or HeuristicSemanticReasoner()
        self.request_json = request_json

    def analyze_issue(
        self,
        normalized_issue: dict[str, Any],
        evidence: list[EvidenceRecord],
        user_input_text: str = "",
    ) -> dict[str, Any]:
        try:
            payload = self.request_json(
                "POST",
                f"{self.base_url}/api/v1/analysis/analyze-issue",
                {
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                payload={
                    "issue": normalized_issue,
                    "user_input_text": user_input_text,
                    "evidence": [ModelSemanticReasoner._compact_evidence(record) for record in evidence],
                },
            )
            problem_statement = str(payload.get("problem_statement", "")).strip()
            suggested_description = str(payload.get("suggested_description", "")).strip()
            if not suggested_description:
                raise ValueError("Codex broker response missing suggested_description")
            return {
                "problem_statement": problem_statement,
                "suggested_description": suggested_description,
                "acceptance_criteria": [str(item).strip() for item in payload.get("acceptance_criteria", []) if str(item).strip()][:3],
                "guidance": [str(item).strip() for item in payload.get("guidance", []) if str(item).strip()][:3],
                "out_of_scope": [str(item).strip() for item in payload.get("out_of_scope", []) if str(item).strip()][:3],
                "error_handling": [str(item).strip() for item in payload.get("error_handling", []) if str(item).strip()][:3],
                "nfrs": [str(item).strip() for item in payload.get("nfrs", []) if str(item).strip()][:3],
                "open_questions": [str(item).strip() for item in payload.get("open_questions", []) if str(item).strip()][:3],
                "recommended_next_step": str(payload.get("recommended_next_step", "")).strip(),
                "risks": [str(item).strip() for item in payload.get("risks", []) if str(item).strip()][:3],
                "reasoning_mode": "codex_broker",
            }
        except Exception:  # noqa: BLE001
            result = self.fallback.analyze_issue(normalized_issue, evidence, user_input_text)
            result["reasoning_mode"] = "codex_broker_fallback"
            return result

    def plan_epic_stories(
        self,
        epic_key: str,
        epic_summary: str,
        normalized_epic: dict[str, Any],
        evidence: list[EvidenceRecord],
        existing_summaries: list[str],
    ) -> list[dict[str, Any]]:
        payload = self.request_json(
            "POST",
            f"{self.base_url}/api/v1/analysis/plan-epic-stories",
            {
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            payload=_build_epic_story_planner_payload(
                epic_key,
                epic_summary,
                normalized_epic,
                evidence,
                existing_summaries,
            ),
        )
        return payload.get("stories", [])


class ScopeResolver:
    def __init__(self, jira: JiraSource) -> None:
        self.jira = jira

    def resolve(self, scope_type: str, scope_value: str) -> list[str]:
        return self.jira.resolve_scope(scope_type, scope_value)


class JiraScanner:
    def __init__(self, jira: JiraSource) -> None:
        self.jira = jira

    def scan(self, issue_key: str) -> dict[str, Any]:
        issue = self.jira.get_issue(issue_key)
        normalized = normalize_jira_source(issue)
        normalized["source_hash"] = stable_hash(normalized)
        normalized["source_revision"] = source_revision(issue)
        return normalized


class ReadinessScorer:
    def score(
        self,
        normalized_issue: dict[str, Any],
        *,
        suggested_description: str = "",
        evidence_count: int = 0,
        analysis: dict[str, Any] | None = None,
        mode: str = "guided",
    ) -> tuple[int, int, list[str]]:
        flags: list[str] = []
        analysis = analysis or {}
        source_has_description = _has_text(normalized_issue["description"])
        generated_has_description = _has_text(suggested_description)
        link_count = len(normalized_issue["links"].get("confluence_pages", [])) + len(normalized_issue["links"].get("bitbucket_prs", []))
        generated_acceptance = _has_values(analysis.get("acceptance_criteria"))
        generated_problem_statement = _has_text(analysis.get("problem_statement"))
        effective_description = normalized_issue["description"] or suggested_description.strip()
        rubric = analysis.get("quality_rubric", {}) if isinstance(analysis.get("quality_rubric", {}), dict) else {}
        rubric_total = int(rubric.get("total_score", 0) or 0)
        consistency_flags = [str(flag).strip() for flag in analysis.get("consistency_flags", []) if str(flag).strip()]
        test_gap_analysis = analysis.get("test_case_gap_analysis")
        if not isinstance(test_gap_analysis, dict):
            test_gap_analysis = _analyze_test_case_gaps(normalized_issue, analysis)
        missing_test_samples = [str(value).strip() for value in test_gap_analysis.get("missing", []) if str(value).strip()]
        missing_unit_test_sample = "unit" in missing_test_samples
        missing_integration_test_sample = "integration" in missing_test_samples

        source_score = 0.0
        if (mode == "guided" and effective_description) or (mode != "guided" and source_has_description):
            source_score += 0.25
        else:
            flags.append("missing_description")
        if normalized_issue["acceptance_criteria"]:
            source_score += 0.25
        else:
            flags.append("missing_acceptance_criteria")
        if normalized_issue["dependencies"]:
            source_score += 0.15
        else:
            flags.append("missing_dependencies")
        if link_count or evidence_count:
            source_score += 0.15
        else:
            flags.append("limited_external_context")
        if normalized_issue["comments"] or normalized_issue["worklogs"]:
            source_score += 0.10
        else:
            flags.append("missing_activity_context")
        if normalized_issue["edge_cases"]:
            source_score += 0.10
        else:
            flags.append("missing_edge_cases")
        if missing_unit_test_sample:
            flags.append("missing_unit_test_case_sample")
        if missing_integration_test_sample:
            flags.append("missing_integration_test_case_sample")
        if missing_test_samples:
            flags.append("test_case_gap")
        if rubric_total and rubric_total < QUALITY_CRITIC_MIN_TOTAL_SCORE:
            flags.append("low_rubric_score")
        for consistency_flag in consistency_flags:
            if consistency_flag not in flags:
                flags.append(consistency_flag)

        if mode == "create_run":
            analysis_bonus = 0.0
            if generated_has_description:
                analysis_bonus += 0.03
            if generated_problem_statement:
                analysis_bonus += 0.02
            if generated_acceptance:
                analysis_bonus += 0.03
            if _has_values(analysis.get("guidance")):
                analysis_bonus += 0.02
            if _has_values(analysis.get("risks")):
                analysis_bonus += 0.03
            if _has_values(analysis.get("error_handling")):
                analysis_bonus += 0.03
            if _has_values(analysis.get("nfrs")):
                analysis_bonus += 0.02
            if _has_values(analysis.get("out_of_scope")):
                analysis_bonus += 0.01
            if _has_text(analysis.get("recommended_next_step")):
                analysis_bonus += 0.02

            draft_quality_score = 0.0
            if generated_has_description:
                draft_quality_score += 0.08
                if generated_problem_statement:
                    draft_quality_score += 0.03
                if generated_acceptance:
                    draft_quality_score += 0.04
                if _has_values(analysis.get("guidance")):
                    draft_quality_score += 0.02
                if _has_values(analysis.get("risks")):
                    draft_quality_score += 0.03
                if _has_values(analysis.get("error_handling")):
                    draft_quality_score += 0.03
                if _has_values(analysis.get("nfrs")):
                    draft_quality_score += 0.01
                if _has_values(analysis.get("out_of_scope")):
                    draft_quality_score += 0.01
                if _has_text(analysis.get("recommended_next_step")):
                    draft_quality_score += 0.02
                if link_count or evidence_count:
                    draft_quality_score += 0.02

            gap_penalty = 0.0
            if not source_has_description:
                gap_penalty += 0.06 if generated_has_description else 0.12
            if not normalized_issue["acceptance_criteria"]:
                gap_penalty += 0.05 if generated_acceptance else 0.15
            if not normalized_issue["dependencies"]:
                gap_penalty += 0.04
            if not normalized_issue["comments"] and not normalized_issue["worklogs"]:
                gap_penalty += 0.03
            if not normalized_issue["edge_cases"]:
                gap_penalty += 0.03
            if not (link_count or evidence_count):
                gap_penalty += 0.06
            if rubric_total and rubric_total < QUALITY_CRITIC_MIN_TOTAL_SCORE:
                gap_penalty += min(0.08, (QUALITY_CRITIC_MIN_TOTAL_SCORE - rubric_total) * 0.01)
            if consistency_flags:
                gap_penalty += min(0.08, 0.02 * len(consistency_flags))
            if missing_test_samples:
                gap_penalty += min(0.14, 0.07 * len(missing_test_samples))

            readiness_score = min(
                0.99,
                round(max(0.0, source_score + min(0.18, analysis_bonus) + min(0.20, draft_quality_score) - gap_penalty), 2),
            )
            confidence_score = min(
                0.99,
                round(
                    max(
                        0.0,
                        readiness_score
                        + (0.03 if (link_count or evidence_count) else 0.0)
                        + (0.02 if generated_has_description else 0.0)
                        + (0.02 if generated_problem_statement and generated_acceptance else 0.0),
                    ),
                    2,
                ),
            )
            create_run_ceiling = 0.99
            if not source_has_description:
                create_run_ceiling = min(create_run_ceiling, 0.89)
            if not normalized_issue["acceptance_criteria"]:
                create_run_ceiling = min(create_run_ceiling, 0.89)
            if not normalized_issue["dependencies"]:
                create_run_ceiling = min(create_run_ceiling, 0.94)
            if missing_test_samples:
                create_run_ceiling = min(create_run_ceiling, 0.84)
            readiness_score = min(readiness_score, create_run_ceiling)
            confidence_score = min(confidence_score, create_run_ceiling)
            return _score_to_ten_scale(readiness_score), _score_to_ten_scale(confidence_score), flags

        analysis_detail_score = 0.0
        if generated_has_description:
            analysis_detail_score += 0.10
        if analysis.get("guidance"):
            analysis_detail_score += 0.10
        if analysis.get("risks"):
            analysis_detail_score += 0.10
        if analysis.get("error_handling"):
            analysis_detail_score += 0.10
        if analysis.get("nfrs"):
            analysis_detail_score += 0.05
        if analysis.get("out_of_scope"):
            analysis_detail_score += 0.05
        if analysis.get("recommended_next_step"):
            analysis_detail_score += 0.05
        blended_source_score = source_score + min(0.45, analysis_detail_score)

        draft_quality_score = 0.0
        if generated_has_description:
            draft_quality_score += 0.28
            if generated_problem_statement:
                draft_quality_score += 0.14
            if generated_acceptance:
                draft_quality_score += 0.18
            if _has_values(analysis.get("guidance")):
                draft_quality_score += 0.10
            if _has_values(analysis.get("risks")):
                draft_quality_score += 0.10
            if _has_values(analysis.get("error_handling")):
                draft_quality_score += 0.10
            if _has_values(analysis.get("nfrs")):
                draft_quality_score += 0.04
            if _has_values(analysis.get("out_of_scope")):
                draft_quality_score += 0.03
            if _has_text(analysis.get("recommended_next_step")):
                draft_quality_score += 0.03
            if link_count or evidence_count:
                draft_quality_score += 0.05

        readiness_score = min(0.99, round(max(blended_source_score, draft_quality_score), 2))
        confidence_score = min(
            0.99,
            round(
                max(
                    readiness_score + (0.05 if (link_count or evidence_count or generated_has_description) else 0.0),
                    draft_quality_score,
                ),
                2,
            ),
        )
        if rubric_total and rubric_total < QUALITY_CRITIC_MIN_TOTAL_SCORE:
            penalty = min(0.08, (QUALITY_CRITIC_MIN_TOTAL_SCORE - rubric_total) * 0.01)
            readiness_score = max(0.0, readiness_score - penalty)
            confidence_score = max(0.0, confidence_score - penalty)
        if consistency_flags:
            penalty = min(0.08, 0.02 * len(consistency_flags))
            readiness_score = max(0.0, readiness_score - penalty)
            confidence_score = max(0.0, confidence_score - penalty)
        if missing_test_samples:
            penalty = min(0.14, 0.07 * len(missing_test_samples))
            readiness_score = max(0.0, readiness_score - penalty)
            confidence_score = max(0.0, confidence_score - penalty)
        # Guided refinement can rapidly increase generated content quality; keep confidence realistic
        # by applying a stricter ceiling than initial create_run scoring.
        guided_ceiling = 0.94
        if not source_has_description:
            guided_ceiling = min(guided_ceiling, 0.89)
        if not normalized_issue["acceptance_criteria"]:
            guided_ceiling = min(guided_ceiling, 0.90)
        if not normalized_issue["dependencies"]:
            guided_ceiling = min(guided_ceiling, 0.92)
        if not (link_count or evidence_count):
            guided_ceiling = min(guided_ceiling, 0.90)
        if analysis and not generated_problem_statement:
            guided_ceiling = min(guided_ceiling, 0.92)
        if analysis and not generated_acceptance:
            guided_ceiling = min(guided_ceiling, 0.90)
        if missing_test_samples:
            guided_ceiling = min(guided_ceiling, 0.84)
        if not analysis and not flags and source_score >= 0.95:
            guided_ceiling = 0.99

        readiness_score = min(readiness_score, guided_ceiling)
        confidence_score = min(confidence_score, guided_ceiling)
        return _score_to_ten_scale(readiness_score), _score_to_ten_scale(confidence_score), flags


class EvidenceResolver:
    def __init__(
        self,
        confluence: ConfluenceSource,
        bitbucket: BitbucketSource,
        confluence_base_url: str = "",
        confluence_use_web_session: bool = False,
        skip_external_fetch: bool = False,
    ) -> None:
        self.confluence = confluence
        self.bitbucket = bitbucket
        self.confluence_base_url = confluence_base_url
        self.confluence_use_web_session = confluence_use_web_session
        self.skip_external_fetch = skip_external_fetch

    def _get_confluence_page(self, page_id: str, timeout_seconds: float | None = None) -> dict[str, Any]:
        if not timeout_seconds:
            return self.confluence.get_page(page_id)
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jira-enhancer-confluence")
        future = executor.submit(self.confluence.get_page, page_id)
        try:
            return future.result(timeout=timeout_seconds)
        except TimeoutError as exc:
            raise TimeoutError(f"Timed out loading Confluence evidence after {timeout_seconds:.0f}s: {page_id}") from exc
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def resolve(
        self,
        run_id: str,
        issue_key: str,
        normalized_issue: dict[str, Any],
        force_external_fetch: bool = False,
    ) -> tuple[list[EvidenceRecord], list[dict[str, str]]]:
        embedded_evidence = _append_embedded_epic_evidence(run_id, issue_key, normalized_issue, [])
        if self.skip_external_fetch and not force_external_fetch:
            return embedded_evidence, []
        evidence: list[EvidenceRecord] = list(embedded_evidence)
        warnings: list[dict[str, str]] = []
        confluence_timeout = EPIC_EVIDENCE_FETCH_TIMEOUT_SEC if force_external_fetch else None
        for page_id in normalized_issue["links"].get("confluence_pages", []):
            try:
                logger.info(
                    "evidence.resolve confluence_fetch_start issue_key=%s page_ref=%s force_external_fetch=%s timeout_sec=%s",
                    issue_key,
                    page_id,
                    force_external_fetch,
                    int(confluence_timeout) if confluence_timeout else "",
                )
                page = self._get_confluence_page(page_id, timeout_seconds=confluence_timeout)
                logger.info(
                    "evidence.resolve confluence_fetch_done issue_key=%s page_ref=%s title=%s",
                    issue_key,
                    page_id,
                    str(page.get("title", ""))[:120],
                )
                evidence.append(
                    EvidenceRecord(
                        run_id=run_id,
                        issue_key=issue_key,
                        source_type="confluence",
                        source_ref=page_id,
                        relevance_score=0.92,
                        selected_for_draft=True,
                        content=page,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "evidence.resolve confluence_fetch_failed issue_key=%s page_ref=%s error=%s",
                    issue_key,
                    page_id,
                    exc,
                )
                warnings.append(
                    _classify_confluence_warning(
                        str(exc),
                        page_id,
                        self.confluence_base_url,
                        self.confluence_use_web_session,
                    )
                )
        for pr_id in normalized_issue["links"].get("bitbucket_prs", []):
            pr = self.bitbucket.get_pr(pr_id)
            evidence.append(
                EvidenceRecord(
                    run_id=run_id,
                    issue_key=issue_key,
                    source_type="bitbucket",
                    source_ref=pr_id,
                    relevance_score=0.88,
                    selected_for_draft=True,
                    content=pr,
                )
            )
        return evidence, _dedupe_warnings(warnings)

    def resolve_from_user_input(self, run_id: str, issue_key: str, user_input_text: str) -> tuple[list[EvidenceRecord], list[dict[str, str]]]:
        if self.skip_external_fetch:
            return [], []
        evidence: list[EvidenceRecord] = []
        warnings: list[dict[str, str]] = []
        for page_id in _parse_confluence_refs(user_input_text):
            try:
                page = self.confluence.get_page(page_id)
                evidence.append(
                    EvidenceRecord(
                        run_id=run_id,
                        issue_key=issue_key,
                        source_type="confluence",
                        source_ref=page_id,
                        relevance_score=0.95,
                        selected_for_draft=True,
                        content=page,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                warnings.append(
                    _classify_confluence_warning(
                        str(exc),
                        page_id,
                        self.confluence_base_url,
                        self.confluence_use_web_session,
                    )
                )
        for pr_ref in _parse_bitbucket_refs(user_input_text):
            pr = self.bitbucket.get_pr(pr_ref)
            evidence.append(
                EvidenceRecord(
                    run_id=run_id,
                    issue_key=issue_key,
                    source_type="bitbucket",
                    source_ref=pr_ref,
                    relevance_score=0.91,
                    selected_for_draft=True,
                    content=pr,
                )
            )
        return evidence, _dedupe_warnings(warnings)


class JiraEnricher:
    def build_draft(
        self,
        run_id: str,
        issue_key: str,
        normalized_issue: dict[str, Any],
        evidence: list[EvidenceRecord],
        readiness_score: float,
        confidence: float,
        analysis: dict[str, Any] | None = None,
        warnings: list[dict[str, str]] | None = None,
        reviewer_guidance: list[str] | None = None,
        suggested_description: str = "",
        reasoning_mode: str = "heuristic",
    ) -> DraftRecord:
        evidence_refs = [f"{record.source_type}:{record.source_ref}" for record in evidence]
        fallback_questions = []
        acceptance_questions = _build_acceptance_questions(normalized_issue, evidence)
        fallback_questions.extend(acceptance_questions)
        if not evidence:
            fallback_questions.append("Which Confluence page or PR should be treated as authoritative context?")
        if not normalized_issue["dependencies"]:
            fallback_questions.append("What upstream dependency must be completed first?")
        fallback_questions = _dedupe_text(fallback_questions)
        analysis = analysis or {}
        concise_problem = _sentence_or_default(
            normalized_issue["description"],
            f"Clarify the requested change for {issue_key}.",
        )
        prompt_suggested_description = str(analysis.get("suggested_description", "")).strip()
        effective_suggested_description = prompt_suggested_description or suggested_description or concise_problem
        anchor_context = effective_suggested_description
        prompt_problem_statement = str(analysis.get("problem_statement", "")).strip()
        problem_statement = prompt_problem_statement or _build_problem_statement(issue_key, normalized_issue, evidence)
        prompt_acceptance = [str(item).strip() for item in analysis.get("acceptance_criteria", []) if str(item).strip()]
        acceptance = prompt_acceptance[:3] or _build_effective_acceptance_criteria(
            normalized_issue,
            analysis,
            evidence,
        )
        dependencies = _bullet_join(
            normalized_issue["dependencies"],
            "No explicit dependency is recorded in Jira yet.",
        )
        edge_cases = _bullet_join(
            normalized_issue["edge_cases"],
            "No explicit edge case is recorded in Jira yet.",
        )
        warnings = _dedupe_warnings(warnings or [])
        guidance = reviewer_guidance or []
        out_of_scope = analysis.get("out_of_scope") or [
            f"Changes outside the scoped Jira intent for {normalized_issue['summary']}.",
        ]
        risks = analysis.get("risks") or [
            "Stale issue content can invalidate approval.",
            "Missing authoritative source can reduce confidence.",
            "Description writeback must preserve non-AI issue content below the AI block.",
        ]
        error_handling = analysis.get("error_handling") or [
            "Retry source lookups idempotently when external evidence fetch fails.",
            "Block writeback and require rerun if Jira changed materially before the write.",
        ]
        nfrs = analysis.get("nfrs") or [
            "Perf budget: keep enrichment latency bounded for interactive review flows.",
            "Security notes: do not expand write scope beyond the AI-owned update block.",
        ]
        open_questions = _dedupe_text((analysis.get("open_questions") or []) + fallback_questions)[:3] or fallback_questions
        recommended_next_step = analysis.get("recommended_next_step") or f"Review the generated AI update for {issue_key} and proceed with approval or regeneration."
        context_packet = analysis.get("context_packet") or _build_context_packet(normalized_issue, evidence, "")
        context_brief = _build_codex_context_brief(
            issue_key,
            normalized_issue,
            acceptance,
            evidence_refs,
            problem_statement,
            context_packet=context_packet,
        )
        implementation_outline = [
            f"Anchor the change around Jira issue {issue_key}: {anchor_context}",
            f"Implement to satisfy the primary acceptance criteria: {acceptance[0]}",
            f"Preserve auditability and approval controls for writeback into Jira description.",
        ]
        if len(dependencies) > 0:
            implementation_outline.append(f"Validate dependency alignment before rollout: {dependencies[0]}")
        if evidence_refs:
            implementation_outline.append(f"Use linked evidence during implementation and review: {', '.join(evidence_refs)}")
        if guidance:
            for line in guidance[:3]:
                implementation_outline.append(line)
        payload = {
            "issue_key": issue_key,
            "summary": f"{issue_key}: {normalized_issue['summary']}",
            "suggested_description": effective_suggested_description,
            "implementation_outline": implementation_outline,
            "out_of_scope": out_of_scope,
            "dependencies": dependencies,
            "risks": risks,
            "edge_cases": edge_cases,
            "error_handling": error_handling,
            "nfrs": nfrs,
            "acceptance_criteria": acceptance,
            "evidence_refs": evidence_refs,
            "readiness_score": readiness_score,
            "confidence": confidence,
            "analysis_mode": reasoning_mode,
            "warnings": warnings,
            "fallback_questions": open_questions,
            "user_input_summary": guidance[0] if guidance else "",
            "user_input_details": guidance,
            "recommended_next_step": recommended_next_step,
            "context_brief": context_brief,
            "context_packet": context_packet,
            "test_case_gap_analysis": analysis.get("test_case_gap_analysis", {}),
            "quality_rubric": analysis.get("quality_rubric", {}),
            "consistency_flags": analysis.get("consistency_flags", []),
            "consistency_notes": analysis.get("consistency_notes", []),
            "critic": analysis.get("critic", {}),
            "codex_user_input_prompt": "",
        }
        payload = _apply_interaction_payload(payload)
        return DraftRecord(
            run_id=run_id,
            issue_key=issue_key,
            draft_version=1,
            confidence=confidence,
            status="draft_ready",
            payload=payload,
            created_at=utc_now(),
        )


class PolicyService:
    policy_version = DEFAULT_POLICY_VERSION

    def evaluate(self, confidence: float, flags: list[str]) -> dict[str, Any]:
        consistency_blockers = {
            "scope_contradiction",
            "placeholder_description_detected",
            "missing_generated_acceptance_criteria",
            "dependency_alignment_gap",
            "weak_evidence_grounding",
            "low_rubric_score",
            "missing_unit_test_case_sample",
            "missing_integration_test_case_sample",
            "test_case_gap",
        }
        approval_required = confidence < 9 or "limited_external_context" in flags or any(flag in consistency_blockers for flag in flags)
        return {
            "policy_version": self.policy_version,
            "approval_required": approval_required,
            "allowed_write_targets": list(AI_WRITE_TARGETS),
            "blocked_actions": [] if not approval_required else ["status_transition"],
            "policy_flags": list(flags),
        }


class ApprovalService:
    def record_decision(
        self,
        run_id: str,
        issue_key: str,
        reviewer: str,
        decision: str,
        reviewer_notes: str = "",
        user_input: dict[str, Any] | None = None,
        reviewed_at: str | None = None,
        causal_envelope: dict[str, Any] | None = None,
    ) -> ApprovalRecord:
        return ApprovalRecord(
            run_id=run_id,
            issue_key=issue_key,
            reviewer=reviewer,
            decision=decision,
            reviewer_notes=reviewer_notes,
            user_input=user_input or {},
            reviewed_at=reviewed_at or utc_now(),
            causal_envelope=causal_envelope or {},
        )


class WritebackService:
    def __init__(self, store: FilesystemStore, jira: JiraSource) -> None:
        self.store = store
        self.jira = jira

    @staticmethod
    def _result(
        *,
        run_id: str,
        issue_key: str,
        status: str,
        causal_transaction_id: str = "",
        idempotency_key: str = "",
        reason: str = "",
        validation_errors: list[str] | None = None,
        before_hash: str = "",
        after_hash: str = "",
        jira_update_id: str = "",
        write_targets: dict[str, Any] | None = None,
        replayed: bool = False,
    ) -> WritebackRecord:
        return WritebackRecord(
            run_id=run_id,
            issue_key=issue_key,
            status=status,
            jira_update_id=jira_update_id,
            write_targets=write_targets or {},
            before_hash=before_hash,
            after_hash=after_hash or before_hash,
            written_at=utc_now(),
            causal_transaction_id=causal_transaction_id,
            idempotency_key=idempotency_key,
            replayed=replayed,
            reason=reason,
            validation_errors=validation_errors or [],
        )

    def writeback(
        self,
        run_id: str,
        issue_key: str,
        draft_record: dict[str, Any],
        approval_record: dict[str, Any],
        policy_snapshot: dict[str, Any],
    ) -> WritebackRecord:
        draft_payload = draft_record.get("payload", {})
        envelope = approval_record.get("causal_envelope") or {}
        causal_transaction_id = str(envelope.get("causal_transaction_id", ""))
        idempotency_key = str(envelope.get("idempotency_key", ""))
        structural_errors = validate_causal_envelope(
            envelope,
            run_id=run_id,
            issue_key=issue_key,
            draft_version=int(draft_record.get("draft_version", 0)),
            draft_payload=draft_payload,
            policy_version=str(policy_snapshot.get("policy_version", "")),
            policy_snapshot=policy_snapshot,
            allowed_write_targets=list(policy_snapshot.get("allowed_write_targets", [])),
        )
        if structural_errors:
            return self._result(
                run_id=run_id,
                issue_key=issue_key,
                status="blocked",
                causal_transaction_id=causal_transaction_id,
                idempotency_key=idempotency_key,
                reason=structural_errors[0],
                validation_errors=structural_errors,
            )

        existing_operation = self.store.get_writeback_operation(idempotency_key)
        if existing_operation and existing_operation.get("status") == "completed":
            result_payload = dict(existing_operation.get("result") or {})
            result_payload["replayed"] = True
            result_payload["reason"] = "idempotent_replay"
            return WritebackRecord(**result_payload)

        source_issue = self.jira.get_issue(issue_key)
        normalized = normalize_jira_source(source_issue)
        current_hash = stable_hash(normalized)
        current_revision = source_revision(source_issue)
        generated_at = str(approval_record.get("reviewed_at") or utc_now())
        updated_description = merge_ai_block_into_description(
            source_issue.get("description", ""),
            run_id,
            draft_payload,
            generated_at,
        )
        intended_target_hash = stable_hash(
            {
                "issue_key": issue_key,
                "target": "description_top_block",
                "description": updated_description,
            }
        )

        if existing_operation:
            current_target_hash = stable_hash(
                {
                    "issue_key": issue_key,
                    "target": "description_top_block",
                    "description": source_issue.get("description", ""),
                }
            )
            if current_target_hash == existing_operation.get("intended_target_hash"):
                recovered = self._result(
                    run_id=run_id,
                    issue_key=issue_key,
                    status="written",
                    causal_transaction_id=causal_transaction_id,
                    idempotency_key=idempotency_key,
                    reason="recovered_after_ambiguous_commit",
                    before_hash=str(existing_operation.get("before_hash", "")),
                    after_hash=stable_hash(source_issue),
                    jira_update_id=f"recovered-{idempotency_key[-12:]}",
                    write_targets={"description": source_issue.get("description", "")},
                    replayed=True,
                )
                self.store.save_writeback_operation(
                    idempotency_key,
                    {
                        **existing_operation,
                        "status": "completed",
                        "completed_at": utc_now(),
                        "result": recovered.to_dict(),
                    },
                )
                return recovered
            return self._result(
                run_id=run_id,
                issue_key=issue_key,
                status="blocked",
                causal_transaction_id=causal_transaction_id,
                idempotency_key=idempotency_key,
                reason="writeback_operation_requires_reconciliation",
                before_hash=current_hash,
                after_hash=current_hash,
            )

        source_errors = validate_causal_envelope(
            envelope,
            run_id=run_id,
            issue_key=issue_key,
            draft_version=int(draft_record.get("draft_version", 0)),
            draft_payload=draft_payload,
            policy_version=str(policy_snapshot.get("policy_version", "")),
            policy_snapshot=policy_snapshot,
            allowed_write_targets=list(policy_snapshot.get("allowed_write_targets", [])),
            current_source_hash=current_hash,
            current_source_revision=current_revision,
        )
        if source_errors:
            return self._result(
                run_id=run_id,
                issue_key=issue_key,
                status="conflict",
                causal_transaction_id=causal_transaction_id,
                idempotency_key=idempotency_key,
                reason=source_errors[0],
                validation_errors=source_errors,
                before_hash=current_hash,
            )

        before_hash = stable_hash(source_issue)
        reservation = {
            "status": "reserved",
            "run_id": run_id,
            "issue_key": issue_key,
            "causal_transaction_id": causal_transaction_id,
            "idempotency_key": idempotency_key,
            "intended_target_hash": intended_target_hash,
            "before_hash": before_hash,
            "reserved_at": utc_now(),
        }
        if not self.store.reserve_writeback_operation(idempotency_key, reservation):
            return self._result(
                run_id=run_id,
                issue_key=issue_key,
                status="blocked",
                causal_transaction_id=causal_transaction_id,
                idempotency_key=idempotency_key,
                reason="writeback_operation_race_detected",
                before_hash=before_hash,
            )
        try:
            update_result = self.jira.write_description(issue_key, updated_description)
        except Exception as exc:  # noqa: BLE001
            self.store.save_writeback_operation(
                idempotency_key,
                {
                    **reservation,
                    "status": "indeterminate",
                    "error": str(exc),
                    "failed_at": utc_now(),
                },
            )
            return self._result(
                run_id=run_id,
                issue_key=issue_key,
                status="indeterminate",
                causal_transaction_id=causal_transaction_id,
                idempotency_key=idempotency_key,
                reason="remote_write_result_unknown",
                before_hash=before_hash,
            )
        updated_issue = update_result.get("issue", {})
        after_hash = stable_hash(updated_issue or {"description": updated_description})
        result = WritebackRecord(
            run_id=run_id,
            issue_key=issue_key,
            status="written",
            jira_update_id=update_result.get("jira_update_id", f"jira-update-{uuid4().hex[:8]}"),
            write_targets={"description": updated_description},
            before_hash=before_hash,
            after_hash=after_hash,
            written_at=utc_now(),
            causal_transaction_id=causal_transaction_id,
            idempotency_key=idempotency_key,
        )
        self.store.save_writeback_operation(
            idempotency_key,
            {
                **reservation,
                "status": "completed",
                "completed_at": utc_now(),
                "result": result.to_dict(),
            },
        )
        return result


class ReplayWorker:
    def __init__(self, store: FilesystemStore) -> None:
        self.store = store

    def replay(self, run_id: str) -> dict[str, Any]:
        files = self.store.list_dead_letters(run_id)
        replayed: list[str] = []
        for file_path in files:
            payload = self.store.snapshot_hash({"replayed_from": file_path.name})
            replayed.append(f"{file_path.name}:{payload[:10]}")
        return {"run_id": run_id, "replayed": replayed}


class MvpOrchestrator:
    def __init__(
        self,
        store: FilesystemStore,
        scope_resolver: ScopeResolver,
        scanner: JiraScanner,
        scorer: ReadinessScorer,
        evidence_resolver: EvidenceResolver,
        semantic_reasoner: SemanticReasoner,
        enricher: JiraEnricher,
        policy_service: PolicyService,
        approval_service: ApprovalService,
        writeback_service: WritebackService,
        replay_worker: ReplayWorker,
        rl_prompt_service=None,
        rl_target_confidence: int = 9,
        rl_max_prompt_retries: int = 10,
        rl_force_codex_for_selected_prompt: bool = True,
    ) -> None:
        self.store = store
        self.scope_resolver = scope_resolver
        self.scanner = scanner
        self.scorer = scorer
        self.evidence_resolver = evidence_resolver
        self.semantic_reasoner = semantic_reasoner
        self.enricher = enricher
        self.policy_service = policy_service
        self.approval_service = approval_service
        self.writeback_service = writeback_service
        self.replay_worker = replay_worker
        self.rl_prompt_service = rl_prompt_service
        self.rl_target_confidence = max(1, min(10, int(rl_target_confidence)))
        self.rl_max_prompt_retries = max(1, int(rl_max_prompt_retries))
        self.rl_force_codex_for_selected_prompt = bool(rl_force_codex_for_selected_prompt)

    def _is_codex_reasoner_active(self) -> bool:
        return isinstance(self.semantic_reasoner, (CodexExecSemanticReasoner, CodexBrokerSemanticReasoner))

    def _build_rl_prompt_attempt_input(
        self,
        selected_prompt_body: str,
        lag_context_text: str,
        attempt: int,
        prior_confidence: int,
    ) -> str:
        parts = [selected_prompt_body.strip()]
        if lag_context_text.strip():
            parts.append(f"RL lag signals:\n{lag_context_text.strip()}")
        parts.append(
            f"RL retry contract: attempt={attempt}, prior_confidence={prior_confidence}/10, target_confidence={self.rl_target_confidence}/10. "
            "Regenerate the prompt response to improve confidence with concrete acceptance criteria, dependencies, evidence grounding, and actionable next steps."
        )
        return "\n\n".join(part for part in parts if part)

    @staticmethod
    def _build_rl_context_text(
        normalized_issue: dict[str, Any],
        warnings: list[dict[str, str]],
        evidence: list[EvidenceRecord] | None = None,
    ) -> str:
        gap_analysis = _analyze_test_case_gaps(normalized_issue, evidence=evidence)
        gap_prompt = str(gap_analysis.get("gap_prompt", "")).strip()
        selected_evidence_parts: list[str] = []
        for record in evidence or []:
            if record.selected_for_draft:
                selected_evidence_parts.extend(_flatten_text(record.content))
        selected_evidence_text = "\n".join(
            part.strip() for part in selected_evidence_parts if str(part).strip()
        )
        return "\n".join(
            [
                str(normalized_issue.get("summary", "")),
                str(normalized_issue.get("description", "")),
                " ".join(normalized_issue.get("acceptance_criteria", [])),
                " ".join(normalized_issue.get("dependencies", [])),
                " ".join(warning.get("code", "") for warning in warnings),
                f"missing_test_case_samples={' '.join(gap_analysis.get('missing', []))}",
                gap_prompt,
                selected_evidence_text,
            ]
        ).strip()

    @staticmethod
    def _has_sufficient_rl_evidence(
        normalized_issue: dict[str, Any],
        evidence: list[EvidenceRecord],
    ) -> bool:
        if any(record.selected_for_draft for record in evidence):
            return True
        if normalized_issue.get("acceptance_criteria") or normalized_issue.get("dependencies"):
            return True
        description_without_links = _strip_links(str(normalized_issue.get("description", "")))
        return len(re.findall(r"[A-Za-z0-9]+", description_without_links)) >= 8

    def _build_rl_lag_context_text(
        self,
        normalized_issue: dict[str, Any],
        warnings: list[dict[str, str]],
        confidence: int,
        attempt: int,
        target_confidence: int,
    ) -> str:
        lag = max(0, target_confidence - confidence)
        missing_signals: list[str] = []
        if not normalized_issue.get("acceptance_criteria"):
            missing_signals.append("missing_acceptance_criteria")
        if not normalized_issue.get("dependencies"):
            missing_signals.append("missing_dependencies")
        if not normalized_issue.get("description"):
            missing_signals.append("missing_description")
        gap_analysis = _analyze_test_case_gaps(normalized_issue)
        if gap_analysis.get("missing"):
            missing_signals.extend(f"missing_{test_type}_test_case_sample" for test_type in gap_analysis["missing"])
        base = self._build_rl_context_text(normalized_issue, warnings)
        return (
            f"{base}\n"
            f"confidence={confidence}/10 target={target_confidence}/10 lag={lag}\n"
            f"attempt={attempt}\n"
            f"missing_signals={' '.join(missing_signals)}"
        ).strip()

    def _save_draft_and_item_agent_state(
        self,
        run_id: str,
        issue_key: str,
        *,
        draft_dict: dict[str, Any] | None = None,
        item: dict[str, Any] | None = None,
        agent_state: dict[str, Any],
    ) -> None:
        if draft_dict is None:
            draft_dict = self.store.get_latest_draft(run_id, issue_key)
        draft_dict["payload"]["agent_state"] = agent_state
        self.store.save_draft(run_id, issue_key, int(draft_dict["draft_version"]), draft_dict)
        if item is None:
            item = self.store.get_run_item(run_id, issue_key)
        item["agent_state"] = agent_state
        self.store.save_run_item(run_id, issue_key, item)

    def _run_autonomous_agent_loop(
        self,
        run_id: str,
        issue_key: str,
        *,
        reviewer_notes: str = "",
    ) -> None:
        previous_state = self.store.get_run_item(run_id, issue_key).get("agent_state", {})
        for _ in range(MAX_AUTONOMOUS_AGENT_STEPS):
            item = self.store.get_run_item(run_id, issue_key)
            draft_dict = self.store.get_latest_draft(run_id, issue_key)
            payload = draft_dict.get("payload", {})
            agent_state = _build_agent_state(
                payload,
                int(item.get("confidence", 0)),
                bool(item.get("approval_required", False)),
                previous_state=previous_state,
            )
            self._save_draft_and_item_agent_state(run_id, issue_key, draft_dict=draft_dict, item=item, agent_state=agent_state)
            if agent_state["next_action"] != "self_refine":
                break

            before_confidence = int(item.get("confidence", 0))
            refined_input = {"details": _autonomous_refinement_input(payload, agent_state)}
            regenerated = self._rebuild_issue_from_user_input(
                run_id,
                issue_key,
                reviewer_notes,
                refined_input,
                existing_interaction=payload.get("interaction", {}),
            )
            refreshed_item = regenerated["item"]
            refreshed_draft = regenerated["draft"]
            previous_state = _build_agent_state(
                refreshed_draft["payload"],
                int(refreshed_item.get("confidence", 0)),
                bool(refreshed_item.get("approval_required", False)),
                previous_state=agent_state,
                history_entry={
                    "action": "self_refine",
                    "before_confidence": before_confidence,
                    "after_confidence": int(refreshed_item.get("confidence", 0)),
                    "at": utc_now(),
                },
            )
            self._save_draft_and_item_agent_state(
                run_id,
                issue_key,
                draft_dict=refreshed_draft,
                item=refreshed_item,
                agent_state=previous_state,
            )
            if int(refreshed_item.get("confidence", 0)) <= before_confidence and previous_state.get("next_action") != "self_refine":
                break

    def _initialize_run(self, scope_type: str, scope_value: str, requestor: str) -> tuple[str, list[str]]:
        issue_keys = self.scope_resolver.resolve(scope_type, scope_value)
        run_id = f"run-{scope_type}-{scope_value.lower().replace('.', '-').replace('_', '-')}-{uuid4().hex[:8]}"
        now = utc_now()
        status_items: list[dict[str, Any]] = []
        for issue_key in issue_keys:
            jira_summary = ""
            try:
                jira_summary = str(self.scanner.jira.get_issue(issue_key).get("summary", ""))
            except Exception:  # noqa: BLE001
                jira_summary = ""
            status_items.append(
                {
                    "issue_key": issue_key,
                    "jira_summary": jira_summary,
                    "status": "queued",
                    "approval_required": False,
                    "readiness_score": 0,
                    "confidence": 0,
                    "warnings": [],
                }
            )
        record = RunRecord(
            run_id=run_id,
            scope_type=scope_type,
            scope_value=scope_value,
            requestor=requestor,
            status="running",
            created_at=now,
            updated_at=now,
            idempotency_key=stable_hash({"scope_type": scope_type, "scope_value": scope_value, "requestor": requestor}),
            items=issue_keys,
        )
        self.store.save_run(record.to_dict())
        self.store.save_status(run_id, {"run_id": run_id, "status": "running", "items": status_items, "warnings": []})
        return run_id, issue_keys

    def _complete_run(self, run_id: str, issue_keys: list[str]) -> dict[str, Any]:
        for issue_key in issue_keys:
            self._process_issue(run_id, issue_key)
        status = self.store.get_status(run_id)
        status["status"] = "awaiting_approval" if any(item["approval_required"] for item in status["items"]) else "completed"
        self.store.save_status(run_id, status)
        record_dict = self.store.get_run(run_id)
        record_dict["status"] = status["status"]
        record_dict["updated_at"] = utc_now()
        self.store.save_run(record_dict)
        return {"runId": run_id, "status": status["status"], "items": issue_keys}

    def fail_run(self, run_id: str, message: str) -> dict[str, Any]:
        warning = {"code": "run_failed", "source": "orchestrator", "message": message}
        status = self.store.get_status(run_id)
        status["status"] = "failed"
        status["warnings"] = _dedupe_warnings(list(status.get("warnings", [])) + [warning])
        self.store.save_status(run_id, status)
        run = self.store.get_run(run_id)
        run["status"] = "failed"
        run["updated_at"] = utc_now()
        self.store.save_run(run)
        return {"runId": run_id, "status": "failed", "error": message}

    def start_run(self, scope_type: str, scope_value: str, requestor: str) -> dict[str, Any]:
        logger.info("Starting run scope_type=%s scope_value=%s requestor=%s", scope_type, scope_value, requestor)
        run_id, issue_keys = self._initialize_run(scope_type, scope_value, requestor)
        logger.info("Run initialized run_id=%s issue_count=%s", run_id, len(issue_keys))
        return {"runId": run_id, "status": "running", "items": issue_keys}

    def continue_run(self, run_id: str) -> dict[str, Any]:
        logger.info("Continuing run run_id=%s", run_id)
        run = self.store.get_run(run_id)
        issue_keys = list(run.get("items", []))
        result = self._complete_run(run_id, issue_keys)
        logger.info("Run completed run_id=%s status=%s", run_id, result.get("status", ""))
        return result

    def _sync_status_item(
        self,
        run_id: str,
        issue_key: str,
        jira_summary: str,
        status_value: str,
        approval_required: bool,
        readiness_score: float,
        confidence: float,
        warnings: list[dict[str, str]] | None = None,
    ) -> None:
        status = self.store.get_status(run_id)
        warnings = _dedupe_warnings(warnings or [])
        updated = False
        for item in status["items"]:
            if item["issue_key"] == issue_key:
                item["jira_summary"] = jira_summary
                item["status"] = status_value
                item["approval_required"] = approval_required
                item["readiness_score"] = readiness_score
                item["confidence"] = confidence
                item["warnings"] = warnings
                updated = True
                break
        if not updated:
            status["items"].append(
                {
                    "issue_key": issue_key,
                    "jira_summary": jira_summary,
                    "status": status_value,
                    "approval_required": approval_required,
                    "readiness_score": readiness_score,
                    "confidence": confidence,
                    "warnings": warnings,
                }
            )
        status["warnings"] = _dedupe_warnings(
            [warning for entry in status["items"] for warning in entry.get("warnings", [])]
        )
        if any(entry["status"] == "awaiting_approval" for entry in status["items"]):
            status["status"] = "awaiting_approval"
        elif all(entry["status"] == "written" for entry in status["items"]):
            status["status"] = "completed"
        else:
            status["status"] = "running"
        self.store.save_status(run_id, status)

        run_record = self.store.get_run(run_id)
        run_record["status"] = status["status"]
        run_record["updated_at"] = utc_now()
        self.store.save_run(run_record)

    def create_run(self, scope_type: str, scope_value: str, requestor: str) -> dict[str, Any]:
        run_id, issue_keys = self._initialize_run(scope_type, scope_value, requestor)
        return self._complete_run(run_id, issue_keys)

    def _process_issue(self, run_id: str, issue_key: str) -> None:
        logger.info("Processing issue run_id=%s issue_key=%s", run_id, issue_key)
        normalized = self.scanner.scan(issue_key)
        evidence, warnings = self.evidence_resolver.resolve(run_id, issue_key, normalized)
        logger.info(
            "Scanned issue run_id=%s issue_key=%s evidence_count=%s warning_count=%s",
            run_id,
            issue_key,
            len(evidence),
            len(warnings),
        )
        rl_decision = None
        rl_prompt_text = ""
        analysis: dict[str, Any]
        readiness_score: int
        confidence: int
        flags: list[str]

        if self.rl_prompt_service:
            current_context = self._build_rl_context_text(normalized, warnings, evidence)
            selected_evidence_count = sum(1 for record in evidence if record.selected_for_draft)
            sufficient_evidence = self._has_sufficient_rl_evidence(normalized, evidence)
            status_items = self.store.get_status(run_id).get("items", [])
            current_item_status = next(
                (
                    str(item.get("status", "unknown"))
                    for item in status_items
                    if item.get("issue_key") == issue_key
                ),
                "unknown",
            )
            prompt_workflow_status = "running" if current_item_status == "queued" else current_item_status
            prompt_governance_context = PromptGovernanceContext(
                has_sufficient_evidence=sufficient_evidence,
                context_available=bool(
                    normalized.get("summary")
                    or normalized.get("description")
                    or normalized.get("acceptance_criteria")
                    or normalized.get("dependencies")
                    or evidence
                ),
                workflow_status=prompt_workflow_status,
                write_target=(
                    "description_top_block"
                    if str(normalized.get("issue_key", "")).strip() == issue_key
                    else ""
                ),
                allowed_write_targets=tuple(AI_WRITE_TARGETS),
                evidence_count=selected_evidence_count,
                scope_type="issue",
            )
            attempt_trajectory: list[dict[str, Any]] = []
            attempted_prompt_ids: list[str] = []
            codex_guardrail_active = self.rl_force_codex_for_selected_prompt and self._is_codex_reasoner_active()
            for attempt in range(1, self.rl_max_prompt_retries + 1):
                candidate = self.rl_prompt_service.select_prompt(
                    run_id,
                    issue_key,
                    current_context,
                    governance_context=prompt_governance_context,
                    excluded_prompt_ids=tuple(attempted_prompt_ids),
                )
                if candidate:
                    rl_decision = candidate
                    attempted_prompt_ids.append(candidate.prompt_id)
                    selected_prompt_pack = dict(candidate.metadata.get("selected_prompt_pack", {}))
                    prompt_body = str(selected_prompt_pack.get("body") or candidate.metadata.get("prompt_body", "")).strip()
                    rl_prompt_text = self._build_rl_prompt_attempt_input(
                        prompt_body,
                        current_context if attempt > 1 else "",
                        attempt,
                        attempt_trajectory[-1]["confidence"] if attempt_trajectory else 0,
                    )
                else:
                    rl_prompt_text = ""
                analysis = self.semantic_reasoner.analyze_issue(normalized, evidence, rl_prompt_text)
                analysis = _apply_quality_critic_loop(normalized, evidence, analysis, rl_prompt_text)
                analysis = _attach_test_case_gap_analysis(normalized, evidence, analysis)
                readiness_score, confidence, flags = self.scorer.score(
                    normalized,
                    suggested_description=analysis.get("suggested_description", ""),
                    evidence_count=len(evidence),
                    analysis=analysis,
                    mode="create_run",
                )
                attempt_record = {
                    "attempt": attempt,
                    "confidence": confidence,
                    "readiness_score": readiness_score,
                    "lag": max(0, self.rl_target_confidence - confidence),
                    "reasoning_mode": str(analysis.get("reasoning_mode", "")),
                    "used_prompt": bool(rl_prompt_text),
                    "test_case_gaps": list((analysis.get("test_case_gap_analysis") or {}).get("missing", [])),
                }
                if candidate:
                    candidate.metadata["attempt"] = attempt
                    candidate.metadata["confidence_after_attempt"] = confidence
                    candidate.metadata.setdefault("confidence_trajectory", []).append(attempt_record)
                attempt_trajectory.append(attempt_record)
                if confidence >= self.rl_target_confidence:
                    break
                current_context = self._build_rl_lag_context_text(
                    normalized,
                    warnings,
                    confidence,
                    attempt,
                    self.rl_target_confidence,
                )
            if rl_decision:
                rl_decision.metadata["codex_guardrail"] = {
                    "enabled": self.rl_force_codex_for_selected_prompt,
                    "codex_reasoner_active": self._is_codex_reasoner_active(),
                    "enforced": codex_guardrail_active,
                }
                rl_decision.metadata["confidence_trajectory"] = attempt_trajectory
                rl_decision.metadata["final_confidence"] = confidence
                rl_decision.metadata["target_confidence"] = self.rl_target_confidence
                rl_decision.metadata["test_case_gap_analysis"] = analysis.get("test_case_gap_analysis", {})
                rl_decision.metadata["test_case_gap_prompt"] = (analysis.get("test_case_gap_analysis") or {}).get("gap_prompt", "")
        else:
            analysis = self.semantic_reasoner.analyze_issue(normalized, evidence, "")
            analysis = _apply_quality_critic_loop(normalized, evidence, analysis, "")
            analysis = _attach_test_case_gap_analysis(normalized, evidence, analysis)
            readiness_score, confidence, flags = self.scorer.score(
                normalized,
                suggested_description=analysis.get("suggested_description", ""),
                evidence_count=len(evidence),
                analysis=analysis,
                mode="create_run",
            )
        logger.info(
            "Issue scored run_id=%s issue_key=%s readiness=%s confidence=%s flags=%s",
            run_id,
            issue_key,
            readiness_score,
            confidence,
            ",".join(flags) if flags else "none",
        )
        item = RunItemRecord(
            run_id=run_id,
            issue_key=issue_key,
            jira_summary=normalized["summary"],
            status="draft_ready",
            source_hash=normalized["source_hash"],
            readiness_score=readiness_score,
            confidence=confidence,
            policy_flags=flags,
            approval_required=False,
            created_at=utc_now(),
            updated_at=utc_now(),
            source_revision=normalized.get("source_revision", ""),
        )
        draft = self.enricher.build_draft(
            run_id,
            issue_key,
            normalized,
            evidence,
            readiness_score,
            confidence,
            analysis=analysis,
            warnings=warnings,
            reviewer_guidance=analysis.get("guidance", []),
            suggested_description=analysis.get("suggested_description", ""),
            reasoning_mode=analysis.get("reasoning_mode", "heuristic"),
        )
        policy = self.policy_service.evaluate(confidence, flags)
        item.approval_required = policy["approval_required"]
        if policy["approval_required"]:
            item.status = "awaiting_approval"
        item.warnings = warnings
        item.agent_state = _build_agent_state(draft.payload, confidence, policy["approval_required"])
        item_dict = item.to_dict()
        if rl_decision:
            item_dict["rl_prompt_decision"] = {
                "decision_id": rl_decision.decision_id,
                "prompt_id": rl_decision.prompt_id,
                "score": rl_decision.score,
                "metadata": rl_decision.metadata,
            }
        self.store.save_run_item(run_id, issue_key, item_dict)
        draft_dict = draft.to_dict()
        draft_dict["payload"]["agent_state"] = item.agent_state
        self.store.save_draft(run_id, issue_key, draft.draft_version, draft_dict)
        for record in evidence:
            self.store.save_evidence(run_id, record.source_type, record.source_ref, record.to_dict())
        self.store.save_approval_request(
            run_id,
            issue_key,
            {
                "run_id": run_id,
                "issue_key": issue_key,
                "approval_required": policy["approval_required"],
                "allowed_write_targets": policy["allowed_write_targets"],
                "warnings": warnings,
                "fallback_questions": draft.payload["fallback_questions"],
            },
        )
        self.store.save_event(
            "enrichment.draft.ready",
            run_id,
            {
                "event_id": f"evt-{uuid4().hex[:10]}",
                "event_type": "enrichment.draft.ready",
                "run_id": run_id,
                "issue_key": issue_key,
                "occurred_at": utc_now(),
                "producer": "jira-enricher",
                "payload": {"draft_version": draft.draft_version, "confidence": confidence, "warnings": warnings},
            },
            issue_key=issue_key,
        )
        self._sync_status_item(
            run_id,
            issue_key,
            item.jira_summary,
            item.status,
            item.approval_required,
            item.readiness_score,
            item.confidence,
            item.warnings,
        )
        self._run_autonomous_agent_loop(run_id, issue_key)
        refreshed_item = self.store.get_run_item(run_id, issue_key)
        logger.info(
            "Issue finalized run_id=%s issue_key=%s status=%s confidence=%s",
            run_id,
            issue_key,
            refreshed_item["status"],
            refreshed_item["confidence"],
        )
        self._sync_status_item(
            run_id,
            issue_key,
            refreshed_item["jira_summary"],
            refreshed_item["status"],
            refreshed_item["approval_required"],
            refreshed_item["readiness_score"],
            refreshed_item["confidence"],
            refreshed_item["warnings"],
        )

    def _next_draft_version(self, run_id: str, issue_key: str) -> int:
        latest = self.store.get_latest_draft(run_id, issue_key)
        return int(latest.get("draft_version", 0)) + 1

    def _rebuild_issue_from_user_input(
        self,
        run_id: str,
        issue_key: str,
        reviewer_notes: str,
        user_input: dict[str, Any] | None,
        existing_interaction: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = self.scanner.scan(issue_key)
        evidence, warnings = self.evidence_resolver.resolve(run_id, issue_key, normalized)
        user_input_text = _extract_user_input_text(user_input)
        combined_input = "\n".join(part for part in [reviewer_notes.strip(), user_input_text] if part.strip())
        user_supplied_evidence, user_input_warnings = self.evidence_resolver.resolve_from_user_input(run_id, issue_key, combined_input)
        evidence = _dedupe_evidence(evidence + user_supplied_evidence)
        warnings = _dedupe_warnings(warnings + user_input_warnings)
        analysis = self.semantic_reasoner.analyze_issue(normalized, evidence, combined_input)
        analysis = _apply_quality_critic_loop(normalized, evidence, analysis, combined_input)
        analysis = _attach_test_case_gap_analysis(normalized, evidence, analysis)
        readiness_score, confidence, flags = self.scorer.score(
            normalized,
            suggested_description=analysis.get("suggested_description", ""),
            evidence_count=len(evidence),
            analysis=analysis,
            mode="guided",
        )
        draft = self.enricher.build_draft(
            run_id,
            issue_key,
            normalized,
            evidence,
            readiness_score,
            confidence,
            analysis=analysis,
            warnings=warnings,
            reviewer_guidance=analysis.get("guidance", []),
            suggested_description=analysis.get("suggested_description", ""),
            reasoning_mode=analysis.get("reasoning_mode", "heuristic"),
        )
        draft_dict = draft.to_dict()
        if existing_interaction is None:
            try:
                existing_interaction = self.store.get_latest_draft(run_id, issue_key).get("payload", {}).get("interaction", {})
            except FileNotFoundError:
                existing_interaction = {}
        draft_dict["payload"] = _apply_interaction_payload(draft_dict["payload"], existing_interaction)
        draft_dict["draft_version"] = self._next_draft_version(run_id, issue_key)
        draft_dict["status"] = "draft_ready"
        draft_dict["created_at"] = utc_now()
        self.store.save_draft(run_id, issue_key, draft_dict["draft_version"], draft_dict)
        for record in user_supplied_evidence:
            self.store.save_evidence(run_id, record.source_type, record.source_ref, record.to_dict())

        policy = self.policy_service.evaluate(confidence, flags)
        self.store.save_approval_request(
            run_id,
            issue_key,
            {
                "run_id": run_id,
                "issue_key": issue_key,
                "approval_required": policy["approval_required"],
                "allowed_write_targets": policy["allowed_write_targets"],
                "warnings": warnings,
                "fallback_questions": draft_dict["payload"]["fallback_questions"],
            },
        )

        item = self.store.get_run_item(run_id, issue_key)
        item["jira_summary"] = normalized["summary"]
        item["source_hash"] = normalized["source_hash"]
        item["source_revision"] = normalized.get("source_revision", "")
        item["readiness_score"] = readiness_score
        item["confidence"] = confidence
        item["policy_flags"] = flags
        item["approval_required"] = policy["approval_required"]
        item["warnings"] = warnings
        item["status"] = "awaiting_approval"
        item["updated_at"] = utc_now()
        self.store.save_run_item(run_id, issue_key, item)
        previous_agent_state = item.get("agent_state", {})
        agent_state = _build_agent_state(draft_dict["payload"], confidence, policy["approval_required"], previous_state=previous_agent_state)
        self._save_draft_and_item_agent_state(run_id, issue_key, draft_dict=draft_dict, item=item, agent_state=agent_state)
        self._sync_status_item(
            run_id,
            issue_key,
            item["jira_summary"],
            item["status"],
            item["approval_required"],
            item["readiness_score"],
            item["confidence"],
            item["warnings"],
        )
        return {"draft": draft_dict, "item": item}

    def submit_interaction_answer(self, run_id: str, issue_key: str, reviewer: str, answer: str) -> dict[str, Any]:
        cleaned_answer = " ".join((answer or "").split()).strip()
        if not cleaned_answer:
            raise ValueError("Answer is required.")
        latest_draft = self.store.get_latest_draft(run_id, issue_key)
        interaction = latest_draft.get("payload", {}).get("interaction", {})
        current_question = str(interaction.get("current_question", "")).strip()
        if not current_question:
            raise ValueError("No pending Codex question exists for this Jira.")

        answers = _normalize_interaction_answers(interaction.get("answers", []))
        answers.append({"question": current_question, "answer": cleaned_answer})
        updated_interaction = {
            "questions": list(interaction.get("questions", [])),
            "answers": answers,
        }
        transcript = _build_interaction_transcript(answers)
        regenerated = self._rebuild_issue_from_user_input(
            run_id,
            issue_key,
            reviewer,
            {"details": transcript},
            existing_interaction=updated_interaction,
        )
        response = {
            "run_id": run_id,
            "issue_key": issue_key,
            "draft": regenerated["draft"],
            "item": regenerated["item"],
            "interaction": regenerated["draft"]["payload"].get("interaction", {}),
        }
        return response

    @staticmethod
    def _refinement_user_input_text(
        base_input: str,
        draft_payload: dict[str, Any],
        confidence: int,
        attempt: int,
    ) -> str:
        refinement_request = (
            f"The current confidence score is {confidence}/10, which is below the target of {TARGET_CONFIDENCE_SCORE}/10. "
            f"Codex refinement attempt {attempt}. Re-evaluate the Jira and improve the suggested description and implementation brief "
            "so ambiguity is reduced. Strengthen concrete scope boundaries, dependencies, edge cases, error handling, and next-step guidance "
            "using only supported Jira context and linked evidence."
        )
        prior_description = str(draft_payload.get("suggested_description", "")).strip()
        prior_outline = "\n".join(str(item).strip() for item in draft_payload.get("implementation_outline", []) if str(item).strip())
        parts = [base_input.strip(), refinement_request]
        if prior_description:
            parts.append(f"Current suggested description:\n{prior_description}")
        if prior_outline:
            parts.append(f"Current implementation outline:\n{prior_outline}")
        return "\n\n".join(part for part in parts if part)

    def _refine_issue_after_user_input(
        self,
        run_id: str,
        issue_key: str,
        reviewer_notes: str,
        user_input: dict[str, Any] | None,
    ) -> None:
        base_input = _extract_user_input_text(user_input)
        for attempt in range(1, MAX_BACKGROUND_REFINEMENT_ATTEMPTS + 1):
            item = self.store.get_run_item(run_id, issue_key)
            if int(item.get("confidence", 0)) >= TARGET_CONFIDENCE_SCORE:
                break
            draft = self.store.get_latest_draft(run_id, issue_key).get("payload", {})
            refined_input = {
                "details": self._refinement_user_input_text(
                    base_input,
                    draft,
                    int(item.get("confidence", 0)),
                    attempt,
                )
            }
            regenerated = self._rebuild_issue_from_user_input(run_id, issue_key, reviewer_notes, refined_input)
            refreshed_item = regenerated["item"]
            if int(refreshed_item.get("confidence", 0)) >= TARGET_CONFIDENCE_SCORE:
                break
            if attempt < MAX_BACKGROUND_REFINEMENT_ATTEMPTS:
                refreshed_item["status"] = "refining"
                refreshed_item["updated_at"] = utc_now()
                self.store.save_run_item(run_id, issue_key, refreshed_item)
                self._sync_status_item(
                    run_id,
                    issue_key,
                    refreshed_item["jira_summary"],
                    refreshed_item["status"],
                    refreshed_item["approval_required"],
                    refreshed_item["readiness_score"],
                    refreshed_item["confidence"],
                    refreshed_item["warnings"],
                )

    def _run_issue_keys(self, run_id: str) -> list[str]:
        return list(self.store.get_run(run_id).get("items", []))

    def get_run(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        run["status_payload"] = self.store.get_status(run_id)
        return run

    def get_run_item(self, run_id: str, issue_key: str) -> dict[str, Any]:
        try:
            item = self.store.get_run_item(run_id, issue_key)
            item["draft"] = self.store.get_latest_draft(run_id, issue_key)
            return item
        except FileNotFoundError:
            run = self.store.get_run(run_id)
            if issue_key not in run.get("items", []):
                raise
            status = self.store.get_status(run_id)
            status_item = next((entry for entry in status.get("items", []) if entry.get("issue_key") == issue_key), {})
            return {
                "run_id": run_id,
                "issue_key": issue_key,
                "jira_summary": status_item.get("jira_summary", ""),
                "status": status_item.get("status", "queued"),
                "source_hash": "",
                "readiness_score": status_item.get("readiness_score", 0),
                "confidence": status_item.get("confidence", 0),
                "policy_flags": [],
                "approval_required": status_item.get("approval_required", False),
                "created_at": run.get("created_at", ""),
                "updated_at": run.get("updated_at", ""),
                "warnings": status_item.get("warnings", []),
                "agent_state": {},
                "draft": {
                    "draft_version": 0,
                    "status": "pending",
                    "payload": {
                        "summary": "",
                        "suggested_description": "",
                        "user_input_summary": "",
                        "user_input_details": [],
                        "context_brief": {
                            "problem_statement": "",
                            "acceptance_focus": "",
                            "dependency_focus": "",
                            "edge_case_focus": "",
                            "evidence_context": "",
                        },
                        "codex_user_input_prompt": "",
                        "fallback_questions": [],
                        "interaction": {
                            "questions": [],
                            "answers": [],
                            "current_question": "",
                            "current_index": 0,
                            "remaining_questions": 0,
                            "status": "awaiting_input",
                            "transcript": "",
                        },
                        "agent_state": {
                            "goal": "",
                            "iterations_completed": 0,
                            "max_iterations": MAX_AUTONOMOUS_AGENT_STEPS,
                            "next_action": "",
                            "status": "",
                            "remaining_questions": 0,
                            "history": [],
                        },
                    },
                    "created_at": "",
                },
            }

    def record_approval(self, run_id: str, issue_key: str, reviewer: str, decision: str, reviewer_notes: str = "", user_input: dict[str, Any] | None = None) -> dict[str, Any]:
        before_item = self.store.get_run_item(run_id, issue_key)
        before_confidence = int(before_item.get("confidence", 0))
        reviewed_at = utc_now()
        causal_envelope: dict[str, Any] = {}
        if decision == "approve":
            approved_draft = self.store.get_latest_draft(run_id, issue_key)
            approved_policy = self.policy_service.evaluate(
                float(before_item.get("confidence", 0)),
                list(before_item.get("policy_flags", [])),
            )
            causal_envelope = build_causal_envelope(
                run_id=run_id,
                issue_key=issue_key,
                source_hash=str(before_item.get("source_hash", "")),
                source_revision_value=str(before_item.get("source_revision", "")),
                draft_version=int(approved_draft.get("draft_version", 0)),
                draft_payload=dict(approved_draft.get("payload") or {}),
                policy_version=str(approved_policy.get("policy_version", "")),
                policy_snapshot=approved_policy,
                allowed_write_targets=list(approved_policy.get("allowed_write_targets", [])),
                decision=decision,
                reviewer=reviewer,
                reviewed_at=reviewed_at,
            )
        approval = self.approval_service.record_decision(
            run_id,
            issue_key,
            reviewer,
            decision,
            reviewer_notes,
            user_input,
            reviewed_at=reviewed_at,
            causal_envelope=causal_envelope,
        )
        self.store.save_approval_decision(run_id, issue_key, approval.to_dict())
        if decision in GUIDANCE_DECISIONS:
            regenerated = self._rebuild_issue_from_user_input(run_id, issue_key, reviewer_notes, user_input)
            if int(regenerated["item"].get("confidence", 0)) < TARGET_CONFIDENCE_SCORE:
                item = regenerated["item"]
                item["status"] = "refining"
                item["updated_at"] = utc_now()
                self.store.save_run_item(run_id, issue_key, item)
                self._sync_status_item(
                    run_id,
                    issue_key,
                    item["jira_summary"],
                    item["status"],
                    item["approval_required"],
                    item["readiness_score"],
                    item["confidence"],
                    item["warnings"],
                )
                Thread(
                    target=self._refine_issue_after_user_input,
                    args=(run_id, issue_key, reviewer_notes, user_input),
                    daemon=True,
                ).start()
                regenerated["item"] = item
            response = approval.to_dict()
            response["draft"] = regenerated["draft"]
            response["item_status"] = regenerated["item"]["status"]
            self._record_rl_outcome_if_possible(run_id, issue_key, decision, before_confidence, int(regenerated["item"].get("confidence", 0)))
            return response
        item = self.store.get_run_item(run_id, issue_key)
        item["status"] = "approved" if decision == "approve" else decision
        if causal_envelope:
            item["causal_transaction_id"] = causal_envelope["causal_transaction_id"]
        item["updated_at"] = utc_now()
        self.store.save_run_item(run_id, issue_key, item)
        self._sync_status_item(
            run_id,
            issue_key,
            item.get("jira_summary", ""),
            item["status"],
            item["approval_required"],
            item["readiness_score"],
            item["confidence"],
            item.get("warnings", []),
        )
        self._record_rl_outcome_if_possible(run_id, issue_key, decision, before_confidence, int(item.get("confidence", 0)))
        return approval.to_dict()

    def _record_rl_outcome_if_possible(
        self,
        run_id: str,
        issue_key: str,
        decision: str,
        confidence_before: int,
        confidence_after: int,
    ) -> None:
        if not self.rl_prompt_service:
            return
        item = self.store.get_run_item(run_id, issue_key)
        rl_info = item.get("rl_prompt_decision") or {}
        decision_id = str(rl_info.get("decision_id", "")).strip()
        prompt_id = str(rl_info.get("prompt_id", "")).strip()
        if not decision_id or not prompt_id:
            return
        self.rl_prompt_service.record_outcome(
            PromptOutcome(
                decision_id=decision_id,
                prompt_id=prompt_id,
                run_id=run_id,
                issue_key=issue_key,
                decision=decision,
                confidence_before=confidence_before,
                confidence_after=confidence_after,
                metadata={
                    "decision_snapshot": rl_info,
                    "confidence_trajectory": (rl_info.get("metadata") or {}).get("confidence_trajectory", []),
                    "target_confidence": self.rl_target_confidence,
                    "stats_snapshot": self.rl_prompt_service.get_prompt_stats_snapshot(top_k=5),
                },
            )
        )

    def record_approval_for_run(
        self,
        run_id: str,
        reviewer: str,
        decision: str,
        reviewer_notes: str = "",
        user_input: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        issue_keys = self._run_issue_keys(run_id)
        results = []
        for issue_key in issue_keys:
            results.append(
                self.record_approval(
                    run_id,
                    issue_key,
                    reviewer=reviewer,
                    decision=decision,
                    reviewer_notes=reviewer_notes,
                    user_input=user_input,
                )
            )
        return {
            "run_id": run_id,
            "decision": decision,
            "reviewer": reviewer,
            "item_count": len(issue_keys),
            "results": results,
        }

    def writeback(self, run_id: str, issue_key: str) -> dict[str, Any]:
        logger.info("Writeback requested run_id=%s issue_key=%s", run_id, issue_key)
        item = self.store.get_run_item(run_id, issue_key)
        self.store.create_lock(issue_key, "ai_implementation_brief", run_id)
        try:
            draft = self.store.get_latest_draft(run_id, issue_key)
            try:
                approval = self.store.get_approval_decision(run_id, issue_key)
            except FileNotFoundError:
                approval = {}
            policy = self.policy_service.evaluate(
                float(item.get("confidence", 0)),
                list(item.get("policy_flags", [])),
            )
            writeback = self.writeback_service.writeback(run_id, issue_key, draft, approval, policy)
            self.store.save_writeback(run_id, issue_key, writeback.to_dict())
            item["status"] = writeback.status
            item["updated_at"] = utc_now()
            self.store.save_run_item(run_id, issue_key, item)
            self._sync_status_item(
                run_id,
                issue_key,
                item.get("jira_summary", ""),
                item["status"],
                item["approval_required"],
                item["readiness_score"],
                item["confidence"],
                item.get("warnings", []),
            )
            logger.info(
                "Writeback finished run_id=%s issue_key=%s status=%s",
                run_id,
                issue_key,
                writeback.status,
            )
            return writeback.to_dict()
        finally:
            self.store.release_lock(issue_key, "ai_implementation_brief")

    def writeback_run(self, run_id: str) -> dict[str, Any]:
        issue_keys = self._run_issue_keys(run_id)
        results = []
        for issue_key in issue_keys:
            results.append(self.writeback(run_id, issue_key))
        return {
            "run_id": run_id,
            "item_count": len(issue_keys),
            "results": results,
        }

    def replay(self, run_id: str) -> dict[str, Any]:
        return self.replay_worker.replay(run_id)

    def suggest_epic_stories(
        self,
        epic_key: str,
        project_key: str,
        requestor: str,
    ) -> dict[str, Any]:
        logger.info(
            "epic_breakdown.step start epic_key=%s project_key=%s requestor=%s",
            epic_key,
            project_key,
            requestor,
        )
        warnings: list[dict[str, str]] = []
        try:
            logger.info("epic_breakdown.step resolve_existing_stories_start epic_key=%s", epic_key)
            existing_issue_keys = self.scope_resolver.resolve("epic", epic_key)
        except ValueError:
            # In mock/local scope maps, an unseen epic should still allow suggestion
            # flow to run so users can bootstrap stories for a new epic key.
            existing_issue_keys = []
            logger.info("epic_breakdown.step resolve_existing_stories_unknown_epic epic_key=%s", epic_key)
        except RuntimeError as exc:
            message = str(exc)
            no_child_story_match = (
                "No issues have a parent epic" in message
                or "parent epic with key or name" in message
            )
            if not no_child_story_match:
                raise
            existing_issue_keys = []
            warnings.append(
                _warning(
                    "epic_existing_story_lookup_empty",
                    "jira",
                    "Jira reported no existing child stories for this epic; generation will continue.",
                    ref=message,
                )
            )
            logger.info(
                "epic_breakdown.step resolve_existing_stories_no_children epic_key=%s error=%s",
                epic_key,
                message,
            )
        logger.info(
            "epic_breakdown.step resolve_existing_stories_done epic_key=%s existing_issue_count=%s",
            epic_key,
            len(existing_issue_keys),
        )
        existing_summaries: list[str] = []
        for issue_key in existing_issue_keys:
            try:
                logger.info("epic_breakdown.step fetch_existing_story_summary_start epic_key=%s issue_key=%s", epic_key, issue_key)
                existing_summaries.append(str(self.scanner.jira.get_issue(issue_key).get("summary", "")).strip())
                logger.info("epic_breakdown.step fetch_existing_story_summary_done epic_key=%s issue_key=%s", epic_key, issue_key)
            except Exception:  # noqa: BLE001
                logger.exception("epic_breakdown.step fetch_existing_story_summary_failed epic_key=%s issue_key=%s", epic_key, issue_key)
                continue
        logger.info("epic_breakdown.step collect_existing_summaries_done epic_key=%s summary_count=%s", epic_key, len(existing_summaries))

        epic_summary = epic_key
        normalized_epic: dict[str, Any] = {
            "issue_key": epic_key,
            "summary": epic_key,
            "description": "",
            "acceptance_criteria": [],
            "dependencies": [],
            "edge_cases": [],
            "comments": [],
            "worklogs": [],
            "links": {"confluence_pages": [], "bitbucket_prs": []},
        }
        try:
            logger.info("epic_breakdown.step fetch_epic_start epic_key=%s", epic_key)
            normalized_epic = self.scanner.scan(epic_key)
            epic_summary = str(normalized_epic.get("summary", epic_key)).strip() or epic_key
            logger.info(
                "epic_breakdown.step fetch_epic_done epic_key=%s summary=%s description_chars=%s",
                epic_key,
                epic_summary,
                len(str(normalized_epic.get("description", ""))),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("epic_breakdown.step fetch_epic_failed epic_key=%s error=%s", epic_key, exc)
            epic_summary = epic_key
        logger.info("epic_breakdown.step resolved_epic_summary epic_key=%s summary=%s", epic_key, epic_summary)

        links = dict(normalized_epic.get("links") or {})
        logger.info(
            "epic_breakdown.step extract_links_start epic_key=%s linked_confluence_count=%s linked_bitbucket_count=%s",
            epic_key,
            len(links.get("confluence_pages") or []),
            len(links.get("bitbucket_prs") or []),
        )
        confluence_pages = _canonical_confluence_refs(
            [str(ref) for ref in links.get("confluence_pages") or []]
            + _parse_confluence_refs(str(normalized_epic.get("description", "")))
        )
        links["confluence_pages"] = confluence_pages
        links["bitbucket_prs"] = sorted(set(links.get("bitbucket_prs") or []))
        normalized_epic["links"] = links
        logger.info(
            "epic_breakdown.step extract_links_done epic_key=%s confluence_refs=%s bitbucket_refs=%s",
            epic_key,
            links["confluence_pages"],
            links["bitbucket_prs"],
        )
        logger.info("epic_breakdown.step resolve_evidence_start epic_key=%s", epic_key)
        evidence, evidence_warnings = self.evidence_resolver.resolve(
            f"epic-breakdown-{epic_key.lower()}",
            epic_key,
            normalized_epic,
        )
        warnings.extend(evidence_warnings)
        logger.info(
            "epic_breakdown.step resolve_evidence_done epic_key=%s evidence_count=%s warning_count=%s evidence_refs=%s",
            epic_key,
            len(evidence),
            len(warnings),
            [record.source_ref for record in evidence],
        )
        logger.info("epic_breakdown.step codex_plan_start epic_key=%s", epic_key)
        candidates = _plan_epic_story_candidates_with_reasoner(
            self.semantic_reasoner,
            epic_key,
            epic_summary,
            normalized_epic,
            evidence,
            existing_summaries,
        )
        skipped_existing_summaries: list[str] = []
        if candidates:
            planned_candidates = []
            for candidate in candidates:
                if _is_existing_story_candidate(candidate, existing_summaries):
                    skipped_existing_summaries.append(candidate["summary"])
                    logger.info(
                        "epic_breakdown.step codex_plan_skipped_existing epic_key=%s summary=%s",
                        epic_key,
                        candidate["summary"],
                    )
                    continue
                planned_candidates.append(candidate)
            candidates = planned_candidates
        logger.info(
            "epic_breakdown.step codex_plan_done epic_key=%s candidate_count=%s",
            epic_key,
            len(candidates),
        )
        linked_context_refs = list(links.get("confluence_pages") or []) + list(links.get("bitbucket_prs") or [])
        has_embedded_context = _has_substantive_embedded_epic_context(normalized_epic, evidence)
        suppress_local_fallback = bool(
            not candidates
            and linked_context_refs
            and not evidence
            and not has_embedded_context
            and callable(getattr(self.semantic_reasoner, "plan_epic_stories", None))
        )
        if suppress_local_fallback:
            warnings.append(
                _warning(
                    "epic_breakdown_codex_context_unavailable",
                    "epic_breakdown",
                    "Codex did not return stories from the linked epic context. Reauthenticate MCP/Codex access or verify the linked Confluence page can be read by Codex.",
                    ref=epic_key,
                )
            )
            logger.info(
                "epic_breakdown.step local_fallback_suppressed epic_key=%s reason=codex_linked_context_unavailable linked_context_refs=%s",
                epic_key,
                linked_context_refs,
            )
        if not candidates and not suppress_local_fallback:
            logger.info("epic_breakdown.step derive_candidates_start epic_key=%s source=deterministic_fallback", epic_key)
            candidates, skipped_existing_summaries = _build_evidence_grounded_epic_story_candidates(
                epic_summary,
                normalized_epic,
                evidence,
                existing_summaries,
            )
        logger.info(
            "epic_breakdown.step derive_candidates_done epic_key=%s candidate_count=%s skipped_existing_count=%s",
            epic_key,
            len(candidates),
            len(skipped_existing_summaries),
        )
        if not candidates and not suppress_local_fallback:
            logger.info("epic_breakdown.step evidence_gap_candidate_start epic_key=%s", epic_key)
            candidates.append(_build_epic_evidence_gap_candidate(epic_key, epic_summary))
            warnings.append(
                _warning(
                    "epic_breakdown_evidence_gap",
                    "epic_breakdown",
                    "Could not derive implementation stories from loaded Jira/Confluence evidence.",
                    ref=epic_key,
                )
            )
            logger.info("epic_breakdown.step evidence_gap_candidate_done epic_key=%s", epic_key)
        logger.info("epic_breakdown.step candidate_list_ready epic_key=%s candidate_count=%s", epic_key, len(candidates))

        suggestions: list[dict[str, Any]] = []
        context_note = (
            f"Epic: {epic_key} - {epic_summary}. Existing stories: "
            + (", ".join(existing_summaries) if existing_summaries else "none")
        )
        evidence_refs = [record.source_ref for record in evidence]
        for index, candidate in enumerate(candidates, start=1):
            summary = str(candidate.get("summary", "")).strip() or f"{epic_summary}: implementation story"
            story_points = int(candidate.get("story_points", 5) or 5)
            logger.info(
                "epic_breakdown.step candidate_start epic_key=%s candidate_index=%s candidate_id=%s summary=%s story_points=%s",
                epic_key,
                index,
                candidate.get("id", "unknown"),
                summary,
                story_points,
            )
            normalized_issue = {
                "issue_key": f"{epic_key}-SUG-{index}",
                "summary": summary,
                "description": str(candidate.get("description", "")).strip()
                or f"Break down epic {epic_key} into implementation-ready story: {summary}",
                "acceptance_criteria": list(candidate.get("acceptance_criteria") or []),
                "dependencies": list(candidate.get("dependencies") or []),
                "edge_cases": list(candidate.get("edge_cases") or []),
                "comments": [],
                "worklogs": [],
                "links": {"confluence_pages": links.get("confluence_pages", []), "bitbucket_prs": links.get("bitbucket_prs", [])},
            }
            best_analysis: dict[str, Any] = {}
            best_confidence = 0
            best_readiness = 0
            guidance_input = (
                f"{context_note}\n"
                f"Story candidate source: {candidate.get('id', 'unknown')}.\n"
                "Keep the story grounded in the loaded epic evidence and do not convert it into generic UI/API/persistence work."
            )
            max_attempts = 1 if not evidence or candidate.get("id") == "evidence_gap" else 3
            for attempt in range(1, max_attempts + 1):
                logger.info(
                    "epic_breakdown.step candidate_attempt_start epic_key=%s candidate_index=%s attempt=%s evidence_count=%s",
                    epic_key,
                    index,
                    attempt,
                    len(evidence),
                )
                analysis = self.semantic_reasoner.analyze_issue(normalized_issue, evidence, guidance_input)
                logger.info(
                    "epic_breakdown.step candidate_reasoner_done epic_key=%s candidate_index=%s attempt=%s reasoning_mode=%s",
                    epic_key,
                    index,
                    attempt,
                    analysis.get("reasoning_mode", ""),
                )
                analysis = _ensure_epic_story_analysis_has_test_cases(summary, analysis)
                logger.info(
                    "epic_breakdown.step candidate_test_cases_done epic_key=%s candidate_index=%s attempt=%s acceptance_count=%s",
                    epic_key,
                    index,
                    attempt,
                    len(analysis.get("acceptance_criteria", []) or []),
                )
                readiness, confidence, _ = self.scorer.score(
                    normalized_issue,
                    suggested_description=analysis.get("suggested_description", ""),
                    evidence_count=len(evidence),
                    analysis=analysis,
                    mode="guided",
                )
                logger.info(
                    "epic_breakdown.step candidate_scored epic_key=%s candidate_index=%s attempt=%s readiness=%s confidence=%s",
                    epic_key,
                    index,
                    attempt,
                    readiness,
                    confidence,
                )
                if not evidence or candidate.get("id") == "evidence_gap":
                    logger.info(
                        "epic_breakdown.step candidate_confidence_capped epic_key=%s candidate_index=%s attempt=%s reason=%s readiness_before=%s confidence_before=%s",
                        epic_key,
                        index,
                        attempt,
                        "missing_evidence" if not evidence else "evidence_gap",
                        readiness,
                        confidence,
                    )
                    readiness = min(readiness, 8)
                    confidence = min(confidence, 8)
                logger.info(
                    "epic_breakdown.step candidate_attempt_done epic_key=%s candidate_index=%s attempt=%s readiness=%s confidence=%s",
                    epic_key,
                    index,
                    attempt,
                    readiness,
                    confidence,
                )
                if confidence >= best_confidence:
                    best_analysis = analysis
                    best_confidence = confidence
                    best_readiness = readiness
                if confidence >= self.rl_target_confidence:
                    logger.info(
                        "epic_breakdown.step candidate_threshold_met epic_key=%s candidate_index=%s attempt=%s confidence=%s target=%s",
                        epic_key,
                        index,
                        attempt,
                        confidence,
                        self.rl_target_confidence,
                    )
                    break
                guidance_input = (
                    f"{context_note}\n"
                    f"Story candidate source: {candidate.get('id', 'unknown')}.\n"
                    f"Improve confidence from {confidence}/10 to at least {self.rl_target_confidence}/10 with specific acceptance criteria and clearer next step."
                )
                logger.info(
                    "epic_breakdown.step candidate_retry_guidance_prepared epic_key=%s candidate_index=%s next_attempt=%s",
                    epic_key,
                    index,
                    attempt + 1,
                )

            logger.info(
                "epic_breakdown.step candidate_finalize_start epic_key=%s candidate_index=%s best_readiness=%s best_confidence=%s",
                epic_key,
                index,
                best_readiness,
                best_confidence,
            )
            suggestions.append(
                {
                    "epic_key": epic_key,
                    "project_key": project_key,
                    "summary": summary,
                    "description": best_analysis.get("suggested_description", normalized_issue["description"]),
                    "acceptance_criteria": best_analysis.get("acceptance_criteria", []),
                    "recommended_next_step": best_analysis.get("recommended_next_step", ""),
                    "story_points": story_points,
                    "readiness_score": best_readiness,
                    "confidence": best_confidence,
                    "meets_threshold": best_confidence >= self.rl_target_confidence,
                    "evidence_refs": evidence_refs,
                }
            )
            logger.info(
                "epic_breakdown.step candidate_done epic_key=%s candidate_index=%s best_readiness=%s best_confidence=%s",
                epic_key,
                index,
                best_readiness,
                best_confidence,
            )

        logger.info(
            "epic_breakdown.step completed epic_key=%s suggested_count=%s warning_count=%s evidence_count=%s",
            epic_key,
            len(suggestions),
            len(warnings),
            len(evidence),
        )
        return {
            "epic_key": epic_key,
            "project_key": project_key,
            "requestor": requestor,
            "existing_story_count": len(existing_issue_keys),
            "existing_stories": existing_issue_keys,
            "evidence_refs": evidence_refs,
            "warnings": _dedupe_warnings(warnings),
            "skipped_existing_story_summaries": skipped_existing_summaries,
            "suggested_stories": suggestions,
        }

    def create_epic_stories_on_approval(
        self,
        epic_key: str,
        project_key: str,
        requestor: str,
        stories: list[dict[str, Any]],
    ) -> dict[str, Any]:
        existing_issue_keys: list[str] = []
        try:
            existing_issue_keys = self.scope_resolver.resolve("epic", epic_key)
        except ValueError:
            existing_issue_keys = []

        existing_stories: list[dict[str, Any]] = []
        existing_by_summary: dict[str, dict[str, Any]] = {}
        if existing_issue_keys:
            for issue_key in existing_issue_keys:
                try:
                    issue = self.scanner.jira.get_issue(issue_key)
                    existing_story = {
                        "key": str(issue.get("key", issue_key)),
                        "summary": str(issue.get("summary", "")).strip(),
                        "description": str(issue.get("description", "")).strip(),
                        "acceptance_criteria": list(issue.get("acceptance_criteria", [])),
                        "dependencies": list(issue.get("dependencies", [])),
                        "edge_cases": list(issue.get("edge_cases", [])),
                        "epic": str(issue.get("epic", "")).strip(),
                        "story_points": issue.get("story_points"),
                    }
                except Exception:  # noqa: BLE001
                    existing_story = {"key": issue_key, "summary": ""}
                existing_stories.append(existing_story)
                normalized_summary = _normalize_summary(str(existing_story.get("summary", "")))
                if normalized_summary:
                    existing_by_summary[normalized_summary] = existing_story

        if existing_issue_keys and not stories:
            logger.info(
                "create_epic_stories_on_approval skipped create because no requested stories and existing stories found epic_key=%s count=%s",
                epic_key,
                len(existing_issue_keys),
            )
            return {
                "epic_key": epic_key,
                "project_key": project_key,
                "requestor": requestor,
                "status": "already_exists",
                "message": f"Stories already exist for epic {epic_key}.",
                "existing_story_count": len(existing_issue_keys),
                "existing_stories": existing_stories,
                "created_count": 0,
                "skipped_count": 0,
                "created": [],
                "skipped": [],
            }

        created: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for story in stories:
            confidence = int(story.get("confidence", 0) or 0)
            approved = bool(story.get("approved", False))
            story_summary = str(story.get("summary", "")).strip()
            if confidence < self.rl_target_confidence or not approved:
                skipped.append(
                    {
                        "summary": story_summary,
                        "reason": "not_approved_or_low_confidence",
                        "confidence": confidence,
                    }
                )
                continue
            existing_match = existing_by_summary.get(_normalize_summary(story_summary))
            if existing_match:
                skipped.append(
                    {
                        "summary": story_summary,
                        "reason": "already_exists",
                        "confidence": confidence,
                        "existing_issue_key": existing_match["key"],
                    }
                )
                continue
            try:
                result = self.scanner.jira.create_issue(
                    project_key=project_key,
                    summary=story_summary,
                    description=str(story.get("description", "")).strip(),
                    issue_type="Story",
                    additional_fields={
                        "epic": epic_key,
                        "assignee": requestor,
                        "story_points": int(story.get("story_points", 0) or 0),
                        "acceptance_criteria": list(story.get("acceptance_criteria", [])),
                    },
                )
                created.append(
                    {
                        "jira_update_id": result.get("jira_update_id", ""),
                        "issue": result.get("issue", {}),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "create_epic_stories_on_approval create_failed epic_key=%s summary=%s error=%s",
                    epic_key,
                    story_summary,
                    exc,
                )
                skipped.append(
                    {
                        "summary": story_summary,
                        "reason": "create_failed",
                        "confidence": confidence,
                        "error": str(exc),
                    }
                )
        create_failures = [item for item in skipped if item.get("reason") == "create_failed"]
        duplicate_skips = [item for item in skipped if item.get("reason") == "already_exists"]
        if created:
            status = "created"
        elif create_failures:
            status = "failed"
        elif duplicate_skips:
            status = "already_exists"
        else:
            status = "failed"
        return {
            "epic_key": epic_key,
            "project_key": project_key,
            "requestor": requestor,
            "status": status,
            "message": f"Epic story creation complete for {epic_key}.",
            "existing_story_count": len(existing_stories),
            "existing_stories": existing_stories,
            "created_count": len(created),
            "skipped_count": len(skipped),
            "created": created,
            "skipped": skipped,
        }
