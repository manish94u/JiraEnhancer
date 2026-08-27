from __future__ import annotations

import unittest

from jira_enhancer.services import ReadinessScorer


class ReadinessScorerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.scorer = ReadinessScorer()

    def test_score_complete_issue(self) -> None:
        normalized_issue = {
            "description": "Detailed description",
            "acceptance_criteria": ["a", "b"],
            "dependencies": ["dep-1"],
            "links": {"confluence_pages": ["1"], "bitbucket_prs": ["2"]},
            "comments": ["comment"],
            "worklogs": ["worklog"],
            "edge_cases": [
                "edge",
                "Unit test case: test_dependency_validation rejects stale source hashes.",
                "Integration test case: verify writeback flow creates the AI block through the Jira API.",
            ],
        }
        readiness, confidence, flags = self.scorer.score(normalized_issue)
        self.assertGreaterEqual(readiness, 9)
        self.assertGreaterEqual(confidence, 10)
        self.assertEqual(flags, [])

    def test_score_caps_confidence_when_test_case_samples_are_missing(self) -> None:
        normalized_issue = {
            "description": "Detailed description",
            "acceptance_criteria": ["a", "b"],
            "dependencies": ["dep-1"],
            "links": {"confluence_pages": ["1"], "bitbucket_prs": ["2"]},
            "comments": ["comment"],
            "worklogs": ["worklog"],
            "edge_cases": ["edge"],
        }
        readiness, confidence, flags = self.scorer.score(
            normalized_issue,
            suggested_description="Clear generated description with implementation and validation details.",
            evidence_count=1,
            analysis={
                "problem_statement": "Problem",
                "acceptance_criteria": ["Behavior is testable."],
                "guidance": ["Guidance"],
                "risks": ["Risk"],
                "error_handling": ["Handle"],
                "nfrs": ["NFR"],
                "out_of_scope": ["Out"],
                "recommended_next_step": "Next",
            },
            mode="create_run",
        )
        self.assertLess(confidence, 9)
        self.assertLess(readiness, 9)
        self.assertIn("missing_unit_test_case_sample", flags)
        self.assertIn("missing_integration_test_case_sample", flags)
        self.assertIn("test_case_gap", flags)

    def test_score_allows_threshold_when_unit_and_integration_samples_exist(self) -> None:
        normalized_issue = {
            "description": "Detailed description",
            "acceptance_criteria": [
                "a",
                "b",
                "Unit test case: test_writeback_blocks_stale_hash verifies stale source protection.",
                "Integration test case: verify Jira writeback API persists the AI block.",
            ],
            "dependencies": ["dep-1"],
            "links": {"confluence_pages": ["1"], "bitbucket_prs": ["2"]},
            "comments": ["comment"],
            "worklogs": ["worklog"],
            "edge_cases": ["edge"],
        }
        readiness, confidence, flags = self.scorer.score(
            normalized_issue,
            suggested_description="Clear generated description with implementation and validation details.",
            evidence_count=1,
            analysis={
                "problem_statement": "Problem",
                "acceptance_criteria": ["Behavior is testable."],
                "guidance": ["Guidance"],
                "risks": ["Risk"],
                "error_handling": ["Handle"],
                "nfrs": ["NFR"],
                "out_of_scope": ["Out"],
                "recommended_next_step": "Next",
            },
            mode="create_run",
        )
        self.assertGreaterEqual(confidence, 9)
        self.assertGreaterEqual(readiness, 9)
        self.assertNotIn("test_case_gap", flags)

    def test_score_incomplete_issue_sets_policy_flags(self) -> None:
        normalized_issue = {
            "description": "",
            "acceptance_criteria": [],
            "dependencies": [],
            "links": {"confluence_pages": [], "bitbucket_prs": []},
            "comments": [],
            "worklogs": [],
            "edge_cases": [],
        }
        readiness, confidence, flags = self.scorer.score(normalized_issue)
        self.assertEqual(readiness, 1)
        self.assertEqual(confidence, 1)
        self.assertIn("missing_description", flags)
        self.assertIn("limited_external_context", flags)

    def test_score_uses_generated_context_and_user_input(self) -> None:
        normalized_issue = {
            "description": "",
            "acceptance_criteria": [],
            "dependencies": [],
            "links": {"confluence_pages": [], "bitbucket_prs": []},
            "comments": [],
            "worklogs": [],
            "edge_cases": [],
        }
        readiness, confidence, flags = self.scorer.score(
            normalized_issue,
            suggested_description="Clarified implementation scope from generated analysis.",
            evidence_count=1,
        )
        self.assertGreater(readiness, 3)
        self.assertGreater(confidence, 3)
        self.assertNotIn("missing_description", flags)
        self.assertNotIn("limited_external_context", flags)

    def test_rich_generated_draft_can_reach_high_confidence(self) -> None:
        normalized_issue = {
            "description": "",
            "acceptance_criteria": [],
            "dependencies": [],
            "links": {"confluence_pages": [], "bitbucket_prs": []},
            "comments": [],
            "worklogs": [],
            "edge_cases": [],
        }
        readiness, confidence, flags = self.scorer.score(
            normalized_issue,
            suggested_description="Clarified implementation scope with concrete boundaries, validations, and writeback behavior.",
            evidence_count=1,
            analysis={
                "problem_statement": "The Jira needs a concrete implementation-ready description rather than a placeholder.",
                "acceptance_criteria": [
                    "The behavior is testable.",
                    "Dependencies are explicit.",
                ],
                "guidance": ["Keep the implementation story-specific."],
                "risks": ["Stale source context can invalidate writeback."],
                "error_handling": ["Block writeback on source conflicts."],
                "nfrs": ["Perf budget remains bounded."],
                "out_of_scope": ["No unrelated workflow redesign."],
                "recommended_next_step": "Review the refined draft and approve it.",
            },
        )
        self.assertGreaterEqual(readiness, 4)
        self.assertGreaterEqual(confidence, 4)
        self.assertIn("missing_acceptance_criteria", flags)

    def test_score_does_not_inflate_from_user_input_without_better_description(self) -> None:
        normalized_issue = {
            "description": "",
            "acceptance_criteria": [],
            "dependencies": [],
            "links": {"confluence_pages": [], "bitbucket_prs": []},
            "comments": [],
            "worklogs": [],
            "edge_cases": [],
        }
        without_description = self.scorer.score(normalized_issue)
        with_same_context = self.scorer.score(
            normalized_issue,
            suggested_description="",
            evidence_count=0,
            analysis={},
        )
        self.assertEqual(without_description, with_same_context)


if __name__ == "__main__":
    unittest.main()
