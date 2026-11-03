Equal-weight sensitivity analysis — 3 November 2026

Purpose
Apply one fixed, uniform alternative to the existing exploratory priority-weighted composite on the current corrected 40-reviewer dataset. No weights are searched, fitted, or selected for significance.

Design
The source contains 1,840 ratings: 1,680 primary ratings and 160 masked repeats. Repeats are excluded from every method-effect calculation. Every input-method cell contains all 40 reviewers. The inference unit is the epic (14 matched epics), not the individual rating.
The seven dimensions are scope fidelity, coverage completeness, story distinctness, acceptance/testability, dependencies/risks, granularity/actionability, and implementation readiness. Uniform weights are exactly 1/7: scores are summed and divided by 7, never multiplied by seven rounded 14.3% weights.
The unchanged priority weights are 5%, 5%, 5%, 5%, 20%, 20%, 40%, in the dimension order above.
Each reviewer composite is linear in the dimension scores. Averaging each dimension across 40 reviewers and then calculating the composite is algebraically identical to averaging the 40 reviewer composites.

Inference
Two-sided exact paired sign-flip tests enumerate all 2^14 sign assignments. Percentile confidence intervals use 100,000 bootstrap resamples of whole paired epics. B3-B2 uses seed 20261313; B3-B1 uses 20261314. Both weighting schemes use identical bootstrap draws for a given contrast. The explicit random generator is NumPy PCG64.
The main Holm family contains the two method contrasts separately within each weighting. An additional reported sensitivity applies Holm to all four weighting-by-contrast tests. This additional family does not correct for post-analysis selection of weights or changes to source ratings.

Method means (anchored 1-5 scale)
priority_weighted: B1=2.556428571, B2=3.606428571, B3=4.147946429
equal_weight: B1=2.736734694, B2=3.724489796, B3=4.283418367

Contrasts
priority_weighted B3-B2: difference=0.541517857; 95% CI [0.508750000, 0.573035714]; raw p=0.000122070312; Holm(two)=0.000244140625; Holm(four sensitivity)=0.000488281250; positive/negative/tied epics=14/0/0.
priority_weighted B3-B1: difference=1.591517857; 95% CI [1.204553571, 1.983839286]; raw p=0.000122070312; Holm(two)=0.000244140625; Holm(four sensitivity)=0.000488281250; positive/negative/tied epics=14/0/0.
equal_weight B3-B2: difference=0.558928571; 95% CI [0.546938776, 0.571173469]; raw p=0.000122070312; Holm(two)=0.000244140625; Holm(four sensitivity)=0.000488281250; positive/negative/tied epics=14/0/0.
equal_weight B3-B1: difference=1.546683673; 95% CI [1.209948980, 1.873214286]; raw p=0.000122070312; Holm(two)=0.000244140625; Holm(four sensitivity)=0.000488281250; positive/negative/tied epics=14/0/0.

Interpretation
B3 has a higher mean than both baselines on all 14 paired epics under either specified weighting. Thus, the observed direction of comparison in this corrected dataset persists when the priority weights are replaced by uniform weights. This is a sensitivity result on the same data, not an independent study or independent validation of rating corrections. The weights remain exploratory.

Files
analyze_weight_sensitivity.py: complete validation, aggregation, and analysis code.
public_epic_dimension_means.csv: 42 public aggregate rows with neutral U01-U14 identifiers, method, count of reviewers, and the seven dimension means. No individual rating, reviewer code, comment, source requirement, or private epic identifier is exported.
reference_priority_epic_scores.csv: byte-for-byte copy of the existing public priority-weight aggregate, used to validate every derived priority score and the neutral-unit mapping.
epic_scores.csv: paired priority/equal-weight scores for all 42 cells.
method_means.csv: six weighting-by-method summaries.
comparisons.csv and results.json: all four contrasts, inference settings, and complete precision results.
source_validation.json: immutable-source checks, sample accounting, and excluded-repeat counts.
CHECKSUMS.sha256: checksums for this standalone analysis package.

Public reproduction (requires Python 3 and NumPy)
  python3 analyze_weight_sensitivity.py --output-dir /tmp/weight-sensitivity-reproduction
This reads the bundled aggregate and priority reference only. It does not require private source data.

Authorized private-source aggregation
  python3 analyze_weight_sensitivity.py --source /path/to/matched_review_unblinded_ratings.csv --output-dir /tmp/weight-sensitivity-private-check
The source must match the declared checksum. The script reads but never rewrites source ratings. It validates the matched grid and recreates only de-identified aggregate outputs.

Validation performed
The private-source aggregation and public-only reproduction produced byte-identical derived outputs. All 42 priority cells, the published priority method means, both paired contrasts, confidence intervals, and p-values match the existing released values within 1e-12. The original ratings and public priority aggregate are unchanged.
Source SHA-256: 61185752527cc5f5361d7c8c7c6fd55d5c62805d44d94d74dfbfb8adcaae1b6e
NumPy version: 2.5.4

Measured two-panel figure
measured_review_composite_components.pdf: vector figure sized 5.15 x 4.05 inches with embedded TrueType fonts.
measured_review_composite_components.png: matching 300 dpi image (1545 x 1215 pixels).
measured_review_composite_components.json: plot provenance, source hashes, composite quartiles/medians, bootstrap settings, and recommended caption.
measured_review_dimension_statistics.csv: all 21 dimension-by-method means and 95% percentile epic-bootstrap intervals.
plot_measured_review_composite_components.py: public-input reproduction script (requires Matplotlib and NumPy).

Panel (a) shows the distributions of the 14 matched epic-level priority-weighted scores per method: descriptive violins, all 14 colored observations, IQR bars, white median markers, and mean labels. Panel (b) shows the seven observed requirement dimensions, on the anchored 1-5 scale. The earlier formula-generated Quality/Efficiency/Governance indices and an unexecuted B0 condition are not used in this measured display.
Dimension intervals use 100,000 paired-epic bootstrap index sets with PCG64 seed 20261103. These intervals estimate individual method means; they are not paired-contrast intervals or multiplicity-adjusted tests. The figure uses the corrected ratings, excludes masked repeats, and explicitly identifies the post-analysis priority weighting as exploratory.

Figure reproduction
  python3 plot_measured_review_composite_components.py --output-dir /tmp/measured-review-figure
A standalone regeneration reproduced the PDF, PNG, provenance JSON, and statistic CSV byte-for-byte. All 42 priority-weighted epic scores match the frozen reference, and all 21 dimension means match the existing public dimension summary within 1e-12. Both the PNG and a raster rendering of the vector PDF were visually inspected. Text, labels, confidence intervals, and markers are unclipped and legible at the intended article width. Source input hashes are unchanged.

Recommended caption
Measured matched-review outcomes from the corrected 40-reviewer dataset. (a) Distributions of priority-weighted package scores across 14 matched epics for B1 (template), B2 (one-shot drafting), and B3 (Jira Enhancer). Each colored point is an epic-level mean over 40 reviewers; violins are descriptive density estimates, dark bars span the interquartile range, white markers show medians, and numeric labels show method means. (b) Means for the seven observed requirement dimensions on the anchored 1–5 scale, with 95% percentile confidence intervals from 100,000 epic-bootstrap resamples. Each method has 560 primary ratings; masked repeats are excluded. The priority weights were selected after the initial analysis, so the composite comparison is exploratory. These measured outcomes are distinct from the formula-generated scoring checks in Appendix A.
