# Reproduction guide

Run the commands in this guide from the repository root. The package supplies executable code, synthetic test fixtures, de-identified field tables, aggregate human-review results, and scoring-model inputs. Each workflow below identifies the data it reads and the result it produces.

## Environment

Use Python 3.11 or newer:

```bash
python3 -m venv .venv-reviewer
source .venv-reviewer/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements-reviewer.txt
```

The `reviewer` dependency group supplies NumPy, SciPy, pandas, Matplotlib, Pillow, and openpyxl. A POSIX shell and `make` run the convenience targets. Rebuilding the paper also requires a LaTeX distribution with `latexmk`.

## Run the replication checks

```bash
make reviewer-check
```

`make reviewer-check` runs the unit tests, verifies the released field artifact, recalculates the matched-review contrasts, executes the full seeded fault schedule, and regenerates the current research figures.

The matched-review verifier reads the public epic means and prints a JSON result containing method means, paired differences, confidence intervals, raw exact p-values, and Holm-adjusted p-values.

## Application and workflow tests

```bash
make test
make mock-demo
```

The test target runs `unittest` discovery over `tests/`. The demonstration processes the bundled `DEMO-18324` fixture with mock connectors and deterministic local reasoning. It writes its newly created run records to `outputs/reproduced/mock-runtime/`.

To run the transaction tests alone:

```bash
PYTHONPATH=src python3 -m unittest tests.test_causal_transaction -v
```

## De-identified field outcomes

```bash
make public-artifact
```

Input: `patent/artifacts/tosem/`, including its analysis summary and de-identified CSV files.

The verifier checks SHA-256 hashes, schemas, neutral identifiers, row counts, deployment-wave counts, creation outcomes, parent links, payload coverage, and descriptive score statistics. The released field rows represent 70 terminal outcomes: 69 created Jira stories and one linked existing record. The latest-wave audit covers 45 parent links and 44 new payloads. Endpoint confidence fields are retrospective export values.

## Source-bound transaction experiment

```bash
make fault-injection
```

This target runs the experiment and its focused tests. The exact experiment command is:

```bash
PYTHONPATH=src python3 patent/run_source_bound_transaction_experiment.py \
  --output-dir outputs/reproduced/source_bound_transaction \
  --trials-per-scenario 2000 \
  --seed 20260726
```

The schedule contains nine scenarios and four control arrangements, yielding 72,000 observations in total and 18,000 per arrangement. The script writes trial rows, scenario summaries, paired comparisons, metadata, checksums, an SVG, and a LaTeX table. The reported rates use this scenario-balanced injected-fault distribution. A3 records zero unsafe mutations and permits all eligible writes under the tested schedules.

## Matched human-review calculations

The study contains 40 reviewers, 14 common epic inputs, 42 B1/B2/B3 packages, and 210 stories. The rating set contains 1,680 primary ratings and 160 masked repeats. The public analysis label is `primary_all_40`.

Scores are averaged over reviewers within each epic and method. The 14 matched epics are the inference units. Masked repeats are used for agreement checks.

| Dimension | Composite weight |
|---|---:|
| Implementation readiness | 40% |
| Dependencies and risks | 20% |
| Granularity and actionability | 20% |
| Scope fidelity | 5% |
| Coverage completeness | 5% |
| Story distinctness | 5% |
| Acceptance and testability | 5% |

The weights were selected after the initial analysis and define an exploratory weighted outcome using corrected reviewer ratings. The same weights apply to all methods and reviewers.

Run:

```bash
make matched-review
```

The target executes `python3 patent/reviewer_study/verify_weighted_results.py`.

Input: `patent/reviewer_study/public_results/matched_review_epic_scores.csv`. Its 42 rows contain 14 neutral unit identifiers and three methods, with each score averaged across all 40 reviewers.

