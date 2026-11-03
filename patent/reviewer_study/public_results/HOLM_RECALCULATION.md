# Holm recalculation

Recalculated from the current 40-reviewer study, with all reviewers included in the main analysis. Each matched epic is one inference unit (n=14). Masked repeats contribute to consistency checks.

The composite retains implementation readiness at 40%, dependencies and risks at 20%, granularity and actionability at 20%, and each remaining dimension at 5%. These revised weights were selected after the initial analysis.

## Calculation

For a family of m tests, sort the raw p-values from smallest to largest. Multiply rank i by (m − i + 1), take the cumulative maximum, cap at 1, and restore the original test order. The two composite contrasts form one family. Review time and required edits each have their own two-contrast family. Each dimension analysis uses seven tests within one method contrast.

Both primary weighted contrasts have positive differences on all 14 epics. Of 16,384 possible sign assignments, two are at least as extreme as the observed absolute mean difference. Each raw two-sided p-value is therefore 2/16,384 = 0.0001220703125. Holm gives 0.000244140625 for both contrasts; the cumulative-maximum step preserves that value for the second tied test.

## Primary results

| Outcome | Contrast | Raw p | Holm p |
|---|---|---:|---:|
| Weighted composite | B3-B2 | 0.000122070312 | 0.000244140625 |
| Weighted composite | B3-B1 | 0.000122070312 | 0.000244140625 |
| review time min | B3-B2 | 0.000122070312 | 0.000244140625 |
| review time min | B3-B1 | 0.004028320312 | 0.004028320312 |
| required edits | B3-B2 | 0.157958984375 | 0.157958984375 |
| required edits | B3-B1 | 0.000122070312 | 0.000244140625 |

Both weighted contrasts give Holm-adjusted p=0.000244140625. The method means and confidence intervals use all 40 reviewers.

[The calculation table](matched_review_holm_calculation.csv) records every raw p-value, family size, sorted rank, multiplier, and adjusted result for the 40-reviewer panel.

## Current input scope

The supplied CSV contains 1,840 rows across 40 reviewer IDs. The analysis includes all 1,680 primary ratings and uses the 160 masked repeats for consistency checks.

Source SHA-256: `61185752527cc5f5361d7c8c7c6fd55d5c62805d44d94d74dfbfb8adcaae1b6e`.

The ratings CSV is read-only throughout recalculation.
