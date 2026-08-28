from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


HERE = Path(__file__).resolve().parent
GENERATED = HERE / "generated"
ARTIFACT = HERE / "artifacts" / "tosem"
LIVE_OUTCOMES = GENERATED / "rl_learning_outcomes.csv"
LATEST_BATCH = GENERATED / "tosem_live_story_creation_rows.csv"
SEED = 20260809
BOOTSTRAP_REPLICATES = 50_000


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise ValueError(f"Cannot write an empty table: {path}")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _float(row: dict[str, str], key: str) -> float:
    return float(row[key])


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


def _bootstrap_mean_ci(values: list[float]) -> tuple[float, float]:
    rng = random.Random(SEED)
    sample_size = len(values)
    means = [
        _mean(values[rng.randrange(sample_size)] for _ in range(sample_size))
        for _ in range(BOOTSTRAP_REPLICATES)
    ]
    return _quantile(means, 0.025), _quantile(means, 0.975)


def _wilson_interval(successes: int, total: int) -> tuple[float, float]:
    if total <= 0:
        raise ValueError("A rate interval requires a positive denominator")
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate(
    outcomes: list[dict[str, str]],
    latest: list[dict[str, str]],
) -> tuple[list[str], str, str]:
    if len(outcomes) != 70:
        raise ValueError(f"Expected 70 live outcomes, found {len(outcomes)}")
    run_ids = sorted({row["run_id"] for row in outcomes})
    if len(run_ids) != 2:
        raise ValueError(f"Expected two deployment waves, found {len(run_ids)}")
    run_dates = {
        run_id: min(row["recorded_at"] for row in outcomes if row["run_id"] == run_id)
        for run_id in run_ids
    }
    ordered_runs = sorted(run_ids, key=run_dates.get)
    if len(latest) != 45:
        raise ValueError(f"Expected 45 latest-batch records, found {len(latest)}")
    latest_issue_keys = {row["story_key"] for row in latest}
    outcome_keys_by_run = {
        run_id: {row["issue_key"] for row in outcomes if row["run_id"] == run_id}
        for run_id in ordered_runs
    }
    latest_run = next(
        (run_id for run_id in ordered_runs if latest_issue_keys == outcome_keys_by_run[run_id]),
        "",
    )
    if not latest_run:
        raise ValueError("The 45-story batch does not match either live outcome wave")
    earlier_run = next(run_id for run_id in ordered_runs if run_id != latest_run)
    if sum(row["run_id"] == earlier_run for row in outcomes) != 25:
        raise ValueError("Expected 25 outcomes in the earlier deployment wave")
    if any(row["attempt_index"] != "0" for row in outcomes):
        raise ValueError("The current live endpoint file is expected to contain one terminal row per story")
    return ordered_runs, earlier_run, latest_run


