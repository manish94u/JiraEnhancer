from __future__ import annotations

import csv
from pathlib import Path

try:
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.lines import Line2D
except ModuleNotFoundError:
    plt = None
    np = None
    Line2D = None

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

RADAR_METHOD_STYLES = {
    "B0_manual": {"color": "#6B7280", "marker": "o", "label": "B0"},
    "B1_template_rules": {"color": "#4C78A8", "marker": "s", "label": "B1"},
    "B2_generic_llm": {"color": "#D27432", "marker": "^", "label": "B2"},
    "B3_jira_enhancer": {"color": "#08775B", "marker": "D", "label": "B3"},
}

RADAR_DATASET_STYLES = {
    "internal_5000": {"linestyle": "-", "linewidth": 2.0, "filled": True},
    "swebench_lite_test": {"linestyle": (0, (4, 2)), "linewidth": 1.6, "filled": False},
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
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
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


def _annotated_radar(ax: plt.Axes, rows: list[dict[str, float | str]]) -> dict[tuple[str, str], list[float]]:
    """Overlay B0-B3 profiles; callers place grouped numeric labels around the radar."""

    metrics = [
        ("Quality", "quality_index"),
        ("Efficiency", "efficiency_index"),
        ("Governance", "governance_index"),
        ("Composite", "composite_index"),
    ]
    angles = np.linspace(0, 2 * np.pi, len(metrics), endpoint=False)
    closed_angles = np.concatenate([angles, [angles[0]]])
    row_lookup = {(str(row["method"]), str(row["dataset"])): row for row in rows}
    plotted: dict[tuple[str, str], list[float]] = {}

    for method, method_style in RADAR_METHOD_STYLES.items():
        for dataset, dataset_style in RADAR_DATASET_STYLES.items():
            key = (method, dataset)
            row = row_lookup[key]
            values = [float(row[field]) for _, field in metrics]
            plotted[key] = values
            closed_values = np.array(values + [values[0]])
            color = str(method_style["color"])
            facecolor = color if bool(dataset_style["filled"]) else "white"
            ax.plot(
                closed_angles,
                closed_values,
                color=color,
                linewidth=float(dataset_style["linewidth"]),
                linestyle=dataset_style["linestyle"],
                marker=str(method_style["marker"]),
                markersize=5.0,
                markerfacecolor=facecolor,
                markeredgecolor=color,
                markeredgewidth=1.0,
                label="_nolegend_",
                zorder=3 if bool(dataset_style["filled"]) else 4,
            )

    ax.set_theta_offset(np.pi / 2)
    ax.set_theta_direction(-1)
    ax.set_xticks(angles)
    ax.set_xticklabels(["", "", "", ""])
    ax.set_ylim(0, 110)
    ax.set_yticks([20, 40, 60, 80, 100])
    ax.set_yticklabels(["20", "40", "60", "80", "100"], fontsize=9.5, color="#536471")
    ax.set_rlabel_position(32)
    ax.grid(color="#D7DBE0", linewidth=0.70)
    ax.spines["polar"].set_color("#8B949E")
    return plotted


def _plot(rows: list[dict[str, float | str]]) -> None:
    method_order = ["B0_manual", "B1_template_rules", "B2_generic_llm", "B3_jira_enhancer"]
    dataset_order = ["internal_5000", "swebench_lite_test"]

    rows_idx = {(str(r["dataset"]), str(r["method"])): r for r in rows}
    if plt is None or np is None:
        width, height = 1800, 720
        image = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(image)
        panel_font = plot_util._font(31, bold=True)
        label_font = plot_util._font(28)
        value_font = plot_util._font(22)

        bar_area = (118, 100, 750, 590)
        plot_util._draw_y_axis(draw, bar_area, 0, 100, "Composite Index")
        left, top, right, bottom = bar_area
        group_w = (right - left) / len(method_order)
        bar_w = 42
        colors = {"internal_5000": "#4F7CAC", "swebench_lite_test": "#B87733"}

        def draw_vertical_value(center_x: float, center_y: float, text: str) -> None:
            layer = Image.new("RGBA", (96, 34), (255, 255, 255, 0))
            layer_draw = ImageDraw.Draw(layer)
            layer_draw.text((48, 17), text, fill="white", font=value_font, anchor="mm")
            rotated = layer.rotate(90, expand=True, resample=Image.Resampling.BICUBIC)
            image.paste(
                rotated,
                (int(center_x - rotated.width / 2), int(center_y - rotated.height / 2)),
                rotated,
            )

        for idx, method in enumerate(method_order):
            center = left + (idx + 0.5) * group_w
            for d_idx, dataset in enumerate(dataset_order):
                value = float(rows_idx[(dataset, method)]["composite_index"])
                x0 = center + (-bar_w - 4 if d_idx == 0 else 4)
                x1 = x0 + bar_w
                y0 = bottom - value / 100.0 * (bottom - top)
                draw.rectangle((x0, y0, x1, bottom), fill=colors[dataset])
                draw_vertical_value((x0 + x1) / 2, (y0 + bottom) / 2, f"{value:.1f}")
            draw.text((center, bottom + 18), METHOD_LABELS[method], fill="#1f2937", font=label_font, anchor="ma")
        draw.text((78, 22), "(A) Composite index by input source", fill="#1f2937", font=panel_font)
        dataset_legend_font = plot_util._font(24)
        for idx, dataset in enumerate(dataset_order):
            dataset_legend_x = 130 + idx * 330
            legend_y = 660
            draw.rectangle((dataset_legend_x, legend_y, dataset_legend_x + 24, legend_y + 18), fill=colors[dataset])
            legend_label = "Internal generated units" if dataset == "internal_5000" else "SWE-bench Lite issue text"
            draw.text((dataset_legend_x + 34, legend_y - 3), legend_label, fill="#1f2937", font=dataset_legend_font)

        import math

        radar_center = (1360, 395)
        radar_radius = 155
        metrics = [
            ("Quality", "quality_index"),
            ("Efficiency", "efficiency_index"),
            ("Governance", "governance_index"),
            ("Composite", "composite_index"),
        ]
        angles = [-math.pi / 2 + 2 * math.pi * idx / len(metrics) for idx in range(len(metrics))]
        draw.text((960, 22), "(B) B0-B3 component profiles", fill="#1f2937", font=panel_font)

        def radar_point(angle: float, value: float) -> tuple[float, float]:
            return (
                radar_center[0] + math.cos(angle) * radar_radius * value / 110.0,
                radar_center[1] + math.sin(angle) * radar_radius * value / 110.0,
            )

        def draw_marker(x: float, y: float, marker: str, color: str, filled: bool) -> None:
            fill = color if filled else "white"
            if marker == "o":
                draw.ellipse((x - 8, y - 8, x + 8, y + 8), fill=fill, outline=color, width=2)
                return
            if marker == "s":
                draw.rectangle((x - 8, y - 8, x + 8, y + 8), fill=fill, outline=color, width=2)
                return
            if marker == "^":
                points = [(x, y - 8), (x - 9, y + 7), (x + 9, y + 7)]
            else:
                points = [(x, y - 9), (x - 9, y), (x, y + 9), (x + 9, y)]
            draw.polygon(points, fill=fill, outline=color)

        for ring in [20, 40, 60, 80, 100]:
            draw.polygon([radar_point(angle, ring) for angle in angles], outline="#D7DBE0")
            ring_x, ring_y = radar_point(angles[1] - 0.18, ring)
            draw.text((ring_x, ring_y), str(ring), fill="#536471", font=plot_util._font(20), anchor="mm")
        for (metric_label, _), angle in zip(metrics, angles, strict=True):
            end = radar_point(angle, 100)
            draw.line((radar_center, end), fill="#D7DBE0", width=2)

        plotted: dict[tuple[str, str], list[float]] = {}
        for method, method_style in RADAR_METHOD_STYLES.items():
            for dataset, dataset_style in RADAR_DATASET_STYLES.items():
                key = (method, dataset)
                values = [float(rows_idx[(dataset, method)][field]) for _, field in metrics]
                plotted[key] = values
                points = [radar_point(angle, value) for angle, value in zip(angles, values, strict=True)]
                draw.line(
                    points + [points[0]],
                    fill=str(method_style["color"]),
                    width=4 if bool(dataset_style["filled"]) else 2,
                )
                for point in points:
                    draw_marker(
                        point[0],
                        point[1],
                        str(method_style["marker"]),
                        str(method_style["color"]),
                        bool(dataset_style["filled"]),
                    )

        metric_group_positions = [
            (1360, 135, "mm"),
            (1585, 300, "lm"),
            (1360, 575, "mm"),
            (1135, 300, "rm"),
        ]
        group_title_font = plot_util._font(24, bold=True)
        for metric_idx, ((metric_label, _), (x_pos, y_pos, anchor)) in enumerate(
            zip(metrics, metric_group_positions, strict=True)
        ):
            draw.text((x_pos, y_pos), metric_label, fill="#1F2937", font=group_title_font, anchor=anchor)
            for row_idx, method in enumerate(method_order):
                internal = plotted[(method, "internal_5000")][metric_idx]
                public = plotted[(method, "swebench_lite_test")][metric_idx]
                method_style = RADAR_METHOD_STYLES[method]
                draw.text(
                    (x_pos, y_pos + 29 + row_idx * 25),
                    f"{method_style['label']}  {internal:.1f} / {public:.1f}",
                    fill=str(method_style["color"]),
                    font=value_font,
                    anchor=anchor,
                )

        legend_x, legend_y = 1010, 80
        for idx, method in enumerate(method_order):
            method_style = RADAR_METHOD_STYLES[method]
            lx = legend_x + idx * 155
            draw_marker(lx, legend_y, str(method_style["marker"]), str(method_style["color"]), True)
            draw.text(
                (lx + 18, legend_y),
                str(method_style["label"]),
                fill="#1f2937",
                font=dataset_legend_font,
                anchor="lm",
            )
        draw.text(
            (1360, 108),
            "filled/solid: internal   hollow/thin: public text   values: internal / public",
            fill="#536471",
            font=plot_util._font(20),
            anchor="mm",
        )

        OUT_PLOT.parent.mkdir(parents=True, exist_ok=True)
        image.save(OUT_PLOT)
        return

    fig = plt.figure(figsize=(11.2, 4.8))
    gs = fig.add_gridspec(1, 2, width_ratios=[0.95, 1.35], wspace=0.16)

    ax1 = fig.add_subplot(gs[0, 0])
    x = np.arange(len(method_order))
    w = 0.32
    colors = {"internal_5000": "#4F7CAC", "swebench_lite_test": "#B87733"}

    for i, dataset in enumerate(dataset_order):
        vals = [float(rows_idx[(dataset, method)]["composite_index"]) for method in method_order]
        shift = -0.18 if i == 0 else 0.18
        legend_label = "Internal generated units" if dataset == "internal_5000" else "SWE-bench Lite issue text"
        bars = ax1.bar(x + shift, vals, width=w, label=legend_label, color=colors[dataset], alpha=0.90)
        for b in bars:
            h = b.get_height()
            ax1.text(
                b.get_x() + b.get_width() / 2,
                h * 0.50,
                f"{h:.1f}",
                ha="center",
                va="center",
                fontsize=10.8,
                color="white",
                fontweight="semibold",
                rotation=90,
                rotation_mode="anchor",
                clip_on=True,
            )

    ax1.set_xticks(x)
    ax1.set_xticklabels([METHOD_LABELS[m] for m in method_order], fontsize=13.0)
    ax1.set_ylabel("Composite index", fontsize=13.0)
    ax1.set_title("(A) Composite index by input source", fontsize=14.8, loc="left", y=1.06)
    ax1.tick_params(axis="y", labelsize=12.0, length=0)
    ax1.set_ylim(0, 100)
    ax1.grid(axis="y", color="#D7DBE0", linewidth=0.75)
    ax1.set_axisbelow(True)
    ax1.spines[["top", "right", "left"]].set_visible(False)
    ax1.spines["bottom"].set_color("#8B949E")
    ax1.legend(fontsize=10.5, loc="upper left", frameon=False)

    panel2 = fig.add_subplot(gs[0, 1])
    panel2.set_axis_off()

    fig.subplots_adjust(left=0.075, right=0.985, top=0.77, bottom=0.12)
    panel_box = panel2.get_position()
    radar_box = [
        panel_box.x0 + panel_box.width * 0.26,
        panel_box.y0 + panel_box.height * 0.27,
        panel_box.width * 0.48,
        panel_box.height * 0.50,
    ]
    ax2 = fig.add_axes(radar_box, projection="polar")
    plotted = _annotated_radar(ax2, rows)

    panel2.text(0.5, 1.17, "(B) B0-B3 component profiles", ha="center", va="bottom", fontsize=15.0)
    method_handles = [
        Line2D(
            [0],
            [0],
            color=str(style["color"]),
            linewidth=2.0,
            marker=str(style["marker"]),
            markersize=5.8,
            markerfacecolor=str(style["color"]),
            markeredgecolor=str(style["color"]),
            label=str(style["label"]),
        )
        for style in RADAR_METHOD_STYLES.values()
    ]
    method_legend = panel2.legend(
        handles=method_handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 1.13),
        ncol=4,
        frameon=False,
        fontsize=10.2,
        handletextpad=0.35,
        columnspacing=0.90,
        borderaxespad=0.0,
    )
    panel2.add_artist(method_legend)
    source_handles = [
        Line2D(
            [0],
            [0],
            color="#374151",
            linewidth=2.0,
            linestyle="-",
            marker="o",
            markersize=5.2,
            markerfacecolor="#374151",
            markeredgecolor="#374151",
            label="Internal (solid/filled)",
        ),
        Line2D(
            [0],
            [0],
            color="#374151",
            linewidth=1.6,
            linestyle=(0, (4, 2)),
            marker="o",
            markersize=5.2,
            markerfacecolor="white",
            markeredgecolor="#374151",
            label="Public text (dashed/hollow)",
        ),
    ]
    panel2.legend(
        handles=source_handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 1.075),
        ncol=2,
        frameon=False,
        fontsize=10.2,
        handletextpad=0.45,
        columnspacing=1.20,
        borderaxespad=0.0,
    )
    metric_groups = [
        ("Quality", 0.50, 0.99, "center"),
        ("Efficiency", 0.83, 0.65, "left"),
        ("Governance", 0.50, 0.22, "center"),
        ("Composite", 0.17, 0.65, "right"),
    ]
    for metric_idx, (metric_label, x_pos, y_pos, alignment) in enumerate(metric_groups):
        panel2.text(
            x_pos,
            y_pos,
            metric_label,
            ha=alignment,
            va="center",
            fontsize=11.5,
            fontweight="semibold",
            color="#1F2937",
        )
        for row_idx, method in enumerate(method_order):
            internal = plotted[(method, "internal_5000")][metric_idx]
            public = plotted[(method, "swebench_lite_test")][metric_idx]
            method_style = RADAR_METHOD_STYLES[method]
            panel2.text(
                x_pos,
                y_pos - 0.052 - row_idx * 0.050,
                f"{method_style['label']}  {internal:.1f} / {public:.1f}",
                ha=alignment,
                va="center",
                fontsize=10.8,
                color=str(method_style["color"]),
                bbox={"boxstyle": "round,pad=0.08", "facecolor": "white", "edgecolor": "none", "alpha": 0.97},
            )
    OUT_PLOT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_PLOT, dpi=300, facecolor="white")
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
