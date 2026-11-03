# Subscriber rebalance — public development work-log extract

Prepared: 3 November 2026. Activity coverage: 2 November 2026 (IST).

This is a sanitized extract of retained development and testing records, not raw
console logs. It accompanies a downstream implementation case associated with
Jira Enhancer. The implementation's Jira Enhancer origin is author-reported;
these records alone do not establish a complete generated-story-to-code chain.
This November 2 iteration is distinct from the October 15 scale-out runs.

## What the record preserves

activity-log.jsonl contains all 1,083 original record positions: 754 command
executions, 145 tool calls, 144 visible assistant messages, 14 user messages,
13 file-change records, 12 turn boundaries and one export metadata record.
Command outcomes are 696 completed and 58 failed. Exit codes are 0:696, 1:44,
2:10 and 255:4. Tool calls include 133 completed and 12 failed records. Failures
were not filtered out. Supplied durations and source item ordering are retained.
Individual command timestamps were not supplied by the original history API.

## User involvement and follow-through

Five source-selected turns contain no additional recorded user messages after
their initiating request or approval. They are documented in
no-additional-input-windows.json, including their wall-clock durations and
command counts. Other turns and all 14 user interventions remain represented.
The work began with a request to review and test changes. Later interventions
included a scoped TLS-certificate-verification exception, SSH access renewal,
interactive sign-in, regression/throughput questions, requested Java/Python
changes, further review, documentation updates and reviewer-account selection.
This supports sustained follow-through within authorized tasks. It does not
show unprompted work, an unattended entire project, or no human involvement.
Elapsed time includes tool and runtime waits; it is not active-compute time.

## Test and revision evidence

test-results.json preserves 60 suite records and their counts, timestamps and
source hashes. Retained Java SDK2 and SDK3 totals are 247 cases each; subscriber
retained totals are 231, with only 26 selected cases associated with the stated
November 2 gate. These files contain mixed executions and are not tied to one
exact source revision. Python's 151-case result is narrative evidence because
its original temporary JUnit/log files were absent. Static-analysis records
preserve missing-class counts. PMD's unsupported-bytecode processing errors
must not be described as a successful complete analysis. No tests were rerun
for this export. Commit metadata preserves 11 commits and numeric file-change
statistics, with identities, subjects and file paths withheld.

## Redactions and limits

All command strings and output objects are withheld. Of 754 source command
records, 650 already lacked command strings and 104 had selected snippets;
53 records contained output objects. Null public fields do not mean no action
occurred. All 144 assistant texts are withheld; the 14 user texts have reviewed
paraphrases that preserve their request/approval role. Raw historical reference
reports and 42 machine-test text copies are not reproduced; their source hashes
and the parsed test statistics are retained. Issue identifiers, branch names,
internal URLs, account/host details, identities, access arguments, personal paths,
chat/item IDs and code/diff text are excluded. This is not a lossless archive,
and it cannot reproduce every command or establish every operational claim.

## Source and output integrity

The original folder's 54-entry manifest was checked before transformation.
SOURCE_HASHES.json records 55 source-file hashes (including that manifest),
using non-identifying aliases. Two desktop metadata files are excluded.
REDACTION_LOG.json documents the transformations. CHECKSUMS.sha256 checks the
public packet. The private alias-to-source-path map is not included.

## Reproducing the extract

With authorized local access to the retained source folder, run:

```sh
python export_sanitized_work_log.py --source SOURCE_FOLDER --destination NEW_EMPTY_FOLDER
```

The script reads existing local records only; it does not execute logged
commands, rerun tests, contact services, publish files or alter the source.
The public packet intentionally contains no confidential source bundle.
