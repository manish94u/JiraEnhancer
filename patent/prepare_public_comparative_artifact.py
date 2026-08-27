#!/usr/bin/env python3
"""Remove source identifiers from the formula-expanded comparison table.

This maintainer utility is used when preparing a reviewer release.  It keeps
only the method, a release-local sample identifier, and the numeric scoring
model outputs.  It never copies Jira, run, or compound unit identifiers.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
DEFAULT_INPUT = HERE / "generated" / "comparative_5000_epic_metrics.csv"
DEFAULT_ARTIFACT_DIR = HERE / "artifacts" / "tosem"
DEFAULT_OUTPUT_NAME = "comparative_5000_units.csv"
METHODS = (
    "B0_manual",
    "B1_template_rules",
    "B2_generic_llm",
    "B3_jira_enhancer",
)
METRIC_COLUMNS = (
    "coverage_score",
    "acceptance_completeness",
    "dependency_capture_rate",
    "rework_rate",
    "time_to_ready_minutes",
    "reviewer_edits_per_story",
    "governance_block_rate",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = ["method", "sample_id", *METRIC_COLUMNS]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _build_rows(source_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    required = {"method", "unit_id", *METRIC_COLUMNS}
    if not source_rows:
        raise SystemExit("The comparative source table is empty.")
    missing = required.difference(source_rows[0])
    if missing:
        raise SystemExit(f"Missing source columns: {', '.join(sorted(missing))}")

    unit_ids = sorted({row["unit_id"] for row in source_rows})
    anonymous = {unit_id: f"U{index:05d}" for index, unit_id in enumerate(unit_ids, start=1)}
    output = [
        {
            "method": row["method"],
            "sample_id": anonymous[row["unit_id"]],
            **{column: row[column] for column in METRIC_COLUMNS},
        }
        for row in source_rows
    ]

    by_method = Counter(row["method"] for row in output)
    if set(by_method) != set(METHODS):
        raise SystemExit(f"Unexpected method set: {sorted(by_method)}")
    if len(output) != 20_000 or any(by_method[method] != 5_000 for method in METHODS):
        raise SystemExit(f"Expected 5,000 rows per method; found {dict(sorted(by_method.items()))}")
    sample_methods: dict[str, set[str]] = defaultdict(set)
    for row in output:
        sample_methods[row["sample_id"]].add(row["method"])
    if len(sample_methods) != 5_000 or any(methods != set(METHODS) for methods in sample_methods.values()):
        raise SystemExit("The source does not contain a complete B0-B3 block for each of 5,000 units.")
    return output


def _write_release_manifest(artifact_dir: Path, comparative_path: Path) -> Path:
    release_files = (
        "manifest.json",
        "analysis_summary.json",
        "anonymized_live_story_outcomes.csv",
        "anonymized_latest_batch_audit.csv",
        comparative_path.name,
    )
    missing = [name for name in release_files if not (artifact_dir / name).is_file()]
    if missing:
        raise SystemExit(f"Cannot build release manifest; missing: {', '.join(missing)}")
    payload: dict[str, Any] = {
        "artifact": "jira-enhancer-tosem-public-reviewer-release-v1",
        "files": {name: _sha256(artifact_dir / name) for name in release_files},
        "privacy_boundary": (
            "The release excludes issue and epic keys, source run IDs, URLs, account names, "
            "email addresses, timestamps, summaries, reviewer identities, and raw connector telemetry."
        ),
        "comparative_table": {
            "design": "deterministic formula-expanded sensitivity diagnostic",
            "methods": list(METHODS),
            "rows_per_method": 5_000,
            "independence_boundary": (
                "The 20,000 rows are not independent production observations and must not be used "
                "as population-level replication evidence."
            ),
        },
    }
    path = artifact_dir / "public_release_manifest.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare the identifier-free comparative reviewer artifact.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--output-name", default=DEFAULT_OUTPUT_NAME)
    args = parser.parse_args()

    source = args.input.resolve()
    artifact_dir = args.artifact_dir.resolve()
    if not source.is_file():
        raise SystemExit(f"Comparative source table is missing: {source}")
    if Path(args.output_name).name != args.output_name:
        raise SystemExit("--output-name must be a file name, not a path")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    output_path = artifact_dir / args.output_name
    rows = _build_rows(_read_csv(source))
    _write_csv(output_path, rows)
    manifest_path = _write_release_manifest(artifact_dir, output_path)
    print(
        json.dumps(
            {
                "output": str(output_path),
                "rows": len(rows),
                "sha256": _sha256(output_path),
                "release_manifest": str(manifest_path),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
