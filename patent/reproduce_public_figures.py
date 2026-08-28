#!/usr/bin/env python3
"""Reproduce public Jira Enhancer figures from de-identified inputs.

This entry point never reads ``runtime/`` or connector logs.  The confidence
figure uses released endpoint telemetry.  The B0--B3 comparison figures use the
released formula-expanded sensitivity table and must not be interpreted as
20,000 independent production observations.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

try:
    import generate_patent_results as plots
except ModuleNotFoundError as exc:  # pragma: no cover - dependency error path
    if exc.name in {"PIL", "Pillow"}:
        raise SystemExit("Figure reproduction requires Pillow. Install the reviewer dependencies first.") from exc
    raise


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
DEFAULT_ARTIFACT_DIR = HERE / "artifacts" / "tosem"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "outputs" / "reproduced" / "public_figures"
ALL_FIGURES = (
    "confidence",
    "raincloud",
    "violin",
    "component",
    "timeline",
    "composite",
    "laplace",
)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require(path: Path) -> None:
    if not path.is_file():
        raise SystemExit(f"Required public input is missing: {path}")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reproduce public figures without private Jira or Confluence telemetry."
    )
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=DEFAULT_ARTIFACT_DIR,
        help=f"Directory containing released CSV inputs (default: {DEFAULT_ARTIFACT_DIR})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for reproduced figures (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--figures",
        nargs="+",
        choices=("all",) + ALL_FIGURES,
        default=["all"],
        help="Figure groups to render. Use 'all' for the complete public subset.",
    )
    parser.add_argument(
        "--no-manifest",
        action="store_true",
        help="Do not write public_figure_manifest.json in the output directory.",
    )
    args = parser.parse_args()

    artifact_dir = args.artifact_dir.resolve()
    output_dir = args.output_dir.resolve()
    selected = set(ALL_FIGURES if "all" in args.figures else args.figures)
    outcomes_path = artifact_dir / "anonymized_live_story_outcomes.csv"
    comparative_path = artifact_dir / "comparative_5000_units.csv"
    if "confidence" in selected:
        _require(outcomes_path)
    if selected.intersection({"raincloud", "violin", "component", "timeline", "composite"}):
        _require(comparative_path)

    output_dir.mkdir(parents=True, exist_ok=True)
    outcomes = _read_csv(outcomes_path) if outcomes_path.is_file() else []
    comparative = _read_csv(comparative_path) if comparative_path.is_file() else []
    method_summary = plots._method_summary(comparative) if comparative else []
    timing_summary = plots._timing_summary(comparative) if comparative else []
    generated: list[Path] = []

    renderers: dict[str, Callable[[], None]] = {
        "confidence": lambda: plots._save_pil_live_story_confidence_radial_chart(
            output_dir / "performance_confidence_readiness.png",
            sorted(outcomes, key=lambda row: str(row["story_id"])),
        ),
        "raincloud": lambda: plots._write_boxplot_png(
            output_dir / "comparative_5000_boxplot.png", comparative
        ),
        "violin": lambda: plots._write_violin_png(
            output_dir / "comparative_5000_violin_main.png", comparative, compact=True
        ),
        "component": lambda: plots._write_component_profile_png(
            output_dir / "comparative_5000_component_profile_main.png", method_summary, compact=True
        ),
        "timeline": lambda: plots._write_execution_timeline_png(
            output_dir / "comparative_5000_execution_timeline_main.png",
            timing_summary,
            compact=True,
            force_pil=True,
        ),
        "composite": lambda: plots._write_comparative_composite_png(
            output_dir / "comparative_composite.png", method_summary
        ),
        "laplace": lambda: plots._write_laplace_convergence_surface(
            output_dir / "laplace_convergence_surface.png",
            output_dir / "laplace_convergence_surface.csv",
            force_pil=True,
        ),
    }
    expected_outputs = {
        "confidence": ["performance_confidence_readiness.png"],
        "raincloud": ["comparative_5000_boxplot.png"],
        "violin": ["comparative_5000_violin_main.png"],
        "component": ["comparative_5000_component_profile_main.png"],
        "timeline": ["comparative_5000_execution_timeline_main.png"],
        "composite": ["comparative_composite.png"],
        "laplace": ["laplace_convergence_surface.png", "laplace_convergence_surface.csv"],
    }
    for figure in ALL_FIGURES:
        if figure not in selected:
            continue
        renderers[figure]()
        for filename in expected_outputs[figure]:
            output = output_dir / filename
            if not output.is_file() or output.stat().st_size == 0:
                raise SystemExit(f"Renderer did not create a non-empty output: {output}")
            generated.append(output)

    result: dict[str, Any] = {
        "artifact_dir": str(artifact_dir),
        "output_dir": str(output_dir),
        "selected_figures": [name for name in ALL_FIGURES if name in selected],
        "inputs": {},
        "outputs": {path.name: _sha256(path) for path in generated},
        "interpretation_boundaries": {
            "confidence": "De-identified terminal workflow telemetry; not an independent human quality rating.",
            "comparative": (
                "Formula-expanded sensitivity units used to inspect the declared scoring model; "
                "not 20,000 independent production observations."
            ),
            "laplace": "Analytical response surface L(t, kappa)=exp(-kappa*t).",
        },
    }
    for path in (outcomes_path, comparative_path):
        if path.is_file() and (
            path == outcomes_path and "confidence" in selected
            or path == comparative_path
            and selected.intersection({"raincloud", "violin", "component", "timeline", "composite"})
        ):
            result["inputs"][path.name] = _sha256(path)

    if not args.no_manifest:
        manifest_path = output_dir / "public_figure_manifest.json"
        _write_json(manifest_path, result)
        result["manifest"] = str(manifest_path)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
