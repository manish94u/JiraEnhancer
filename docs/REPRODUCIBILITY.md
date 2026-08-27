# Reproducibility guide

This guide separates fully offline checks from optional or restricted steps. It
does not require Jira, Confluence, Bitbucket, an LLM account, or private study
workbooks for the core reviewer workflow.

## Environment

Use Python 3.10 or newer. Create an isolated environment, then install the
reviewer dependencies:

```bash
python3 -m venv .venv-reviewer
source .venv-reviewer/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements-reviewer.txt
```

All commands below run from the repository root. Generated files go under
`outputs/reproduced/`, which Git ignores.

## Five-minute offline check

```bash
make test
make mock-demo
make public-artifact
```

`make test` runs the Python unit-test suite. `make mock-demo` processes the
bundled mock issue `DEMO-18324`. It explicitly disables live connectors, model
calls, and the local Codex configuration. No credentials are needed.

`make public-artifact` verifies the released SHA-256 hashes. It also checks the
public schemas for identifiers. Finally, it recomputes the reported counts and
descriptive statistics from the de-identified CSV files. This verifies the
released package. It does not recreate de-identification from private source
telemetry.

## Fault-injection experiment

```bash
make fault-injection
```

This runs the source-bound transaction experiment with the declared seed and
2,000 trials per scenario. It writes a fresh evidence bundle to
`outputs/reproduced/source_bound_transaction/`. The target then runs the focused
causal-transaction unit tests.

The experiment is synthetic and scenario-balanced. Its rates show behavior
under the declared injected faults. They are not estimates of fault prevalence
in a production Jira deployment.

## Public figures

```bash
make public-figures
```

The command rebuilds the public figure subset in
`outputs/reproduced/public_figures/`. It reads only the released files in
`patent/artifacts/tosem/` and writes a manifest with output hashes.

The terminal-confidence figure uses de-identified workflow telemetry. The
B0--B3 distribution figures use formula-expanded sensitivity units. Those units
inspect the declared scoring model. They are not independent production
observations and should not be used as population-level evidence.

Architecture figures are committed directly because they are design diagrams.
Their editable sources are included with the figure assets when available.

## Optional public-dataset diagnostic

This step needs internet access the first time because it retrieves the public
SWE-bench Lite test split:

```bash
make install-benchmark
make public-benchmark
```

The diagnostic applies the manuscript's formula-based scoring functions to
public issue text. It is a sensitivity and transfer check. It does not execute
the complete Jira Enhancer workflow, and it is not a compute-matched comparison
of deployed systems.

## Build the manuscript

A TeX distribution with `latexmk` is required. The build uses committed figures
and the bibliography in `patent/`:

```bash
make paper
```

The compiled PDF and intermediate files are written to
`patent/out_tosem/`, which Git ignores. The committed reviewer PDF remains
unchanged.

To remove LaTeX intermediate files:

```bash
make paper-clean
```

Figure regeneration is intentionally not a prerequisite for the paper build.
This prevents a manuscript compile from reading private runtime data.

## Evidence and access boundaries

The public repository includes code, tests, design figures, synthetic fault
injection, de-identified aggregate evidence, and scripts that operate on those
released inputs. It excludes credentials, connector logs, runtime stores,
personal identifiers, private reviewer workbooks, and raw enterprise issue
text.

The matched-review scripts may be inspected and run with a compatible workbook
supplied by the reviewer. The original workbooks cannot be redistributed. Model
assisted B2/B3 pilot outputs are likewise limited to release-approved aggregate
records; raw prompts and enterprise source packets are outside the public
artifact boundary.

These restrictions mean that reviewers can verify the released calculations
and rerun the offline mechanisms, but cannot recreate every empirical extraction
from the confidential source systems.
