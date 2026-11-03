#!/usr/bin/env python3
"""Plot corrected human-rating profiles with the revised exploratory weights."""
from pathlib import Path
import argparse
import csv
import math

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "public_results/matched_review_quality_factor_comparison.csv"
OUT = ROOT / "public_results"
ORDER = [
    ("implementation_readiness", "Implementation\nreadiness"),
    ("dependency_risk_treatment", "Dependencies\nand risks"),
    ("granularity_actionability", "Granularity and\nactionability"),
    ("acceptance_testability", "Acceptance and\ntestability"),
    ("story_distinctness", "Story\ndistinctness"),
    ("coverage_completeness", "Coverage\ncompleteness"),
    ("scope_fidelity", "Scope\nfidelity"),
]
COLORS = {"B1": "#64748b", "B2": "#2563eb", "B3": "#087f6c"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUT)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with SOURCE.open(newline="", encoding="utf-8") as handle:
        rows = {r["dimension"]: r for r in csv.DictReader(handle)
                if r["analysis"] == "primary_all_40"}
    assert set(rows) == {key for key, _ in ORDER}
    assert math.isclose(sum(float(r["weight"]) for r in rows.values()), 1)
    totals = {m: sum(float(r["weight"]) * float(r[m + "_mean"])
                     for r in rows.values()) for m in COLORS}
    with (OUT / "matched_review_method_summary.csv").open(newline="") as handle:
        for row in csv.DictReader(handle):
            if row["analysis"] == "primary_all_40":
                assert math.isclose(totals[row["method"]], float(row["composite_mean"]), abs_tol=1e-12)
    with (OUT / "matched_review_pairwise.csv").open(newline="") as handle:
        comparison = next(row for row in csv.DictReader(handle)
                          if row["analysis"] == "primary_all_40"
                          and row["outcome"] == "composite" and row["contrast"] == "B3-B2")

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11,
                         "pdf.fonttype": 42, "svg.fonttype": "none"})
    fig = plt.figure(figsize=(12, 7.5), facecolor="white")
    ax = fig.add_axes([.12, .20, .50, .60], projection="polar")
    ax.set_theta_offset(math.pi / 2)
    ax.set_theta_direction(-1)
    angles = [2 * math.pi * i / len(ORDER) for i in range(len(ORDER))]
    ax.set_xticks(angles)
    ax.set_xticklabels([f"{label}\n{float(rows[key]['weight']):.0%}" for key, label in ORDER], fontsize=10.5)
    ax.tick_params(axis="x", pad=20)
    for tick, (key, _) in zip(ax.get_xticklabels(), ORDER):
        if float(rows[key]["weight"]) >= .2:
            tick.set_fontweight("bold")
    ax.set_ylim(0, 5)
    ax.set_yticks([1, 2, 3, 4, 5])
    ax.set_yticklabels(["1", "2", "3", "4", "5"], fontsize=9, color="#667085")
    ax.set_rlabel_position(73)
    ax.grid(color="#cbd5e1", linewidth=.7)
    ax.spines["polar"].set_color("#cbd5e1")
    for method in COLORS:
        values = [float(rows[key][method + "_mean"]) for key, _ in ORDER]
        assert all(1 <= value <= 5 for value in values)
        ax.plot(angles + angles[:1], values + values[:1], color=COLORS[method],
                linewidth=2.4 if method == "B3" else 1.9,
                linestyle="--" if method == "B1" else "-", marker="o", markersize=4)
        ax.fill(angles + angles[:1], values + values[:1], color=COLORS[method], alpha=.045)

    fig.text(.06, .95, "Human-rated requirement quality", fontsize=20, weight="bold", color="#182230")
    fig.text(.06, .905, "40 reviewers · 14 matched epics · 560 ratings per method", fontsize=11, color="#475467")
    fig.text(.73, .80, "Weighted composite", fontsize=13, weight="bold", color="#182230")
    names = {"B1": "Template baseline", "B2": "One-shot drafting", "B3": "Jira Enhancer"}
    for y, method in zip([.72, .61, .50], COLORS):
        fig.text(.73, y, f"{method}  {names[method]}", color=COLORS[method], fontsize=11, weight="bold")
        fig.text(.73, y - .055, f"{totals[method]:.2f} / 5", color=COLORS[method], fontsize=20, weight="bold")
    fig.text(.73, .30,
             f"B3 − B2: {float(comparison['mean_difference']):+.2f} points\n"
             f"95% CI [{float(comparison['ci_low']):.2f}, {float(comparison['ci_high']):.2f}]\n"
             f"Holm p = {float(comparison['holm_p']):.6f}",
             fontsize=11, color="#344054", linespacing=1.7)
    fig.text(.06, .065, "Axes show mean rubric ratings on a 1–5 scale; percentages label composite weights.", fontsize=10, color="#475467")
    fig.text(.06, .035, "Revised weights were selected after the initial analysis; the weighted outcome is exploratory.", fontsize=9.5, color="#667085")
    for extension in ("png", "pdf"):
        target = args.output_dir / f"matched_review_weighted_radar.{extension}"
        fig.savefig(target, dpi=300, facecolor="white")
        print(target)
    plt.close(fig)


if __name__ == "__main__":
    main()
