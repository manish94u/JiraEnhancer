# Five-Reviewer Matched Review Results

## Design

- Five completed, method-blinded workbooks.
- Fourteen common epic/evidence inputs.
- Three executed methods: B1 deterministic template/rule, B2 one-shot generic LLM, and B3 Jira Enhancer.
- Forty-two primary packages and 210 primary stories.
- Four masked repeats per reviewer, giving 20 repeat pairs.
- Reviewers 3--5 are the prespecified primary analysis because Reviewers 1--2 had earlier B3-only exposure.
- All five reviewers form the sensitivity analysis.
- The epic/input is the inference unit (n=14); masked repeats do not enter method-effect estimates.

## Integrity

The five completed workbooks passed structural and content-integrity validation. Instructions, rubrics, artifact catalogs, rating order, and artifact identifiers are unchanged from the issued files. All 230 rows are complete, no required values are missing, and the formula-error scans are clean.

## Primary Results

| Method | Composite mean [95% CI] | Readiness mean | Review time median [IQR] | Required edits median [IQR] | Ready or minor |
|---|---:|---:|---:|---:|---:|
| B1 | 2.74 [2.45, 3.04] | 2.50 | 9.0 [8.0, 10.0] | 10.5 [7.0, 14.0] | 50.0% |
| B2 | 4.21 [3.88, 4.53] | 3.52 | 10.0 [10.0, 11.8] | 2.5 [1.0, 5.0] | 66.7% |
| B3 | 4.31 [4.09, 4.51] | 3.74 | 9.0 [9.0, 10.0] | 2.0 [2.0, 3.0] | 88.1% |

The three-method clustered omnibus test gives Q=19.75 with Monte Carlo p<0.00001.

- Primary B3--B2 composite contrast: +0.10, 95% CI [-0.13, 0.36], Holm-adjusted exact p=0.485, paired rank-biserial r=0.12.
- Secondary B3--B1 composite contrast: +1.56, 95% CI [1.23, 1.91], Holm-adjusted exact p=0.00024, r=1.00.
- B3--B2 assessment-time contrast: -1.02 minutes, 95% CI [-1.57, -0.48], Holm-adjusted exact p=0.0098.
- B3--B2 required-edits contrast: -0.64, 95% CI [-1.55, 0.19], Holm-adjusted exact p=0.205.
- B3--B1 required-edits contrast: -8.02, 95% CI [-9.90, -6.21], Holm-adjusted exact p=0.00024.

The all-five sensitivity analysis preserves these conclusions.

## Dimension Profile

B3 is descriptively higher than B2 on granularity/actionability (+0.55), dependency/risk treatment (+0.29), implementation readiness (+0.21), story distinctness (+0.14), and scope fidelity (+0.07). It is lower on acceptance/testability (-0.43) and coverage completeness (-0.14). None of the seven exploratory dimension contrasts survives Holm correction.

## Agreement Diagnostics

- Primary composite ICC(2,1): 0.999.
- Pairwise exact composite agreement: 96.8%.
- Pairwise within-one-point composite agreement: 100%.
- All 12 primary masked-repeat pairs reproduce all 84 dimension ratings and dispositions exactly.
- Every primary package has three distinct narrative comments.
- Mean pairwise comment-text similarity: 0.127.

The score agreement is unusually high. It is reported together with comment distinctness and should be treated as a limited diagnostic rather than evidence that reviewers are generally interchangeable.

## Resource Accounting

| Method | Model calls | Calls/package | Cumulative model seconds | Static top-up stories |
|---|---:|---:|---:|---:|
| B1 | 0 | 0.0 | 0 | 24 |
| B2 | 14 | 1.0 | 2,087 | 0 |
| B3 | 173 | 12.36 | 19,906 | 0 |

Cumulative invocation seconds sum model-call durations and are not elapsed wall-clock time because inputs were processed in parallel.

## Defensible Interpretation

The matched study supports a large B3 advantage over deterministic templating. It does not support an overall artifact-quality advantage over the executed one-shot generic-LLM baseline. B3 does show lower human assessment time, a more implementation-oriented dimension profile, and fewer major-revision decisions, but it uses substantially more model calls. Governance and mutation safety are evaluated in separate experiments because those mechanisms were hidden during artifact review.

The study does not establish downstream defects, delivery cycle time, trust, end-to-end productivity, compute efficiency, or cross-domain generalization.
