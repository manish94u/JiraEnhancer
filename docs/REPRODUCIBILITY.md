# Reproducibility guide

This guide separates fully offline checks from optional or restricted steps. It
does not require Jira, Confluence, Bitbucket, an LLM account, or private study
workbooks for the core reviewer workflow.

## Environment

Use Python 3.11 or newer. Create an isolated environment, then install the
reviewer dependencies:

```bash
python3 -m venv .venv-reviewer
source .venv-reviewer/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements-reviewer.txt
```

All commands below run from the repository root. The offline reviewer targets
write regenerated outputs under `outputs/reproduced/`, which Git ignores. The
optional public benchmark writes manuscript-ready outputs under
`patent/generated/`. The manuscript build writes intermediates and its compiled
copy under `patent/out_tosem/`.

## Five-minute offline check

```bash
make test
make mock-demo
make public-artifact
```

`make test` runs the Python unit-test suite. `make mock-demo` processes the
bundled mock issue `DEMO-18324`. It explicitly disables live connectors, model
calls, and the local Codex configuration. No credentials are needed.

`make public-artifact` verifies only the released package under
`patent/artifacts/tosem/`. It checks SHA-256 hashes and public schemas for
identifiers. It also recomputes the reported counts and descriptive statistics
from the de-identified CSV files. This verifies the released package. It does
not recreate de-identification from private source telemetry.

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
`outputs/reproduced/public_figures/`. The confidence and scoring figures read
released files in `patent/artifacts/tosem/`. The arm-learning and critic-reward
figures read the sanitized trace in `patent/artifacts/b2_b3_pilot/`. No target
reads `runtime/`. `reproduce_public_figures.py` writes the public-figure
manifest with output hashes. The separate B2/B3 arm-learning command writes its
two PNG files after that manifest is created. The timeline and Laplace figures
use the committed Pillow renderer so their layout does not change when
Matplotlib is installed.

The terminal-confidence figure uses de-identified workflow telemetry. The
B0--B3 distribution figures use formula-expanded sensitivity units. Those units
inspect the declared scoring model. They are not independent production
observations and should not be used as population-level evidence.

The target also rebuilds the B2/B3 arm-selection figure. It reads the sanitized
99-step posterior trace in `patent/artifacts/b2_b3_pilot/`. The trace contains
arm identifiers, rewards, and posterior values. It contains no source packet,
generated text, or held-out input identifier. This figure shows how selection
weights changed. It is not a learning curve for human-rated quality or delivery
performance.

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
and `patent/references_2020_plus.bib`:

```bash
make paper
```

The compiled PDF and intermediate files are written to
`patent/out_tosem/`, which Git ignores. The repository also includes the current
47-page reviewer PDF at `patent/jiraenhancer_tosem_acm.pdf`.

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

The released four-mode audit builder may be inspected and run with compatible
review inputs supplied by the reviewer. Other matched-review processing scripts
and the original workbooks cannot be redistributed. The
B2/B3 pilot release contains aggregate results and a sanitized posterior trace.
Raw prompts, generated outputs, evaluator narratives, and enterprise source
packets remain outside the public artifact boundary.

These restrictions mean that reviewers can verify the released calculations
and rerun the offline mechanisms, but cannot recreate every empirical extraction
from the confidential source systems.

## Build the clean reviewer release

The private development tree contains credentials and restricted runtime data.
Do not publish its Git history. Build a new public tree from the reviewed
allowlist instead:

```bash
python scripts/build_reviewer_release.py \
  --destination ../JiraEnhancer-reviewer-release
```

The destination must be empty and outside the private source tree. The builder
creates `RELEASE_FILE_MANIFEST.json` with a SHA-256 digest for every copied file.
Initialize and publish Git history only from that clean destination.
