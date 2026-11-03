#!/usr/bin/env python3
"""Reproduce the revised, exploratory weighted outcome from public epic means.

The published rows contain aggregate scores, not individual reviewer ratings.
Weights were selected after the initial analysis: readiness 40%, dependencies
and risks 20%, granularity and actionability 20%, and four other dimensions 5%
each. This verifier checks aggregation and inference from those weighted means;
it does not reconstruct the private source ratings.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
from pathlib import Path

import numpy as np

DEFAULT_INPUT = Path(__file__).resolve().parent / "public_results/matched_review_epic_scores.csv"
ANALYSES = ("primary_all_40",)
METHODS = ("B1", "B2", "B3")
UNITS = tuple(f"U{index:02d}" for index in range(1, 15))
SEED = 20260813
BOOTSTRAP_RESAMPLES = 100_000


def exact_sign_flip(values: np.ndarray) -> float:
    values = values[np.abs(values) > 1e-12]
    if not len(values):
        return 1.0
    observed = abs(float(values.sum()))
    extreme = sum(
        abs(float(np.dot(values, signs))) >= observed - 1e-12
        for signs in itertools.product((-1.0, 1.0), repeat=len(values))
    )
    return extreme / (2 ** len(values))


def holm(p_values: list[float]) -> list[float]:
    adjusted = [0.0] * len(p_values)
    running = 0.0
    for position, index in enumerate(sorted(range(len(p_values)), key=p_values.__getitem__)):
        running = max(running, min(1.0, (len(p_values) - position) * p_values[index]))
        adjusted[index] = running
    return adjusted


def bootstrap_ci(values: np.ndarray, seed: int) -> list[float]:
    rng = np.random.default_rng(seed)
    samples = rng.integers(0, len(values), size=(BOOTSTRAP_RESAMPLES, len(values)))
    return np.quantile(values[samples].mean(axis=1), [.025, .975]).tolist()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Public aggregate unit-score CSV")
    args = parser.parse_args()
    with args.input.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["unit_id", "method", "priority_weighted", "analysis"]:
            raise ValueError("Expected only unit_id, method, priority_weighted, analysis columns.")
        rows = list(reader)
    if len(rows) != 42:
        raise ValueError(f"Expected 42 aggregate rows; found {len(rows)}.")
    scores = {}
    for row in rows:
        unit, method, analysis = row["unit_id"], row["method"], row["analysis"]
        if unit not in UNITS or method not in METHODS or analysis not in ANALYSES:
            raise ValueError("Unexpected unit, method, or reviewer-set label.")
        key = (analysis, unit, method)
        if key in scores:
            raise ValueError(f"Duplicate aggregate row: {key}")
        value = float(row["priority_weighted"])
        if not np.isfinite(value) or not 1 <= value <= 5:
            raise ValueError(f"Weighted score outside the 1–5 scale: {key}")
        scores[key] = value
    expected = set(itertools.product(ANALYSES, UNITS, METHODS))
    if set(scores) != expected:
        raise ValueError("Incomplete matched unit/method/reviewer-set grid.")
    results = []
    for analysis in ANALYSES:
        vectors = {method: np.array([scores[(analysis, unit, method)] for unit in UNITS]) for method in METHODS}
        family = []
        for index, baseline in enumerate(("B2", "B1")):
            difference = vectors["B3"] - vectors[baseline]
            low, high = bootstrap_ci(difference, SEED + 500 + index)
            family.append({
                "analysis": analysis, "contrast": f"B3-{baseline}", "n_epics": len(UNITS),
                "B3_mean": float(vectors["B3"].mean()),
                "baseline_mean": float(vectors[baseline].mean()),
                "mean_difference": float(difference.mean()),
                "ci95_low": low, "ci95_high": high,
                "exact_sign_flip_p": exact_sign_flip(difference),
                "positive_epics": int(np.sum(difference > 1e-12)),
                "negative_epics": int(np.sum(difference < -1e-12)),
                "tied_epics": int(np.sum(np.abs(difference) <= 1e-12)),
            })
        for row, adjusted in zip(family, holm([row["exact_sign_flip_p"] for row in family])):
            row["holm_p_within_weighting"] = adjusted
        results.extend(family)
    print(json.dumps({
        "outcome": "revised_priority_weighted_composite",
        "weighting_status": "exploratory; selected after initial analysis",
        "input_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
        "aggregate_rows": len(rows), "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
        "bootstrap_seed_base": SEED + 500, "holm_family": "two method contrasts within each reviewer set",
        "results": results,
    }, indent=2))


if __name__ == "__main__":
    main()
