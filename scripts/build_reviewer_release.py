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
    "ARTIFACT_README.md",
    "generate_b2_b3_learning_figure.py",
    "generate_cross_dataset_comparison.py",
    "generate_patent_results.py",
    "references_2020_plus.bib",
    "reproduce_public_figures.py",
    "run_public_benchmark_eval.py",
    "run_source_bound_transaction_experiment.py",
    "verify_public_artifact.py",
)

EMSE_FILES = (
    "jira_enhancer_emse.tex",
    "jira_enhancer_emse.pdf",
    "jira_enhancer_emse.bbl",
    "references.bib",
    "sn-jnl.cls",
    "sn-basic.bst",
    "Makefile",
    "fig_system_architecture_embedded.png",
    "fig_system_control_flow_embedded.png",
    "fig_agentic_module_story_issue_enhancer.png",
    "fig_agentic_module_epic_breaker.png",
    "fig_agentic_module_prompt_generator.png",
    "fig_agentic_module_rl_controller.png",
    "fig_matched_iteration_effort.pdf",
    "fig_matched_review_weighted_radar.pdf",
    "fig_b2_b3_arm_learning.png",
    "fig_b2_b3_critic_reward_heatmap.png",
    "fig_measured_review_composite_components.pdf",
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
    "b2_b3_arm_learning.png",
    "b2_b3_critic_reward_heatmap.png",
    "comparative_5000_method_summary.csv",
    "comparative_cross_dataset_summary.csv",
    "comparative_cross_dataset_table.tex",
    "public_benchmark_swebench_lite_method_summary.csv",
    "public_benchmark_swebench_lite_method_summary_table.tex",
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
)

FIXTURE_FILES = ("jira_issues.json", "confluence_pages.json", "bitbucket_prs.json", "scopes.json")
TEST_FILES = (
    "__init__.py", "test_api.py", "test_causal_transaction.py",
    "test_codex_orchestrator.py", "test_four_mode_audit.py", "test_reasoning.py",
    "test_rl.py", "test_scorer.py", "test_sources.py", "test_store.py", "test_workflow.py",
)
EVIDENCE_FILES = {
    "patent/artifacts/evaluation": (
        "analysis_summary.json", "anonymized_latest_batch_audit.csv",
        "anonymized_live_story_outcomes.csv", "comparative_5000_units.csv",
        "manifest.json", "public_release_manifest.json",
    ),
    "patent/artifacts/b2_b3_pilot": (
        "README.md", "aggregate_results.json", "arm_learning_curve.csv",
        "critic_learning_summary.json", "exploitation_summary.json",
    ),
    "patent/reviewer_study/public_results": (
        "README.md", "RESULTS.md", "HOLM_RECALCULATION.md",
        "matched_generation_resource_summary.csv", "matched_review_dimension_pairwise.csv",
        "matched_review_dimension_summary.csv", "matched_review_epic_scores.csv",
        "matched_review_holm_calculation.csv", "matched_review_method_summary.csv",
        "matched_review_method_summary_table.tex", "matched_review_pairwise.csv",
        "matched_review_pairwise_table.tex", "matched_review_quality_factor_comparison.csv",
        "matched_review_quality_factor_table.tex", "matched_review_reliability.csv",
        "matched_review_reliability_table.tex", "matched_review_weighted_radar.pdf",
        "matched_review_weighted_radar.png", "iteration_story_survival.csv",
        "iteration_epic_survival.csv", "matched_iteration_effort.pdf", "matched_iteration_effort.png",
    ),
    "patent/artifacts/remediation/weight_sensitivity_20261103": (
        "README.txt", "CHECKSUMS.sha256", "analyze_weight_sensitivity.py",
        "plot_measured_review_composite_components.py", "public_epic_dimension_means.csv",
        "reference_priority_epic_scores.csv", "epic_scores.csv", "method_means.csv",
        "comparisons.csv", "results.json", "source_validation.json",
        "measured_review_composite_components.pdf", "measured_review_composite_components.png",
        "measured_review_composite_components.json", "measured_review_dimension_statistics.csv",
    ),
}


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
    _copy_filtered_tree(Path("src/jira_enhancer"), destination, {".py"})
    for name in FIXTURE_FILES:
        _copy_file(Path("src/jira_enhancer/data") / name, destination)
    for name in TEST_FILES:
        _copy_file(Path("tests") / name, destination)
    _copy_filtered_tree(Path("openapi"), destination, {".yaml", ".yml"})
    _copy_file(Path("scripts/build_reviewer_release.py"), destination)

    for name in PATENT_FILES:
        _copy_file(Path("patent") / name, destination)
    for name in EMSE_FILES:
        _copy_file(Path("patent/emse") / name, destination)
    for name in IMAGE_FILES:
        _copy_file(Path("patent/images") / name, destination)
    for name in GENERATED_FILES:
        _copy_file(Path("patent/generated") / name, destination)

    for directory, names in EVIDENCE_FILES.items():
        for name in names:
            _copy_file(Path(directory) / name, destination)
    _copy_file(Path("patent/reviewer_study/build_four_mode_audit.py"), destination)
    _copy_file(Path("patent/reviewer_study/verify_weighted_results.py"), destination)
    _copy_file(Path("patent/reviewer_study/plot_weighted_review_radar.py"), destination)
    _copy_file(Path("patent/reviewer_study/plot_iteration_effort.py"), destination)

    files = sorted(path for path in destination.rglob("*") if path.is_file())
    manifest = {
        "artifact": "jira-enhancer-replication-package-v2",
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
