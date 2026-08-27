from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_STUDY = Path(__file__).resolve().parent / "private_matched_comparison_20260811"
DEFAULT_ANALYSIS = (
    Path(__file__).resolve().parent
    / "completed_matched_reviews_20260813"
    / "analysis"
    / "matched_review_analysis_summary.json"
)
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "outputs" / "tosem_four_mode_ablation_review"
INPUT_IDS = tuple(f"E{index:02d}" for index in range(1, 15))
EXECUTED_METHODS = ("B1", "B2", "B3")
ALL_METHODS = ("B0",) + EXECUTED_METHODS
STORIES_PER_PACKAGE = 5
EVENT_SCHEMA = "four-mode-audit-event-v1"

PRIVACY_PATTERNS = {
    "internal_jira_id": re.compile(r"\bOIC-\d+\b", re.IGNORECASE),
    "internal_host": re.compile(
        r"(?:jira|confluence|bitbucket)\.[a-z0-9-]*corp\.(?:com|net|org)",
        re.IGNORECASE,
    ),
    "email": re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE),
    "url": re.compile(r"https?://", re.IGNORECASE),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_text_exclusive(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())


def _write_json_exclusive(path: Path, value: Any) -> None:
    _write_text_exclusive(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _write_csv_exclusive(path: Path, rows: Iterable[dict[str, Any]], headers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)
        handle.flush()
        os.fsync(handle.fileno())


def _relative(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(REPO_ROOT))
    except ValueError:
        return str(resolved)


def _parse_timestamp(value: Any, label: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label}: timestamp is required")
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError(f"{label}: timestamp must include a timezone")
    return parsed


def _privacy_findings(value: Any) -> list[str]:
    text = json.dumps(value, ensure_ascii=False)
    return [label for label, pattern in PRIVACY_PATTERNS.items() if pattern.search(text)]


def _validate_story(story: Any, *, method: str, input_id: str, index: int) -> None:
    if not isinstance(story, dict):
        raise ValueError(f"{method}/{input_id}/story-{index}: story must be an object")
    for field in ("summary", "description"):
        if not str(story.get(field, "")).strip():
            raise ValueError(f"{method}/{input_id}/story-{index}: {field} is required")
    criteria = story.get("acceptance_criteria")
    if not isinstance(criteria, list) or not any(str(item).strip() for item in criteria):
        raise ValueError(f"{method}/{input_id}/story-{index}: acceptance criteria are required")
    for field in ("dependencies", "edge_cases", "risks", "nfrs", "evidence_refs"):
        if field in story and not isinstance(story[field], list):
            raise ValueError(f"{method}/{input_id}/story-{index}: {field} must be a list")
    try:
        points = int(story.get("story_points", 3))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{method}/{input_id}/story-{index}: invalid story points") from exc
    if points < 1 or points > 13:
        raise ValueError(f"{method}/{input_id}/story-{index}: story points must be in [1,13]")


def _validate_package(
    artifact: Any,
    *,
    method: str,
    input_id: str,
    input_sha256: str,
    human_b0: bool = False,
) -> dict[str, Any]:
    if not isinstance(artifact, dict):
        raise ValueError(f"{method}/{input_id}: artifact must be an object")
    if human_b0:
        allowed_top_level = {
            "schema_version",
            "study_version",
            "artifact_kind",
            "method",
            "input_id",
            "input_sha256",
            "authored_at",
            "story_count",
            "stories",
            "private_generation_metadata",
        }
        unknown = sorted(set(artifact) - allowed_top_level)
        if unknown:
            raise ValueError(f"B0/{input_id}: unknown top-level fields: {','.join(unknown)}")
        if artifact.get("schema_version") != "b0-human-artifact-v1":
            raise ValueError(f"B0/{input_id}: invalid schema_version")
        if artifact.get("study_version") != "matched-b0-b1-b2-b3-v1":
            raise ValueError(f"B0/{input_id}: invalid study_version")
        if artifact.get("artifact_kind") != "human_manual_package":
            raise ValueError(f"B0/{input_id}: invalid artifact_kind")
        _parse_timestamp(artifact.get("authored_at"), f"B0/{input_id}/authored_at")
    if artifact.get("method") != method or artifact.get("input_id") != input_id:
        raise ValueError(f"{method}/{input_id}: method or input identifier mismatch")
    if artifact.get("input_sha256") != input_sha256:
        raise ValueError(f"{method}/{input_id}: frozen input hash mismatch")
    stories = artifact.get("stories")
    if not isinstance(stories, list) or len(stories) != STORIES_PER_PACKAGE:
        raise ValueError(f"{method}/{input_id}: exactly five stories are required")
    if int(artifact.get("story_count", -1)) != STORIES_PER_PACKAGE:
        raise ValueError(f"{method}/{input_id}: story_count must equal five")
    normalized_summaries: list[str] = []
    for index, story in enumerate(stories, start=1):
        _validate_story(story, method=method, input_id=input_id, index=index)
        normalized_summaries.append(" ".join(str(story["summary"]).casefold().split()))
    if len(set(normalized_summaries)) != STORIES_PER_PACKAGE:
        raise ValueError(f"{method}/{input_id}: story summaries must be distinct")
    findings = _privacy_findings(stories)
    if findings:
        raise ValueError(f"{method}/{input_id}: privacy findings: {','.join(findings)}")

    metadata = artifact.get("private_generation_metadata")
    if not isinstance(metadata, dict):
        raise ValueError(f"{method}/{input_id}: private_generation_metadata is required")
    model_calls = int(metadata.get("model_calls", -1))
    if human_b0:
        _validate_b0_metadata(metadata, stories, input_id, input_sha256)
    elif method == "B1" and model_calls != 0:
        raise ValueError(f"B1/{input_id}: model_calls must be zero")
    elif method == "B2" and (model_calls != 1 or int(metadata.get("retry_count", -1)) != 0):
        raise ValueError(f"B2/{input_id}: expected one model call and no retry")
    elif method == "B3":
        diagnostics = metadata.get("story_diagnostics")
        if not isinstance(diagnostics, list) or len(diagnostics) != STORIES_PER_PACKAGE:
            raise ValueError(f"B3/{input_id}: five story diagnostics are required")
        if metadata.get("writeback_enabled") is not False:
            raise ValueError(f"B3/{input_id}: matched run must disable writeback")
    return metadata


def _validate_b0_metadata(
    metadata: dict[str, Any], stories: list[dict[str, Any]], input_id: str, input_sha256: str
) -> None:
    if metadata.get("execution") != "independent_human_manual_planning":
        raise ValueError(f"B0/{input_id}: execution provenance is invalid")
    if int(metadata.get("model_calls", -1)) != 0:
        raise ValueError(f"B0/{input_id}: model_calls must be zero")
    if not str(metadata.get("author_code", "")).strip() or not str(metadata.get("experience_band", "")).strip():
        raise ValueError(f"B0/{input_id}: pseudonymous author and experience band are required")
    if metadata.get("input_packet_sha256") != input_sha256:
        raise ValueError(f"B0/{input_id}: authoring input hash mismatch")
    allowed_tools = metadata.get("allowed_tools")
    if not isinstance(allowed_tools, list) or not all(str(tool).strip() for tool in allowed_tools):
        raise ValueError(f"B0/{input_id}: allowed_tools must be a list of nonempty labels")
    try:
        revision_count = int(metadata.get("revision_count", -1))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"B0/{input_id}: revision_count must be a nonnegative integer") from exc
    if revision_count < 0:
        raise ValueError(f"B0/{input_id}: revision_count must be a nonnegative integer")
    started = _parse_timestamp(metadata.get("started_at"), f"B0/{input_id}/started_at")
    completed = _parse_timestamp(metadata.get("completed_at"), f"B0/{input_id}/completed_at")
    active_seconds = float(metadata.get("active_authoring_seconds", 0) or 0)
    if completed < started or active_seconds <= 0 or active_seconds > (completed - started).total_seconds():
        raise ValueError(f"B0/{input_id}: authoring time is inconsistent")
    attestation = metadata.get("attestation")
    required_attestation = {
        "independently_authored": True,
        "generative_ai_used": False,
        "saw_other_mode_outputs": False,
        "used_only_frozen_input": True,
        "other_person_collaboration_used": False,
        "study_team_repaired_output": False,
    }
    if not isinstance(attestation, dict):
        raise ValueError(f"B0/{input_id}: author attestation is required")
    if attestation.get("version") != "b0-human-attestation-v1":
        raise ValueError(f"B0/{input_id}: invalid attestation version")
    required_attestation["author_not_reviewer"] = True
    for field, expected in required_attestation.items():
        if attestation.get(field) is not expected:
            raise ValueError(f"B0/{input_id}: attestation {field} must be {expected}")
    _parse_timestamp(attestation.get("attested_at"), f"B0/{input_id}/attested_at")
    for index, story in enumerate(stories, start=1):
        if story.get("planner") != "human_manual":
            raise ValueError(f"B0/{input_id}/story-{index}: planner must be human_manual")
        refs = story.get("evidence_refs", [])
        if refs and any(str(ref) != f"evidence:{input_id}" for ref in refs):
            raise ValueError(f"B0/{input_id}/story-{index}: evidence reference is outside the frozen input")


