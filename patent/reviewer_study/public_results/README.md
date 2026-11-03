# Matched-review public results

This directory contains de-identified aggregate results from the matched B1/B2/B3 study: 40 reviewers, 14 common inputs, 42 packages, and 210 stories. All 40 reviewers contribute to the main analysis, with 560 primary ratings per method. The source contains 1,680 primary ratings and 160 masked repeats.

The current composite uses implementation readiness at 40%, dependencies and risks at 20%, granularity and actionability at 20%, and each remaining dimension at 5%. These weights were selected after the initial analysis and define a revised exploratory outcome. The aggregate tables use the current reviewer-corrected ratings.

B3's weighted mean is 4.15, compared with 3.61 for B2. The paired difference is +0.54 points (95% epic-bootstrap CI [0.51, 0.57], Holm-adjusted exact p=0.000244). B3 scores higher on all 14 epics. Mean assessment time is 2.17 minutes lower for B3 (95% CI [-2.39, -1.98], p=0.000244). Ready-or-Minor-revision ratings are 77.3% for B3 and 64.6% for B2.

The files report method summaries, paired contrasts, individual rubric dimensions, agreement diagnostics, resource accounting, and manuscript-ready LaTeX tables. `matched_review_epic_scores.csv` supplies 42 paired weighted means under neutral unit identifiers. Recalculate both method contrasts for the 40-reviewer study from the repository root with:

```bash
python3 patent/reviewer_study/verify_weighted_results.py
```

The command reproduces means, exact sign-flip tests, Holm adjustments, and paired bootstrap intervals. `RESULTS.md` explains the calculations and study design. [Holm recalculation](HOLM_RECALCULATION.md) gives the test families and exact adjusted values, with a [step-by-step calculation table](matched_review_holm_calculation.csv). Private reviewer-level rows, comments, allocation keys, and enterprise packets remain access-controlled.

Each epic is one matched inference unit. Scores are averaged across reviewers within each epic and method, with masked repeats reserved for consistency checks. The analysis uses exact paired sign-flip tests, 100,000 epic-bootstrap resamples, and Holm adjustment across the two method contrasts within each outcome. B2 and B3 use different numbers of model calls; the results compare complete workflows. Governance and mutation safety are assessed in separate experiments.
