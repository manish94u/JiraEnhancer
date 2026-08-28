PYTHON ?= python3
LATEXMK ?= latexmk

REPRO_ROOT ?= outputs/reproduced
PUBLIC_ARTIFACT_DIR ?= patent/artifacts/tosem
PUBLIC_FIGURE_DIR ?= $(REPRO_ROOT)/public_figures
FAULT_OUTPUT_DIR ?= $(REPRO_ROOT)/source_bound_transaction
PAPER_OUTPUT_DIR ?= out_tosem
PAPER_TEX ?= jiraenhancer_tosem_acm.tex

.PHONY: help install-reviewer install-benchmark test mock-demo \
	public-artifact fault-injection public-figures public-benchmark \
	reviewer-check paper paper-clean

help:
	@echo "Reviewer targets:"
	@echo "  make install-reviewer  Install the offline analysis and figure dependencies"
	@echo "  make test              Run the complete unit-test suite"
	@echo "  make mock-demo         Run one local issue with mock data and no external service"
	@echo "  make public-artifact   Verify released hashes, schemas, counts, and statistics"
	@echo "  make fault-injection   Re-run the seeded source-bound transaction experiment"
	@echo "  make public-figures    Rebuild the public figure subset from de-identified inputs"
	@echo "  make reviewer-check    Run the offline reviewer checks above"
	@echo "  make install-benchmark Install the optional public-dataset dependencies"
	@echo "  make public-benchmark  Run the optional SWE-bench Lite sensitivity diagnostic"
	@echo "  make paper             Compile the TOSEM manuscript from committed figures"

install-reviewer:
	$(PYTHON) -m pip install -e '.[reviewer]'

install-benchmark:
	$(PYTHON) -m pip install -e '.[reviewer,benchmark]'

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -p 'test_*.py' -v

mock-demo:
	CODEX_CONFIG_PATH=$(REPRO_ROOT)/no-codex-config.toml \
	JIRA_ENHANCER_CONNECTOR_MODE=mock \
	JIRA_ENHANCER_REASONING_MODE=heuristic \
	JIRA_ENHANCER_USE_CODEX_EXEC=false \
	JIRA_ENHANCER_RL_ENABLED=false \
	PYTHONPATH=src $(PYTHON) -m jira_enhancer \
		--storage-root $(REPRO_ROOT)/mock-runtime \
		run --scope-type issue --scope-value DEMO-18324 \
		--requestor reviewer@example.org

public-artifact:
	PYTHONPATH=src $(PYTHON) patent/verify_public_artifact.py \
		--artifact-dir $(PUBLIC_ARTIFACT_DIR)

fault-injection:
	PYTHONPATH=src $(PYTHON) patent/run_source_bound_transaction_experiment.py \
		--output-dir $(FAULT_OUTPUT_DIR) \
		--trials-per-scenario 2000 --seed 20260726
	PYTHONPATH=src $(PYTHON) -m unittest tests.test_causal_transaction -v

public-figures:
	PYTHONPATH=src $(PYTHON) patent/reproduce_public_figures.py \
		--artifact-dir $(PUBLIC_ARTIFACT_DIR) \
		--output-dir $(PUBLIC_FIGURE_DIR)
	PYTHONPATH=src $(PYTHON) patent/generate_b2_b3_learning_figure.py \
		--output $(PUBLIC_FIGURE_DIR)/b2_b3_arm_learning.png \
		--reward-output $(PUBLIC_FIGURE_DIR)/b2_b3_critic_reward_heatmap.png

# This optional diagnostic downloads the public SWE-bench Lite test split on
# first use. Its formula-expanded rows are sensitivity outputs, not independent
# observations of comparative system performance.
public-benchmark:
	HF_HOME=$(REPRO_ROOT)/huggingface \
	PYTHONPATH=src $(PYTHON) patent/run_public_benchmark_eval.py
	PYTHONPATH=src $(PYTHON) patent/generate_cross_dataset_comparison.py

reviewer-check: test public-artifact fault-injection public-figures

# Figure generation is deliberately separate. This target compiles the paper
# from the committed, reviewer-visible figure inputs and outputs.
paper:
	mkdir -p patent/$(PAPER_OUTPUT_DIR)
	cd patent && $(LATEXMK) -pdf -interaction=nonstopmode -halt-on-error \
		-outdir=$(PAPER_OUTPUT_DIR) $(PAPER_TEX)

paper-clean:
	cd patent && $(LATEXMK) -c -outdir=$(PAPER_OUTPUT_DIR) $(PAPER_TEX)
