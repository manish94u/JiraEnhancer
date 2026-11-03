#!/usr/bin/env python3
"""Rebuild the recorded-refinement figure from released aggregate survival tables.

Run from the repository root:
  python3 patent/reviewer_study/plot_iteration_effort.py --output-dir outputs/reproduced/iteration_effort
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path


HERE = Path(__file__).resolve().parent
PUBLIC_RESULTS = HERE / "public_results"
FIELDS = {"time", "at_risk", "events", "censored", "proportion_not_yet_observed"}


def read_curve(path: Path, expected_units: int) -> list[dict[str, int | float]]:
    """Validate risk-set accounting and recompute each product-limit value."""
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if set(reader.fieldnames or ()) != FIELDS:
            raise ValueError(f"Unexpected survival-table columns: {path.name}")
        raw_rows = list(reader)
    if not raw_rows:
        raise ValueError(f"Empty survival table: {path.name}")

    curve: list[dict[str, int | float]] = []
    at_risk = expected_units
    previous_time = 0
    probability = 1.0
    for row in raw_rows:
        time = int(row["time"])
        recorded_risk = int(row["at_risk"])
        events = int(row["events"])
        censored = int(row["censored"])
        recorded_probability = float(row["proportion_not_yet_observed"])
        if time <= previous_time or recorded_risk != at_risk or at_risk <= 0:
            raise ValueError(f"Invalid time or at-risk denominator: {path.name}, time={time}")
        if events < 0 or censored < 0 or not 0 < events + censored <= at_risk:
            raise ValueError(f"Invalid event/censor totals: {path.name}, time={time}")
        # Events use the risk set before censoring at the same recorded time.
        probability *= 1.0 - events / at_risk
        if not math.isfinite(recorded_probability) or not math.isclose(
            probability, recorded_probability, rel_tol=0.0, abs_tol=1e-12
        ):
            raise ValueError(f"Product-limit value mismatch: {path.name}, time={time}")
        curve.append(dict(time=time, at_risk=at_risk, events=events, censored=censored,
                          proportion_not_yet_observed=probability))
        at_risk -= events + censored
        previous_time = time
    if at_risk != 0:
        raise ValueError(f"Event/censor totals do not account for {expected_units} units: {path.name}")
    return curve


def render(story_curve: list[dict[str, int | float]],
           epic_curve: list[dict[str, int | float]], output_dir: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MaxNLocator, PercentFormatter

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "axes.spines.top": False, "axes.spines.right": False})
    fig, axes = plt.subplots(1, 2, figsize=(10.4, 4.5), gridspec_kw={"width_ratios": [1, 1.15]})
    for ax, curve, title, xlabel in [
        (axes[0], story_curve, "(a) Stories: first observed score ≥ 9", "Enrichment passes per story"),
        (axes[1], epic_curve, "(b) Epics: all five stories score ≥ 9", "Enrichment calls completed within epic")]:
        xs = [0] + [row["time"] for row in curve]
        ys = [1.0] + [row["proportion_not_yet_observed"] for row in curve]
        ax.step(xs, ys, where="post", color="#176b88", linewidth=2)
        for row in curve:
            if row["censored"]:
                ax.plot(row["time"], row["proportion_not_yet_observed"], "+",
                        color="#a64d21", ms=10, mew=1.7)
        ax.set(title=title, xlabel=xlabel, ylim=(-0.03, 1.07), xlim=(-0.1, xs[-1] + 0.35))
        ax.yaxis.set_major_formatter(PercentFormatter(1.0))
        ax.xaxis.set_major_locator(MaxNLocator(integer=True))
        ax.grid(axis="y", alpha=.2)
    stories = int(story_curve[0]["at_risk"])
    epics = int(epic_curve[0]["at_risk"])
    story_events = sum(int(row["events"]) for row in story_curve)
    story_censored = sum(int(row["censored"]) for row in story_curve)
    epic_events = sum(int(row["events"]) for row in epic_curve)
    epic_censored = sum(int(row["censored"]) for row in epic_curve)
    axes[0].set_ylabel("Not yet observed at threshold")
    axes[0].set_xticks([0, 1, 2, 3])
    axes[0].text(.08, .45, f"{stories} stories / {epics} matched inputs\n"
                 f"{story_events} crossed; {story_censored} stopped below 9\nMaximum three passes",
                 transform=axes[0].transAxes, fontsize=9)
    axes[1].text(.06, .12, f"{epic_events} complete epics; {epic_censored} incomplete\n"
                 "Planning calls excluded from x-axis", transform=axes[1].transAxes, fontsize=9)
    fig.text(.07, .07, "+  Budget-end censoring. Curves summarize recorded confidence thresholds, not approval or writeback.", fontsize=9)
    fig.text(.07, .025, "Scores start after the first enrichment pass; native pre-refinement scores were not archived.", fontsize=9)
    fig.subplots_adjust(left=.075, right=.985, top=.88, bottom=.24, wspace=.28)
    output_dir.mkdir(parents=True, exist_ok=True)
    for extension in ("png", "pdf"):
        fig.savefig(output_dir / f"matched_iteration_effort.{extension}", dpi=220, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--story-survival", type=Path,
                        default=PUBLIC_RESULTS / "iteration_story_survival.csv")
    parser.add_argument("--epic-survival", type=Path,
                        default=PUBLIC_RESULTS / "iteration_epic_survival.csv")
    parser.add_argument("--output-dir", type=Path, default=PUBLIC_RESULTS)
    args = parser.parse_args()
    story_curve = read_curve(args.story_survival, expected_units=70)
    epic_curve = read_curve(args.epic_survival, expected_units=14)
    if [row["time"] for row in story_curve] != [1, 2, 3]:
        raise ValueError("Expected the declared three-attempt story horizon")
    render(story_curve, epic_curve, args.output_dir)
    print(json.dumps({"stories": 70, "epics": 14,
                      "story_threshold_crossings": sum(int(row["events"]) for row in story_curve),
                      "complete_epics": sum(int(row["events"]) for row in epic_curve),
                      "output_dir": str(args.output_dir)}, indent=2))


if __name__ == "__main__":
    main()
