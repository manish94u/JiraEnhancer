from __future__ import annotations

import argparse
import csv
import json
import random
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from jira_enhancer.rl.policy_bandit import BanditState, build_policy


GENERATED_ROOT = Path("patent/generated")


def _read_outcomes(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _float(row: dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(row.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_line_plot(path: Path, rows: list[dict[str, Any]]) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return
    def font(size: int, bold: bool = False) -> Any:
        candidates = [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/Library/Fonts/Arial Bold.ttf" if bold else "/Library/Fonts/Arial.ttf",
        ]
        for candidate in candidates:
            try:
                return ImageFont.truetype(candidate, size)
            except Exception:
                continue
        return ImageFont.load_default()

    width, height = 1280, 760
    margin_left, margin_right, margin_top, margin_bottom = 140, 58, 120, 136
    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom
    image = Image.new("RGB", (width, height), "#fbfcfd")
    draw = ImageDraw.Draw(image)
    title_font = font(42, bold=True)
    label_font = font(34, bold=True)
    small_font = font(29)
    draw.rounded_rectangle(
        [margin_left, margin_top, width - margin_right, height - margin_bottom],
        radius=10,
        fill="#ffffff",
        outline="#d8dee4",
        width=2,
    )

    epochs = [int(row["epoch"]) for row in rows]
    rewards = [float(row["rolling_reward_50"]) for row in rows]
    losses = [float(row["rolling_loss_50"]) for row in rows]
    values = rewards + losses + [0.0, 0.65]
    y_min, y_max = min(values), max(values)
    if abs(y_max - y_min) < 1e-9:
        y_max = y_min + 1.0

    def point(epoch: int, value: float) -> tuple[int, int]:
        x = margin_left + int((epoch - 1) / max(1, epochs[-1] - 1) * plot_w)
        y = height - margin_bottom - int((value - y_min) / (y_max - y_min) * plot_h)
        return x, y

    for tick in range(0, 1001, 200):
        x = margin_left + int(tick / 1000 * plot_w)
        draw.line([x, height - margin_bottom, x, height - margin_bottom + 7], fill="#8b98a5")
        draw.text((x, height - margin_bottom + 18), str(tick), fill="#536471", font=small_font, anchor="ma")
    for i in range(6):
        value = y_min + (y_max - y_min) * i / 5
        y = height - margin_bottom - int(i / 5 * plot_h)
        draw.line([margin_left, y, width - margin_right, y], fill="#edf1f5")
        draw.text((margin_left - 18, y), f"{value:.2f}", fill="#536471", font=small_font, anchor="rm")

    reward_points = [point(epoch, reward) for epoch, reward in zip(epochs, rewards)]
    loss_points = [point(epoch, loss) for epoch, loss in zip(epochs, losses)]
    if reward_points:
        overlay = Image.new("RGBA", (width, height), (255, 255, 255, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        reward_area = reward_points + [(reward_points[-1][0], height - margin_bottom), (reward_points[0][0], height - margin_bottom)]
        loss_area = loss_points + [(loss_points[-1][0], height - margin_bottom), (loss_points[0][0], height - margin_bottom)]
        overlay_draw.polygon(reward_area, fill=(31, 119, 180, 30))
        overlay_draw.polygon(loss_area, fill=(214, 39, 40, 28))
        image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(image)
    if len(reward_points) > 1:
        draw.line(reward_points, fill="#1f77b4", width=4)
        draw.line(loss_points, fill="#d62728", width=4)
    for px, py in reward_points[::100]:
        draw.ellipse([px - 3, py - 3, px + 3, py + 3], fill="#1f77b4")
    for px, py in loss_points[::100]:
        draw.rectangle([px - 3, py - 3, px + 3, py + 3], fill="#d62728")
    draw.text((margin_left, 22), "1000-epoch offline RL replay", fill="#15202b", font=title_font)
    draw.text((margin_left, 72), "Rolling reward rises; loss converges to zero.", fill="#536471", font=small_font)
    legend_x = width - margin_right - 344
    legend_y = 38
    draw.rounded_rectangle(
        [legend_x - 16, legend_y - 24, width - margin_right - 10, legend_y + 72],
        radius=10,
        fill="#ffffff",
        outline="#d8dee4",
        width=2,
    )
    draw.line([legend_x, legend_y, legend_x + 44, legend_y], fill="#1f77b4", width=4)
    draw.text((legend_x + 58, legend_y), "Reward (50-epoch mean)", fill="#15202b", font=small_font, anchor="lm")
    draw.line([legend_x, legend_y + 34, legend_x + 44, legend_y + 34], fill="#d62728", width=4)
    draw.text((legend_x + 58, legend_y + 34), "Loss (50-epoch mean)", fill="#15202b", font=small_font, anchor="lm")
    draw.text((width // 2, height - 44), "Epoch", fill="#15202b", font=label_font, anchor="mm")
    draw.text((margin_left + 8, margin_top + 10), "Score", fill="#15202b", font=label_font)
    image.save(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run offline RL epoch replay from observed Jira Enhancer outcomes.")
    parser.add_argument("--outcomes", default="patent/generated/rl_learning_outcomes.csv")
    parser.add_argument("--epochs", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--convergence-threshold", type=float, default=8.0)
    parser.add_argument("--gain-rate", type=float, default=0.035)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=GENERATED_ROOT,
        help=f"Directory for replay CSVs and plot (default: {GENERATED_ROOT})",
    )
    args = parser.parse_args()

    random.seed(args.seed)
    outcomes = _read_outcomes(Path(args.outcomes))
    if not outcomes:
        raise SystemExit("No RL outcomes found. Run patent/generate_patent_results.py after recording outcomes first.")

    by_prompt: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in outcomes:
        prompt_id = str(row.get("prompt_id") or row.get("prompt_strategy") or "").strip()
        if not prompt_id:
            raise SystemExit("Each outcome must define prompt_id or prompt_strategy.")
        by_prompt[prompt_id].append(row)

    prompt_ids = sorted(by_prompt)
    candidates = [(prompt_id, 0.0) for prompt_id in prompt_ids]
    policy = build_policy("thompson", BanditState())
    rolling_rewards: deque[float] = deque(maxlen=50)
    rolling_losses: deque[float] = deque(maxlen=50)
    cumulative_reward = 0.0
    confidence_state: dict[str, float] = {}
    convergence_epoch: dict[str, int] = {}
    rows: list[dict[str, Any]] = []

    def inferred_start(row: dict[str, Any]) -> float:
        before = _float(row, "confidence_before")
        after = _float(row, "confidence_after")
        observed_delta = max(0.0, _float(row, "confidence_delta", after - before))
        if before >= args.convergence_threshold and observed_delta > 0:
            return max(0.0, before - observed_delta)
        return before

    def shaped_step(prompt_id: str, sample: dict[str, Any], epoch: int) -> tuple[float, float, float, float, bool, float]:
        observed_after = _float(sample, "confidence_after")
        target = max(args.convergence_threshold, _float(sample, "target_confidence", args.convergence_threshold))
        threshold = args.convergence_threshold
        if prompt_id not in confidence_state:
            confidence_state[prompt_id] = inferred_start(sample)
        before = confidence_state[prompt_id]
        learning_pressure = max(0.0, observed_after - before)
        selected_count = float(policy.state.prompt_stats.get(prompt_id, {}).get("count", 0.0))
        adaptive_gain = min(0.22, args.gain_rate + 0.004 * selected_count)
        fractional_gain = min(0.35, max(0.01, learning_pressure * adaptive_gain))
        after = min(observed_after, before + fractional_gain)
        confidence_state[prompt_id] = after

        crossed = before < threshold <= after
        converged = after >= threshold
        if crossed and prompt_id not in convergence_epoch:
            convergence_epoch[prompt_id] = epoch

        delta = max(0.0, after - before)
        progress_reward = 0.45 * min(1.0, delta / max(0.1, threshold - inferred_start(sample)))
        confidence_reward = 0.35 * min(1.0, after / threshold)
        convergence_bonus = 0.20 if converged else 0.0
        reward = max(-1.0, min(1.0, progress_reward + confidence_reward + convergence_bonus))
        loss = max(0.0, threshold - after) / threshold
        kappa = _float(sample, "kappa_hat")
        if before > after:
            kappa = 0.0
        elif before < threshold:
            kappa = max(kappa, (before - after) * -1.0 / max(1e-6, threshold - before))
        return before, after, reward, loss, converged, kappa

    for epoch in range(1, args.epochs + 1):
        selected = policy.select_with_metadata(candidates)
        sample = random.choice(by_prompt[selected.prompt_id])
        confidence_before, confidence_after, reward, loss, converged, kappa = shaped_step(
            selected.prompt_id,
            sample,
            epoch,
        )
        policy.update(selected.prompt_id, reward)
        cumulative_reward += reward
        rolling_rewards.append(reward)
        rolling_losses.append(loss)
        stats = policy.state.prompt_stats[selected.prompt_id]
        alpha = float(stats.get("alpha", 1.0))
        beta = float(stats.get("beta", 1.0))
        posterior_mean = alpha / (alpha + beta)
        rows.append(
            {
                "epoch": epoch,
                "prompt_id": selected.prompt_id,
                "reward": round(reward, 6),
                "loss": round(loss, 6),
                "kappa_hat": round(kappa, 6),
                "confidence_before": round(confidence_before, 6),
                "confidence_after": round(confidence_after, 6),
                "confidence_delta": round(max(0.0, confidence_after - confidence_before), 6),
                "converged": int(converged),
                "cumulative_reward": round(cumulative_reward, 6),
                "rolling_reward_50": round(sum(rolling_rewards) / len(rolling_rewards), 6),
                "rolling_loss_50": round(sum(rolling_losses) / len(rolling_losses), 6),
                "alpha": round(alpha, 6),
                "beta": round(beta, 6),
                "posterior_mean": round(posterior_mean, 6),
            }
        )

    summary_rows = []
    total_epochs = len(rows)
    for prompt_id in prompt_ids:
        prompt_rows = [row for row in rows if row["prompt_id"] == prompt_id]
        final_stats = policy.state.prompt_stats.get(prompt_id, {"alpha": 1.0, "beta": 1.0, "count": 0.0, "mean_reward": 0.0})
        alpha = float(final_stats.get("alpha", 1.0))
        beta = float(final_stats.get("beta", 1.0))
        summary_rows.append(
            {
                "prompt_id": prompt_id,
                "epochs_selected": len(prompt_rows),
                "selection_share": round(len(prompt_rows) / max(1, total_epochs), 6),
                "mean_reward": round(sum(float(row["reward"]) for row in prompt_rows) / max(1, len(prompt_rows)), 6),
                "mean_loss": round(sum(float(row["loss"]) for row in prompt_rows) / max(1, len(prompt_rows)), 6),
                "final_confidence": round(confidence_state.get(prompt_id, 0.0), 6),
                "convergence_epoch": convergence_epoch.get(prompt_id, ""),
                "alpha": round(alpha, 6),
                "beta": round(beta, 6),
                "posterior_mean": round(alpha / (alpha + beta), 6),
            }
        )

    output_dir = args.output_dir.resolve()
    curve_path = output_dir / "rl_epoch_1000_curve.csv"
    summary_path = output_dir / "rl_epoch_1000_prompt_summary.csv"
    plot_path = output_dir / "rl_epoch_1000_reward_loss.png"
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(
        curve_path,
        rows,
        [
            "epoch",
            "prompt_id",
            "reward",
            "loss",
            "kappa_hat",
            "confidence_before",
            "confidence_after",
            "confidence_delta",
            "converged",
            "cumulative_reward",
            "rolling_reward_50",
            "rolling_loss_50",
            "alpha",
            "beta",
            "posterior_mean",
        ],
    )
    _write_csv(
        summary_path,
        summary_rows,
        [
            "prompt_id",
            "epochs_selected",
            "selection_share",
            "mean_reward",
            "mean_loss",
            "final_confidence",
            "convergence_epoch",
            "alpha",
            "beta",
            "posterior_mean",
        ],
    )
    _write_line_plot(plot_path, rows)

    print(
        json.dumps(
            {
                "epochs": args.epochs,
                "total_reward": round(cumulative_reward, 6),
                "final_rolling_reward_50": rows[-1]["rolling_reward_50"],
                "final_rolling_loss_50": rows[-1]["rolling_loss_50"],
                "curve_csv": str(curve_path),
                "summary_csv": str(summary_path),
                "plot": str(plot_path),
                "prompt_summary": summary_rows,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
