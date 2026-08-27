#!/usr/bin/env python3
"""Verify the de-identified TOSEM evidence package without private inputs.

The verifier uses only Python's standard library.  It checks the published
SHA-256 manifests, validates the privacy-oriented table schemas, and
recomputes the counts and descriptive statistics in ``analysis_summary.json``.
It does not attempt to recreate the de-identification step because the source
telemetry is intentionally not part of the public artifact.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


HERE = Path(__file__).resolve().parent
DEFAULT_ARTIFACT_DIR = HERE / "artifacts" / "tosem"
FORBIDDEN_HEADERS = {
    "account",
    "account_name",
    "email",
    "epic_key",
    "issue_key",
    "jira_url",
    "requestor",
    "run_id",
    "summary",
    "timestamp",
    "unit_id",
    "url",
}
FORBIDDEN_VALUE_PATTERNS = (
    re.compile(r"https?://", re.IGNORECASE),
    re.compile(r"\b[A-Z][A-Z0-9]{2,}-\d+\b"),
    re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b"),
)


class VerificationError(RuntimeError):
    """Raised when a released artifact does not match its declared evidence."""


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mean(values: Iterable[float]) -> float:
    materialized = list(values)
    return statistics.fmean(materialized) if materialized else 0.0


def _sample_sd(values: Iterable[float]) -> float:
    materialized = list(values)
    return statistics.stdev(materialized) if len(materialized) > 1 else 0.0


def _quantile(values: Iterable[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _bootstrap_mean_ci(values: list[float], *, seed: int, replicates: int) -> tuple[float, float]:
    if not values:
        raise VerificationError("Cannot bootstrap an empty sample")
    rng = random.Random(seed)
    sample_size = len(values)
    means = [
        _mean(values[rng.randrange(sample_size)] for _ in range(sample_size))
        for _ in range(replicates)
    ]
    return _quantile(means, 0.025), _quantile(means, 0.975)


def _wilson_interval(successes: int, total: int) -> tuple[float, float]:
    if total <= 0:
        raise VerificationError("A Wilson interval needs a positive denominator")
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1.0 + z * z / total
    center = (proportion + z * z / (2.0 * total)) / denominator
    spread = (
        z
        * math.sqrt((proportion * (1.0 - proportion) + z * z / (4.0 * total)) / total)
        / denominator
    )
    return max(0.0, center - spread), min(1.0, center + spread)


def _two_sided_sign_p(positive: int, negative: int) -> float:
    non_ties = positive + negative
    if non_ties == 0:
        return 1.0
    tail = min(positive, negative)
    probability = sum(math.comb(non_ties, k) for k in range(tail + 1)) / (2**non_ties)
    return min(1.0, 2.0 * probability)


def _is_true(value: str) -> bool:
    return value.strip().lower() == "true"


def _assert_equal(label: str, actual: Any, expected: Any) -> None:
    if actual != expected:
        raise VerificationError(f"{label}: calculated {actual!r}, reported {expected!r}")


def _assert_close(label: str, actual: float, expected: float, digits: int = 6) -> None:
    tolerance = 0.5 * (10 ** (-digits)) + 1e-12
    if not math.isclose(float(actual), float(expected), rel_tol=1e-10, abs_tol=tolerance):
        raise VerificationError(f"{label}: calculated {actual!r}, reported {expected!r}")


def _verify_manifest(artifact_dir: Path, manifest_name: str) -> int:
    manifest_path = artifact_dir / manifest_name
    if not manifest_path.is_file():
        raise VerificationError(f"Missing manifest: {manifest_path}")
    manifest = _read_json(manifest_path)
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise VerificationError(f"{manifest_name} does not declare any files")
    for relative_name, expected_digest in sorted(files.items()):
        relative_path = Path(relative_name)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise VerificationError(f"Unsafe path in {manifest_name}: {relative_name}")
        target = artifact_dir / relative_path
        if not target.is_file():
            raise VerificationError(f"Missing declared file: {target}")
        actual_digest = _sha256(target)
        if actual_digest != expected_digest:
            raise VerificationError(
                f"SHA-256 mismatch for {relative_name}: {actual_digest} != {expected_digest}"
            )
    return len(files)


def _verify_public_schema(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        raise VerificationError(f"Released table is empty: {path}")
    forbidden = sorted(FORBIDDEN_HEADERS.intersection(rows[0]))
    if forbidden:
        raise VerificationError(f"Private identifier columns in {path.name}: {', '.join(forbidden)}")
    for line_number, row in enumerate(rows, start=2):
        for value in row.values():
            materialized = str(value or "")
            if any(pattern.search(materialized) for pattern in FORBIDDEN_VALUE_PATTERNS):
                raise VerificationError(
                    f"Private-looking value in {path.name}:{line_number}: {materialized!r}"
                )


def _verify_comparative_units(path: Path) -> dict[str, Any]:
    rows = _read_csv(path)
    _verify_public_schema(path, rows)
    required = {
        "method",
        "sample_id",
        "coverage_score",
        "acceptance_completeness",
        "dependency_capture_rate",
        "rework_rate",
        "time_to_ready_minutes",
        "reviewer_edits_per_story",
        "governance_block_rate",
    }
    missing = required.difference(rows[0])
    if missing:
        raise VerificationError(f"Missing comparative columns: {', '.join(sorted(missing))}")
    expected_methods = {
        "B0_manual",
        "B1_template_rules",
        "B2_generic_llm",
        "B3_jira_enhancer",
    }
    by_method = Counter(row["method"] for row in rows)
    _assert_equal("comparative method set", set(by_method), expected_methods)
    _assert_equal("comparative rows", len(rows), 20_000)
    for method in sorted(expected_methods):
        _assert_equal(f"comparative rows for {method}", by_method[method], 5_000)
    sample_methods: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        sample_methods[row["sample_id"]].add(row["method"])
    _assert_equal("comparative unique samples", len(sample_methods), 5_000)
    if any(methods != expected_methods for methods in sample_methods.values()):
        raise VerificationError("Each anonymized sample must contain exactly one row for B0-B3")
    return {"rows": len(rows), "samples": len(sample_methods), "methods": dict(sorted(by_method.items()))}


def _verify_summary(artifact_dir: Path) -> dict[str, Any]:
    outcomes_path = artifact_dir / "anonymized_live_story_outcomes.csv"
    audit_path = artifact_dir / "anonymized_latest_batch_audit.csv"
    outcomes = _read_csv(outcomes_path)
    audit = _read_csv(audit_path)
    summary = _read_json(artifact_dir / "analysis_summary.json")
    _verify_public_schema(outcomes_path, outcomes)
    _verify_public_schema(audit_path, audit)

    before = [float(row["confidence_before"]) for row in outcomes]
    after = [float(row["confidence_after"]) for row in outcomes]
    deltas = [end - start for start, end in zip(before, after)]
    positive = sum(delta > 0 for delta in deltas)
    negative = sum(delta < 0 for delta in deltas)
    waves = sorted({row["wave"] for row in outcomes})
    seed = int(summary["bootstrap_seed"])
    replicates = int(summary["bootstrap_replicates"])
    delta_ci_low, delta_ci_high = _bootstrap_mean_ci(deltas, seed=seed, replicates=replicates)
    positive_low, positive_high = _wilson_interval(positive, len(deltas))

    created = [row for row in audit if row["story_status"] == "created"]
    duplicate_safe = [row for row in audit if row["story_status"] == "existing_link_checked"]
    linked = sum(_is_true(row["linked"]) for row in audit)
    coverage_fields = (
        "has_external_evidence_reference",
        "has_acceptance_criteria",
        "has_test_criteria",
        "has_operational_observability_criteria",
    )
    covered = sum(all(_is_true(row[field]) for field in coverage_fields) for row in created)
    earlier_wave_size = min(Counter(row["wave"] for row in outcomes).values())
    new_issue_attempts = earlier_wave_size + len(created)
    new_low, new_high = _wilson_interval(new_issue_attempts, new_issue_attempts)
    linked_low, linked_high = _wilson_interval(linked, len(audit))
    coverage_low, coverage_high = _wilson_interval(covered, len(created))

    integer_checks = {
        "n_live": len(outcomes),
        "n_waves": len(waves),
        "positive_deltas": positive,
        "negative_deltas": negative,
        "new_issues_created": new_issue_attempts,
        "new_issue_attempts": new_issue_attempts,
        "duplicate_safe_existing": len(duplicate_safe),
        "latest_n": len(audit),
        "latest_linked": linked,
        "payload_audited": len(created),
        "payload_covered": covered,
    }
    for label, actual in integer_checks.items():
        _assert_equal(label, actual, int(summary[label]))

    four_digit_checks = {
        "confidence_before_mean": _mean(before),
        "confidence_before_sd": _sample_sd(before),
        "confidence_after_mean": _mean(after),
        "confidence_after_sd": _sample_sd(after),
        "delta_mean": _mean(deltas),
        "delta_sd": _sample_sd(deltas),
        "delta_median": statistics.median(deltas),
        "delta_iqr_low": _quantile(deltas, 0.25),
        "delta_iqr_high": _quantile(deltas, 0.75),
        "delta_ci_low": delta_ci_low,
        "delta_ci_high": delta_ci_high,
    }
    for label, actual in four_digit_checks.items():
        _assert_close(label, round(actual, 4), float(summary[label]), digits=4)

    six_digit_checks = {
        "positive_delta_rate": positive / len(deltas),
        "positive_delta_ci_low": positive_low,
        "positive_delta_ci_high": positive_high,
        "new_issue_rate": new_issue_attempts / new_issue_attempts,
        "new_issue_ci_low": new_low,
        "new_issue_ci_high": new_high,
        "latest_link_rate": linked / len(audit),
        "latest_link_ci_low": linked_low,
        "latest_link_ci_high": linked_high,
        "payload_coverage_rate": covered / len(created),
        "payload_coverage_ci_low": coverage_low,
        "payload_coverage_ci_high": coverage_high,
    }
    for label, actual in six_digit_checks.items():
        _assert_close(label, round(actual, 6), float(summary[label]), digits=6)
    _assert_close(
        "exact_two_sided_sign_p",
        _two_sided_sign_p(positive, negative),
        float(summary["exact_two_sided_sign_p"]),
        digits=12,
    )

    by_wave: dict[str, list[dict[str, str]]] = defaultdict(list)
    by_prompt: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in outcomes:
        by_wave[row["wave"]].append(row)
        by_prompt[row["prompt_strategy"]].append(row)

    reported_waves = {row["wave"]: row for row in summary["wave_summary"]}
    for wave in waves:
        rows = by_wave[wave]
        reported = reported_waves[wave]
        wave_deltas = [float(row["confidence_after"]) - float(row["confidence_before"]) for row in rows]
        ci_low, ci_high = _bootstrap_mean_ci(wave_deltas, seed=seed, replicates=replicates)
        _assert_equal(f"{wave} n", len(rows), int(reported["n"]))
        for label, actual in {
            "confidence_before_mean": _mean(float(row["confidence_before"]) for row in rows),
            "confidence_after_mean": _mean(float(row["confidence_after"]) for row in rows),
            "confidence_delta_mean": _mean(wave_deltas),
            "confidence_delta_ci95_low": ci_low,
            "confidence_delta_ci95_high": ci_high,
            "reward_mean": _mean(float(row["reward"]) for row in rows),
        }.items():
            _assert_close(f"{wave} {label}", round(actual, 4), float(reported[label]), digits=4)

    reported_prompts = {row["prompt_strategy"]: row for row in summary["prompt_summary"]}
    _assert_equal("prompt strategies", set(by_prompt), set(reported_prompts))
    for prompt, rows in sorted(by_prompt.items()):
        reported = reported_prompts[prompt]
        _assert_equal(f"{prompt} n", len(rows), int(reported["n"]))
        for label, actual in {
            "confidence_delta_mean": _mean(
                float(row["confidence_after"]) - float(row["confidence_before"]) for row in rows
            ),
            "terminal_confidence_mean": _mean(float(row["confidence_after"]) for row in rows),
            "reward_mean": _mean(float(row["reward"]) for row in rows),
        }.items():
            _assert_close(f"{prompt} {label}", round(actual, 4), float(reported[label]), digits=4)

    return {
        "outcomes": len(outcomes),
        "waves": len(waves),
        "latest_audit_rows": len(audit),
        "confidence_delta_mean": round(_mean(deltas), 4),
        "positive_deltas": positive,
        "linked_latest": linked,
        "payloads_covered": covered,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify hashes, privacy boundaries, counts, and statistics in the public TOSEM artifact."
    )
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=DEFAULT_ARTIFACT_DIR,
        help=f"Artifact directory (default: {DEFAULT_ARTIFACT_DIR})",
    )
    parser.add_argument(
        "--skip-release-manifest",
        action="store_true",
        help="Verify the evidence manifest only, even if public_release_manifest.json exists.",
    )
    args = parser.parse_args()
    artifact_dir = args.artifact_dir.resolve()

    result: dict[str, Any] = {
        "artifact_dir": str(artifact_dir),
        "evidence_manifest_files": _verify_manifest(artifact_dir, "manifest.json"),
        "summary": _verify_summary(artifact_dir),
    }
    release_manifest = artifact_dir / "public_release_manifest.json"
    if release_manifest.is_file() and not args.skip_release_manifest:
        result["public_release_manifest_files"] = _verify_manifest(
            artifact_dir, "public_release_manifest.json"
        )
    comparative = artifact_dir / "comparative_5000_units.csv"
    if comparative.is_file():
        result["comparative"] = _verify_comparative_units(comparative)

    result["status"] = "verified"
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
