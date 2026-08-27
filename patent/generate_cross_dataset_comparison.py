from __future__ import annotations

import csv
from pathlib import Path

try:
    import matplotlib.pyplot as plt
    import numpy as np
except ModuleNotFoundError:
    plt = None
    np = None

from PIL import Image, ImageDraw

import generate_patent_results as plot_util

REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATED_ROOT = REPO_ROOT / "patent" / "generated"

INTERNAL_PATH = GENERATED_ROOT / "comparative_5000_method_summary.csv"
PUBLIC_PATH = GENERATED_ROOT / "public_benchmark_swebench_lite_method_summary.csv"

OUT_CSV = GENERATED_ROOT / "comparative_cross_dataset_summary.csv"
OUT_TEX = GENERATED_ROOT / "comparative_cross_dataset_table.tex"
OUT_PLOT = GENERATED_ROOT / "comparative_cross_dataset_multiplot.png"

DATASET_LABELS = {
    "internal_5000": "Internal 5,000-unit run",
    "swebench_lite_test": "SWE-bench Lite test",
}

METHOD_LABELS = {
    "B0_manual": "B0",
    "B1_template_rules": "B1",
    "B2_generic_llm": "B2",
    "B3_jira_enhancer": "B3",
}


def _read_rows(path: Path, dataset_key: str) -> list[dict[str, float | str]]:
    rows: list[dict[str, float | str]] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(
                {
                    "dataset": dataset_key,
                    "dataset_label": DATASET_LABELS[dataset_key],
                    "method": row["method"],
                    "method_short": METHOD_LABELS[row["method"]],
                    "n_runs": int(float(row["n_runs"])),
                    "quality_index": float(row["quality_index"]),
                    "efficiency_index": float(row["efficiency_index"]),
                    "governance_index": float(row["governance_index"]),
                    "composite_index": float(row["composite_index"]),
                    "mean_time_to_ready_minutes": float(row["mean_time_to_ready_minutes"]),
                }
            )
    return rows


def _add_deltas(rows: list[dict[str, float | str]]) -> list[dict[str, float | str]]:
    b3_by_dataset: dict[str, float] = {}
    for row in rows:
        if row["method"] == "B3_jira_enhancer":
            b3_by_dataset[str(row["dataset"])] = float(row["composite_index"])

    out: list[dict[str, float | str]] = []
    for row in rows:
        dataset = str(row["dataset"])
        delta = b3_by_dataset[dataset] - float(row["composite_index"])
        enriched = dict(row)
        enriched["delta_to_b3_composite"] = delta
        out.append(enriched)
    return out


def _write_csv(rows: list[dict[str, float | str]]) -> None:
    fieldnames = [
        "dataset",
        "dataset_label",
        "method",
        "method_short",
        "n_runs",
        "quality_index",
        "efficiency_index",
        "governance_index",
        "composite_index",
        "mean_time_to_ready_minutes",
        "delta_to_b3_composite",
    ]
    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_latex_table(rows: list[dict[str, float | str]]) -> None:
    ordered = sorted(rows, key=lambda r: (str(r["dataset"]), str(r["method_short"])))

    lines: list[str] = []
    lines.append("\\begin{tabular}{llrrrrrr}")
    lines.append("\\hline")
    lines.append("Dataset & Method & N & Quality & Efficiency & Governance & Composite & $\\Delta$ to B3 \\\\")
    lines.append("\\hline")

    for row in ordered:
        dataset = str(row["dataset_label"])
        method = str(row["method_short"])
        n_runs = int(row["n_runs"])
        quality = float(row["quality_index"])
        eff = float(row["efficiency_index"])
        gov = float(row["governance_index"])
        comp = float(row["composite_index"])
        delta = float(row["delta_to_b3_composite"])
        lines.append(
            f"{dataset} & {method} & {n_runs} & {quality:.2f} & {eff:.2f} & {gov:.2f} & {comp:.2f} & {delta:+.2f} \\\\"
        )

    lines.append("\\hline")
    lines.append("\\end{tabular}")
    OUT_TEX.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _radar(ax: plt.Axes, rows: list[dict[str, float | str]]) -> None:
    categories = ["Quality", "Efficiency", "Governance", "Composite"]
    angles = np.linspace(0, 2 * np.pi, len(categories), endpoint=False)
    angles = np.concatenate([angles, [angles[0]]])

    style = {
        ("B2_generic_llm", "internal_5000"): ("#d17b0f", "B2 (Internal)"),
        ("B3_jira_enhancer", "internal_5000"): ("#0b6e4f", "B3 (Internal)"),
        ("B2_generic_llm", "swebench_lite_test"): ("#e7b46a", "B2 (SWE-bench Lite)"),
        ("B3_jira_enhancer", "swebench_lite_test"): ("#5c9f8d", "B3 (SWE-bench Lite)"),
    }

    for row in rows:
        key = (str(row["method"]), str(row["dataset"]))
        if key not in style:
            continue
        color, label = style[key]
        values = np.array(
            [
                float(row["quality_index"]),
                float(row["efficiency_index"]),
                float(row["governance_index"]),
                float(row["composite_index"]),
            ]
        )
        values = np.concatenate([values, [values[0]]])
        ax.plot(angles, values, color=color, linewidth=2.0, label=label)
        ax.fill(angles, values, color=color, alpha=0.12)

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(categories, fontsize=16)
    ax.set_ylim(0, 100)
    ax.set_yticks([20, 40, 60, 80, 100])
    ax.set_yticklabels(["20", "40", "60", "80", "100"], fontsize=14)
    ax.grid(alpha=0.35)


