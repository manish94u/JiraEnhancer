# Evaluation and Replication Artifact

This guide reproduces the matched-review statistics, verifies the released field records, and reruns the transaction-safety experiment. Run commands from the repository root after installing the analysis dependencies:

```bash
make install-reviewer
```

## Matched requirement-quality comparison

The study compares 42 packages containing 210 stories, produced by B1, B2, and B3 from 14 common epic inputs. Forty reviewers contributed 1,840 ratings: 1,680 primary ratings and 160 masked repeats. The released [epic-score table](reviewer_study/public_results/matched_review_epic_scores.csv) contains 42 aggregate rows, one per epic and method. Each aggregate uses all 40 reviewers; the 14 matched epics are the units of inference. Masked repeats supply consistency diagnostics.

The corrected ratings use a composite with implementation readiness at 40%, dependencies and risks at 20%, granularity and actionability at 20%, and each remaining rubric dimension at 5%. These weights were selected after the initial analysis, making this an exploratory weighted outcome. The identical weights apply to every method and reviewer, retaining the anchored 1–5 scale.

```bash
python3 patent/reviewer_study/verify_weighted_results.py
```

The verifier reads `patent/reviewer_study/public_results/matched_review_epic_scores.csv` and prints a JSON report with the input checksum, method means, paired differences, exact sign-flip tests, Holm adjustments, and 100,000-resample epic-bootstrap intervals.

The weighted means are B1=2.56, B2=3.61, and B3=4.15. B3 exceeds B2 by 0.54 points (95% CI [0.51, 0.57], Holm-adjusted p=0.000244) and B1 by 1.59 points ([1.20, 1.98], p=0.000244). B3 scores higher on all 14 epics in both contrasts. The published review-time analysis reports a 2.17-minute lower mean assessment time for B3 than B2 (95% CI [-2.39, -1.98], p=0.000244).

The [results report](reviewer_study/public_results/RESULTS.md) links these outcomes to the study design. The same directory contains method summaries, paired contrasts, dimension scores, agreement diagnostics, resource totals, and LaTeX tables. The [Holm calculation](reviewer_study/public_results/HOLM_RECALCULATION.md) explains the adjustment across the two method contrasts.

The comparison evaluates complete workflows: B2 used 14 drafting calls and B3 used 173 planning and refinement calls across the 14 inputs.

## Authenticated field records

```bash
python3 patent/verify_public_artifact.py
```

The verifier reads `patent/artifacts/evaluation/` and prints a JSON verification summary. It checks the SHA-256 manifests, table schemas, and reported counts and descriptive statistics against these released inputs:

- `anonymized_live_story_outcomes.csv`: 70 terminal story endpoints from two deployment waves.
- `anonymized_latest_batch_audit.csv`: the latest 45-story batch and its payload audit.
- `analysis_summary.json`: the reported endpoint and audit statistics.
- `manifest.json` and `public_release_manifest.json`: file checksums.
- `comparative_5000_units.csv`: formula-generated scoring profiles used for the sensitivity analysis.

The field records describe authenticated workflow and writeback outcomes; the generated profiles describe the scoring formulas under deterministic perturbations.

## Transaction-safety experiment

```bash
make fault-injection
```

This target runs `patent/run_source_bound_transaction_experiment.py` with seed `20260726` and 2,000 payloads per scenario, then runs the transaction unit tests. Nine fault scenarios are applied to each of four arrangements, producing 72,000 method-level observations. The arrangements receive the same payloads and fault schedules.

Outputs are written to `outputs/reproduced/source_bound_transaction/`: trial records, method and scenario summaries, paired comparisons, a metadata file, checksums, a report, an SVG figure, and a LaTeX table. A3's 18,000 observations record zero unsafe mutations and successful completion of all eligible writes under the declared fault schedules.

## Figures

```bash
make public-figures
```

Figures are written to `outputs/reproduced/public_figures/`. The [B2/B3 pilot guide](artifacts/b2_b3_pilot/README.md) describes the aggregate diagnostic records and the command for reproducing its posterior-learning plot and critic-reward heatmap.
