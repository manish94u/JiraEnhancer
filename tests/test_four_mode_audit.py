from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "patent" / "reviewer_study" / "build_four_mode_audit.py"
SPEC = importlib.util.spec_from_file_location("build_four_mode_audit", MODULE_PATH)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _story(input_id: str, index: int, planner: str) -> dict[str, object]:
    return {
        "summary": f"{input_id} story {index}",
        "description": f"Implement the bounded behavior for story {index}.",
        "acceptance_criteria": ["The specified behavior is verified."],
        "dependencies": [],
        "edge_cases": [],
        "risks": [],
        "nfrs": [],
        "story_points": 3,
        "planner": planner,
        "evidence_refs": [f"evidence:{input_id}"],
    }


class FourModeAuditTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path, dict[str, str]]:
        source = root / "source"
        packets = []
        hashes: dict[str, str] = {}
        for input_id in audit.INPUT_IDS:
            input_hash = hashlib.sha256(input_id.encode("utf-8")).hexdigest()
            hashes[input_id] = input_hash
            packets.append(
                {
                    "input_id": input_id,
                    "input_sha256": input_hash,
                    "epic_title": f"Epic {input_id}",
                    "epic_description": "Description",
                    "evidence_title": "Evidence",
                    "evidence_text": "Evidence text",
                }
            )
            for method in audit.EXECUTED_METHODS:
                metadata: dict[str, object]
                if method == "B1":
                    metadata = {"model_calls": 0, "execution": "deterministic template/rule core"}
                elif method == "B2":
                    metadata = {"model_calls": 1, "retry_count": 0, "execution": "single generic LLM pass"}
                else:
                    diagnostics = [
                        {
                            "story_index": index,
                            "confidence": 9,
                            "approval_required": True,
                            "policy_flags": ["test_flag"],
                            "confidence_trajectory": [{"attempt": 1, "confidence": 9}],
                        }
                        for index in range(1, 6)
                    ]
                    metadata = {
                        "model_calls": 6,
                        "writeback_enabled": False,
                        "story_diagnostics": diagnostics,
                        "execution": "Jira Enhancer test fixture",
                    }
                artifact = {
                    "method": method,
                    "input_id": input_id,
                    "input_sha256": input_hash,
                    "story_count": 5,
                    "stories": [_story(input_id, index, method) for index in range(1, 6)],
                    "private_generation_metadata": metadata,
                }
                _write_json(source / "generation" / "artifacts" / method / f"{input_id}.json", artifact)
        _write_json(source / "sanitized_input_packets.json", {"packet_count": 14, "packets": packets})

        analysis = root / "analysis.json"
        method_rows = []
        for method, mean, ready, minor, major, rate in (
            ("B1", 2.7, 0, 21, 21, 0.5),
            ("B2", 4.2, 18, 10, 14, 2 / 3),
            ("B3", 4.3, 18, 19, 5, 37 / 42),
        ):
            method_rows.append(
                {
                    "method": method,
                    "composite_mean": mean,
                    "ready_count": ready,
                    "minor_revision_count": minor,
                    "major_revision_count": major,
                    "ready_or_minor_rate": rate,
                }
            )
        pairwise = [
            {
                "contrast": "B3-B2",
                "outcome": "composite",
                "mean_difference": 0.1,
                "ci_low": -0.1,
                "ci_high": 0.3,
            },
            {
                "contrast": "B3-B2",
                "outcome": "review_time_min",
                "mean_difference": -1.0,
                "ci_low": -1.5,
                "ci_high": -0.5,
            },
            {
                "contrast": "B3-B2",
                "outcome": "required_edits",
                "mean_difference": -0.6,
                "ci_low": -1.5,
                "ci_high": 0.2,
            },
        ]
        resources = [
            {"method": "B1", "model_calls": 0},
            {"method": "B2", "model_calls": 14},
            {"method": "B3", "model_calls": 84},
        ]
        _write_json(
            analysis,
            {
                "analysis_version": "test-v1",
                "primary_reviewers": [3, 4, 5],
                "validation": {"status": "pass"},
                "primary_method_summary": method_rows,
                "primary_pairwise": pairwise,
                "primary_dimension_pairwise": [],
                "generation_resources": resources,
            },
        )
        return source, analysis, hashes

    def _write_b0(self, root: Path, hashes: dict[str, str], *, generative_ai_used: bool = False) -> None:
        for input_id in audit.INPUT_IDS:
            artifact = {
                "schema_version": "b0-human-artifact-v1",
                "study_version": "matched-b0-b1-b2-b3-v1",
                "artifact_kind": "human_manual_package",
                "method": "B0",
                "input_id": input_id,
                "input_sha256": hashes[input_id],
                "authored_at": "2026-08-27T10:30:00+00:00",
                "story_count": 5,
                "stories": [_story(input_id, index, "human_manual") for index in range(1, 6)],
                "private_generation_metadata": {
                    "execution": "independent_human_manual_planning",
                    "model_calls": 0,
                    "author_code": "H01",
                    "experience_band": "6-10_years",
                    "input_packet_sha256": hashes[input_id],
                    "allowed_tools": ["text_editor"],
                    "revision_count": 1,
                    "started_at": "2026-08-27T10:00:00+00:00",
                    "completed_at": "2026-08-27T10:30:00+00:00",
                    "active_authoring_seconds": 1200,
                    "attestation": {
                        "version": "b0-human-attestation-v1",
                        "attested_at": "2026-08-27T10:31:00+00:00",
                        "independently_authored": True,
                        "generative_ai_used": generative_ai_used,
                        "saw_other_mode_outputs": False,
                        "used_only_frozen_input": True,
                        "other_person_collaboration_used": False,
                        "study_team_repaired_output": False,
                        "author_not_reviewer": True,
                    },
                },
            }
            _write_json(root / f"{input_id}.json", artifact)

    def test_pending_bundle_is_sealed_and_sources_are_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source, analysis, _ = self._fixture(root)
            source_paths = sorted(source.glob("generation/artifacts/*/*.json"))
            before = {path: _sha(path) for path in source_paths}
            run_dir = audit.build_run(
                source_study=source,
                analysis_path=analysis,
                output_root=root / "runs",
                run_id="pending-run",
            )
            summary = json.loads((run_dir / "audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "pending_b0_human_artifacts")
            self.assertFalse(summary["four_mode_results_available"])
            self.assertEqual(summary["validated_historical_packages"], 42)
            self.assertEqual(len(summary["missing_b0_input_ids"]), 14)
            self.assertEqual(audit.verify_run(run_dir)["status"], "pass")
            self.assertEqual(before, {path: _sha(path) for path in source_paths})
            self.assertFalse((run_dir / "artifacts" / "B0").exists())
            draft = (run_dir / "DRAFT_RESULTS_FOR_REVIEW.md").read_text(encoding="utf-8")
            self.assertIn("not yet a completed four-mode experiment", draft)
            self.assertIn("B3 is stronger than one-shot B2 on the evaluated workflow indicators", draft)

    def test_run_id_cannot_be_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source, analysis, _ = self._fixture(root)
            arguments = {
                "source_study": source,
                "analysis_path": analysis,
                "output_root": root / "runs",
                "run_id": "fixed-run",
            }
            audit.build_run(**arguments)
            with self.assertRaises(FileExistsError):
                audit.build_run(**arguments)

    def test_valid_human_b0_moves_status_to_review_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source, analysis, hashes = self._fixture(root)
            b0_root = root / "b0"
            self._write_b0(b0_root, hashes)
            run_dir = audit.build_run(
                source_study=source,
                analysis_path=analysis,
                output_root=root / "runs",
                run_id="b0-complete",
                b0_root=b0_root,
            )
            summary = json.loads((run_dir / "audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "pending_four_mode_blinded_review")
            self.assertTrue(summary["four_mode_artifacts_complete"])
            self.assertFalse(summary["four_mode_results_available"])
            self.assertEqual(summary["validated_b0_packages"], 14)
            self.assertEqual(audit.verify_run(run_dir)["status"], "pass")

    def test_b0_with_ai_attestation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source, analysis, hashes = self._fixture(root)
            b0_root = root / "b0"
            self._write_b0(b0_root, hashes, generative_ai_used=True)
            with self.assertRaisesRegex(ValueError, "generative_ai_used"):
                audit.build_run(
                    source_study=source,
                    analysis_path=analysis,
                    output_root=root / "runs",
                    run_id="invalid-b0",
                    b0_root=b0_root,
                )


if __name__ == "__main__":
    unittest.main()
