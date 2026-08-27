# Jira Enhancer

Jira Enhancer is a governance-aware workflow for turning incomplete Jira issues and epics into reviewable planning artifacts. It gathers linked evidence, scores readiness, selects a bounded prompt policy, produces a draft, and requires explicit human approval before writeback. The repository accompanies the TOSEM manuscript **“Jira Enhancer: A Governance-Aware Human–AI Collaboration System for Epic-to-Story Decomposition in Software Engineering.”**

This is a reviewer-facing artifact. It contains the implementation, tests, API contracts, manuscript source, publication figures, deterministic experiments, and de-identified evidence that can be shared publicly. It does not contain credentials, internal Jira records, reviewer workbooks, reviewer comments, or confidential source packets.

![Jira Enhancer system architecture](patent/images/system_architecture_embedded.png)

## What is included

```text
.
├── src/jira_enhancer/              # application, connectors, governance, and RL policy code
├── tests/                          # unit, workflow, RL, and transaction-safety tests
├── openapi/                        # external, internal, and event contracts
├── patent/
│   ├── artifacts/tosem/            # de-identified and synthetic reviewer artifacts
│   ├── artifacts/b2_b3_pilot/      # aggregate pilot data and sanitized posterior trace
│   ├── generated/                  # manuscript figures and selected result tables
│   ├── images/                     # architecture and module figures
│   ├── reviewer_study/public_results/
│   ├── reproduce_public_figures.py
│   ├── generate_b2_b3_learning_figure.py
│   ├── run_rl_epoch_simulation.py
│   ├── run_source_bound_transaction_experiment.py
│   ├── verify_public_artifact.py
│   ├── jiraenhancer_tosem_acm.tex
│   └── jiraenhancer_tosem_acm.pdf
├── .env.example                    # placeholders only; never commit a real .env
├── Makefile
└── pyproject.toml
```

## Evidence map and claim boundaries

The paper combines several forms of evidence. They answer different questions and must not be pooled as if they were one experiment.

| Evidence | Public input | Reproduction level | What it supports |
|---|---|---|---|
| Unit and workflow tests | Synthetic fixtures | Fully executable offline | Application behavior and workflow contracts |
| Source-bound transaction experiment | Fixed-seed synthetic fault schedule | Fully executable offline | Mutation safety under the declared injected faults |
| Authenticated Jira outcome summary | 70 de-identified terminal outcomes and a 45-item audit | Hash and statistic verification | Observed endpoint counts and confidence changes in the reported deployment waves |
| Matched blinded review | Aggregate de-identified result tables | Result inspection; raw workbooks are restricted | Artifact ratings, review time, and revision decisions for the reported study |
| 1,000-epoch policy replay | De-identified outcome rows | Fully executable offline | Diagnostic Thompson-sampling behavior over recorded outcome profiles |
| 5,000-unit B0–B3 analysis | Anonymized formula-expanded units | Fully executable offline | Sensitivity of the declared scoring model; not 5,000 independent field observations |
| SWE-bench Lite analysis | Public issue text | Executable after dataset download | Cross-dataset scoring sensitivity; not patch generation or SWE-bench resolution performance |
| B2/B3 model-assisted pilot | Aggregate scores, learned-state summary, and sanitized posterior trace | Aggregate verification and learning-figure reproduction; not a model replay | A limited one-shot-versus-governed text comparison under the recorded model conditions |

The raw enterprise inputs are withheld because they contain issue text, URLs, account data, and operational metadata. The completed human-review workbooks are withheld because they contain reviewer-level records and comments. The public files preserve the aggregate evidence used by the manuscript without publishing those records.

## Requirements

- Python 3.10 or newer
- A POSIX shell for the Makefile examples
- A LaTeX distribution with `latexmk` for rebuilding the manuscript
- Network access only for the optional SWE-bench Lite download or live connectors

The core application uses the Python standard library. Research and figure scripts use the `reviewer` dependency group.

## Five-minute reviewer check

Create an isolated environment and install the project:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[reviewer]'
```

Run the public artifact verification, the full test suite, the seeded fault
schedule, and public figure regeneration:

```bash
make reviewer-check
```

To run only the manuscript-sized fault schedule, use:

```bash
make fault-injection
```

Generated files are written below `outputs/reproduced/`. Archived publication outputs remain unchanged. The public figure target also rebuilds the learned arm-selection curve from its sanitized posterior trace.

## Run the application with synthetic data

The mock mode requires no Jira account and performs no remote writes.

```bash
export CODEX_CONFIG_PATH="$(pwd)/reviewer_runtime/no-codex-config.toml"
export JIRA_ENHANCER_CONNECTOR_MODE=mock
export JIRA_ENHANCER_REASONING_MODE=heuristic
export JIRA_ENHANCER_USE_CODEX_EXEC=false
export JIRA_ENHANCER_RL_ENABLED=false

