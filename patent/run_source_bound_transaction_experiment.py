from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jira_enhancer.causal_transaction import (
    DEFAULT_POLICY_VERSION,
    build_causal_envelope,
    validate_causal_envelope,
)
from jira_enhancer.utils import stable_hash


METHODS = (
    "A0_ordinary_approval_log",
    "A1_source_hash_conditional",
    "A2_source_hash_atomic_idempotency",
    "A3_source_bound_causal_transaction",
)
SCENARIOS = (
    "valid_control",
    "source_edit",
    "source_aba",
    "draft_drift",
    "policy_drift",
    "target_drift",
    "response_loss_retry",
    "concurrent_retry",
    "combined_source_and_draft_drift",
)
FAULT_SCENARIOS = set(SCENARIOS) - {"valid_control", "response_loss_retry", "concurrent_retry"}


@dataclass(frozen=True)
class TransactionState:
    source_hash: str
    source_revision: str
    draft_version: int
    draft_payload: dict[str, Any]
    policy_version: str
    policy_snapshot: dict[str, Any]
    targets: tuple[str, ...]


@dataclass(frozen=True)
class Trial:
    trial_id: int
    scenario: str
    approved: TransactionState
    runtime: TransactionState
    attempts: int


def _policy_snapshot(version: str, targets: tuple[str, ...], confidence: int = 9) -> dict[str, Any]:
    return {
        "policy_version": version,
        "approval_required": True,
        "allowed_write_targets": list(targets),
        "blocked_actions": ["status_transition"],
        "policy_flags": ["human_approval_required"],
        "confidence": confidence,
    }


def _make_trial(trial_id: int, scenario: str, rng: random.Random) -> Trial:
    issue_key = f"EXP-{trial_id:06d}"
    source_payload = {
        "issue_key": issue_key,
        "summary": f"Planning record {rng.randrange(1_000_000):06d}",
        "description": f"Approved source context {rng.randrange(1_000_000):06d}",
    }
    approved_source_hash = stable_hash(source_payload)
    approved_revision = str(rng.randrange(1, 10_000_000))
    approved_draft = {
        "summary": f"Approved draft {rng.randrange(1_000_000):06d}",
        "acceptance_criteria": [
            f"Given approved context {trial_id}",
            "When the bounded writeback executes",
            "Then the approved draft is written once",
        ],
    }
    targets = ("description_top_block",)
    approved = TransactionState(
        source_hash=approved_source_hash,
        source_revision=approved_revision,
        draft_version=1,
        draft_payload=approved_draft,
        policy_version=DEFAULT_POLICY_VERSION,
        policy_snapshot=_policy_snapshot(DEFAULT_POLICY_VERSION, targets),
        targets=targets,
    )

    runtime_source_hash = approved.source_hash
    runtime_revision = approved.source_revision
    runtime_draft_version = approved.draft_version
    runtime_draft = dict(approved.draft_payload)
    runtime_policy_version = approved.policy_version
    runtime_targets = approved.targets
    attempts = 1

    if scenario == "source_edit":
        runtime_source_hash = stable_hash({**source_payload, "description": "Concurrent human edit"})
        runtime_revision = str(int(approved_revision) + 1)
    elif scenario == "source_aba":
        runtime_revision = str(int(approved_revision) + 2)
    elif scenario == "draft_drift":
        runtime_draft_version = 2
        runtime_draft = {**approved_draft, "summary": f"{approved_draft['summary']} regenerated"}
    elif scenario == "policy_drift":
        runtime_policy_version = "jira-writeback-policy-v2"
    elif scenario == "target_drift":
        runtime_targets = ("description_top_block", "labels")
    elif scenario in {"response_loss_retry", "concurrent_retry"}:
        attempts = 2
    elif scenario == "combined_source_and_draft_drift":
        runtime_source_hash = stable_hash({**source_payload, "summary": "Concurrent scope change"})
        runtime_revision = str(int(approved_revision) + 1)
        runtime_draft_version = 2
        runtime_draft = {**approved_draft, "summary": f"{approved_draft['summary']} regenerated"}
        attempts = 2

    runtime = TransactionState(
        source_hash=runtime_source_hash,
        source_revision=runtime_revision,
        draft_version=runtime_draft_version,
        draft_payload=runtime_draft,
        policy_version=runtime_policy_version,
        policy_snapshot=_policy_snapshot(runtime_policy_version, runtime_targets),
        targets=runtime_targets,
    )
    return Trial(
        trial_id=trial_id,
        scenario=scenario,
        approved=approved,
        runtime=runtime,
        attempts=attempts,
    )