@dataclass
class EventLog:
    path: Path
    run_id: str
    sequence: int = 0
    previous_hash: str | None = None

    def __post_init__(self) -> None:
        _write_text_exclusive(self.path, "")

    def emit(
        self,
        event_type: str,
        *,
        stage: str,
        status: str,
        level: str = "INFO",
        method: str = "",
        input_id: str = "",
        data: dict[str, Any] | None = None,
    ) -> None:
        self.sequence += 1
        record = {
            "schema_version": EVENT_SCHEMA,
            "sequence": self.sequence,
            "event_id": f"{self.run_id}:{self.sequence:06d}",
            "run_id": self.run_id,
            "recorded_at": _utc_now(),
            "event_type": event_type,
            "level": level,
            "stage": stage,
            "status": status,
            "method": method,
            "input_id": input_id,
            "data": data or {},
            "previous_event_sha256": self.previous_hash,
        }
        event_hash = _sha256_bytes(_canonical_json(record))
        record["event_sha256"] = event_hash
        descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND)
        try:
            os.write(descriptor, _canonical_json(record) + b"\n")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self.previous_hash = event_hash


def _load_packets(source_study: Path) -> tuple[dict[str, dict[str, Any]], Path]:
    packet_path = source_study / "sanitized_input_packets.json"
    payload = _read_json(packet_path)
    packets = payload.get("packets", []) if isinstance(payload, dict) else []
    by_id = {str(packet.get("input_id")): packet for packet in packets if isinstance(packet, dict)}
    if tuple(sorted(by_id)) != INPUT_IDS:
        raise ValueError("The frozen study must contain exactly E01 through E14")
    for input_id, packet in by_id.items():
        if not re.fullmatch(r"[0-9a-f]{64}", str(packet.get("input_sha256", ""))):
            raise ValueError(f"{input_id}: invalid input_sha256")
    return by_id, packet_path


