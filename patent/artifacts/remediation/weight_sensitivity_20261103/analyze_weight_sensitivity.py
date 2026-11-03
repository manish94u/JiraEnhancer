#!/usr/bin/env python3
"""Reproduce a fixed equal-weight sensitivity analysis of corrected ratings.

The public input contains one seven-dimension mean vector per epic and method,
averaged across all 40 reviewers. The equal-weight score is sum(dimensions)/7;
weights are never rounded to 14.3%. Existing priority weights are retained as a
reference. No weights are fitted or searched. Only NumPy and the standard
library are required.

Default: reproduce results from the bundled public aggregate.
--source PRIVATE_CSV: validate the immutable corrected source and regenerate
the de-identified aggregate before analysis. No individual ratings are exported.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
EXPECTED_SOURCE_SHA256 = "61185752527cc5f5361d7c8c7c6fd55d5c62805d44d94d74dfbfb8adcaae1b6e"
DIMENSIONS = (
    "scope_fidelity", "coverage_completeness", "story_distinctness",
    "acceptance_testability", "dependency_risk_treatment",
    "granularity_actionability", "implementation_readiness",
)
PRIORITY_WEIGHTS = np.array([.05, .05, .05, .05, .20, .20, .40])
METHODS = ("B1", "B2", "B3")
UNITS = tuple(f"U{i:02d}" for i in range(1, 15))
SEED_BASE = 20261313
BOOTSTRAP_RESAMPLES = 100_000
AGGREGATE_COLUMNS = ["unit_id", "method", "reviewer_count", *DIMENSIONS]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields or list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def aggregate_private(source: Path) -> tuple[list[dict], dict]:
    before = digest(source)
    if before != EXPECTED_SOURCE_SHA256:
        raise ValueError("Source checksum differs from the declared corrected ratings.")
    rows = read_csv(source)
    if len(rows) != 1840:
        raise ValueError("Expected 1,840 source rows.")
    if Counter(r["record_type"] for r in rows) != {"primary": 1680, "masked_repeat": 160}:
        raise ValueError("Unexpected primary/repeat accounting.")
    reviewers = sorted({r["reviewer"] for r in rows})
    if len(reviewers) != 40 or set(Counter(r["reviewer"] for r in rows).values()) != {46}:
        raise ValueError("Expected 40 reviewers with 46 source rows each.")
    groups: dict[tuple[str, str], list] = defaultdict(list)
    seen = set()
    for row in rows:
        scores = np.array([float(row[d]) for d in DIMENSIONS])
        if not np.isfinite(scores).all() or not ((scores >= 1) & (scores <= 5)).all():
            raise ValueError("Missing or out-of-range dimension rating.")
        if not np.equal(scores, np.floor(scores)).all():
            raise ValueError("Expected integer ratings on the anchored 1-5 dimensions.")
        if not np.isclose(float(row["composite"]), scores.sum() / 7, atol=1e-12, rtol=0):
            raise ValueError("Stored source composite differs from the arithmetic mean.")
        if row["record_type"] != "primary":
            continue
        key = (row["input_id"], row["method"], row["reviewer"])
        if key in seen:
            raise ValueError("Repeated primary input/method/reviewer key.")
        seen.add(key)
        groups[(row["input_id"], row["method"])].append((row["reviewer"], scores))
    inputs = sorted({key[0] for key in groups})
    if len(inputs) != 14 or set(groups) != set(itertools.product(inputs, METHODS)):
        raise ValueError("Expected a complete 14-input, three-method grid.")
    result = []
    for private_id, unit_id in zip(inputs, UNITS):
        for method in METHODS:
            cell = groups[(private_id, method)]
            if len(cell) != 40 or {r for r, _ in cell} != set(reviewers):
                raise ValueError("An input/method cell does not contain all 40 reviewers.")
            means = np.mean([scores for _, scores in cell], axis=0)
            result.append({"unit_id": unit_id, "method": method, "reviewer_count": 40,
                           **dict(zip(DIMENSIONS, means.tolist()))})
    if digest(source) != before:
        raise ValueError("Source changed during aggregation.")
    return result, {
        "source_sha256_before": before, "source_sha256_after": digest(source),
        "source_unchanged": True, "source_rows": 1840, "primary_ratings": 1680,
        "excluded_masked_repeats": 160, "reviewers": 40, "epics": 14,
        "packages": 42, "primary_ratings_per_method": 560,
        "public_mapping": "Sorted input order mapped to U01-U14, as in the existing public priority aggregate.",
        "raw_rating_values_modified": False,
    }


def validate_aggregate(rows: list[dict]) -> dict[tuple[str, str], np.ndarray]:
    if len(rows) != 42:
        raise ValueError("Expected 42 aggregate rows.")
    cells = {}
    for row in rows:
        key = row["unit_id"], row["method"]
        if key in cells or int(row["reviewer_count"]) != 40:
            raise ValueError("Duplicate cell or unexpected reviewer count.")
        v = np.array([float(row[d]) for d in DIMENSIONS])
        if not np.isfinite(v).all() or not ((v >= 1) & (v <= 5)).all():
            raise ValueError("Invalid aggregate dimension mean.")
        cells[key] = v
    if set(cells) != set(itertools.product(UNITS, METHODS)):
        raise ValueError("Incomplete neutral-unit/method grid.")
    return cells


def exact_sign_flip(differences: np.ndarray) -> float:
    d = differences[np.abs(differences) > 1e-12]
    if not len(d):
        return 1.0
    observed = abs(float(d.sum()))
    extreme = sum(abs(float(np.dot(d, signs))) >= observed - 1e-12
                  for signs in itertools.product((-1.0, 1.0), repeat=len(d)))
    return extreme / 2 ** len(d)


def holm(p_values: list[float]) -> list[float]:
    adjusted = [0.0] * len(p_values)
    running = 0.0
    for rank, index in enumerate(sorted(range(len(p_values)), key=p_values.__getitem__)):
        running = max(running, min(1.0, (len(p_values) - rank) * p_values[index]))
        adjusted[index] = running
    return adjusted


def bootstrap_ci(d: np.ndarray, seed: int) -> list[float]:
    rng = np.random.Generator(np.random.PCG64(seed))
    sample = rng.integers(0, len(d), size=(BOOTSTRAP_RESAMPLES, len(d)))
    return np.quantile(d[sample].mean(axis=1), [.025, .975]).tolist()


def analyze(cells: dict, reference: Path) -> tuple[dict, list[dict]]:
    existing = {(r["unit_id"], r["method"]): float(r["priority_weighted"])
                for r in read_csv(reference)}
    if set(existing) != set(cells):
        raise ValueError("Reference priority aggregate has a different unit/method grid.")
    schemes = {"priority_weighted": {}, "equal_weight": {}}
    epic_scores = []
    for method in METHODS:
        matrix = np.array([cells[(u, method)] for u in UNITS])
        schemes["priority_weighted"][method] = matrix @ PRIORITY_WEIGHTS
        schemes["equal_weight"][method] = matrix.sum(axis=1) / 7.0
        for i, unit in enumerate(UNITS):
            priority = float(schemes["priority_weighted"][method][i])
            if not np.isclose(priority, existing[(unit, method)], atol=1e-12, rtol=0):
                raise ValueError("Priority score does not match the released aggregate or mapping.")
            epic_scores.append({"unit_id": unit, "method": method,
                               "priority_weighted": priority,
                               "equal_weight": float(schemes["equal_weight"][method][i]),
                               "analysis": "primary_all_40"})
    results = []
    for scheme, vectors in schemes.items():
        family = []
        for index, baseline in enumerate(("B2", "B1")):
            d = vectors["B3"] - vectors[baseline]
            low, high = bootstrap_ci(d, SEED_BASE + index)
            family.append({
                "weighting": scheme, "contrast": f"B3-{baseline}", "n_epics": 14,
                "B3_mean": float(vectors["B3"].mean()),
                "baseline_mean": float(vectors[baseline].mean()),
                "mean_difference": float(d.mean()), "ci95_low": low, "ci95_high": high,
                "exact_sign_flip_p": exact_sign_flip(d),
                "positive_epics": int((d > 1e-12).sum()),
                "negative_epics": int((d < -1e-12).sum()),
                "tied_epics": int((np.abs(d) <= 1e-12).sum()),
                "bootstrap_seed": SEED_BASE + index,
            })
        for row, p in zip(family, holm([r["exact_sign_flip_p"] for r in family])):
            row["holm_p_two_contrasts_within_weighting"] = p
        results.extend(family)
    for row, p in zip(results, holm([r["exact_sign_flip_p"] for r in results])):
        row["holm_p_four_contrast_sensitivity"] = p
    return {
        "analysis": "primary_all_40_fixed_weight_sensitivity",
        "software": {"numpy_version": np.__version__, "random_generator": "numpy.random.PCG64"},
        "source_sha256": EXPECTED_SOURCE_SHA256,
        "dimensions": list(DIMENSIONS),
        "weight_schemes": {
            "priority_weighted": dict(zip(DIMENSIONS, PRIORITY_WEIGHTS.tolist())),
            "equal_weight": {d: "1/7" for d in DIMENSIONS},
        },
        "weighting_status": "Both analyses are exploratory; this sensitivity analysis applies one fixed alternative without searching weights.",
        "equal_weight_computation": "Sum of the seven dimension means divided by 7; never seven rounded 14.3% weights.",
        "unit_of_inference": "14 paired epic clusters; each cell averages all 40 primary reviewer ratings; masked repeats excluded.",
        "bootstrap": {"resamples": BOOTSTRAP_RESAMPLES, "interval": "95% percentile",
                      "seeds": {"B3-B2": SEED_BASE, "B3-B1": SEED_BASE + 1},
                      "resampling": "Whole paired epics, same seeds for both weighting schemes."},
        "multiplicity": {
            "main": "Holm adjustment over the two method contrasts separately within each weighting.",
            "additional_sensitivity": "Holm adjustment over all four weighting-by-method contrasts; this does not correct post-analysis selection of weights or rating corrections.",
        },
        "method_means": {scheme: {m: float(v.mean()) for m, v in vectors.items()}
                         for scheme, vectors in schemes.items()},
        "priority_reference_validation": {"matched_cells": 42, "absolute_tolerance": 1e-12,
                                          "reference_sha256": digest(reference)},
        "comparisons": results,
        "interpretation": "The comparison assesses sensitivity to replacing the priority weights with uniform weights on the same corrected ratings. It does not independently validate the rating corrections, eliminate all post hoc selection concerns, or isolate workflow components.",
    }, sorted(epic_scores, key=lambda r: (r["unit_id"], r["method"]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="Optional immutable private ratings CSV")
    parser.add_argument("--aggregate", type=Path, default=HERE / "public_epic_dimension_means.csv")
    parser.add_argument("--reference-priority", type=Path, default=HERE / "reference_priority_epic_scores.csv")
    parser.add_argument("--output-dir", type=Path, default=HERE)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.source:
        rows, validation = aggregate_private(args.source)
    else:
        rows = read_csv(args.aggregate)
        validation = None
    cells = validate_aggregate(rows)
    report, scores = analyze(cells, args.reference_priority)
    if args.source:
        if digest(args.source) != EXPECTED_SOURCE_SHA256:
            raise ValueError("Source changed during analysis.")
        write_csv(args.output_dir / "public_epic_dimension_means.csv", rows, AGGREGATE_COLUMNS)
        (args.output_dir / "source_validation.json").write_text(json.dumps(validation, indent=2) + "\n")
    write_csv(args.output_dir / "epic_scores.csv", scores)
    write_csv(args.output_dir / "comparisons.csv", report["comparisons"])
    method_rows = [{"weighting": scheme, "method": method, "mean": value,
                    "n_epics": 14, "reviewers": 40, "primary_ratings": 560}
                   for scheme, means in report["method_means"].items()
                   for method, value in means.items()]
    write_csv(args.output_dir / "method_means.csv", method_rows)
    (args.output_dir / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"method_means": report["method_means"], "comparisons": report["comparisons"]}, indent=2))


if __name__ == "__main__":
    main()