def _is_stale(trial: Trial) -> bool:
    approved = trial.approved
    runtime = trial.runtime
    return any(
        (
            approved.source_hash != runtime.source_hash,
            approved.source_revision != runtime.source_revision,
            approved.draft_version != runtime.draft_version,
            stable_hash(approved.draft_payload) != stable_hash(runtime.draft_payload),
            approved.policy_version != runtime.policy_version,
            stable_hash(approved.policy_snapshot) != stable_hash(runtime.policy_snapshot),
            approved.targets != runtime.targets,
        )
    )


def _evaluate(method: str, trial: Trial) -> dict[str, Any]:
    approved = trial.approved
    runtime = trial.runtime
    blocked = False
    block_reasons: list[str] = []

    if method in {"A1_source_hash_conditional", "A2_source_hash_atomic_idempotency"}:
        if approved.source_hash != runtime.source_hash:
            blocked = True
            block_reasons.append("source_hash_mismatch")
    elif method == "A3_source_bound_causal_transaction":
        envelope = build_causal_envelope(
            run_id=f"run-{trial.trial_id}",
            issue_key=f"EXP-{trial.trial_id:06d}",
            source_hash=approved.source_hash,
            source_revision_value=approved.source_revision,
            draft_version=approved.draft_version,
            draft_payload=approved.draft_payload,
            policy_version=approved.policy_version,
            policy_snapshot=approved.policy_snapshot,
            allowed_write_targets=list(approved.targets),
            decision="approve",
            reviewer="reviewer@example.org",
            reviewed_at="2026-07-26T00:00:00Z",
        )
        block_reasons = validate_causal_envelope(
            envelope,
            run_id=f"run-{trial.trial_id}",
            issue_key=f"EXP-{trial.trial_id:06d}",
            draft_version=runtime.draft_version,
            draft_payload=runtime.draft_payload,
            policy_version=runtime.policy_version,
            policy_snapshot=runtime.policy_snapshot,
            allowed_write_targets=list(runtime.targets),
            current_source_hash=runtime.source_hash,
            current_source_revision=runtime.source_revision,
        )
        blocked = bool(block_reasons)

    if blocked:
        remote_mutations = 0
    elif method in {"A2_source_hash_atomic_idempotency", "A3_source_bound_causal_transaction"}:
        remote_mutations = 1
    else:
        remote_mutations = trial.attempts

    stale_mutation = int(remote_mutations > 0 and _is_stale(trial))
    duplicate_mutation = int(remote_mutations > 1)
    unsafe_mutation = int(stale_mutation or duplicate_mutation)
    valid_expected_write = trial.scenario in {"valid_control", "response_loss_retry", "concurrent_retry"}
    correct_outcome = int(
        (valid_expected_write and remote_mutations == 1)
        or (not valid_expected_write and remote_mutations == 0)
    )
    return {
        "trial_id": trial.trial_id,
        "scenario": trial.scenario,
        "method": method,
        "blocked": int(blocked),
        "block_reasons": "|".join(block_reasons),
        "remote_mutations": remote_mutations,
        "stale_mutation": stale_mutation,
        "duplicate_mutation": duplicate_mutation,
        "unsafe_mutation": unsafe_mutation,
        "correct_outcome": correct_outcome,
    }