def _source_record(path: Path) -> dict[str, Any]:
    return {"path": _relative(path), "sha256": _sha256_file(path), "size_bytes": path.stat().st_size}


def _historical_artifacts(
    source_study: Path,
    packets: dict[str, dict[str, Any]],
    events: EventLog,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[Path]]:
    inventory: list[dict[str, Any]] = []
    prompt_metrics: list[dict[str, Any]] = []
    paths: list[Path] = []
    for input_id in INPUT_IDS:
        metric: dict[str, Any] = {
            "input_id": input_id,
            "b2_model_calls": 0,
            "b3_total_model_calls": 0,
            "b3_planning_calls": 0,
            "b3_refinement_calls": 0,
            "b3_attempt_1_stories": 0,
            "b3_attempt_2_stories": 0,
            "b3_attempt_3_stories": 0,
            "b3_target_attained": 0,
            "b3_budget_exhausted_below_target": 0,
            "b3_approval_required": 0,
            "b3_policy_flag_occurrences": 0,
        }
        for method in EXECUTED_METHODS:
            path = source_study / "generation" / "artifacts" / method / f"{input_id}.json"
            artifact = _read_json(path)
            metadata = _validate_package(
                artifact,
                method=method,
                input_id=input_id,
                input_sha256=str(packets[input_id]["input_sha256"]),
            )
            paths.append(path)
            artifact_hash = _sha256_file(path)
            inventory.append(
                {
                    "method": method,
                    "input_id": input_id,
                    "status": "historical_execution_validated",
                    "story_count": STORIES_PER_PACKAGE,
                    "model_calls": int(metadata.get("model_calls", 0) or 0),
                    "artifact_path": _relative(path),
                    "artifact_sha256": artifact_hash,
                }
            )
            events.emit(
                "artifact_validated",
                stage="source_inventory",
                status="pass",
                method=method,
                input_id=input_id,
                data={"artifact_path": _relative(path), "artifact_sha256": artifact_hash},
            )
            if method == "B2":
                metric["b2_model_calls"] = int(metadata.get("model_calls", 0) or 0)
            if method == "B3":
                diagnostics = list(metadata.get("story_diagnostics", []))
                refinement_calls = 0
                for diagnostic in diagnostics:
                    trajectory = list(diagnostic.get("confidence_trajectory", []))
                    attempts = len(trajectory)
                    refinement_calls += attempts
                    if attempts in (1, 2, 3):
                        metric[f"b3_attempt_{attempts}_stories"] += 1
                    terminal = float(diagnostic.get("confidence", 0) or 0)
                    if terminal >= 9:
                        metric["b3_target_attained"] += 1
                    elif attempts >= 3:
                        metric["b3_budget_exhausted_below_target"] += 1
                    if diagnostic.get("approval_required") is True:
                        metric["b3_approval_required"] += 1
                    metric["b3_policy_flag_occurrences"] += len(list(diagnostic.get("policy_flags", [])))
                metric["b3_total_model_calls"] = int(metadata.get("model_calls", 0) or 0)
                metric["b3_planning_calls"] = max(0, metric["b3_total_model_calls"] - refinement_calls)
                metric["b3_refinement_calls"] = refinement_calls
        prompt_metrics.append(metric)
    return inventory, prompt_metrics, paths