PYTHONPATH=src python -m jira_enhancer \
  --storage-root ./reviewer_runtime \
  run \
  --scope-type issue \
  --scope-value DEMO-18324 \
  --requestor reviewer@example.org
```

The global `--storage-root` option must appear before the subcommand. The command prints a `run_id`. Use it in the following commands:

```bash
PYTHONPATH=src python -m jira_enhancer --storage-root ./reviewer_runtime \
  show-run --run-id RUN_ID

PYTHONPATH=src python -m jira_enhancer --storage-root ./reviewer_runtime \
  approve --run-id RUN_ID --issue-key DEMO-18324 \
  --decision approve --reviewer approver@example.org

PYTHONPATH=src python -m jira_enhancer --storage-root ./reviewer_runtime \
  writeback --run-id RUN_ID --issue-key DEMO-18324
```

In mock mode, writeback updates only the local filesystem-backed Jira fixture.

Start the local UI and API with:

```bash
PYTHONPATH=src python -m jira_enhancer \
  --storage-root ./reviewer_runtime serve --host 127.0.0.1 --port 8080
```

Open <http://127.0.0.1:8080/>. The UI can create a run, inspect candidates, record approval or rejection, request regeneration or user input, execute approved writeback, and replay a run.

## Workflow

The implementation keeps reasoning separate from mutation:

1. The scope resolver expands an issue, epic, sprint, or release.
2. The scanner reads issue fields and linked evidence.
3. The scorer records readiness, confidence, warnings, and policy flags.
4. The prompt service filters policy arms by governance predicates and selects an eligible arm.
5. The reasoning layer creates or refines a candidate draft.
6. Validation checks schema, contradictions, required fields, and duplicate targets.
7. A reviewer records an explicit decision.
8. A source-bound causal envelope binds the source revision, draft, policy, reviewer decision, target set, and idempotency key.
9. Writeback proceeds only if the envelope still matches the current state.

The RL component adapts prompt selection. It does not bypass validation, policy, approval, or transaction checks.

## API contracts

The HTTP contracts are in [`openapi/`](openapi/). Principal endpoints include:

- `POST /api/v1/runs`
- `GET /api/v1/runs/{runId}`
- `GET /api/v1/runs/{runId}/items/{issueKey}`
- `POST /api/v1/runs/{runId}/items/{issueKey}/approval`
- `POST /api/v1/runs/{runId}/items/{issueKey}/writeback`
- `POST /api/v1/replay/{runId}`
- `POST /api/v1/epics/{epicKey}/stories/suggest`
- `POST /api/v1/epics/{epicKey}/stories/create`

The approval contract accepts `approve`, `reject`, `regenerate`, `user_input`, and `prompt`. This allows a reviewer to give a suggestion or a new prompt before a candidate is approved.

## Reproduce the public evidence

### 1. Verify the de-identified Jira artifact

```bash
python patent/verify_public_artifact.py
```

The verifier checks SHA-256 hashes, row counts, wave counts, confidence statistics, creation outcomes, link checks, and audited payload coverage against `patent/artifacts/tosem/analysis_summary.json`.

The verification starts from already de-identified rows. It does not recreate the export from restricted enterprise records.

### 2. Run all application tests

```bash
PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py' -v
```

Run the transaction tests alone with:

```bash
PYTHONPATH=src python -m unittest tests.test_causal_transaction -v
```

### 3. Reproduce the source-bound transaction experiment

```bash
PYTHONPATH=src python patent/run_source_bound_transaction_experiment.py \
  --output-dir outputs/reproduced/source_bound_transaction \
  --trials-per-scenario 2000 \
  --seed 20260726
```

The experiment applies the same transaction payload and fault schedule to four control arrangements. It writes trial rows, summaries, paired comparisons, an SVG, a LaTeX table, metadata, and checksums. These are scenario-balanced synthetic fault outcomes. They are not estimates of production incident prevalence.

### 4. Reproduce the offline RL replay

```bash
PYTHONPATH=src python patent/run_rl_epoch_simulation.py \
  --outcomes patent/artifacts/tosem/anonymized_live_story_outcomes.csv \
  --output-dir outputs/reproduced/rl_replay \
  --epochs 1000 \
  --seed 42
