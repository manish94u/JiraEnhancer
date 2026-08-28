#!/usr/bin/env python3
"""Regenerate the reviewer-safe B3 prompt-arm learning figure.

The public figure is rendered from a sanitized posterior trace and two public
summary files.  The optional ``--observations`` arguments rebuild that trace
from the archived private critic logs.  No input identifier, source text, or
critic explanation is written to the public CSV.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from jira_enhancer.rl.policy_bandit import (  # noqa: E402
    BanditState,
    ThompsonSamplingBandit,
)


DEFAULT_CURVE = SCRIPT_DIR / "artifacts/b2_b3_pilot/arm_learning_curve.csv"
DEFAULT_SUMMARY = SCRIPT_DIR / "artifacts/b2_b3_pilot/critic_learning_summary.json"
DEFAULT_EXPLOITATION = SCRIPT_DIR / "artifacts/b2_b3_pilot/exploitation_summary.json"
DEFAULT_OUTPUT = SCRIPT_DIR / "generated/b2_b3_arm_learning.png"
DEFAULT_REWARD_OUTPUT = SCRIPT_DIR / "generated/b2_b3_critic_reward_heatmap.png"

CURVE_FIELDS = ("sequence", "prompt_id", "reward", "alpha", "beta", "posterior_mean")

DISPLAY_LABELS = {
    "missing_acceptance_criteria": "Acceptance",
    "dependency_heavy": "Dependencies",
    "low_external_context": "Low-context",
    "failure_recovery_paths": "Recovery",
    "nonfunctional_constraints": "NFRs",
    "interface_data_contracts": "Interfaces",
    "operational_readiness": "Operations",
    "ambiguity_resolution": "Ambiguity",
    "security_privacy_boundaries": "Security",
}

SELECTED_COLORS = {
    "ambiguity_resolution": "#0072B2",
    "failure_recovery_paths": "#D55E00",
    "interface_data_contracts": "#009E73",
}
OTHER_COLOR = "#6B7280"
OTHER_BAR_COLOR = "#B8C0CC"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--curve-csv", type=Path, default=DEFAULT_CURVE)
    parser.add_argument("--summary-json", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--exploitation-json", type=Path, default=DEFAULT_EXPLOITATION)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--reward-output", type=Path, default=DEFAULT_REWARD_OUTPUT)
    parser.add_argument(
        "--observations",
        type=Path,
        action="append",
        default=[],
        help=(
            "Archived critic JSONL file. Repeat in the exact update order. "
            "When supplied, the sanitized curve CSV is rebuilt before plotting."
        ),
    )
    return parser.parse_args()


def load_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return payload


def iter_observations(paths: Sequence[Path]) -> Iterable[dict[str, object]]:
    for path in paths:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"Expected a JSON object at {path}:{line_number}")
            yield row


def rebuild_sanitized_curve(
    observation_paths: Sequence[Path], curve_path: Path, summary: dict[str, object]
) -> None:
    """Apply the production Thompson Beta update in archived row order."""

    prompt_stats = summary.get("prompt_stats")
    if not isinstance(prompt_stats, dict):
        raise ValueError("Learning summary has no prompt_stats object")
    expected_prompt_ids = set(prompt_stats)

    state = BanditState(prompt_stats={}, epsilon=0.0)
    learner = ThompsonSamplingBandit(state)
    public_rows: list[dict[str, str | int]] = []
    seen_pairs: set[tuple[str, str]] = set()

    for sequence, row in enumerate(iter_observations(observation_paths), 1):
        prompt_id = str(row["prompt_id"])
        input_id = str(row["input_id"])
        reward = float(row["reward"])
        pair = (input_id, prompt_id)
        if pair in seen_pairs:
            raise ValueError(f"Duplicate critic pair: {input_id}:{prompt_id}")
        if prompt_id not in expected_prompt_ids:
            raise ValueError(f"Unknown prompt arm: {prompt_id}")
        if not -1.0 <= reward <= 1.0:
            raise ValueError(f"Reward outside [-1, 1] at sequence {sequence}")
        seen_pairs.add(pair)

        # This calls the implementation used by the runtime.  It bounds the
        # reward, maps it to success=(reward+1)/2, and updates alpha and beta.
        learner.update(prompt_id, reward)
        learned = state.prompt_stats[prompt_id]
        alpha = float(learned["alpha"])
        beta = float(learned["beta"])
        public_rows.append(
            {
                "sequence": sequence,
                "prompt_id": prompt_id,
                "reward": repr(reward),
                "alpha": repr(alpha),
                "beta": repr(beta),
                "posterior_mean": repr(alpha / (alpha + beta)),
            }
        )

    expected_count = int(summary.get("observation_count", 0))
    if len(public_rows) != expected_count:
        raise ValueError(f"Expected {expected_count} observations, found {len(public_rows)}")
    if {prompt_id for _, prompt_id in seen_pairs} != expected_prompt_ids:
        raise ValueError("The critic logs do not cover every prompt arm")

    curve_path.parent.mkdir(parents=True, exist_ok=True)
    with curve_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CURVE_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(public_rows)


def read_and_validate_curve(
    curve_path: Path, summary: dict[str, object]
) -> tuple[list[dict[str, float | int | str]], dict[str, float]]:
    prompt_stats = summary.get("prompt_stats")
    if not isinstance(prompt_stats, dict):
        raise ValueError("Learning summary has no prompt_stats object")

    rows: list[dict[str, float | int | str]] = []
    with curve_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != CURVE_FIELDS:
            raise ValueError(f"Unexpected columns in {curve_path}")
        for expected_sequence, raw in enumerate(reader, 1):
            sequence = int(raw["sequence"])
            if sequence != expected_sequence:
                raise ValueError("Curve sequence must be contiguous and start at 1")
            prompt_id = raw["prompt_id"]
            if prompt_id not in prompt_stats:
                raise ValueError(f"Unknown prompt arm in curve: {prompt_id}")
            reward = float(raw["reward"])
            alpha = float(raw["alpha"])
            beta = float(raw["beta"])
            posterior_mean = float(raw["posterior_mean"])
            if not math.isclose(posterior_mean, alpha / (alpha + beta), rel_tol=0.0, abs_tol=1e-14):
                raise ValueError(f"Posterior mismatch at sequence {sequence}")
            rows.append(
                {
                    "sequence": sequence,
                    "prompt_id": prompt_id,
                    "reward": reward,
                    "alpha": alpha,
                    "beta": beta,
                    "posterior_mean": posterior_mean,
                }
            )

    expected_count = int(summary.get("observation_count", 0))
    if len(rows) != expected_count:
        raise ValueError(f"Expected {expected_count} curve rows, found {len(rows)}")

    final_rows: dict[str, dict[str, float | int | str]] = {}
    for row in rows:
        final_rows[str(row["prompt_id"])] = row

    final_means: dict[str, float] = {}
    for prompt_id, expected_raw in prompt_stats.items():
        if not isinstance(expected_raw, dict) or prompt_id not in final_rows:
            raise ValueError(f"Missing final state for {prompt_id}")
        actual = final_rows[prompt_id]
        for field in ("alpha", "beta", "posterior_mean"):
            expected_value = float(expected_raw[field])
            actual_value = float(actual[field])
            if not math.isclose(actual_value, expected_value, rel_tol=0.0, abs_tol=1e-12):
                raise ValueError(
                    f"Final {field} for {prompt_id} is {actual_value}, expected {expected_value}"
                )
        final_means[prompt_id] = float(actual["posterior_mean"])
    return rows, final_means


def selected_arms(exploitation: dict[str, object]) -> list[str]:
    sequences = exploitation.get("selected_arm_sequences")
    if not isinstance(sequences, dict):
        raise ValueError("Exploitation summary has no selected_arm_sequences object")
    selected: list[str] = []
    for sequence in sequences.values():
        if not isinstance(sequence, list):
            raise ValueError("Each selected arm sequence must be a list")
        for prompt_id in sequence:
            prompt_id = str(prompt_id)
            if prompt_id not in selected:
                selected.append(prompt_id)
    if len(selected) != 3:
        raise ValueError(f"Expected three held-out selected arms, found {len(selected)}")
    return selected


def build_histories(
    rows: Sequence[dict[str, float | int | str]], prompt_ids: Sequence[str], selected: Sequence[str]
) -> tuple[list[int], dict[str, list[float]], list[float], dict[str, list[tuple[int, float]]]]:
    current = {prompt_id: 0.5 for prompt_id in prompt_ids}
    other = [prompt_id for prompt_id in prompt_ids if prompt_id not in selected]
    x_values = [0]
    histories = {prompt_id: [0.5] for prompt_id in selected}
    other_mean = [sum(current[prompt_id] for prompt_id in other) / len(other)]
    update_markers: dict[str, list[tuple[int, float]]] = {prompt_id: [] for prompt_id in selected}

    for row in rows:
        sequence = int(row["sequence"])
        prompt_id = str(row["prompt_id"])
        current[prompt_id] = float(row["posterior_mean"])
        x_values.append(sequence)
        for selected_id in selected:
            histories[selected_id].append(current[selected_id])
        other_mean.append(sum(current[other_id] for other_id in other) / len(other))
        if prompt_id in update_markers:
            update_markers[prompt_id].append((sequence, current[prompt_id]))
    return x_values, histories, other_mean, update_markers


def render_figure(
    rows: Sequence[dict[str, float | int | str]],
    final_means: dict[str, float],
    selected: Sequence[str],
    output_path: Path,
) -> None:
    prompt_ids = list(final_means)
    unknown_labels = set(prompt_ids) - set(DISPLAY_LABELS)
    unknown_colors = set(selected) - set(SELECTED_COLORS)
    if unknown_labels:
        raise ValueError(f"Missing display labels for: {sorted(unknown_labels)}")
    if unknown_colors:
        raise ValueError(f"Missing selected-arm colors for: {sorted(unknown_colors)}")

    x_values, histories, other_mean, update_markers = build_histories(rows, prompt_ids, selected)

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 8.4,
            "axes.titlesize": 9.0,
            "axes.labelsize": 8.4,
            "xtick.labelsize": 8.0,
            "ytick.labelsize": 8.0,
            "legend.fontsize": 7.8,
        }
    )
    fig, (ax_curve, ax_bar) = plt.subplots(
        1,
        2,
        figsize=(7.15, 3.55),
        gridspec_kw={"width_ratios": (1.14, 1.0)},
    )
    fig.patch.set_facecolor("white")

    for prompt_id in selected:
        color = SELECTED_COLORS[prompt_id]
        ax_curve.plot(
            x_values,
            histories[prompt_id],
            color=color,
            linewidth=2.0,
            drawstyle="steps-post",
            label=DISPLAY_LABELS[prompt_id],
        )
        marker_x, marker_y = zip(*update_markers[prompt_id], strict=True)
        ax_curve.scatter(marker_x, marker_y, s=16, color=color, zorder=3)

    ax_curve.plot(
        x_values,
        other_mean,
        color=OTHER_COLOR,
        linewidth=1.8,
        linestyle="--",
        drawstyle="steps-post",
        label="Other arms (mean)",
    )
    ax_curve.axhline(0.5, color="#A7ADB5", linewidth=0.9, linestyle=":", zorder=0)
    ax_curve.set_xlim(0, len(rows))
    ax_curve.set_ylim(0.47, 0.94)
    ax_curve.set_xticks((0, 20, 40, 60, 80, 99))
    ax_curve.set_yticks((0.5, 0.6, 0.7, 0.8, 0.9))
    ax_curve.set_xlabel("Critic observation sequence")
    ax_curve.set_ylabel("Beta posterior mean")
    ax_curve.set_title("(a) Posterior trajectories", fontweight="bold", pad=5)
    ax_curve.grid(axis="y", color="#D7DBE0", linewidth=0.7)
    ax_curve.spines[["top", "right"]].set_visible(False)
    ax_curve.legend(
        loc="lower right",
        frameon=False,
        ncol=1,
        borderaxespad=0.25,
        labelspacing=0.25,
        handlelength=1.6,
    )

    ordered = sorted(final_means, key=final_means.get, reverse=True)
    values = [final_means[prompt_id] for prompt_id in ordered]
    colors = [SELECTED_COLORS.get(prompt_id, OTHER_BAR_COLOR) for prompt_id in ordered]
    bars = ax_bar.barh(range(len(ordered)), values, color=colors, height=0.66)
    ax_bar.set_yticks(range(len(ordered)), [DISPLAY_LABELS[prompt_id] for prompt_id in ordered])
    ax_bar.invert_yaxis()
    ax_bar.set_xlim(0.45, 0.96)
    ax_bar.set_xticks((0.5, 0.6, 0.7, 0.8, 0.9))
    ax_bar.set_xlabel("Final posterior mean")
    ax_bar.set_title("(b) Final posterior means", fontweight="bold", pad=5)
    ax_bar.grid(axis="x", color="#D7DBE0", linewidth=0.7)
    ax_bar.set_axisbelow(True)
    ax_bar.spines[["top", "right", "left"]].set_visible(False)
    ax_bar.tick_params(axis="y", length=0)
    for bar, value in zip(bars, values, strict=True):
        ax_bar.text(
            min(value + 0.008, 0.952),
            bar.get_y() + bar.get_height() / 2,
            f"{value:.3f}",
            va="center",
            ha="left" if value < 0.944 else "right",
            fontsize=7.8,
            color="#222222",
        )

    fig.suptitle(
        "Posterior learning for bounded prompt-policy arms",
        fontsize=10.2,
        fontweight="bold",
        y=0.985,
    )
    fig.subplots_adjust(left=0.095, right=0.99, top=0.84, bottom=0.17, wspace=0.55)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        output_path,
        dpi=300,
        facecolor="white",
        bbox_inches="tight",
        pad_inches=0.04,
        metadata={"Software": "JiraEnhancer reviewer artifact generator"},
    )
    plt.close(fig)


def render_reward_heatmap(
    rows: Sequence[dict[str, float | int | str]],
    final_means: dict[str, float],
    selected: Sequence[str],
    output_path: Path,
) -> None:
    """Render all archived critic rewards without implying live attempt telemetry."""

    ordered = sorted(final_means, key=final_means.get, reverse=True)
    rewards_by_arm: dict[str, list[float]] = {prompt_id: [] for prompt_id in ordered}
    for row in rows:
        rewards_by_arm[str(row["prompt_id"])].append(float(row["reward"]))
    observation_counts = {len(values) for values in rewards_by_arm.values()}
    if observation_counts != {11}:
        raise ValueError(f"Expected 11 critic observations per arm, found {sorted(observation_counts)}")

    matrix = [rewards_by_arm[prompt_id] for prompt_id in ordered]
    fig, ax = plt.subplots(figsize=(3.55, 2.72))
    image = ax.imshow(
        matrix,
        aspect="auto",
        interpolation="nearest",
        cmap="RdYlBu",
        vmin=-0.5,
        vmax=1.0,
    )
    ax.set_xticks(range(11), [str(index) for index in range(1, 12)])
    ax.set_yticks(range(len(ordered)), [DISPLAY_LABELS[prompt_id] for prompt_id in ordered])
    ax.set_xlabel("Audit observation within each arm", fontsize=7.8)
    ax.tick_params(axis="x", labelsize=7.0, length=0)
    ax.tick_params(axis="y", labelsize=7.1, length=0)
    for tick, prompt_id in zip(ax.get_yticklabels(), ordered, strict=True):
        if prompt_id in selected:
            tick.set_fontweight("bold")

    ax.set_xticks([value - 0.5 for value in range(1, 11)], minor=True)
    ax.set_yticks([value - 0.5 for value in range(1, len(ordered))], minor=True)
    ax.grid(which="minor", color="white", linewidth=0.65)
    ax.tick_params(which="minor", bottom=False, left=False)
    for spine in ax.spines.values():
        spine.set_color("#6B7280")
        spine.set_linewidth(0.7)

    colorbar = fig.colorbar(image, ax=ax, fraction=0.045, pad=0.025)
    colorbar.set_label("Critic reward", fontsize=7.4)
    colorbar.set_ticks([-0.5, 0.0, 0.5, 1.0])
    colorbar.ax.tick_params(labelsize=6.8, length=2)
    colorbar.outline.set_linewidth(0.6)
    fig.text(
        0.51,
        0.985,
        "Bold labels: strategies selected on held-out packets",
        ha="center",
        va="top",
        fontsize=7.2,
    )
    fig.subplots_adjust(left=0.285, right=0.91, top=0.91, bottom=0.18)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        output_path,
        dpi=300,
        facecolor="white",
        bbox_inches="tight",
        pad_inches=0.025,
        metadata={"Software": "JiraEnhancer reviewer artifact generator"},
    )
    plt.close(fig)


def main() -> None:
    args = parse_args()
    summary = load_json(args.summary_json)
    exploitation = load_json(args.exploitation_json)
    if args.observations:
        rebuild_sanitized_curve(args.observations, args.curve_csv, summary)
    rows, final_means = read_and_validate_curve(args.curve_csv, summary)
    selected = selected_arms(exploitation)
    if any(prompt_id not in final_means for prompt_id in selected):
        raise ValueError("Exploitation summary refers to an unknown prompt arm")
    render_figure(rows, final_means, selected, args.output)
    render_reward_heatmap(rows, final_means, selected, args.reward_output)
    print(f"Validated {len(rows)} critic updates across {len(final_means)} bounded prompt-policy arms.")
    print(f"Selected held-out arms: {', '.join(selected)}")
    print(f"Wrote {args.output}")
    print(f"Wrote {args.reward_output}")


if __name__ == "__main__":
    main()