def _rate_interval(events: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if total == 0:
        return 0.0, 0.0
    p = events / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    spread = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return max(0.0, center - spread), min(1.0, center + spread)


def _logsumexp(values: list[float]) -> float:
    maximum = max(values)
    return maximum + math.log(sum(math.exp(value - maximum) for value in values))


def _mcnemar_exact_log10(baseline_fail: list[int], causal_fail: list[int]) -> tuple[int, int, float]:
    baseline_only = sum(1 for baseline, causal in zip(baseline_fail, causal_fail) if baseline and not causal)
    causal_only = sum(1 for baseline, causal in zip(baseline_fail, causal_fail) if causal and not baseline)
    discordant = baseline_only + causal_only
    if discordant == 0:
        return baseline_only, causal_only, 0.0
    k = min(baseline_only, causal_only)
    log_terms = [
        math.lgamma(discordant + 1)
        - math.lgamma(index + 1)
        - math.lgamma(discordant - index + 1)
        - discordant * math.log(2)
        for index in range(k + 1)
    ]
    log_p = min(0.0, math.log(2) + _logsumexp(log_terms))
    return baseline_only, causal_only, log_p / math.log(10)


def _paired_difference_interval(baseline: list[int], causal: list[int]) -> tuple[float, float, float]:
    differences = [base - treatment for base, treatment in zip(baseline, causal)]
    mean = sum(differences) / len(differences)
    if len(differences) < 2:
        return mean, mean, mean
    variance = sum((value - mean) ** 2 for value in differences) / (len(differences) - 1)
    margin = 1.959963984540054 * math.sqrt(variance / len(differences))
    return mean, max(-1.0, mean - margin), min(1.0, mean + margin)


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_svg(path: Path, summaries: list[dict[str, Any]]) -> None:
    width, height = 1280, 760
    left, top, panel_width, panel_height = 130, 145, 450, 400
    colors = {
        METHODS[0]: "#8B5E3C",
        METHODS[1]: "#3C6E8F",
        METHODS[2]: "#527A52",
        METHODS[3]: "#0B6E69",
    }
    labels = {
        METHODS[0]: "A0",
        METHODS[1]: "A1",
        METHODS[2]: "A2",
        METHODS[3]: "A3",
    }
    legend_labels = {
        METHODS[0]: "A0: ordinary approval + log",
        METHODS[1]: "A1: source-hash conditional write",
        METHODS[2]: "A2: source hash + atomic idempotency",
        METHODS[3]: "A3: source-bound causal transaction",
    }
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>',
        '<text x="640" y="52" text-anchor="middle" font-family="Arial" font-size="28" font-weight="700" fill="#17212b">Source-Bound Causal Transaction Ablation</text>',
        '<text x="640" y="84" text-anchor="middle" font-family="Arial" font-size="15" fill="#4b5b68">Controlled fault injection; identical transaction schedules across methods</text>',
    ]
    for panel_index, (metric, title) in enumerate(
        (("stale_mutation_rate", "Stale mutation rate"), ("duplicate_mutation_rate", "Duplicate mutation rate"))
    ):
        panel_left = left + panel_index * 610
        parts.append(
            f'<text x="{panel_left + panel_width / 2}" y="{top - 24}" text-anchor="middle" font-family="Arial" font-size="20" font-weight="700" fill="#17212b">{title}</text>'
        )
        for tick in range(0, 6):
            value = tick / 5
            y = top + panel_height - value * panel_height
            parts.append(f'<line x1="{panel_left}" y1="{y:.1f}" x2="{panel_left + panel_width}" y2="{y:.1f}" stroke="#d8e0e6" stroke-width="1"/>')
            parts.append(
                f'<text x="{panel_left - 12}" y="{y + 5:.1f}" text-anchor="end" font-family="Arial" font-size="13" fill="#4b5b68">{value:.0%}</text>'
            )
        bar_width = 78
        gap = 28
        for index, summary in enumerate(summaries):
            value = float(summary[metric])
            x = panel_left + 18 + index * (bar_width + gap)
            bar_height = value * panel_height
            y = top + panel_height - bar_height
            parts.append(
                f'<rect x="{x}" y="{y:.1f}" width="{bar_width}" height="{bar_height:.1f}" fill="{colors[summary["method"]]}" rx="3"/>'
            )
            parts.append(
                f'<text x="{x + bar_width / 2}" y="{max(top + 18, y - 9):.1f}" text-anchor="middle" font-family="Arial" font-size="14" font-weight="700" fill="#17212b">{value:.1%}</text>'
            )
            parts.append(
                f'<text x="{x + bar_width / 2}" y="{top + panel_height + 28}" text-anchor="middle" font-family="Arial" font-size="14" font-weight="700" fill="#33434f">{labels[summary["method"]]}</text>'
            )
    legend_positions = ((130, 620), (650, 620), (130, 662), (650, 662))
    for method, (x, y) in zip(METHODS, legend_positions):
        parts.append(f'<rect x="{x}" y="{y - 14}" width="16" height="16" fill="{colors[method]}" rx="2"/>')
        parts.append(
            f'<text x="{x + 26}" y="{y}" font-family="Arial" font-size="14" fill="#33434f">{legend_labels[method]}</text>'
        )
    parts.extend(
        [
            '<text x="640" y="718" text-anchor="middle" font-family="Arial" font-size="13" fill="#4b5b68">A3 binds source revision, approved draft, policy snapshot, reviewer decision, target set, and idempotent operation.</text>',
            "</svg>",
        ]
    )
    path.write_text("\n".join(parts), encoding="utf-8")


