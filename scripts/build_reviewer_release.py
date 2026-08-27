#!/usr/bin/env python3
"""Build a clean Jira Enhancer reviewer release from an explicit allowlist.

The private development tree contains runtime state and restricted study data.
This script copies only the reviewed files listed below. It refuses to merge
into a non-empty destination and never copies Git history.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

TOP_LEVEL_FILES = (
    ".env.example",
    ".gitignore",
    "Makefile",
    "README.md",
    "docs/REPRODUCIBILITY.md",
    "pyproject.toml",
    "requirements-reviewer.txt",
)

PATENT_FILES = (
    "analyze_tosem_live_evidence.py",
    "generate_cross_dataset_comparison.py",
    "generate_patent_results.py",
    "jiraenhancer_tosem_acm.pdf",
    "jiraenhancer_tosem_acm.tex",
    "prepare_public_comparative_artifact.py",
    "references_2020_plus.bib",
    "reproduce_public_figures.py",
    "run_public_benchmark_eval.py",
    "run_rl_epoch_simulation.py",
    "run_source_bound_transaction_experiment.py",
    "verify_public_artifact.py",
)

IMAGE_FILES = (
    "agentic_module_epic_breaker.png",
    "agentic_module_prompt_generator.png",
    "agentic_module_rl_controller.png",
    "agentic_module_story_issue_enhancer.png",
    "system_architecture.drawio",
    "system_architecture_embedded.png",
    "system_control_flow_embedded.png",
)

GENERATED_FILES = (
    "comparative_5000_boxplot.png",
    "comparative_5000_execution_timeline_main.png",
    "comparative_5000_method_summary.csv",
    "comparative_5000_radar_main.png",
    "comparative_5000_significance_global.csv",
    "comparative_5000_significance_pairwise.csv",
    "comparative_5000_significance_table.tex",
    "comparative_5000_stats.csv",
    "comparative_5000_timing_summary.csv",
    "comparative_5000_violin_main.png",
    "comparative_composite.png",
    "comparative_cross_dataset_multiplot.png",
    "comparative_cross_dataset_summary.csv",
    "comparative_cross_dataset_table.tex",
    "laplace_convergence_surface.csv",
    "laplace_convergence_surface.png",
    "notation_table.tex",
    "performance_confidence_readiness.png",
    "public_benchmark_swebench_lite_before_after.csv",
    "public_benchmark_swebench_lite_before_after_table.tex",
    "public_benchmark_swebench_lite_method_summary.csv",
    "public_benchmark_swebench_lite_method_summary_table.tex",
    "public_benchmark_swebench_lite_significance_global.csv",
    "public_benchmark_swebench_lite_significance_pairwise.csv",
    "public_benchmark_swebench_lite_significance_table.tex",
    "rl_confidence_lift.png",
    "rl_epoch_1000_curve.csv",
    "rl_epoch_1000_prompt_summary.csv",
    "rl_epoch_1000_reward_loss.png",
    "rl_learning_attempt_summary.csv",
    "rl_learning_prompt_summary.csv",
    "rl_learning_reward_loss.png",
    "source_bound_transaction_ablation.png",
    "source_bound_transaction_ablation.svg",
    "source_bound_transaction_checksums.json",
    "source_bound_transaction_evidence.md",
    "source_bound_transaction_metadata.json",
    "source_bound_transaction_pairwise.csv",
    "source_bound_transaction_scenario_summary.csv",
    "source_bound_transaction_summary.csv",
    "source_bound_transaction_summary_table.tex",
    "source_bound_transaction_trials.csv",
    "tosem_cumulative_live_story_evidence_table.tex",
    "tosem_live_evidence_statistics.csv",
    "tosem_live_evidence_statistics_table.tex",
    "tosem_live_story_creation_table.tex",
)


def _copy_file(relative: Path, destination: Path) -> None:
    source = ROOT / relative
    if not source.is_file():
        raise FileNotFoundError(f"Required release file is missing: {source}")
    target = destination / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _copy_filtered_tree(relative: Path, destination: Path, suffixes: set[str]) -> None:
    source_root = ROOT / relative
    if not source_root.is_dir():
        raise FileNotFoundError(f"Required release directory is missing: {source_root}")
    for source in sorted(source_root.rglob("*")):
        if source.is_file() and source.suffix.lower() in suffixes:
            _copy_file(source.relative_to(ROOT), destination)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build(destination: Path) -> dict[str, object]:
    destination = destination.resolve()
    if destination == ROOT or ROOT in destination.parents:
        raise ValueError("The release destination must be outside the private source tree")
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(f"Release destination is not empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)

    for name in TOP_LEVEL_FILES:
        _copy_file(Path(name), destination)
    _copy_filtered_tree(Path("src/jira_enhancer"), destination, {".py", ".json"})
    _copy_filtered_tree(Path("tests"), destination, {".py"})
    _copy_filtered_tree(Path("openapi"), destination, {".yaml", ".yml"})
    _copy_file(Path("scripts/build_reviewer_release.py"), destination)

    for name in PATENT_FILES:
        _copy_file(Path("patent") / name, destination)
    for name in IMAGE_FILES:
        _copy_file(Path("patent/images") / name, destination)
    for name in GENERATED_FILES:
        _copy_file(Path("patent/generated") / name, destination)

    _copy_filtered_tree(Path("patent/artifacts/tosem"), destination, {".csv", ".json", ".md"})
    _copy_filtered_tree(Path("patent/artifacts/b2_b3_pilot"), destination, {".json", ".md"})
    _copy_filtered_tree(Path("patent/reviewer_study/public_results"), destination, {".csv", ".json", ".md", ".tex"})
    _copy_file(Path("patent/reviewer_study/build_four_mode_audit.py"), destination)

    files = sorted(path for path in destination.rglob("*") if path.is_file())
    manifest = {
        "artifact": "jira-enhancer-reviewer-release-v1",
        "history_boundary": "Fresh public history; private development Git history is excluded.",
        "file_count": len(files),
        "files": {
            path.relative_to(destination).as_posix(): _sha256(path)
            for path in files
        },
    }
    manifest_path = destination / "RELEASE_FILE_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"destination": str(destination), "file_count": len(files), "manifest": str(manifest_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the allowlisted Jira Enhancer reviewer release.")
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.destination), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
