#!/usr/bin/env python3
"""Render measured corrected-reviewer outcomes at a 5.15-inch article width.

Inputs are the released 14-epic/40-reviewer dimension means and priority-score
reference. There is no synthetic B0 treatment or quality/efficiency/governance
index in this figure. No rating source or existing analysis result is modified.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np

from analyze_weight_sensitivity import (
    DIMENSIONS, METHODS, UNITS, PRIORITY_WEIGHTS, validate_aggregate, read_csv,
    EXPECTED_SOURCE_SHA256,
)

HERE = Path(__file__).resolve().parent
COLORS = {"B1": "#2876B8", "B2": "#DB7B23", "B3": "#2A8B57"}
MARKERS = {"B1": "o", "B2": "s", "B3": "^"}
LABELS = [
    "Scope\nfidelity", "Coverage\ncompleteness", "Story\ndistinctness",
    "Acceptance &\ntestability", "Dependencies\n& risks",
    "Granularity &\nactionability", "Implementation\nreadiness",
]
BOOTSTRAP_RESAMPLES = 100_000
BOOTSTRAP_SEED = 20261103


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aggregate", type=Path, default=HERE / "public_epic_dimension_means.csv")
    parser.add_argument("--reference-priority", type=Path, default=HERE / "reference_priority_epic_scores.csv")
    parser.add_argument("--output-dir", type=Path, default=HERE)
    args = parser.parse_args()
    hashes = {str(p): digest(p) for p in (args.aggregate, args.reference_priority)}
    cells = validate_aggregate(read_csv(args.aggregate))
    reference = {(r["unit_id"], r["method"]): float(r["priority_weighted"])
                 for r in read_csv(args.reference_priority)}
    matrices = {m: np.array([cells[(u, m)] for u in UNITS]) for m in METHODS}
    scores = {m: matrices[m] @ PRIORITY_WEIGHTS for m in METHODS}
    for method in METHODS:
        for i, unit in enumerate(UNITS):
            if not np.isclose(scores[method][i], reference[(unit, method)], atol=1e-12, rtol=0):
                raise ValueError("Priority score/reference mismatch.")
    means = {m: float(v.mean()) for m, v in scores.items()}
    if not np.allclose(list(means.values()), [2.5564285714285715, 3.6064285714285718, 4.147946428571428], atol=1e-12, rtol=0):
        raise ValueError("Unexpected composite means.")
    rng = np.random.Generator(np.random.PCG64(BOOTSTRAP_SEED))
    resamples = rng.integers(0, len(UNITS), size=(BOOTSTRAP_RESAMPLES, len(UNITS)))
    dimension_means = {m: matrices[m].mean(axis=0) for m in METHODS}
    intervals = {m: np.quantile(matrices[m][resamples].mean(axis=1), [.025, .975], axis=0)
                 for m in METHODS}

    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 8,
        "axes.labelsize": 8, "axes.titlesize": 8.5,
        "xtick.labelsize": 8, "ytick.labelsize": 7.6,
        "axes.linewidth": .65, "lines.linewidth": .9,
        "pdf.fonttype": 42, "ps.fonttype": 42,
        "savefig.facecolor": "white", "figure.facecolor": "white",
    })
    fig = plt.figure(figsize=(5.15, 4.05))
    left = fig.add_axes([.105, .245, .292, .61])
    right = fig.add_axes([.665, .245, .315, .61])

    for ax in (left, right):
        ax.spines[["top", "right"]].set_visible(False)
        ax.tick_params(length=2.5, width=.6, pad=3)
        ax.set_axisbelow(True)
    left.grid(axis="y", color="#DDDFE2", linewidth=.55)
    violin = left.violinplot([scores[m] for m in METHODS], positions=[1, 2, 3],
                            widths=.78, showmeans=False, showmedians=False,
                            showextrema=False, points=150, bw_method=.45)
    for body, method in zip(violin["bodies"], METHODS):
        body.set_facecolor(COLORS[method])
        body.set_edgecolor(COLORS[method])
        body.set_alpha(.18)
        body.set_linewidth(.8)
    summary_rows = []
    for pos, method in enumerate(METHODS, 1):
        v = scores[method]
        # Every epic is drawn once. Deterministic rank jitter has no statistical role.
        ranks = np.argsort(np.argsort(v, kind="stable"), kind="stable")
        jitter = np.array([-.24, -.13, -.19, -.08, -.27, -.16, -.10] * 2)[ranks]
        left.scatter(pos + jitter, v, s=15, marker=MARKERS[method],
                     color=COLORS[method], edgecolors="white", linewidths=.35,
                     zorder=3, clip_on=False)
        q1, median, q3 = np.quantile(v, [.25, .5, .75])
        left.plot([pos+.12, pos+.12], [q1, q3], color="#30343B", linewidth=2.4, zorder=4)
        left.scatter([pos+.12], [median], s=17, facecolor="white",
                     edgecolor="#30343B", linewidth=.75, zorder=5)
        left.text(pos, 1.28, f"{means[method]:.2f}", ha="center", va="center",
                  color=COLORS[method], fontweight="bold", fontsize=8)
        summary_rows.append({"method": method, "composite_mean": means[method],
                             "composite_q1": float(q1), "composite_median": float(median),
                             "composite_q3": float(q3), "n_epics": 14})
    left.set(xlim=(.45, 3.5), ylim=(1, 5), xticks=[1, 2, 3], xticklabels=METHODS,
             yticks=[1, 2, 3, 4, 5], ylabel="Priority-weighted score (1–5)",
             xlabel="Method (means shown)")
    left.set_title("(a) Composite distribution", loc="left", pad=9, fontweight="bold")

    ypos = np.arange(len(DIMENSIONS))
    for row in range(len(DIMENSIONS)):
        if row % 2 == 0:
            right.axhspan(row-.48, row+.48, facecolor="#F4F5F6", zorder=0)
    for method, offset in zip(METHODS, [-.23, 0, .23]):
        v = dimension_means[method]
        lo, hi = intervals[method]
        right.errorbar(v, ypos+offset, xerr=np.vstack([v-lo, hi-v]),
                       fmt=MARKERS[method], markersize=3.9, color=COLORS[method],
                       markeredgecolor="white", markeredgewidth=.35,
                       capsize=1.6, capthick=.7, elinewidth=.9, zorder=3)
    right.set(xlim=(1, 5), ylim=(6.65, -.65), xticks=[1, 2, 3, 4, 5],
              yticks=ypos, yticklabels=LABELS, xlabel="Rating mean (1–5)")
    right.tick_params(axis="y", length=0, pad=6)
    right.grid(axis="x", color="#DDDFE2", linewidth=.55)
    right.set_title("(b) Dimension means", loc="left", pad=9, fontweight="bold")

    handles = [Line2D([], [], color=COLORS[m], marker=MARKERS[m], linestyle="none",
                      markersize=4.4, label=f"{m} {name}")
               for m, name in zip(METHODS, ["Template", "One-shot LLM", "Jira Enhancer"])]
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(.54, .995),
               ncol=3, frameon=False, fontsize=8, handletextpad=.35,
               columnspacing=1.0, borderaxespad=0)
    fig.text(.53, .122, "14 matched epics · 40 reviewers · 560 primary ratings per method",
             ha="center", va="center", fontsize=7.5)
    fig.text(.53, .075, "(a) Dots: epic means; bar: IQR; white marker: median.  (b) 95% epic-bootstrap CIs.",
             ha="center", va="center", fontsize=7)
    fig.text(.53, .030, "Priority weights selected after the initial analysis; composite findings are exploratory.",
             ha="center", va="center", fontsize=7)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = args.output_dir / "measured_review_composite_components"
    fig.savefig(stem.with_suffix(".pdf"), metadata={"Title": "Measured matched-review composite and requirement dimensions", "Author": "Jira Enhancer study", "CreationDate": None, "ModDate": None})
    fig.savefig(stem.with_suffix(".png"), dpi=300)
    plt.close(fig)
    with (args.output_dir / "measured_review_dimension_statistics.csv").open("w", newline="") as h:
        w = csv.DictWriter(h, fieldnames=["dimension", "method", "mean", "ci95_low", "ci95_high", "n_epics", "reviewers_per_epic"])
        w.writeheader()
        for j, dimension in enumerate(DIMENSIONS):
            for method in METHODS:
                w.writerow({"dimension": dimension, "method": method,
                            "mean": dimension_means[method][j],
                            "ci95_low": intervals[method][0,j], "ci95_high": intervals[method][1,j],
                            "n_epics": 14, "reviewers_per_epic": 40})
    provenance = {
        "figure": "measured_review_composite_components",
        "source_rating_sha256": EXPECTED_SOURCE_SHA256,
        "aggregate_sha256": digest(args.aggregate),
        "priority_reference_sha256": digest(args.reference_priority),
        "input_files_unchanged": all(digest(Path(p)) == h for p, h in hashes.items()),
        "sample": {"epics": 14, "reviewers": 40, "methods": list(METHODS), "primary_ratings_per_method": 560, "masked_repeats_excluded": 160},
        "composite": {"weights": dict(zip(DIMENSIONS, PRIORITY_WEIGHTS.tolist())), "status": "exploratory; weights selected after initial analysis", "statistics": summary_rows},
        "violin": {"kernel": "Gaussian KDE via matplotlib.violinplot", "bandwidth_factor": .45, "points": 150, "interpretation": "descriptive density of 14 epic means, with all 14 observations displayed"},
        "dimension_intervals": {"method": "95% percentile epic bootstrap", "resamples": BOOTSTRAP_RESAMPLES, "seed": BOOTSTRAP_SEED, "rng": "PCG64", "pairing": "same resampled epic indices used for every dimension and method", "scope": "intervals for individual method means, not paired contrasts and not multiplicity-adjusted"},
        "design": {"width_inches": 5.15, "height_inches": 4.05, "png_dpi": 300, "minimum_font_points": 7, "pdf": "vector with embedded TrueType fonts"},
        "software": {"numpy": np.__version__, "matplotlib": matplotlib.__version__},
        "caption_recommendation": "Measured matched-review outcomes from the corrected 40-reviewer dataset. (a) Distributions of priority-weighted package scores across 14 matched epics for B1 (template), B2 (one-shot drafting), and B3 (Jira Enhancer). Each colored point is an epic-level mean over 40 reviewers; violins are descriptive density estimates, dark bars span the interquartile range, white markers show medians, and numeric labels show method means. (b) Means for the seven observed requirement dimensions on the anchored 1–5 scale, with 95% percentile confidence intervals from 100,000 epic-bootstrap resamples. Each method has 560 primary ratings; masked repeats are excluded. The priority weights were selected after the initial analysis, so the composite comparison is exploratory. These measured outcomes are distinct from the formula-generated scoring checks in Appendix A.",
    }
    if not provenance["input_files_unchanged"]:
        raise ValueError("An input changed during plotting.")
    stem.with_suffix(".json").write_text(json.dumps(provenance, indent=2) + "\n")
    print(json.dumps({"pdf": str(stem.with_suffix('.pdf')), "png": str(stem.with_suffix('.png')), "means": means}, indent=2))


if __name__ == "__main__":
    main()