The verifier enumerates the exact paired sign-flip distribution and uses 100,000 paired epic-bootstrap resamples with base seed `20260813`. Holm's adjustment is applied to the B3–B2 and B3–B1 contrasts within the weighted-composite family.

Expected weighted means are B1 = 2.56, B2 = 3.61, and B3 = 4.15. The B3–B2 difference is +0.54 points (95% CI [0.51, 0.57]). For each weighted contrast, two of the 16,384 sign assignments are as extreme as the observed difference: raw p = 0.0001220703125 and Holm-adjusted p = 0.000244140625.

The [results summary](../patent/reviewer_study/public_results/RESULTS.md), [Holm calculation](../patent/reviewer_study/public_results/HOLM_RECALCULATION.md), and [calculation table](../patent/reviewer_study/public_results/matched_review_holm_calculation.csv) contain the aggregate results for the declared outcome families. Seven-dimension summaries, reliability statistics, revision dispositions, and resource accounting accompany those files.

The recorded mean assessment-time difference is −2.17 minutes for B3 versus B2 (95% CI [−2.39, −1.98], Holm-adjusted p = 0.000244). The complete workflow comparison used 14 model calls for B2 and 173 for B3; cumulative invocation durations were 2,087 and 19,906 seconds respectively.

Rebuild the radar plot from the dimension summaries:

```bash
python3 patent/reviewer_study/plot_weighted_review_radar.py
```

This writes `matched_review_weighted_radar.png` and `.pdf` under `patent/reviewer_study/public_results/`.

## Research figures

```bash
make public-figures
```

The target rebuilds four figures used by the current manuscript: recorded refinement effort, the matched-review radar, arm-learning posterior means, and the model-critic reward heatmap. Their inputs are the released refinement-effort tables, seven-dimension review summaries, and the sanitized model-critic trace in `patent/artifacts/b2_b3_pilot/`.

The refinement-effort figure describes the recorded attempt counts for 70 stories in matched B3 generation. The radar combines raw dimension means with the declared priority weights. The held-out diagnostic tables describe model assessments of three packets; the 99-step training trace records critic rewards and posterior updates from 11 packets, supporting the arm-learning and reward-heatmap figures.

To regenerate the latter two figures directly:

```bash
PYTHONPATH=src python3 patent/generate_b2_b3_learning_figure.py \
  --output outputs/reproduced/public_figures/b2_b3_arm_learning.png \
  --reward-output outputs/reproduced/public_figures/b2_b3_critic_reward_heatmap.png
```

Architecture diagrams are supplied as manuscript design assets, including `patent/images/system_architecture.drawio`.

## Public issue-text sensitivity

```bash
make install-benchmark
make public-benchmark
```

The accompanying 5,000-unit files describe deterministic scoring-formula sensitivity on generated units. The public-data command extends this check to issue descriptions: its first run retrieves the `princeton-nlp/SWE-bench_Lite` test split. The scripts apply the declared scoring formulas to public issue descriptions and write comparative statistics, tables, and figures under `patent/generated/`. The dataset cache is created under `outputs/reproduced/huggingface/`.

## Build the manuscript

```bash
make paper
```

The build reads the committed LaTeX source, bibliography, tables, and figures. It writes the PDF and intermediate files to `patent/out_tosem/`. The supplied current reviewer copy is the [46-page manuscript](../patent/jiraenhancer_tosem_acm.pdf).

The TeX command can also be run directly:

```bash
cd patent
latexmk -pdf -interaction=nonstopmode -halt-on-error \
  -outdir=out_tosem jiraenhancer_tosem_acm.tex
```

Figure generation and manuscript compilation are separate commands, allowing the supplied figure assets to be used directly. `make paper-clean` removes LaTeX intermediate files.

## File verification

`RELEASE_FILE_MANIFEST.json` lists the SHA-256 digest of every distributed file. The field artifact and transaction experiment also provide data-specific checksum files. Together these identify the inputs used by each replication workflow.