def _b0_artifacts(
    b0_root: Path | None,
    packets: dict[str, dict[str, Any]],
    events: EventLog,
) -> tuple[list[dict[str, Any]], list[str], list[Path]]:
    inventory: list[dict[str, Any]] = []
    missing: list[str] = []
    paths: list[Path] = []
    for input_id in INPUT_IDS:
        path = b0_root / f"{input_id}.json" if b0_root else None
        if path is None or not path.exists():
            missing.append(input_id)
            inventory.append(
                {
                    "method": "B0",
                    "input_id": input_id,
                    "status": "pending_human_artifact",
                    "story_count": 0,
                    "model_calls": 0,
                    "artifact_path": "",
                    "artifact_sha256": "",
                }
            )
            events.emit(
                "artifact_pending",
                stage="b0_intake",
                status="pending",
                level="WARNING",
                method="B0",
                input_id=input_id,
                data={"reason": "independently authored human package not supplied"},
            )
            continue
        artifact = _read_json(path)
        _validate_package(
            artifact,
            method="B0",
            input_id=input_id,
            input_sha256=str(packets[input_id]["input_sha256"]),
            human_b0=True,
        )
        paths.append(path)
        artifact_hash = _sha256_file(path)
        inventory.append(
            {
                "method": "B0",
                "input_id": input_id,
                "status": "human_execution_validated",
                "story_count": STORIES_PER_PACKAGE,
                "model_calls": 0,
                "artifact_path": _relative(path),
                "artifact_sha256": artifact_hash,
            }
        )
        events.emit(
            "artifact_validated",
            stage="b0_intake",
            status="pass",
            method="B0",
            input_id=input_id,
            data={"artifact_path": _relative(path), "artifact_sha256": artifact_hash},
        )
    return inventory, missing, paths