def _write_report(
    path: Path,
    *,
    seed: int,
    trials_per_scenario: int,
    summaries: list[dict[str, Any]],
    pairwise: list[dict[str, Any]],
    scenario_summaries: list[dict[str, Any]],
) -> None:
    summary_by_method = {row["method"]: row for row in summaries}
    closest = summary_by_method["A2_source_hash_atomic_idempotency"]
    causal = summary_by_method["A3_source_bound_causal_transaction"]
    scenario_lookup = {
        (row["method"], row["scenario"]): row
        for row in scenario_summaries
    }
    a2_stale_scenarios = [
        scenario
        for scenario in SCENARIOS
        if float(scenario_lookup[(METHODS[2], scenario)]["stale_mutation_rate"]) > 0
    ]
    lines = [
        "# Source-Bound Causal Transaction: Controlled Ablation Evidence",
        "",
        "## Scope",
        "",
        "This report isolates the source-bound causal transaction from the complete Jira Enhancer B3 system. "
        "It is controlled fault-injection evidence, not a claim about external-customer production outcomes.",
        "",
        f"- Random seed: `{seed}`",
        f"- Trials per scenario: `{trials_per_scenario}`",
        f"- Logical transactions per method: `{trials_per_scenario * len(SCENARIOS)}`",
        f"- Total method-level observations: `{trials_per_scenario * len(SCENARIOS) * len(METHODS)}`",
        "- Paired design: every method receives the same transaction payload and fault schedule.",
        "",
        "## Compared Arrangements",
        "",
        "1. **A0 Ordinary approval + log:** checks approval/workflow eligibility and records the result.",
        "2. **A1 Source-hash conditional write:** A0 plus a content-hash precondition.",
        "3. **A2 Source hash + atomic idempotency:** A1 plus an atomic operation key, representing the strongest control baseline.",
        "4. **A3 Source-bound causal transaction:** binds source hash and revision, approved draft version/hash, policy snapshot, target set, reviewer decision, and idempotent operation.",
        "",
        "## Aggregate Results",
        "",
        "| Method | Stale mutation | Duplicate mutation | Any unsafe mutation | Eligible write success | Correct outcome |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in summaries:
        lines.append(
            f"| {row['method']} | {float(row['stale_mutation_rate']):.2%} | "
            f"{float(row['duplicate_mutation_rate']):.2%} | {float(row['unsafe_mutation_rate']):.2%} | "
            f"{float(row['eligible_write_success_rate']):.2%} | "
            f"{float(row['correct_outcome_rate']):.2%} |"
        )
    lines.extend(
        [
            "",
            "## Residual Technical Effect",
            "",
            f"Against A2, the strongest baseline, A3 reduced stale mutations from "
            f"{float(closest['stale_mutation_rate']):.2%} to {float(causal['stale_mutation_rate']):.2%} "
            f"without reducing valid-control write success. The A2 failures occurred in: "
            f"{', '.join(a2_stale_scenarios)}.",
            "",
            "The residual gain comes from cross-artifact causal binding rather than source hashing alone. "
            "A content hash cannot detect an ABA revision, and source-bound conditional writes do not detect a regenerated "
            "draft, changed policy, or changed target when the Jira source text itself is unchanged.",
            "",
            "## Paired Inference",
            "",
            "| Baseline | Metric | Risk reduction | 95% paired CI | McNemar discordance | log10(p) |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in pairwise:
        lines.append(
            f"| {row['baseline']} | {row['metric']} | {float(row['risk_difference']):.4f} | "
            f"[{float(row['ci95_low']):.4f}, {float(row['ci95_high']):.4f}] | "
            f"{row['baseline_only_failures']} / {row['causal_only_failures']} | "
            f"{float(row['mcnemar_log10_p']):.2f} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation Boundary",
            "",
            "The result demonstrates the mechanism under declared, repeatable fault schedules and supports a technical-effect argument. "
            "It does not establish a universal production effect or, by itself, a legal conclusion of non-obviousness. "
            "The strongest next evidence would repeat these fault classes through an instrumented Jira staging connector and record "
            "remote request identifiers, revisions, and mutation counts.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_latex_table(path: Path, summaries: list[dict[str, Any]]) -> None:
    labels = {
        METHODS[0]: "A0 ordinary approval/log",
        METHODS[1]: "A1 source-hash conditional",
        METHODS[2]: "A2 source hash + idempotency",
        METHODS[3]: "A3 source-bound causal",
    }
    lines = [
        r"\begin{center}",
        r"\captionof{table}{Controlled source-bound transaction ablation. Rates are scenario-balanced fault-injection outcomes, not production incidence estimates.}",
        r"\label{tab:source-bound-causal-ablation}",
        r"\par\vspace{0.6\baselineskip}",
        r"\small",
        r"\setlength{\tabcolsep}{4pt}",
        r"\begin{tabularx}{\linewidth}{@{}>{\raggedright\arraybackslash}Xcccc@{}}",
        r"\toprule",
        r"Arrangement & Stale & Duplicate & Unsafe & Correct \\",
        r"\midrule",
    ]
    for row in summaries:
        lines.append(
            f"{labels[row['method']]} & "
            f"{100 * float(row['stale_mutation_rate']):.1f}\\% & "
            f"{100 * float(row['duplicate_mutation_rate']):.1f}\\% & "
            f"{100 * float(row['unsafe_mutation_rate']):.1f}\\% & "
            f"{100 * float(row['correct_outcome_rate']):.1f}\\% \\\\"
        )
    lines.extend(
        [
            r"\bottomrule",
            r"\end{tabularx}",
            r"\end{center}",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run_experiment(output_dir: Path, trials_per_scenario: int, seed: int) -> dict[str, Any]:
    rng = random.Random(seed)
    schedule = [
        scenario
        for scenario in SCENARIOS
        for _ in range(trials_per_scenario)
    ]
    rng.shuffle(schedule)
    trials = [_make_trial(index + 1, scenario, rng) for index, scenario in enumerate(schedule)]
    observations = [
        _evaluate(method, trial)
        for trial in trials
        for method in METHODS
    ]

    by_method: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_method_scenario: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in observations:
        by_method[row["method"]].append(row)
        by_method_scenario[(row["method"], row["scenario"])].append(row)

    summaries: list[dict[str, Any]] = []
    for method in METHODS:
        rows = by_method[method]
        total = len(rows)
        eligible_rows = [
            row
            for row in rows
            if row["scenario"] in {"valid_control", "response_loss_retry", "concurrent_retry"}
        ]
        fault_rows = [row for row in rows if row["scenario"] in FAULT_SCENARIOS]
        stale_count = sum(int(row["stale_mutation"]) for row in rows)
        duplicate_count = sum(int(row["duplicate_mutation"]) for row in rows)
        unsafe_count = sum(int(row["unsafe_mutation"]) for row in rows)
        correct_count = sum(int(row["correct_outcome"]) for row in rows)
        stale_low, stale_high = _rate_interval(stale_count, total)
        duplicate_low, duplicate_high = _rate_interval(duplicate_count, total)
        summaries.append(
            {
                "method": method,
                "n": total,
                "stale_mutations": stale_count,
                "stale_mutation_rate": stale_count / total,
                "stale_ci95_low": stale_low,
                "stale_ci95_high": stale_high,
                "duplicate_mutations": duplicate_count,
                "duplicate_mutation_rate": duplicate_count / total,
                "duplicate_ci95_low": duplicate_low,
                "duplicate_ci95_high": duplicate_high,
                "unsafe_mutations": unsafe_count,
                "unsafe_mutation_rate": unsafe_count / total,
                "correct_outcomes": correct_count,
                "correct_outcome_rate": correct_count / total,
                "eligible_write_success_rate": sum(
                    int(row["remote_mutations"] == 1) for row in eligible_rows
                )
                / len(eligible_rows),
                "fault_safe_block_rate": sum(
                    int(row["remote_mutations"] == 0) for row in fault_rows
                )
                / len(fault_rows),
                "mean_remote_mutations": sum(int(row["remote_mutations"]) for row in rows) / total,
            }
        )

    scenario_summaries: list[dict[str, Any]] = []
    for method in METHODS:
        for scenario in SCENARIOS:
            rows = by_method_scenario[(method, scenario)]
            total = len(rows)
            scenario_summaries.append(
                {
                    "method": method,
                    "scenario": scenario,
                    "n": total,
                    "stale_mutation_rate": sum(int(row["stale_mutation"]) for row in rows) / total,
                    "duplicate_mutation_rate": sum(int(row["duplicate_mutation"]) for row in rows) / total,
                    "unsafe_mutation_rate": sum(int(row["unsafe_mutation"]) for row in rows) / total,
                    "correct_outcome_rate": sum(int(row["correct_outcome"]) for row in rows) / total,
                    "mean_remote_mutations": sum(int(row["remote_mutations"]) for row in rows) / total,
                }
            )

    causal_rows = by_method[METHODS[-1]]
    pairwise: list[dict[str, Any]] = []
    for baseline in METHODS[:-1]:
        baseline_rows = by_method[baseline]
        for metric in ("stale_mutation", "duplicate_mutation", "unsafe_mutation"):
            baseline_values = [int(row[metric]) for row in baseline_rows]
            causal_values = [int(row[metric]) for row in causal_rows]
            baseline_only, causal_only, log10_p = _mcnemar_exact_log10(baseline_values, causal_values)
            difference, ci_low, ci_high = _paired_difference_interval(baseline_values, causal_values)
            pairwise.append(
                {
                    "baseline": baseline,
                    "treatment": METHODS[-1],
                    "metric": metric,
                    "n_pairs": len(baseline_values),
                    "baseline_only_failures": baseline_only,
                    "causal_only_failures": causal_only,
                    "risk_difference": difference,
                    "ci95_low": ci_low,
                    "ci95_high": ci_high,
                    "mcnemar_log10_p": log10_p,
                }
            )

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(
        output_dir / "source_bound_transaction_trials.csv",
        observations,
        list(observations[0].keys()),
    )
    _write_csv(
        output_dir / "source_bound_transaction_summary.csv",
        summaries,
        list(summaries[0].keys()),
    )
    _write_csv(
        output_dir / "source_bound_transaction_scenario_summary.csv",
        scenario_summaries,
        list(scenario_summaries[0].keys()),
    )
    _write_csv(
        output_dir / "source_bound_transaction_pairwise.csv",
        pairwise,
        list(pairwise[0].keys()),
    )
    _write_svg(output_dir / "source_bound_transaction_ablation.svg", summaries)
    _write_latex_table(output_dir / "source_bound_transaction_summary_table.tex", summaries)
    _write_report(
        output_dir / "source_bound_transaction_evidence.md",
        seed=seed,
        trials_per_scenario=trials_per_scenario,
        summaries=summaries,
        pairwise=pairwise,
        scenario_summaries=scenario_summaries,
    )
    metadata = {
        "experiment": "source-bound-causal-transaction-ablation-v1",
        "seed": seed,
        "trials_per_scenario": trials_per_scenario,
        "scenarios": list(SCENARIOS),
        "methods": list(METHODS),
        "logical_transactions_per_method": len(trials),
        "total_observations": len(observations),
        "schedule_hash": stable_hash(schedule),
        "summary_hash": stable_hash(summaries),
        "python_version": sys.version.split()[0],
        "interpretation": "Controlled fault-injection evidence; not external-customer production evidence.",
    }
    repository_root = Path(__file__).resolve().parents[1]
    implementation_files = (
        Path(__file__).resolve(),
        repository_root / "src/jira_enhancer/causal_transaction.py",
        repository_root / "src/jira_enhancer/services.py",
        repository_root / "src/jira_enhancer/store.py",
        repository_root / "tests/test_causal_transaction.py",
    )
    metadata["implementation_sha256"] = {
        str(path.relative_to(repository_root)): _file_sha256(path)
        for path in implementation_files
    }
    (output_dir / "source_bound_transaction_metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    artifact_names = (
        "source_bound_transaction_trials.csv",
        "source_bound_transaction_summary.csv",
        "source_bound_transaction_scenario_summary.csv",
        "source_bound_transaction_pairwise.csv",
        "source_bound_transaction_evidence.md",
        "source_bound_transaction_ablation.svg",
        "source_bound_transaction_summary_table.tex",
        "source_bound_transaction_metadata.json",
    )
    artifact_hashes = {
        name: _file_sha256(output_dir / name)
        for name in artifact_names
    }
    (output_dir / "source_bound_transaction_checksums.json").write_text(
        json.dumps(artifact_hashes, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return {
        "metadata": metadata,
        "summary": summaries,
        "pairwise": pairwise,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the source-bound causal transaction ablation.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "generated",
    )
    parser.add_argument("--trials-per-scenario", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=20260726)
    args = parser.parse_args()
    result = run_experiment(args.output_dir, args.trials_per_scenario, args.seed)
    print(json.dumps(result["metadata"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
