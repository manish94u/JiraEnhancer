# Matched-review public results

This directory contains de-identified aggregate results from the matched B1/B2/B3 review: 14 common inputs, 42 packages, 210 stories, and five reviewers. Reviewers 3--5 form the primary set because Reviewers 1--2 had earlier exposure to B3-only material. All five reviewers form the sensitivity analysis.

The current composite uses implementation readiness at 40%, dependencies and risks at 20%, granularity and actionability at 20%, and each remaining dimension at 5%. These weights were selected after the initial analysis and define a revised exploratory outcome. The aggregate tables use the current reviewer-corrected ratings.

B3's weighted mean is 4.32, compared with 3.59 for B2. The paired difference is +0.74 points (95% epic-bootstrap CI [0.55, 0.93], Holm-adjusted exact p=0.000244). B3 scores higher on all 14 epics. The corrected time records show 2.15 minutes lower mean assessment time for B3 (95% CI [-2.35, -1.99], p=0.000244).

The files report method summaries, paired contrasts, individual rubric dimensions, agreement diagnostics, resource accounting, and manuscript-ready LaTeX tables. `matched_review_epic_scores.csv` supplies paired weighted means under neutral unit identifiers. Recalculate both reviewer sets and both method contrasts from the repository root with:

```bash
python3 patent/reviewer_study/verify_weighted_results.py
```

The command reproduces means, exact sign-flip tests, Holm adjustments, and paired bootstrap intervals. `RESULTS.md` explains the calculations and study design. Private reviewer-level rows, comments, allocation keys, and enterprise packets remain access-controlled.

Each epic is one matched inference unit. Scores are averaged across reviewers within each epic and method, with masked repeats reserved for consistency checks. The analysis uses exact paired sign-flip tests, 100,000 epic-bootstrap resamples, and Holm adjustment across the two method contrasts within each outcome. B2 and B3 use different numbers of model calls; the results compare complete workflows. Governance and mutation safety are assessed in separate experiments.