def _analysis_snapshot(analysis_path: Path) -> dict[str, Any]:
    analysis = _read_json(analysis_path)
    methods = {
        row["method"]: row
        for row in analysis.get("primary_method_summary", [])
        if row.get("method") in EXECUTED_METHODS
    }
    pairwise = {
        row["outcome"]: row
        for row in analysis.get("primary_pairwise", [])
        if row.get("contrast") == "B3-B2"
    }
    dimensions = [
        row
        for row in analysis.get("primary_dimension_pairwise", [])
        if row.get("contrast") == "B3-B2"
    ]
    resources = {
        row["method"]: row
        for row in analysis.get("generation_resources", [])
        if row.get("method") in EXECUTED_METHODS
    }
    required_methods = set(EXECUTED_METHODS)
    if set(methods) != required_methods or set(resources) != required_methods:
        raise ValueError("Historical analysis is missing a B1, B2, or B3 summary")
    if set(pairwise) != {"composite", "review_time_min", "required_edits"}:
        raise ValueError("Historical analysis is missing a B3-B2 outcome")
    return {
        "analysis_version": analysis.get("analysis_version"),
        "source_hash": _sha256_file(analysis_path),
        "source_path": _relative(analysis_path),
        "primary_reviewers": analysis.get("primary_reviewers", []),
        "validation": analysis.get("validation", {}),
        "method_summary": methods,
        "b3_vs_b2": pairwise,
        "b3_vs_b2_dimensions": dimensions,
        "generation_resources": resources,
    }


def _b0_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "b0-human-artifact-v1",
        "title": "Independent human B0 package",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "study_version",
            "artifact_kind",
            "method",
            "input_id",
            "input_sha256",
            "authored_at",
            "story_count",
            "stories",
            "private_generation_metadata",
        ],
        "properties": {
            "schema_version": {"const": "b0-human-artifact-v1"},
            "study_version": {"const": "matched-b0-b1-b2-b3-v1"},
            "artifact_kind": {"const": "human_manual_package"},
            "method": {"const": "B0"},
            "input_id": {"enum": list(INPUT_IDS)},
            "input_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "authored_at": {"type": "string", "format": "date-time"},
            "story_count": {"const": STORIES_PER_PACKAGE},
            "stories": {"type": "array", "minItems": 5, "maxItems": 5},
            "private_generation_metadata": {"type": "object"},
        },
    }


