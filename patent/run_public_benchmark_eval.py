from __future__ import annotations

import ast
import csv
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
from datasets import load_dataset
from scipy import stats

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from patent import generate_patent_results as g

GENERATED_ROOT = REPO_ROOT / "patent" / "generated"


def _parse_list_like(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    if not isinstance(value, str):
        return []
    raw = value.strip()
    if not raw:
        return []
    try:
        parsed = ast.literal_eval(raw)
        if isinstance(parsed, list):
            return [str(v) for v in parsed]
    except Exception:
        return []
    return []


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _build_public_units() -> list[dict[str, Any]]:
    ds = load_dataset("princeton-nlp/SWE-bench_Lite", split="test")
    units: list[dict[str, Any]] = []
    for idx, row in enumerate(ds):
        problem = str(row.get("problem_statement", "") or "")
        hints = str(row.get("hints_text", "") or "")
        tokens = g._tokenize_summary(problem)
        unique_count = len(set(tokens))
        ambiguity_hits = sum(1 for t in tokens if t in {"maybe", "could", "should", "sometimes", "might"})
        fail_to_pass = _parse_list_like(row.get("FAIL_TO_PASS"))
        pass_to_pass = _parse_list_like(row.get("PASS_TO_PASS"))
        fail_count = float(len(fail_to_pass))
        pass_count = float(len(pass_to_pass))
        has_hints = 1.0 if hints.strip() else 0.0
        long_issue = 1.0 if len(problem) > 1200 else 0.0
        complexity = g._clamp(
            0.30 * min(len(tokens), 450) / 450.0
            + 0.24 * min(unique_count, 260) / 260.0
            + 0.20 * min(fail_count, 24.0) / 24.0
            + 0.16 * has_hints
            + 0.10 * long_issue,
            0.0,
            1.0,
        )
        noise = g._stable_noise(f"swe:{row['instance_id']}", -0.5, 0.5)
        observed_confidence = g._clamp(7.4 - 1.7 * complexity + 0.25 * has_hints + 0.35 * noise, 1.0, 10.0)
        observed_readiness = g._clamp(7.1 - 1.8 * complexity + 0.20 * has_hints + 0.30 * noise, 1.0, 10.0)
        units.append(
            {
                "run_id": "swebench-lite-test",
                "issue_key": str(row.get("instance_id", f"swe-{idx:04d}")),
                "unit_id": f"swebench-lite::{row.get('instance_id', idx)}",
                "jira_summary": problem,
                "tokens": tokens,
                "token_count": len(tokens),
                "unique_token_count": unique_count,
                "ambiguity_hits": ambiguity_hits,
                "complexity_score": complexity,
                "warning_count": g._clamp(fail_count / 6.0 + long_issue, 0.0, 8.0),
                "policy_flag_count": g._clamp(pass_count / 10.0 + has_hints, 0.0, 12.0),
                "approval_required": 1.0,
                "observed_confidence": observed_confidence,
                "observed_readiness": observed_readiness,
                "repo": str(row.get("repo", "")),
            }
        )
    return units


def _composite_array(rows: list[dict[str, Any]]) -> np.ndarray:
    return np.array([g._composite_from_row(r) for r in rows], dtype=float)


def _bootstrap_ci_mean_diff(a: np.ndarray, b: np.ndarray, n_boot: int = 5000, seed: int = 42) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    idx_a = rng.integers(0, len(a), size=(n_boot, len(a)))
    idx_b = rng.integers(0, len(b), size=(n_boot, len(b)))
    diffs = a[idx_a].mean(axis=1) - b[idx_b].mean(axis=1)
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    return float(lo), float(hi)


def _cliffs_delta(a: np.ndarray, b: np.ndarray) -> float:
    gt = 0
    lt = 0
    for x in a:
        gt += int(np.sum(x > b))
        lt += int(np.sum(x < b))
    denom = len(a) * len(b)
    if denom == 0:
        return 0.0
    return float((gt - lt) / denom)


def main() -> None:
    units = _build_public_units()
    b0 = g._b0_manual_baseline(units)
    b1, b1_details = g._b1_template_baseline(units)
    b2, b2_details = g._b2_generic_llm_baseline(units)
    b3 = g._b3_jira_enhancer(units)
    comparative_rows = b0 + b1 + b2 + b3

    method_summary = g._method_summary(comparative_rows)
    pairwise = g._pairwise_vs_b3(method_summary)

    by_method: dict[str, list[dict[str, Any]]] = {}
    for row in comparative_rows:
        by_method.setdefault(str(row["method"]), []).append(row)

    b3_comp = _composite_array(by_method["B3_jira_enhancer"])
    b2_comp = _composite_array(by_method["B2_generic_llm"])
    b1_comp = _composite_array(by_method["B1_template_rules"])
    b0_comp = _composite_array(by_method["B0_manual"])

    kw = stats.kruskal(b0_comp, b1_comp, b2_comp, b3_comp)
    anova = stats.f_oneway(b0_comp, b1_comp, b2_comp, b3_comp)

    sig_rows: list[dict[str, Any]] = [
        {
            "test": "Kruskal-Wallis",
            "metric": "composite_index",
            "statistic": float(kw.statistic),
            "p_value": float(kw.pvalue),
            "n_total": int(len(comparative_rows)),
            "dataset": "SWE-bench Lite test split",
        },
        {
            "test": "One-way ANOVA",
            "metric": "composite_index",
            "statistic": float(anova.statistic),
            "p_value": float(anova.pvalue),
            "n_total": int(len(comparative_rows)),
            "dataset": "SWE-bench Lite test split",
        },
    ]

    comparisons = [
        ("B3_vs_B2", b3_comp, b2_comp),
        ("B3_vs_B1", b3_comp, b1_comp),
        ("B3_vs_B0", b3_comp, b0_comp),
    ]
    pairwise_stats: list[dict[str, Any]] = []
    pvals: list[float] = []
    for name, a, b in comparisons:
        u = stats.mannwhitneyu(a, b, alternative="two-sided")
        lo, hi = _bootstrap_ci_mean_diff(a, b, n_boot=3000, seed=42)
        pairwise_stats.append(
            {
                "comparison": name,
                "mean_b3": float(np.mean(a)),
                "mean_baseline": float(np.mean(b)),
                "mean_diff": float(np.mean(a) - np.mean(b)),
                "ci95_mean_diff_low": lo,
                "ci95_mean_diff_high": hi,
                "cliffs_delta": _cliffs_delta(a, b),
                "p_value": float(u.pvalue),
                "n_b3": int(len(a)),
                "n_baseline": int(len(b)),
                "dataset": "SWE-bench Lite test split",
            }
        )
        pvals.append(float(u.pvalue))

    # Holm correction
    m = len(pvals)
    order = np.argsort(pvals)
    holm = [0.0] * m
    for rank, idx in enumerate(order):
        holm[idx] = min((m - rank) * pvals[idx], 1.0)
    for i in range(m - 2, -1, -1):
        holm[order[i]] = max(holm[order[i]], holm[order[i + 1]])
    for i, row in enumerate(pairwise_stats):
        row["p_value_holm"] = holm[i]
        row["significant_0_05"] = "yes" if holm[i] < 0.05 else "no"

    before_after = [
        {
            "dataset": "SWE-bench Lite test split",
            "before_method": "B2_generic_llm",
            "after_method": "B3_jira_enhancer",
            "before_mean_composite": float(np.mean(b2_comp)),
            "after_mean_composite": float(np.mean(b3_comp)),
            "absolute_gain": float(np.mean(b3_comp) - np.mean(b2_comp)),
            "relative_gain_percent": float((np.mean(b3_comp) - np.mean(b2_comp)) / np.mean(b2_comp) * 100.0),
            "before_p90_composite": float(np.percentile(b2_comp, 90)),
            "after_p90_composite": float(np.percentile(b3_comp, 90)),
        }
    ]

    _write_csv(
        GENERATED_ROOT / "public_benchmark_swebench_lite_metrics.csv",
        comparative_rows,
        [
            "method",
            "run_id",
            "issue_key",
            "unit_id",
            "coverage_score",
            "acceptance_completeness",
            "dependency_capture_rate",
            "rework_rate",
            "time_to_ready_minutes",
            "reviewer_edits_per_story",
            "governance_block_rate",
        ],
    )
    _write_csv(
        GENERATED_ROOT / "public_benchmark_swebench_lite_method_summary.csv",
        method_summary,
        list(method_summary[0].keys()),
    )
    _write_csv(
        GENERATED_ROOT / "public_benchmark_swebench_lite_pairwise_vs_b3.csv",
        pairwise,
        list(pairwise[0].keys()),
    )
    _write_csv(
        GENERATED_ROOT / "public_benchmark_swebench_lite_significance_global.csv",
        sig_rows,
        list(sig_rows[0].keys()),
    )
    _write_csv(
        GENERATED_ROOT / "public_benchmark_swebench_lite_significance_pairwise.csv",
        pairwise_stats,
        list(pairwise_stats[0].keys()),
    )
    _write_csv(
        GENERATED_ROOT / "public_benchmark_swebench_lite_before_after.csv",
        before_after,
        list(before_after[0].keys()),
    )
    _write_csv(
        GENERATED_ROOT / "public_benchmark_swebench_lite_b1_core_details.csv",
        b1_details,
        list(b1_details[0].keys()),
    )
    _write_csv(
        GENERATED_ROOT / "public_benchmark_swebench_lite_b2_core_details.csv",
        b2_details,
        list(b2_details[0].keys()),
    )

    # Compact LaTeX table
    table_tex = GENERATED_ROOT / "public_benchmark_swebench_lite_before_after_table.tex"
    lines = [
        r"\begin{tabular}{lrrrr}",
        r"\toprule",
        r"Comparison & Before mean & After mean & Absolute gain & Relative gain (\%)\\",
        r"\midrule",
    ]
    row = before_after[0]
    lines.append(
        r"B2 $\rightarrow$ B3 on SWE-bench Lite"
        + f" & {row['before_mean_composite']:.2f}"
        + f" & {row['after_mean_composite']:.2f}"
        + f" & {row['absolute_gain']:.2f}"
        + f" & {row['relative_gain_percent']:.2f}"
        + r"\\"
    )
    lines.extend([r"\bottomrule", r"\end{tabular}"])
    table_tex.write_text("\n".join(lines), encoding="utf-8")

    sig_table_tex = GENERATED_ROOT / "public_benchmark_swebench_lite_significance_table.tex"
    lines = [
        r"\begin{tabular}{lrrrrr}",
        r"\toprule",
        r"Comparison & $\Delta\mu$ & 95\% CI (low) & 95\% CI (high) & Cliff's $\delta$ & Holm-$p$\\",
        r"\midrule",
    ]
    for row in pairwise_stats:
        p_txt = "<1e-300" if row["p_value_holm"] == 0 else f"{row['p_value_holm']:.2e}"
        lines.append(
            f"{row['comparison'].replace('B3_vs_', 'B3 vs ')}"
            f" & {row['mean_diff']:.2f}"
            f" & {row['ci95_mean_diff_low']:.2f}"
            f" & {row['ci95_mean_diff_high']:.2f}"
            f" & {row['cliffs_delta']:.3f}"
            f" & {p_txt}"
            + r"\\"
        )
    lines.extend([r"\bottomrule", r"\end{tabular}"])
    sig_table_tex.write_text("\n".join(lines), encoding="utf-8")

    print("Public benchmark rows:", len(units))
    print("Before/After:", before_after[0])
    for row in pairwise_stats:
        print(row["comparison"], "delta", round(row["mean_diff"], 3), "holm_p", row["p_value_holm"])


if __name__ == "__main__":
    main()
