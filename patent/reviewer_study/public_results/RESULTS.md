# Matched Review Results

The current report uses the corrected reviewer ratings and the revised priority-weighted composite. Implementation readiness receives 40%, dependencies and risks 20%, granularity and actionability 20%, and each remaining dimension 5%. The weights were selected after the initial analysis, so this is an exploratory weighted outcome.

## Design

Five reviewers rated 42 method-blinded packages from 14 common inputs and three executed methods: B1 deterministic templates, B2 one-shot drafting, and B3 Jira Enhancer. Reviewers 3–5 form the primary analysis; all five form the sensitivity analysis. Masked repeats are excluded from method-effect estimates. Scores are averaged within each epic and method before paired inference.

## Current results

| Method | Weighted composite [95% CI] | Readiness | Review time, median [IQR] | Required edits, median [IQR] | Ready or minor |
|---|---:|---:|---:|---:|---:|
| B1 | 2.564 [2.300, 2.832] | 2.500 | 9.0 [8.0, 10.0] | 10.5 [7.0, 14.0] | 50.0% |
| B2 | 3.586 [3.204, 3.957] | 3.524 | 10.0 [10.0, 11.8] | 2.5 [1.0, 5.0] | 66.7% |
| B3 | 4.321 [4.114, 4.517] | 4.595 | 8.0 [8.0, 9.0] | 2.0 [2.0, 3.0] | 88.1% |

| Outcome | Contrast | Mean difference [95% CI] | Raw p | Holm p |
|---|---|---:|---:|---:|
| Weighted composite | B3-B2 | 0.736 [0.550, 0.930] | 0.00012207 | 0.00024414 |
| Weighted composite | B3-B1 | 1.757 [1.430, 2.096] | 0.00012207 | 0.00024414 |
| review time min | B3-B2 | -2.152 [-2.348, -1.986] | 0.00012207 | 0.00024414 |
| review time min | B3-B1 | -0.748 [-1.248, -0.276] | 0.00878906 | 0.00878906 |
| required edits | B3-B2 | -0.643 [-1.548, 0.190] | 0.20507812 | 0.20507812 |
| required edits | B3-B1 | -8.024 [-9.905, -6.214] | 0.00012207 | 0.00024414 |

The three-method permutation comparison gives Q=23.286, Monte Carlo p=0.00000500. Exact paired sign-flip tests and 100,000 epic-bootstrap resamples use 14 matched epics. Holm adjustment covers the two method contrasts within each outcome family.

B3 has a higher weighted composite than B2 and B1 on all 14 matched epics. The all-five-reviewer sensitivity analysis also favors B3 on all 14 epics for both contrasts. These results describe the corrected ratings under the revised scoring rule; they do not isolate the effect of the critic, schema, or learning policy.

## Dimension results

| Dimension | Weight | B2 | B3 | Difference | Holm p |
|---|---:|---:|---:|---:|---:|
| Scope fidelity | 5% | 3.857 | 4.071 | 0.214 | 0.007812 |
| Coverage completeness | 5% | 3.929 | 4.786 | 0.857 | 0.000854 |
| Story distinctness | 5% | 4.000 | 4.214 | 0.214 | 0.007812 |
| Acceptance criteria and testability | 5% | 3.643 | 4.024 | 0.381 | 0.000854 |
| Dependencies and risks | 20% | 3.786 | 4.571 | 0.786 | 0.000854 |
| Granularity and actionability | 20% | 3.238 | 3.571 | 0.333 | 0.000854 |
| Implementation readiness | 40% | 3.524 | 4.595 | 1.071 | 0.005859 |

## Agreement and source checks

For the weighted composite, primary-reviewer ICC(2,1) is 0.9762; exact pairwise agreement is 66.7% and within-one-point agreement is 100.0%. Across 12 primary masked-repeat pairs, 100.0% of dimension ratings agree exactly, and 100.0% of dispositions agree. These diagnostics were recomputed from the current corrected CSV.

The current source contains 230 complete rating rows, including 210 primary ratings and 20 masked repeats. Its rating ranges, matched grid, composite calculation, and source checksum were checked. The source CSV is unchanged by this analysis. The study authors identify the corrected ratings as corrections supplied by the original reviewers.

Source SHA-256: `3f59f0f191d80768e10d07a0365d4f3d8a20f2c04f6e0cdb31b97055030cb61f`.

## Resource accounting

| Method | Model calls | Calls/package | Cumulative invocation seconds |
|---|---:|---:|---:|
| B1 | 0 | 0.00 | 0 |
| B2 | 14 | 1.00 | 2,087 |
| B3 | 173 | 12.36 | 19,906 |

Invocation durations are summed across calls and are not elapsed wall-clock time. Governance and mutation safety come from separate experiments. The matched artifact study does not measure downstream delivery, isolated learning effects, or general computational efficiency.

## Reproduction

From the repository root, run:

```bash
python3 patent/reviewer_study/verify_weighted_results.py
```

The verifier recalculates the weighted means, both paired method contrasts, exact sign-flip p-values, Holm adjustments, and bootstrap intervals from the public `matched_review_epic_scores.csv` file. That file contains aggregate values under neutral unit identifiers for both reviewer sets.