def _method_status_rows(inventory: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for method in ALL_METHODS:
        selected = [row for row in inventory if row["method"] == method]
        complete = [row for row in selected if int(row["story_count"]) == STORIES_PER_PACKAGE]
        statuses = sorted({str(row["status"]) for row in selected})
        rows.append(
            {
                "method": method,
                "packages_complete": len(complete),
                "packages_required": len(INPUT_IDS),
                "stories_complete": sum(int(row["story_count"]) for row in complete),
                "stories_required": len(INPUT_IDS) * STORIES_PER_PACKAGE,
                "status": "|".join(statuses),
            }
        )
    return rows


def _draft_report(
    run_id: str,
    status: str,
    missing_b0: list[str],
    snapshot: dict[str, Any],
    prompt_metrics: list[dict[str, Any]],
) -> str:
    b2 = snapshot["method_summary"]["B2"]
    b3 = snapshot["method_summary"]["B3"]
    composite = snapshot["b3_vs_b2"]["composite"]
    review_time = snapshot["b3_vs_b2"]["review_time_min"]
    edits = snapshot["b3_vs_b2"]["required_edits"]
    resources = snapshot["generation_resources"]
    refinements = sum(int(row["b3_refinement_calls"]) for row in prompt_metrics)
    attempt_1 = sum(int(row["b3_attempt_1_stories"]) for row in prompt_metrics)
    attempt_2 = sum(int(row["b3_attempt_2_stories"]) for row in prompt_metrics)
    attempt_3 = sum(int(row["b3_attempt_3_stories"]) for row in prompt_metrics)
    target = sum(int(row["b3_target_attained"]) for row in prompt_metrics)
    exhausted = sum(int(row["b3_budget_exhausted_below_target"]) for row in prompt_metrics)
    approval = sum(int(row["b3_approval_required"]) for row in prompt_metrics)
    flags = sum(int(row["b3_policy_flag_occurrences"]) for row in prompt_metrics)
    b2_ready_minor = int(b2["ready_count"]) + int(b2["minor_revision_count"])
    b3_ready_minor = int(b3["ready_count"]) + int(b3["minor_revision_count"])
    missing_text = ", ".join(missing_b0) if missing_b0 else "none"
    return f"""# B0–B3 Ablation: Draft Results for Review

> **DRAFT — NOT FOR MANUSCRIPT.** This audit performed no new model generation and no Jira mutation.

Run ID: `{run_id}`  
Audit status: `{status}`

## Current execution status

The frozen corpus contains 14 common epic/evidence inputs. B1, B2, and B3 each have 14 validated historical packages with five stories per package. B0 has no valid independently authored human packages in the retained study data. Missing B0 inputs: {missing_text}.

Therefore, this is not yet a completed four-mode experiment. It must not be reported as an executed B0–B3 result. The formula-generated B0 values are not used in this document.

## One-shot B2 versus governed B3

B2 used one model call per epic. It used 14 calls in total. It had no retry, critic pass, confidence-linked repair, policy gate, or writeback step.

B3 used {int(resources['B3']['model_calls'])} successful model calls. These consisted of 14 epic-planning calls and {refinements} story-refinement calls. The 70 stories used one, two, or three refinement attempts in {attempt_1}, {attempt_2}, and {attempt_3} cases, respectively. B3 reached its internal confidence target for {target}/70 stories. The remaining {exhausted} stories exhausted the three-attempt budget below the target. Human approval was still required for {approval}/70 stories. The isolated run recorded {flags} policy-flag occurrences. These counts describe the study harness and are not production prevalence estimates.

This evidence shows that B3 automated bounded prompt upgrades that B2 did not perform. It does not show how many human prompts B2 would have required. No human repair session was run for B2.

## Historical blinded-review results

The primary reviewers gave B3 a mean composite score of {float(b3['composite_mean']):.4f}, compared with {float(b2['composite_mean']):.4f} for B2. The paired epic-level difference was {float(composite['mean_difference']):+.4f}, with a 95% interval from {float(composite['ci_low']):.4f} to {float(composite['ci_high']):.4f}. This does not establish an overall artifact-quality advantage for B3.

B3 required {abs(float(review_time['mean_difference'])):.2f} fewer reviewer minutes per package on average. The 95% interval for the B3-minus-B2 difference was [{float(review_time['ci_low']):.2f}, {float(review_time['ci_high']):.2f}] minutes. B3 also required {abs(float(edits['mean_difference'])):.2f} fewer substantive edits on average, although that interval included zero.

Ready-or-Minor decisions were {b3_ready_minor}/42 ({100*float(b3['ready_or_minor_rate']):.1f}%) for B3 and {b2_ready_minor}/42 ({100*float(b2['ready_or_minor_rate']):.1f}%) for B2. Major-revision decisions were {int(b3['major_revision_count'])} for B3 and {int(b2['major_revision_count'])} for B2. These disposition counts are descriptive.

## What the current evidence supports

B3 is stronger than one-shot B2 on the evaluated workflow indicators of review time, Ready-or-Minor disposition, major-revision count, automated refinement, and governance diagnostics. B3 is not currently shown to be better on the overall blinded artifact-quality composite. It also uses substantially more model calls: {int(resources['B3']['model_calls'])} versus {int(resources['B2']['model_calls'])}.

## Required completion step

Independent human authors must produce five stories for every frozen input, using only the supplied packet and no generative AI. Their authoring time and attestations must be logged. After B0 is complete, all 56 packages must be rerandomized and rated together. A B0-only review extension cannot be merged with the earlier B1–B3 ratings without introducing session and order confounding.

Only after that new blinded review can the study report an executed four-mode comparison.
"""


def _fingerprint(paths: Iterable[Path]) -> tuple[list[dict[str, Any]], str]:
    records = sorted((_source_record(path) for path in paths), key=lambda row: row["path"])
    return records, _sha256_bytes(_canonical_json(records))


def build_run(
    *,
    source_study: Path,
    analysis_path: Path,
    output_root: Path,
    run_id: str,
    b0_root: Path | None = None,
) -> Path:
    run_dir = output_root.resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    events = EventLog(run_dir / "events.ndjson", run_id)
    events.emit("run_started", stage="initialization", status="pass", data={"four_mode_generation": False})

    packets, packet_path = _load_packets(source_study.resolve())
    analysis_path = analysis_path.resolve()
    historical, prompt_metrics, historical_paths = _historical_artifacts(source_study.resolve(), packets, events)
    b0_inventory, missing_b0, b0_paths = _b0_artifacts(b0_root.resolve() if b0_root else None, packets, events)
    snapshot = _analysis_snapshot(analysis_path)
    all_source_paths = [packet_path.resolve(), analysis_path] + historical_paths + b0_paths
    source_inventory, initial_source_hash = _fingerprint(all_source_paths)
    events.emit(
        "source_inventory_completed",
        stage="source_inventory",
        status="pass",
        data={"file_count": len(source_inventory), "inventory_sha256": initial_source_hash},
    )

    inventory = b0_inventory + historical
    method_rows = _method_status_rows(inventory)
    status = "pending_b0_human_artifacts" if missing_b0 else "pending_four_mode_blinded_review"
    four_mode_artifacts_complete = not missing_b0
    request = {
        "schema_version": "four-mode-audit-request-v1",
        "run_id": run_id,
        "created_at": _utc_now(),
        "source_study": _relative(source_study),
        "analysis_source": _relative(analysis_path),
        "b0_root": _relative(b0_root) if b0_root else None,
        "fixed_input_ids": list(INPUT_IDS),
        "evidence_character_limit": 24_000,
        "generation_performed": False,
        "jira_mutation_performed": False,
    }
    summary = {
        "schema_version": "four-mode-audit-summary-v1",
        "run_id": run_id,
        "status": status,
        "four_mode_artifacts_complete": four_mode_artifacts_complete,
        "four_mode_execution_complete": False,
        "four_mode_results_available": False,
        "missing_b0_input_ids": missing_b0,
        "validated_historical_packages": len(historical),
        "validated_b0_packages": len(b0_inventory) - len(missing_b0),
        "algorithm_contract": {
            "all_four_modes_executed_on_common_inputs": False,
            "common_four_mode_blinded_scoring_available": False,
            "quality_efficiency_governance_composite_time_tuple_available": False,
            "historical_b3_human_approval_performed": False,
            "historical_b3_writeback_performed": False,
            "formula_generated_results_used_as_execution_evidence": False,
        },
    }
    validation = {
        "status": "pass",
        "historical_methods": list(EXECUTED_METHODS),
        "historical_artifact_count": len(historical),
        "b0_artifact_count": len(b0_inventory) - len(missing_b0),
        "missing_b0_input_ids": missing_b0,
        "source_inventory_sha256": initial_source_hash,
        "notes": [
            "B1-B3 are historical executions validated in place; they were not regenerated.",
            "B0 is accepted only with independent human-authorship metadata and attestation.",
            "No formula-generated comparison is treated as empirical execution evidence.",
        ],
    }

    _write_json_exclusive(run_dir / "run_request.json", request)
    _write_json_exclusive(run_dir / "b0_human_artifact.schema.json", _b0_schema())
    _write_json_exclusive(run_dir / "source_inventory.json", source_inventory)
    _write_json_exclusive(run_dir / "existing_results_snapshot.json", snapshot)
    _write_json_exclusive(run_dir / "validation_report.json", validation)
    _write_json_exclusive(run_dir / "audit_summary.json", summary)
    _write_csv_exclusive(
        run_dir / "method_status.csv",
        method_rows,
        ["method", "packages_complete", "packages_required", "stories_complete", "stories_required", "status"],
    )
    _write_csv_exclusive(
        run_dir / "artifact_inventory.csv",
        inventory,
        ["method", "input_id", "status", "story_count", "model_calls", "artifact_path", "artifact_sha256"],
    )
    prompt_headers = list(prompt_metrics[0].keys())
    _write_csv_exclusive(run_dir / "prompt_refinement_metrics.csv", prompt_metrics, prompt_headers)
    _write_text_exclusive(
        run_dir / "DRAFT_RESULTS_FOR_REVIEW.md",
        _draft_report(run_id, status, missing_b0, snapshot, prompt_metrics),
    )
    _write_text_exclusive(
        run_dir / "B0_AUTHORING_INSTRUCTIONS.md",
        """# B0 Independent Human Authoring Instructions

Use the same frozen anonymized input packet provided to B1, B2, and B3. The effective evidence text is limited to the first 24,000 characters. Produce exactly five distinct implementation stories. Do not use generative AI, formula-generated B0 output, B1/B2/B3 artifacts, or study-team repairs.

Record a pseudonymous author code, experience band, start and completion timestamps, active authoring time, allowed tools, revision count, and the required attestation. Authors must not act as reviewers. Submit one JSON package per input, named E01.json through E14.json, using `b0_human_artifact.schema.json`.

After all 14 B0 packages pass validation, rerandomize B0-B3 together and conduct a new blinded review. Do not append B0-only ratings to the earlier B1-B3 review.
""",
    )

    final_inventory, final_source_hash = _fingerprint(all_source_paths)
    if final_source_hash != initial_source_hash or final_inventory != source_inventory:
        raise RuntimeError("A source file changed while the audit bundle was being built")
    events.emit(
        "run_completed",
        stage="sealing",
        status=status,
        level="WARNING" if missing_b0 else "INFO",
        data={
            "four_mode_artifacts_complete": four_mode_artifacts_complete,
            "four_mode_results_available": False,
            "missing_b0_count": len(missing_b0),
        },
    )

    output_paths = sorted(
        path for path in run_dir.iterdir() if path.name not in {"seal.json", "checksums.sha256"}
    )
    output_hashes = {path.name: _sha256_file(path) for path in output_paths}
    seal = {
        "schema_version": "four-mode-audit-seal-v1",
        "run_id": run_id,
        "sealed_at": _utc_now(),
        "status": status,
        "event_count": events.sequence,
        "final_event_sha256": events.previous_hash,
        "events_file_sha256": _sha256_file(run_dir / "events.ndjson"),
        "source_inventory_sha256": initial_source_hash,
        "output_sha256": output_hashes,
        "script_sha256": _sha256_file(Path(__file__).resolve()),
    }
    _write_json_exclusive(run_dir / "seal.json", seal)
    checksum_paths = sorted(path for path in run_dir.iterdir() if path.name != "checksums.sha256")
    checksum_text = "".join(f"{_sha256_file(path)}  {path.name}\n" for path in checksum_paths)
    _write_text_exclusive(run_dir / "checksums.sha256", checksum_text)
    return run_dir


def verify_run(run_dir: Path) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    errors: list[str] = []
    checksum_path = run_dir / "checksums.sha256"
    for line in checksum_path.read_text(encoding="utf-8").splitlines():
        expected, name = line.split("  ", 1)
        path = run_dir / name
        if not path.is_file() or _sha256_file(path) != expected:
            errors.append(f"checksum:{name}")

    previous: str | None = None
    count = 0
    for count, line in enumerate((run_dir / "events.ndjson").read_text(encoding="utf-8").splitlines(), start=1):
        event = json.loads(line)
        event_hash = event.pop("event_sha256", None)
        if event.get("sequence") != count or event.get("previous_event_sha256") != previous:
            errors.append(f"event_chain:{count}")
        calculated = _sha256_bytes(_canonical_json(event))
        if event_hash != calculated:
            errors.append(f"event_hash:{count}")
        previous = event_hash

    seal = _read_json(run_dir / "seal.json")
    if seal.get("event_count") != count or seal.get("final_event_sha256") != previous:
        errors.append("seal:event_chain")
    if seal.get("events_file_sha256") != _sha256_file(run_dir / "events.ndjson"):
        errors.append("seal:events_file")
    for name, expected in seal.get("output_sha256", {}).items():
        path = run_dir / name
        if not path.is_file() or _sha256_file(path) != expected:
            errors.append(f"seal:output:{name}")
    return {"status": "pass" if not errors else "fail", "errors": errors, "event_count": count}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build or verify an immutable B0-B3 ablation review audit.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--source-study", type=Path, default=DEFAULT_SOURCE_STUDY)
    build.add_argument("--analysis", type=Path, default=DEFAULT_ANALYSIS)
    build.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    build.add_argument("--run-id", required=True)
    build.add_argument("--b0-root", type=Path)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "build":
        output = build_run(
            source_study=args.source_study,
            analysis_path=args.analysis,
            output_root=args.output_root,
            run_id=args.run_id,
            b0_root=args.b0_root,
        )
        print(json.dumps({"run_dir": str(output), "verification": verify_run(output)}, indent=2))
    else:
        result = verify_run(args.run_dir)
        print(json.dumps(result, indent=2, sort_keys=True))
        if result["status"] != "pass":
            raise SystemExit(2)


if __name__ == "__main__":
    main()
