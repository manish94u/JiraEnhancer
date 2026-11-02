# Reproducibility guide

The core reviewer workflow runs locally from the released code, synthetic fixtures, and de-identified evidence. Optional steps cover public-dataset downloads, model-backed generation, and authenticated enterprise connectors.

## Environment

Use Python 3.11 or newer. Create an isolated environment and install the reviewer dependencies:

```bash
python3 -m venv .venv-reviewer
source .venv-reviewer/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements-reviewer.txt
```

Run the commands below from the repository root. Offline reviewer targets write their outputs under `outputs/reproduced/`. The optional public benchmark writes manuscript-ready outputs under `patent/generated/`; LaTeX intermediates and the compiled paper go to `patent/out_tosem/`.

## Five-minute offline check

```bash
make test
make mock-demo
make public-artifact
```

`make test` runs the Python unit tests. `make mock-demo` processes the bundled `DEMO-18324` fixture using deterministic local reasoning and stores the run locally.

`make public-artifact` verifies the released package under `patent/artifacts/tosem/`. It checks SHA-256 hashes, schemas, identifiers, counts, and descriptive statistics against the de-identified CSV files. Exporting the confidential source telemetry is a separate access-controlled step.

## Fault-injection experiment

```bash
make fault-injection
```

The experiment uses the declared seed, nine scenarios, 2,000 trials per scenario, and four control arrangements, giving 72,000 observations. It writes a fresh evidence bundle to `outputs/reproduced/source_bound_transaction/` and runs the focused transaction tests. The resulting rates describe behavior under these scenario-balanced injected faults.

## Public figures

```bash
make public-figures
```

The command writes figures to `outputs/reproduced/public_figures/`. The endpoint and scoring figures read the released files in `patent/artifacts/tosem/`; the arm-learning and critic-reward figures read the sanitized trace in `patent/artifacts/b2_b3_pilot/`.

`reproduce_public_figures.py` writes a manifest with output hashes. The separate B2/B3 arm-learning command then writes its two PNG files. The committed Pillow renderer keeps the timeline and Laplace layouts consistent across environments.

Endpoint score fields in the released telemetry are retrospective export values. The B0--B3 distribution figures use formula-expanded sensitivity units derived from reused inputs. Their purpose is to inspect the scoring equations. The 99-step arm trace records model-critic rewards and posterior values, showing how prompt-selection weights changed. Human artifact ratings are reported in the matched review below.

Architecture figures are supplied as design diagrams, with editable sources alongside the figure assets where available.

## Current matched human review

The current analysis uses reviewer-corrected ratings for 42 B1/B2/B3 packages and 210 stories from 14 common epic inputs. Reviewers 3--5 form the primary set; all five reviewers form the sensitivity analysis. Each epic is one paired inference unit. Masked repeats contribute to agreement checks.

The revised composite gives implementation readiness 40%, dependencies and risks 20%, granularity and actionability 20%, and each remaining rubric dimension 5%. These weights were selected after the initial analysis and define an exploratory weighted outcome. The primary weighted means are B3=4.32 and B2=3.59, with a difference of +0.74 points (95% epic-bootstrap CI [0.55, 0.93], Holm-adjusted exact p=0.000244).

The public tables in `patent/reviewer_study/public_results/` report method summaries, paired contrasts, rubric dimensions, agreement diagnostics, and resource accounting. Recalculate the current weighted contrasts from the released epic means with:

```bash
python3 patent/reviewer_study/verify_weighted_results.py
```

The command reads `matched_review_epic_scores.csv`, which contains 84 aggregate rows: 14 neutral unit identifiers, three methods, and two reviewer sets. It recomputes means, exact sign-flip p-values, Holm adjustments, and 100,000-resample paired confidence intervals. Reviewer-level ratings, narrative comments, and allocation keys remain access-controlled. The private source calculation is implemented in `patent/artifacts/remediation/weighted_quality/update_current_reports.py`.

The comparison measures complete workflows: B2 used 14 model calls and B3 used 173. The corrected assessment-time records show 2.15 minutes lower mean review time for B3 (95% CI [-2.35, -1.99], Holm-adjusted exact p=0.000244).

## Public-dataset diagnostic

The first run retrieves the public SWE-bench Lite test split:

```bash
make install-benchmark
make public-benchmark
```

This diagnostic applies the declared scoring functions to public issue descriptions. It measures the sensitivity of the formulas across source texts, separately from the executed B1/B2/B3 human-review experiment.

## Build the manuscript

Install a TeX distribution with `latexmk`, then run:

```bash
make paper
```

The build reads the committed figures and `patent/references_2020_plus.bib`. It writes the PDF and intermediate files to `patent/out_tosem/`. The current 44-page reviewer PDF is `patent/jiraenhancer_tosem_acm.pdf`. Figure regeneration is a separate target, so compiling the paper uses the existing publication assets.

Clean the LaTeX intermediate files with:

```bash
make paper-clean
```

## Evidence access

The public repository supplies code, tests, design figures, synthetic fault schedules, de-identified evidence, aggregate human-review results, and scripts for those released inputs. Enterprise source text, reviewer-level records, credentials, and live runtime state remain in access-controlled storage.

The four-mode audit builder accepts compatible review inputs. The B2/B3 diagnostic release provides aggregate results and a sanitized posterior trace; its enterprise packets and generated artifacts remain restricted. Each reproduction path above states the input and output that it verifies.

## Build the reviewer release

The release builder creates a fresh public tree from a reviewed file allowlist:

```bash
python scripts/build_reviewer_release.py \
  --destination ../JiraEnhancer-reviewer-release
```

Use an empty destination outside the private source tree. The builder writes `RELEASE_FILE_MANIFEST.json` with a SHA-256 digest for each copied file. Initialize and publish the public Git history from this clean destination, keeping credentials and restricted runtime material in the private development environment.
