# Matched Review Results

The current report uses the corrected reviewer ratings and the revised priority-weighted composite. Implementation readiness receives 40%, dependencies and risks 20%, granularity and actionability 20%, and each remaining dimension 5%. The weights were selected after the initial analysis, so this is an exploratory weighted outcome.

## Design

Forty reviewers rated 42 method-blinded packages from 14 common inputs and three executed methods: B1 deterministic templates, B2 one-shot drafting, and B3 Jira Enhancer. All 40 reviewers form the main analysis. Masked repeats are excluded from method-effect estimates. Scores are averaged within each epic and method before paired inference.

## Current results

| Method | Weighted composite [95% CI] | Readiness | Review time, median [IQR] | Required edits, median [IQR] | Ready or minor |
|---|---:|---:|---:|---:|---:|
| B1 | 2.556 [2.319, 2.801] | 2.486 | 9.0 [8.3, 9.5] | 10.0 [7.0, 14.0] | 6.6% |
| B2 | 3.606 [3.284, 3.918] | 3.543 | 10.0 [9.3, 11.3] | 3.0 [1.0, 5.0] | 64.6% |
| B3 | 4.148 [3.853, 4.429] | 4.075 | 8.0 [7.5, 8.8] | 2.0 [2.0, 3.0] | 77.3% |

| Outcome | Contrast | Mean difference [95% CI] | Raw p | Holm p |
|---|---|---:|---:|---:|
| Weighted composite | B3-B2 | 0.542 [0.509, 0.573] | 0.00012207 | 0.00024414 |
| Weighted composite | B3-B1 | 1.592 [1.205, 1.984] | 0.00012207 | 0.00024414 |
| review time min | B3-B2 | -2.169 [-2.393, -1.980] | 0.00012207 | 0.00024414 |
| review time min | B3-B1 | -0.769 [-1.197, -0.356] | 0.00402832 | 0.00402832 |
| required edits | B3-B2 | -0.643 [-1.477, 0.134] | 0.15795898 | 0.15795898 |
| required edits | B3-B1 | -7.957 [-9.750, -6.243] | 0.00012207 | 0.00024414 |

The three-method permutation comparison gives Q=24.571, Monte Carlo p=0.00000500. Exact paired sign-flip tests and 100,000 epic-bootstrap resamples use 14 matched epics. Holm adjustment covers the two method contrasts within each outcome family.

B3 has a higher weighted composite than B2 and B1 on all 14 matched epics. These results describe the corrected ratings under the revised scoring rule and compare the complete generation workflows.

## Dimension results

| Dimension | Weight | B2 | B3 | Difference | Holm p |
|---|---:|---:|---:|---:|---:|
| Scope fidelity | 5% | 3.871 | 4.452 | 0.580 | 0.000854 |
| Coverage completeness | 5% | 3.929 | 4.518 | 0.589 | 0.000854 |
| Story distinctness | 5% | 4.000 | 4.600 | 0.600 | 0.000854 |
| Acceptance criteria and testability | 5% | 3.643 | 4.189 | 0.546 | 0.000854 |
| Dependencies and risks | 20% | 3.829 | 4.404 | 0.575 | 0.000854 |
| Granularity and actionability | 20% | 3.257 | 3.746 | 0.489 | 0.000854 |
| Implementation readiness | 40% | 3.543 | 4.075 | 0.532 | 0.000854 |

## Agreement and source checks

For the weighted composite, primary-reviewer ICC(2,1) is 0.8152; exact pairwise agreement is 4.7% and within-one-point agreement is 91.6%. Across 160 primary masked-repeat pairs, 96.4% of dimension ratings agree exactly, and 99.4% of dispositions agree. These diagnostics were recomputed from the current corrected CSV.

The current source contains 1,840 rows across 40 reviewer IDs. All 40 reviewers contribute to the main analysis: 1,680 primary ratings and 160 masked repeats. Rating ranges, the 40-reviewer matched grid, composite calculation, and source checksum were checked. The source CSV is unchanged by this analysis.

Source SHA-256: `61185752527cc5f5361d7c8c7c6fd55d5c62805d44d94d74dfbfb8adcaae1b6e`.

## Resource accounting

| Method | Model calls | Calls/package | Cumulative invocation seconds |
|---|---:|---:|---:|
| B1 | 0 | 0.00 | 0 |
| B2 | 14 | 1.00 | 2,087 |
| B3 | 173 | 12.36 | 19,906 |

Invocation durations sum the time across model calls. The matched artifact study measures requirement quality, readiness, and recorded review effort for the complete workflows. Governance and mutation safety are evaluated in separate experiments.

## Reproduction

From the repository root, run:

```bash
python3 patent/reviewer_study/verify_weighted_results.py
```

The verifier recalculates weighted means, exact sign-flip tests, Holm adjustments, and paired bootstrap intervals from the public aggregate `matched_review_epic_scores.csv`. [Holm recalculation](HOLM_RECALCULATION.md) explains the test families and the step-down calculation.
