# TOSEM Evaluation Artifact

This artifact separates the manuscript's evidence into reproducible classes and provides a de-identified export of the two authenticated Jira workflow waves.

## Five-Reviewer Matched Baseline Assessment

The matched study compares 42 packages and 210 stories produced by B1, B2, and B3 from 14 common inputs. Five reviewers assessed the packages under an anchored seven-dimension rubric. Reviewers 3--5 form the primary set because Reviewers 1--2 had earlier B3-only exposure; all five reviewers form the sensitivity analysis.

The current source is the reviewer-corrected rating table at `patent/reviewer_study/completed_matched_reviews_20260813/analysis/matched_review_unblinded_ratings.csv`. It contains 210 primary ratings and 20 masked repeats. The private method-allocation key and enterprise input packets remain access-controlled.

The reported composite assigns 40% to implementation readiness, 20% to dependencies and risks, 20% to granularity and actionability, and 5% each to scope fidelity, coverage completeness, story distinctness, and acceptance/testability. These weights were selected after the initial analysis, so the weighted comparison is a revised exploratory outcome. Ratings are averaged within each epic-method cell before the 14 paired epics are compared. Masked repeats contribute to consistency diagnostics rather than method-effect estimates.

Primary weighted means are B1=2.56, B2=3.59, and B3=4.32 on the 1--5 scale. B3 exceeds B2 by 0.74 points (95% epic-bootstrap CI [0.55, 0.93], Holm-adjusted exact p=0.000244), and B1 by 1.76 points ([1.43, 2.10], p=0.000244). B3 scores higher on all 14 epics in both comparisons. B3 also has 2.15 minutes lower recorded assessment time than B2 (95% CI [-2.35, -1.99], p=0.000244).

Recalculate the current weighted analysis from the corrected private ratings:

```bash
python3 patent/artifacts/remediation/weighted_quality/update_current_reports.py
```

The command writes method summaries, paired contrasts, dimension analyses, agreement diagnostics, and LaTeX tables under `patent/reviewer_study/completed_matched_reviews_20260813/analysis/`. The detailed interpretation is in `patent/reviewer_study/completed_matched_reviews_20260813/MATCHED_REVIEW_RESULTS.md`; de-identified aggregate tables are released under `patent/reviewer_study/public_results/`.

The public paired epic means support recalculation of both reviewer sets and method contrasts:

```bash
python3 patent/reviewer_study/verify_weighted_results.py
```

This command recomputes means, exact sign-flip tests, Holm adjustments, and paired bootstrap intervals from `public_results/matched_review_epic_scores.csv`.

The comparison measures complete generation workflows. B2 makes one drafting request per input, while B3 applies bounded refinement and governance checks. B2 uses 14 model calls and B3 uses 173 across the 14 packages. Mutation safety is evaluated in the separate fault-injection experiment.

## Live Evidence Analysis

Run from the repository root:

```bash
python3 patent/analyze_tosem_live_evidence.py
```

The command validates the expected 70-story/two-wave structure, computes paired endpoint statistics with a fixed-seed bootstrap, audits the latest 45-story batch, and writes:

- `patent/generated/tosem_live_evidence_statistics.csv`
- `patent/generated/tosem_live_evidence_statistics_table.tex`
- `patent/artifacts/tosem/anonymized_live_story_outcomes.csv`
- `patent/artifacts/tosem/anonymized_latest_batch_audit.csv`
- `patent/artifacts/tosem/analysis_summary.json`
- `patent/artifacts/tosem/manifest.json`

The shareable files use de-identified records. Enterprise issue text, identifiers, URLs, account data, and timestamps remain in restricted source storage.

## Mutation-Safety Fault Injection

Run the controlled ablation with the declared seed and scenario count:

```bash
PYTHONPATH=src python3 patent/run_source_bound_transaction_experiment.py \
  --output-dir patent/generated \
  --trials-per-scenario 2000 \
  --seed 20260726
```

Verify the implementation-level transaction tests:

```bash
PYTHONPATH=src python3 -m pytest tests/test_causal_transaction.py -q
```

The ablation uses the same transaction payload and fault schedule for all four arrangements. Its scenario-balanced rates describe the outcomes under the declared injected faults.

## Study interpretation

The 70 authenticated story endpoints describe workflow execution and writeback outcomes. The matched review measures package quality, readiness, and assessment effort for B1, B2, and B3 on 14 common inputs. Its resource totals are 14 model calls for B2 and 173 for B3, so quality and review-time comparisons concern complete workflows with different computational budgets.

The rating records support within-panel agreement and matched-epic comparisons. Reviewer-experience bands and method-origin guesses are unavailable in those records. The 5,000-unit analysis checks the scoring equations under deterministic perturbations of reused inputs, while the SWE-bench Lite analysis checks the same formulas on public issue descriptions. Delivery outcomes and production incident prevalence belong to separate evaluation settings.
