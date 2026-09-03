# Jira Enhancer — TOSEM submission files

This folder contains the files prepared on 3 September 2026 for the ACM TOSEM
special issue on Human–AI Collaboration in Software Engineering.

## Paper

**Title:** *Jira Enhancer: A Governance-Aware Human–AI Collaboration System for
Epic-to-Story Decomposition in Software Engineering*

Jira Enhancer reads the available context for an incomplete Jira epic and
prepares story proposals for review. A reviewer must approve a proposal before
the system can write it to Jira. The paper reports authenticated deployment
evidence, a matched human review, controlled fault injection, a held-out B2/B3
diagnostic, and scoring sensitivity analyses.

`main/JiraEnhancer_TOSEM_HumanAI_SI_Main_Manuscript.pdf` is the 46-page
manuscript submitted for review.

## Code and public reviewer material

The public GitHub repository is:

<https://github.com/manish94u/JiraEnhancer>

It contains the implementation, automated tests, API contracts, manuscript
figures, public or de-identified evidence, and scripts used for the published
analyses. The repository README explains the evidence map and the reviewer
checks. The source ZIP below is a fixed copy of the files used to compile this
submission.

## LaTeX source

`source/JiraEnhancer_TOSEM_HumanAI_SI_Source.zip`

The archive contains the manuscript source, bibliography, figures, and table
fragments required for the paper. After extracting it, run this command from
the `JiraEnhancer_TOSEM_Source` directory:

```text
latexmk -pdf -interaction=nonstopmode -halt-on-error jiraenhancer_tosem_acm.tex
```

## Turnitin reports

Both reports refer to the manuscript in this folder and use Turnitin submission
ID `trn:oid:::1:3639364983`.

- `Turnitin_Report/AI_Report/JiraEnhancer_Turnitin_AI_Report.pdf` is the
  AI-writing report. It has 48 pages, including the Turnitin cover and analysis
  pages. The result appears as `*%` because Turnitin does not display a numeric
  score below 20%.
- `Turnitin_Report/Plagiarism_Report/JiraEnhancer_Turnitin_Plagiarism_Report.pdf`
  is the similarity report. It has 50 pages, including the Turnitin report
  pages, and records 8% overall similarity. The report excludes the
  bibliography and quoted text from this figure.

## Checksums

`SHA256SUMS.txt` contains SHA-256 hashes for the manuscript, source archive,
both Turnitin reports, and this README.
