LATEXMK ?= latexmk

.PHONY: all
all:
	$(LATEXMK) -pdf -interaction=nonstopmode -halt-on-error -outdir=.build jira_enhancer_emse.tex
	cp .build/jira_enhancer_emse.pdf jira_enhancer_emse.pdf
	@if [ -f .build/jira_enhancer_emse.bbl ]; then \
		cp .build/jira_enhancer_emse.bbl jira_enhancer_emse.bbl; \
	fi