def _plot(rows: list[dict[str, float | str]]) -> None:
    method_order = ["B0_manual", "B1_template_rules", "B2_generic_llm", "B3_jira_enhancer"]
    dataset_order = ["internal_5000", "swebench_lite_test"]

    rows_idx = {(str(r["dataset"]), str(r["method"])): r for r in rows}
    if plt is None or np is None:
        width, height = 1450, 760
        image = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(image)
        title_font = plot_util._font(40, bold=True)
        subtitle_font = plot_util._font(31)
        title = "Cross-Dataset Comparative View"
        title_width, _ = plot_util._text_size(draw, title, title_font)
        draw.text(((width - title_width) / 2, 16), title, fill="#172026", font=title_font)
        subtitle = "Internal 5,000-Unit and SWE-bench Lite Test"
        subtitle_width, _ = plot_util._text_size(draw, subtitle, subtitle_font)
        draw.text(((width - subtitle_width) / 2, 64), subtitle, fill="#536471", font=subtitle_font)

        bar_area = (132, 164, 780, 634)
        plot_util._draw_y_axis(draw, bar_area, 0, 100, "Composite Index")
        left, top, right, bottom = bar_area
        group_w = (right - left) / len(method_order)
        bar_w = 42
        colors = {"internal_5000": "#0b6e4f", "swebench_lite_test": "#d17b0f"}
        label_font = plot_util._font(29, bold=True)
        value_font = plot_util._font(25, bold=True)
        for idx, method in enumerate(method_order):
            center = left + (idx + 0.5) * group_w
            for d_idx, dataset in enumerate(dataset_order):
                value = float(rows_idx[(dataset, method)]["composite_index"])
                x0 = center + (-bar_w - 4 if d_idx == 0 else 4)
                x1 = x0 + bar_w
                y0 = bottom - value / 100.0 * (bottom - top)
                draw.rectangle((x0, y0, x1, bottom), fill=colors[dataset])
                draw.text(((x0 + x1) / 2, y0 - 22), f"{value:.1f}", fill="#1f2937", font=value_font, anchor="mm")
            draw.text((center, bottom + 18), METHOD_LABELS[method], fill="#1f2937", font=label_font, anchor="ma")
        draw.text((left, 118), "(A) Composite: Internal vs Public", fill="#1f2937", font=plot_util._font(30, bold=True))
        dataset_legend_font = plot_util._font(23)
        draw.rounded_rectangle(
            (150, 690, 785, 744),
            radius=8,
            fill="#ffffff",
            outline="#d8dee4",
            width=2,
        )
        for idx, dataset in enumerate(dataset_order):
            dataset_legend_x = 168 + idx * 310
            legend_y = 706
            draw.rectangle((dataset_legend_x, legend_y, dataset_legend_x + 24, legend_y + 18), fill=colors[dataset])
            draw.text((dataset_legend_x + 34, legend_y - 3), DATASET_LABELS[dataset], fill="#1f2937", font=dataset_legend_font)

        radar_center = (1110, 402)
        radar_radius = 192
        categories = ["Quality", "Efficiency", "Governance", "Composite"]
        angles = [2 * 3.141592653589793 * idx / len(categories) for idx in range(len(categories))]
        style = {
            ("B2_generic_llm", "internal_5000"): ("#d17b0f", "B2 (Internal)"),
            ("B3_jira_enhancer", "internal_5000"): ("#0b6e4f", "B3 (Internal)"),
            ("B2_generic_llm", "swebench_lite_test"): ("#e7b46a", "B2 (SWE-bench Lite)"),
            ("B3_jira_enhancer", "swebench_lite_test"): ("#5c9f8d", "B3 (SWE-bench Lite)"),
        }

        def pt(angle: float, value: float) -> tuple[float, float]:
            return (
                radar_center[0] + math.cos(angle - math.pi / 2) * radar_radius * value / 100.0,
                radar_center[1] + math.sin(angle - math.pi / 2) * radar_radius * value / 100.0,
            )

        import math

        for ring in [20, 40, 60, 80, 100]:
            draw.polygon([pt(angle, ring) for angle in angles], outline="#d1d5db")
        for angle, label in zip(angles, categories):
            draw.line((radar_center, pt(angle, 100)), fill="#9ca3af", width=1)
            draw.text(pt(angle, 116), label, fill="#1f2937", font=plot_util._font(27, bold=True), anchor="mm")
        for row in rows:
            key = (str(row["method"]), str(row["dataset"]))
            if key not in style:
                continue
            color, _ = style[key]
            values = [
                float(row["quality_index"]),
                float(row["efficiency_index"]),
                float(row["governance_index"]),
                float(row["composite_index"]),
            ]
            points = [pt(angle, value) for angle, value in zip(angles, values)]
            draw.line(points + [points[0]], fill=color, width=4)
        draw.text((842, 118), "(B) B2 vs B3 Radar", fill="#1f2937", font=plot_util._font(30, bold=True))
        legend_x, legend_y = 842, 662
        for idx, (_, (color, label)) in enumerate(style.items()):
            lx = legend_x + (idx % 2) * 286
            ly = legend_y + (idx // 2) * 36
            draw.line((lx, ly, lx + 30, ly), fill=color, width=6)
            draw.text((lx + 42, ly), label, fill="#1f2937", font=plot_util._font(25), anchor="lm")

        OUT_PLOT.parent.mkdir(parents=True, exist_ok=True)
        image.save(OUT_PLOT)
        return

    fig = plt.figure(figsize=(13, 5.2))
    gs = fig.add_gridspec(1, 2, width_ratios=[1.2, 1.0])

    ax1 = fig.add_subplot(gs[0, 0])
    x = np.arange(len(method_order))
    w = 0.36
    colors = {"internal_5000": "#0b6e4f", "swebench_lite_test": "#d17b0f"}

    for i, dataset in enumerate(dataset_order):
        vals = [float(rows_idx[(dataset, method)]["composite_index"]) for method in method_order]
        shift = -w / 2 if i == 0 else w / 2
        bars = ax1.bar(x + shift, vals, width=w, label=DATASET_LABELS[dataset], color=colors[dataset], alpha=0.88)
        for b in bars:
            h = b.get_height()
            ax1.text(b.get_x() + b.get_width() / 2, h + 0.5, f"{h:.1f}", ha="center", va="bottom", fontsize=14)

    ax1.set_xticks(x)
    ax1.set_xticklabels([METHOD_LABELS[m] for m in method_order], fontsize=15)
    ax1.set_ylabel("Composite Index", fontsize=16)
    ax1.set_title("(A) Composite Across Internal vs Public Benchmark", fontsize=17)
    ax1.tick_params(axis="y", labelsize=14)
    ax1.set_ylim(0, 100)
    ax1.grid(axis="y", alpha=0.28)
    ax1.legend(fontsize=14, loc="upper left")

    ax2 = fig.add_subplot(gs[0, 1], projection="polar")
    _radar(ax2, rows)
    ax2.set_title("(B) B2 vs B3 Radar (Internal + SWE-bench Lite)", pad=22, fontsize=17)
    ax2.legend(loc="upper right", bbox_to_anchor=(1.35, 1.15), fontsize=14)

    fig.suptitle("Cross-Dataset Comparative View: Internal 5,000-Unit and SWE-bench Lite Test", y=1.04, fontsize=18)
    fig.tight_layout()
    OUT_PLOT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_PLOT, dpi=220, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    rows = _read_rows(INTERNAL_PATH, "internal_5000") + _read_rows(PUBLIC_PATH, "swebench_lite_test")
    rows = _add_deltas(rows)
    _write_csv(rows)
    _write_latex_table(rows)
    _plot(rows)
    print(f"Wrote {OUT_CSV}")
    print(f"Wrote {OUT_TEX}")
    print(f"Wrote {OUT_PLOT}")


if __name__ == "__main__":
    main()