```

This replay uses Thompson sampling over de-identified observed outcome profiles. The 1,000 epochs are offline policy steps. They are not 1,000 live Jira mutations.

### 5. Rebuild the public figures

```bash
make public-figures
```

This target reads only released files under `patent/artifacts/`. It regenerates the de-identified confidence view, the scoring-model comparison figures, and the learned arm-selection curve. It does not read `runtime/`.

To rebuild only the learned-selection curve, use:

```bash
PYTHONPATH=src python patent/generate_b2_b3_learning_figure.py \
  --output outputs/reproduced/figures/b2_b3_arm_learning.png
```

The curve is rebuilt from a sanitized 99-step posterior trace. It contains no input identifier or source text. The figure shows learned selection weights, not human-rated quality or delivery performance.

The editable architecture source is `patent/images/system_architecture.drawio`. The four module diagrams and control-flow image are provided as publication-resolution PNG files.

### 6. Inspect the matched-review aggregates

Public aggregate results are under:

```text
patent/reviewer_study/public_results/
```

The directory contains method summaries, paired contrasts, dimension summaries, reliability diagnostics, and resource accounting. It excludes raw ratings, comments, identity mappings, private allocation keys, and workbooks. Therefore, the public release supports inspection of the reported aggregates, not a fresh unblinding from raw human-review records.

### 7. Run the optional SWE-bench Lite sensitivity diagnostic

Install the benchmark extras and run:

```bash
python -m pip install -e '.[benchmark]'
python patent/run_public_benchmark_eval.py
python patent/generate_cross_dataset_comparison.py
```

The first run downloads `princeton-nlp/SWE-bench_Lite` from Hugging Face. This analysis sends public issue text through the declared scoring formulas. It does not generate patches or execute the SWE-bench test harness. Its output is a cross-dataset sensitivity check, not comparative software-resolution performance.

## Build the manuscript

The repository includes the exact LaTeX source, bibliography, included tables, and figures needed by the manuscript.

```bash
cd patent
latexmk -pdf -interaction=nonstopmode -halt-on-error \
  -outdir=out_tosem jiraenhancer_tosem_acm.tex
```

The prebuilt reviewer copy is `patent/jiraenhancer_tosem_acm.pdf`.

## Optional live connectors

Live mode is not needed to review or reproduce the public artifact. It can connect to Jira, Confluence, and Bitbucket through REST adapters. Copy the placeholder file and supply credentials through the environment:

```bash
cp .env.example .env
export JIRA_ENHANCER_CONNECTOR_MODE=live
export JIRA_BASE_URL=https://jira.example.com
export JIRA_USERNAME=user@example.com
export JIRA_TOKEN='replace-with-a-local-secret'
export CONFLUENCE_BASE_URL=https://confluence.example.com
export CONFLUENCE_USERNAME=user@example.com
export CONFLUENCE_TOKEN='replace-with-a-local-secret'
export BITBUCKET_BASE_URL=https://bitbucket.example.com
export BITBUCKET_USERNAME=user@example.com
export BITBUCKET_TOKEN='replace-with-a-local-secret'
```

Never commit `.env`. Live writeback should first be tested against a non-production project with restricted field mappings and a least-privilege service account.

## Reproducibility limitations

- Restricted enterprise records cannot be reconstructed from the public artifact.
- Human-review aggregates cannot be recalculated from raw workbooks because reviewer-level files are not released.
- The B2/B3 pilot figure can be rebuilt from the released posterior trace. The underlying model generations cannot be replayed from public data because the enterprise source packets and generated text are restricted.
- Model service versions, stochastic generation, and external dataset versions may affect a fresh rerun.
- Formula-expanded 5,000-unit rows are diagnostic pseudo-replications. They are not independent observations.
- Public benchmark results assess the scoring model on issue text. They do not assess code generation or patch correctness.
- The repository does not claim downstream defect reduction, delivery-time savings, compute efficiency, or cross-domain generalization.

## Security and data-handling notes

- The published Git history is created from a reviewed allowlist. The private development history is not included.
- Real tokens and local runtime state are excluded.
- All published live-outcome rows use de-identified IDs.
- Generated public figures use neutral identifiers and contain no internal issue keys.
- `.gitignore` blocks credentials, runtime state, private reviewer folders, and build debris.

If a credential is ever committed locally, deleting the file in a later commit is not sufficient. Rotate the credential and publish from clean history.

## License and citation

No open-source license is granted by this repository at present. The code and artifact are supplied for scholarly review and reproducibility inspection. Contact the authors before redistribution or reuse.

Please cite the manuscript title above. Formal publication metadata will be added after acceptance.