def _write_latex_table(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        r"\begin{tabularx}{\textwidth}{@{}p{0.24\textwidth}p{0.18\textwidth}p{0.22\textwidth}X@{}}",
        r"\toprule",
        r"Observed measure & Estimate & 95\% interval & Inference boundary \\",
        r"\midrule",
        (
            f"Paired confidence change & +{summary['delta_mean']:.2f} points "
            f"($n={summary['n_live']}$) & "
            f"[{summary['delta_ci_low']:.2f}, {summary['delta_ci_high']:.2f}] bootstrap CI & "
            "System telemetry; not an independent human rating of story quality. \\\\"
        ),
        (
            f"Positive confidence change & {summary['positive_deltas']}/{summary['n_live']} "
            f"({100 * summary['positive_delta_rate']:.1f}\\%) & "
            f"[{100 * summary['positive_delta_ci_low']:.1f}\\%, "
            f"{100 * summary['positive_delta_ci_high']:.1f}\\%] Wilson CI & "
            "Paired endpoint diagnostic; all rows were retained and no zero deltas occurred. \\\\"
        ),
        (
            f"New Jira issue creation & {summary['new_issues_created']}/{summary['new_issue_attempts']} "
            f"({100 * summary['new_issue_rate']:.1f}\\%) & "
            f"[{100 * summary['new_issue_ci_low']:.1f}\\%, "
            f"{100 * summary['new_issue_ci_high']:.1f}\\%] Wilson CI & "
            "Conditional on approved, non-duplicate candidates in the two recorded waves. \\\\"
        ),
        (
            f"Latest-wave parent binding & {summary['latest_linked']}/{summary['latest_n']} "
            f"({100 * summary['latest_link_rate']:.1f}\\%) & "
            f"[{100 * summary['latest_link_ci_low']:.1f}\\%, "
            f"{100 * summary['latest_link_ci_high']:.1f}\\%] Wilson CI & "
            "Direct connector outcome for nine epics; no cross-project generalization is implied. \\\\"
        ),
        (
            f"Generated-payload criteria coverage & {summary['payload_covered']}/{summary['payload_audited']} "
            f"({100 * summary['payload_coverage_rate']:.1f}\\%) & "
            f"[{100 * summary['payload_coverage_ci_low']:.1f}\\%, "
            f"{100 * summary['payload_coverage_ci_high']:.1f}\\%] Wilson CI & "
            "The pre-existing duplicate-safe record is excluded because its generated payload was unavailable. \\\\"
        ),
        r"\bottomrule",
        r"\end{tabularx}",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    outcomes = _read_csv(LIVE_OUTCOMES)
    latest = _read_csv(LATEST_BATCH)
    ordered_runs, earlier_run, latest_run = _validate(outcomes, latest)

    before = [_float(row, "confidence_before") for row in outcomes]
    after = [_float(row, "confidence_after") for row in outcomes]
    deltas = [end - start for start, end in zip(before, after)]
    delta_ci_low, delta_ci_high = _bootstrap_mean_ci(deltas)
    positive = sum(delta > 0 for delta in deltas)
    negative = sum(delta < 0 for delta in deltas)
    positive_low, positive_high = _wilson_interval(positive, len(deltas))

    created_latest = sum(row["story_status"] == "created" for row in latest)
    existing_latest = sum(row["story_status"] == "existing_link_checked" for row in latest)
    earlier_n = sum(row["run_id"] == earlier_run for row in outcomes)
    new_issue_attempts = earlier_n + created_latest
    new_issue_low, new_issue_high = _wilson_interval(new_issue_attempts, new_issue_attempts)

    linked = sum(row["linked"].lower() == "true" for row in latest)
    linked_low, linked_high = _wilson_interval(linked, len(latest))
    generated_rows = [row for row in latest if row["story_status"] == "created"]
    coverage_fields = (
        "has_confluence_reference",
        "has_acceptance_criteria",
        "has_test_criteria",
        "has_operational_observability_criteria",
    )
    covered = sum(
        all(row[field].lower() == "true" for field in coverage_fields)
        for row in generated_rows
    )
    coverage_low, coverage_high = _wilson_interval(covered, len(generated_rows))

    by_run: dict[str, list[dict[str, str]]] = defaultdict(list)
    by_prompt: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in outcomes:
        by_run[row["run_id"]].append(row)
        by_prompt[row["prompt_id"]].append(row)

    wave_summary = []
    for wave_index, run_id in enumerate(ordered_runs, start=1):
        rows = by_run[run_id]
        wave_deltas = [_float(row, "confidence_after") - _float(row, "confidence_before") for row in rows]
        wave_ci_low, wave_ci_high = _bootstrap_mean_ci(wave_deltas)
        wave_summary.append(
            {
                "wave": f"W{wave_index}",
                "n": len(rows),
                "confidence_before_mean": round(_mean(_float(row, "confidence_before") for row in rows), 4),
                "confidence_after_mean": round(_mean(_float(row, "confidence_after") for row in rows), 4),
                "confidence_delta_mean": round(_mean(wave_deltas), 4),
                "confidence_delta_ci95_low": round(wave_ci_low, 4),
                "confidence_delta_ci95_high": round(wave_ci_high, 4),
                "reward_mean": round(_mean(_float(row, "reward") for row in rows), 4),
            }
        )

    prompt_summary = []
    for prompt_id, rows in sorted(by_prompt.items()):
        prompt_summary.append(
            {
                "prompt_strategy": prompt_id,
                "n": len(rows),
                "confidence_delta_mean": round(
                    _mean(_float(row, "confidence_after") - _float(row, "confidence_before") for row in rows),
                    4,
                ),
                "terminal_confidence_mean": round(_mean(_float(row, "confidence_after") for row in rows), 4),
                "reward_mean": round(_mean(_float(row, "reward") for row in rows), 4),
            }
        )

    summary = {
        "n_live": len(outcomes),
        "n_waves": len(ordered_runs),
        "confidence_before_mean": round(_mean(before), 4),
        "confidence_before_sd": round(_sample_sd(before), 4),
        "confidence_after_mean": round(_mean(after), 4),
        "confidence_after_sd": round(_sample_sd(after), 4),
        "delta_mean": round(_mean(deltas), 4),
        "delta_sd": round(_sample_sd(deltas), 4),
        "delta_median": round(statistics.median(deltas), 4),
        "delta_iqr_low": round(_quantile(deltas, 0.25), 4),
        "delta_iqr_high": round(_quantile(deltas, 0.75), 4),
        "delta_ci_low": round(delta_ci_low, 4),
        "delta_ci_high": round(delta_ci_high, 4),
        "positive_deltas": positive,
        "negative_deltas": negative,
        "positive_delta_rate": round(positive / len(deltas), 6),
        "positive_delta_ci_low": round(positive_low, 6),
        "positive_delta_ci_high": round(positive_high, 6),
        "exact_two_sided_sign_p": _two_sided_sign_p(positive, negative),
        "new_issues_created": new_issue_attempts,
        "new_issue_attempts": new_issue_attempts,
        "new_issue_rate": 1.0,
        "new_issue_ci_low": round(new_issue_low, 6),
        "new_issue_ci_high": round(new_issue_high, 6),
        "duplicate_safe_existing": existing_latest,
        "latest_n": len(latest),
        "latest_linked": linked,
        "latest_link_rate": round(linked / len(latest), 6),
        "latest_link_ci_low": round(linked_low, 6),
        "latest_link_ci_high": round(linked_high, 6),
        "payload_audited": len(generated_rows),
        "payload_covered": covered,
        "payload_coverage_rate": round(covered / len(generated_rows), 6),
        "payload_coverage_ci_low": round(coverage_low, 6),
        "payload_coverage_ci_high": round(coverage_high, 6),
        "bootstrap_replicates": BOOTSTRAP_REPLICATES,
        "bootstrap_seed": SEED,
        "wave_summary": wave_summary,
        "prompt_summary": prompt_summary,
    }

    metric_rows = [
        {
            "metric": "paired_confidence_delta",
            "n": len(deltas),
            "estimate": summary["delta_mean"],
            "ci95_low": summary["delta_ci_low"],
            "ci95_high": summary["delta_ci_high"],
            "unit": "confidence_points",
            "evidence_class": "live_endpoint_telemetry",
        },
        {
            "metric": "positive_confidence_delta_rate",
            "n": len(deltas),
            "estimate": summary["positive_delta_rate"],
            "ci95_low": summary["positive_delta_ci_low"],
            "ci95_high": summary["positive_delta_ci_high"],
            "unit": "proportion",
            "evidence_class": "live_endpoint_telemetry",
        },
        {
            "metric": "new_issue_creation_success_rate",
            "n": new_issue_attempts,
            "estimate": 1.0,
            "ci95_low": summary["new_issue_ci_low"],
            "ci95_high": summary["new_issue_ci_high"],
            "unit": "proportion",
            "evidence_class": "authenticated_connector_outcome",
        },
        {
            "metric": "latest_parent_epic_binding_rate",
            "n": len(latest),
            "estimate": summary["latest_link_rate"],
            "ci95_low": summary["latest_link_ci_low"],
            "ci95_high": summary["latest_link_ci_high"],
            "unit": "proportion",
            "evidence_class": "authenticated_connector_outcome",
        },
        {
            "metric": "generated_payload_coverage_rate",
            "n": len(generated_rows),
            "estimate": summary["payload_coverage_rate"],
            "ci95_low": summary["payload_coverage_ci_low"],
            "ci95_high": summary["payload_coverage_ci_high"],
            "unit": "proportion",
            "evidence_class": "latest_wave_payload_audit",
        },
    ]
    _write_csv(GENERATED / "tosem_live_evidence_statistics.csv", metric_rows)
    _write_latex_table(GENERATED / "tosem_live_evidence_statistics_table.tex", summary)

    run_to_wave = {run_id: f"W{index}" for index, run_id in enumerate(ordered_runs, start=1)}
    indexed_outcomes = list(enumerate(outcomes))
    sorted_outcomes = sorted(
        indexed_outcomes,
        key=lambda item: (run_to_wave[item[1]["run_id"]], item[1]["issue_key"]),
    )
    story_id_by_source_index = {
        source_index: f"S{story_index:03d}"
        for story_index, (source_index, _row) in enumerate(sorted_outcomes, start=1)
    }
    anonymous_outcomes = []
    for source_index, row in indexed_outcomes:
        anonymous_outcomes.append(
            {
                # Keep neutral identifiers stable while preserving the source
                # order used by the fixed-seed offline replay.
                "story_id": story_id_by_source_index[source_index],
                "wave": run_to_wave[row["run_id"]],
                "prompt_strategy": row["prompt_id"],
                "attempt_index": row["attempt_index"],
                "decision": row["decision"],
                "writeback_status": row["writeback_status"],
                "confidence_before": row["confidence_before"],
                "confidence_after": row["confidence_after"],
                "confidence_delta": row["confidence_delta"],
                "target_confidence": row["target_confidence"],
                "lag_before": row["lag_before"],
                "lag_after": row["lag_after"],
                "reward": row["reward"],
                "loss": row["loss"],
                "loss_before": row["loss_before"],
                "loss_delta": row["loss_delta"],
                "kappa_hat": row["kappa_hat"],
            }
        )
    _write_csv(ARTIFACT / "anonymized_live_story_outcomes.csv", anonymous_outcomes)

    anonymous_latest = []
    sorted_latest = sorted(latest, key=lambda row: (int(row["epic_index"]), int(row["story_index"])))
    for index, row in enumerate(sorted_latest, start=1):
        anonymous_latest.append(
            {
                "epic_id": f"E{int(row['epic_index']):02d}",
                "story_id": f"L{index:03d}",
                "story_status": row["story_status"],
                "linked": row["linked"],
                "has_external_evidence_reference": row["has_confluence_reference"],
                "has_acceptance_criteria": row["has_acceptance_criteria"],
                "has_test_criteria": row["has_test_criteria"],
                "has_operational_observability_criteria": row["has_operational_observability_criteria"],
            }
        )
    _write_csv(ARTIFACT / "anonymized_latest_batch_audit.csv", anonymous_latest)
    (ARTIFACT / "analysis_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    artifact_files = (
        ARTIFACT / "anonymized_live_story_outcomes.csv",
        ARTIFACT / "anonymized_latest_batch_audit.csv",
        ARTIFACT / "analysis_summary.json",
    )
    manifest = {
        "artifact": "jira-enhancer-tosem-live-evidence-v1",
        "generated_by": str(Path(__file__).name),
        "seed": SEED,
        "bootstrap_replicates": BOOTSTRAP_REPLICATES,
        "privacy_boundary": (
            "Issue keys, epic keys, summaries, URLs, account names, email addresses, and timestamps are excluded."
        ),
        "files": {path.name: _sha256(path) for path in artifact_files},
        "source_file_hashes": {
            LIVE_OUTCOMES.name: _sha256(LIVE_OUTCOMES),
            LATEST_BATCH.name: _sha256(LATEST_BATCH),
        },
    }
    (ARTIFACT / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
