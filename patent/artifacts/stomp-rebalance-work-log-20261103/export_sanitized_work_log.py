#!/usr/bin/env python3
"""Create an allowlisted, redacted work-log export from retained local records.

Reads only the source folder. Does not execute logged commands or contact services.
Raw command strings, outputs, messages, source paths and identities are never copied.
"""
import argparse
import collections
import csv
import hashlib
import json
from pathlib import Path
import re
import shutil

USER_SUMMARIES = [
    "Requested review and testing of connection/subscription transfer changes in the Java client in the Mumbai validation environment.",
    "Explicitly approved disabling TLS certificate verification for the disposable test clients in this run after certificate validation failed; the exception was scoped to those test processes.",
    "Confirmed completion of the requested SSH access renewal for remaining cleanup checks after the prior session expired.",
    "Confirmed completion of interactive sign-in after the operator session expired, allowing the remaining temporary-resource cleanup.",
    "Requested pull-request review.",
    "Requested checks for regressions and latency.",
    "Requested investigation of the observed throughput reduction.",
    "Questioned why event handling was affected and asked whether probing and connection migration could remain separate from that path.",
    "Approved the previously discussed Java event-path simplification.",
    "Asked whether the Python client followed the same design.",
    "Requested the corresponding Python changes.",
    "Requested another pull-request review.",
    "Requested documentation updates and, if checks passed, creation of pull requests with specified reviewers; reviewer identities are withheld.",
    "Resolved the requested reviewer-account ambiguity; the person's identity and account are withheld."
]
TURN_SUMMARIES = {
    1: USER_SUMMARIES[0], 2: USER_SUMMARIES[1], 3: USER_SUMMARIES[2],
    4: USER_SUMMARIES[3], 5: "Requested pull-request review, regression/latency checks, and throughput investigation in three recorded messages.",
    6: USER_SUMMARIES[7], 7: USER_SUMMARIES[8], 8: USER_SUMMARIES[9],
    9: USER_SUMMARIES[10], 10: USER_SUMMARIES[11], 11: USER_SUMMARIES[12], 12: USER_SUMMARIES[13]
}
COMPONENTS = {"java-sdk2": "java-client-sdk2", "java-sdk3": "java-client-sdk3", "subscriber": "subscriber-service"}


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def require(value, message):
    if not value:
        raise ValueError(message)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--private-source-map", type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    dest = args.destination.resolve()
    require(source != dest and source not in dest.parents, "Destination must be outside the source folder")
    require(not dest.exists() or not any(dest.iterdir()), "Destination must be absent or empty")
    source_files = sorted(p for p in source.rglob("*") if p.is_file() and p.name != ".DS_Store")
    before = {p.relative_to(source).as_posix(): digest(p) for p in source_files}
    original_manifest = json.loads((source / "manifest.json").read_text())
    for entry in original_manifest["files"]:
        require(before.get(entry["path"]) == entry["sha256"], "Original manifest checksum mismatch")
        require((source / entry["path"]).stat().st_size == entry["bytes"], "Original source size mismatch")
    require(len(original_manifest["files"]) == 54, "Unexpected original manifest record count")
    activity = [json.loads(line) for line in (source / "activity-log.jsonl").read_text().splitlines() if line]
    summary = json.loads((source / "turn-summary.json").read_text())
    windows = json.loads((source / "autonomous-intervals.json").read_text())
    commits = json.loads((source / "commits.json").read_text())
    tests = json.loads((source / "test-results.json").read_text())
    turn_map = {t["turn_id"]: f"T{i:02d}" for i, t in enumerate(summary["turns"], 1)}
    source_kinds = collections.Counter(r["kind"] for r in activity)
    require(source_kinds == {"command_execution": 754, "tool_call": 145, "visible_assistant_message": 144,
                             "user_message": 14, "file_change": 13, "turn": 12, "export_metadata": 1},
            "Unexpected source event counts")
    source_command_status = collections.Counter(r["status"] for r in activity if r["kind"] == "command_execution")
    source_exit = collections.Counter(r["exit_code"] for r in activity if r["kind"] == "command_execution")
    source_tool_status = collections.Counter(r["status"] for r in activity if r["kind"] == "tool_call")
    public = []
    user_index = 0
    scalar_keys = ["item_index", "started_at_utc", "completed_at_utc", "elapsed_ms", "duration_ms", "exit_code", "status", "phase"]
    for ordinal, record in enumerate(activity, 1):
        kind = record["kind"]
        row = {"record_index": ordinal, "kind": kind}
        if "turn_id" in record:
            row["turn"] = turn_map[record["turn_id"]]
        for key in scalar_keys:
            if key in record:
                row[key] = record[key]
        if kind == "export_metadata":
            row.update({"coverage": "12 completed turns on 2026-11-02; earlier history is incomplete",
                        "attribution": "Work followed user requests, approvals and subsequent corrections"})
        elif kind == "command_execution":
            row.update({"command_present_in_source": record["command"] is not None,
                        "command_truncated_in_source": record["command_truncated"],
                        "command": None, "command_policy": "withheld in public export",
                        "output_present_in_source": record["output"] is not None,
                        "output": None, "working_directory": "withheld"})
        elif kind == "user_message":
            row.update({"summary": USER_SUMMARIES[user_index],
                        "summary_is_paraphrase": True, "original_text_withheld": True,
                        "source_may_have_truncated_text": record.get("text_may_be_truncated_by_source", False)})
            user_index += 1
        elif kind == "visible_assistant_message":
            row.update({"text": None, "original_text_withheld": True,
                        "source_text_character_count": len(record["text"])})
        elif kind == "file_change":
            row["files"] = [{"file_alias": f"F{i:03d}", "change_type": f["change_type"],
                             "diff_available_in_source": f["diff_available"],
                             "diff_truncated_in_source": f["diff_truncated"]}
                            for i, f in enumerate(record["files"], 1)]
            row["file_alias_scope"] = "within this file-change record only; original paths withheld"
        elif kind == "tool_call":
            row["tool"] = record["tool"]
            row["arguments_title_and_server_withheld"] = True
        public.append(row)
    require(user_index == 14, "User-message summary coverage mismatch")
    require(collections.Counter(r["kind"] for r in public) == source_kinds, "Event count changed")
    require(collections.Counter(r["status"] for r in public if r["kind"] == "command_execution") == source_command_status,
            "Command failure statuses changed")
    require(collections.Counter(r["exit_code"] for r in public if r["kind"] == "command_execution") == source_exit,
            "Exit-code distribution changed")
    require(collections.Counter(r["status"] for r in public if r["kind"] == "tool_call") == source_tool_status,
            "Tool failure statuses changed")

    public_turns = []
    for i, turn in enumerate(summary["turns"], 1):
        fields = ["start_ist", "end_ist", "elapsed_minutes", "user_message_count", "command_execution_records", "file_change_records", "tool_calls"]
        row = {k: turn[k] for k in fields}
        row.update({"turn": f"T{i:02d}", "initiating_context_summary": TURN_SUMMARIES[i]})
        records = [r for r in public if r.get("turn") == row["turn"]]
        for field, kind in [("user_message_count", "user_message"), ("command_execution_records", "command_execution"),
                            ("file_change_records", "file_change"), ("tool_calls", "tool_call")]:
            require(row[field] == sum(r["kind"] == kind for r in records), "Per-turn count mismatch")
        public_turns.append(row)
    public_windows = []
    for window in windows:
        turn_alias = turn_map[window["turn_id"]]
        index = int(turn_alias[1:])
        require(window["user_message_count"] == 1 and window["additional_user_messages_recorded_in_turn"] == 0,
                "Window does not match no-additional-input definition")
        public_windows.append({k: window[k] for k in ["start_ist", "end_ist", "elapsed_minutes", "user_message_count",
                               "command_execution_records", "file_change_records", "tool_calls",
                               "additional_user_messages_recorded_in_turn"]} | {
            "turn": turn_alias, "title": window["title"], "initiating_context_summary": TURN_SUMMARIES[index],
            "meaning": "No additional recorded user messages after the initiating request or approval within this selected turn; not unprompted work"})
    require(len(public_windows) == 5, "Unexpected window count")

    public_commits = {"interpretation": "Retained metadata; not a complete implementation history or proof of machine authorship", "components": {}}
    for component in ["java", "python"]:
        rows = []
        for i, commit in enumerate(commits[component]["commits"], 1):
            rows.append({"commit_index": i, "commit": commit["commit"], "author_time": commit["author_time"],
                         "commit_time": commit["commit_time"], "file_change_count": len(commit["files"]),
                         "files": [{"file_alias": f"F{j:03d}", "lines_added": f["lines_added"], "lines_deleted": f["lines_deleted"]}
                                   for j, f in enumerate(commit["files"], 1)],
                         "author_identity_subject_and_file_paths_withheld": True})
        public_commits["components"][component + "-implementation"] = rows
    public_tests = {
        "provenance": "Parsed from retained reports; no tests were rerun for either the source export or this public export",
        "limits": ["Mixed executions; file timestamps do not establish an exact source revision",
                   "Subscriber full retained total includes older suites; only 26 selected cases belong to the reported November 2 gate",
                   "Python 151-case result is narrative evidence; original temporary JUnit/log files were absent",
                   "PMD reported unsupported-bytecode processing errors; goal completion was not treated as valid analysis"],
        "modules": {},
        "recorded_python_result": {k: tests["recorded_python_result"][k] for k in ["tests", "failures", "skips", "new_run"]}
    }
    public_tests["recorded_python_result"]["evidence_kind"] = "recorded narrative; original raw test files absent"
    for name, module in tests["modules"].items():
        row = {k: module[k] for k in ["all_retained_totals", "selected_text_copy_totals", "exact_head_bound"]}
        row["suite_records"] = []
        for i, suite in enumerate(module["suite_files"], 1):
            keep = ["source_mtime_ist", "source_sha256", "tests", "failures", "errors", "skipped", "seconds",
                    "suite_timestamp", "selected_for_text_copy"]
            row["suite_records"].append({"suite_alias": f"S{i:02d}"} | {k: suite[k] for k in keep})
        for tool in ["checkstyle", "spotbugs"]:
            row[tool] = {k: v for k, v in module[tool].items() if k != "source_path"}
        for field in ["tests", "failures", "errors", "skipped"]:
            require(sum(s[field] for s in row["suite_records"]) == row["all_retained_totals"][field],
                    "Retained test totals mismatch")
            require(sum(s[field] for s in row["suite_records"] if s["selected_for_text_copy"]) == row["selected_text_copy_totals"][field],
                    "Selected test totals mismatch")
        public_tests["modules"][COMPONENTS[name]] = row

    source_entries = []
    private_map = []
    for i, p in enumerate(source_files, 1):
        relative = p.relative_to(source).as_posix()
        alias = f"SOURCE_{i:03d}"
        category = "top-level metadata" if len(p.relative_to(source).parts) == 1 else p.relative_to(source).parts[0]
        source_entries.append({"source_alias": alias, "category": category, "bytes": p.stat().st_size, "sha256": before[relative]})
        private_map.append({"source_alias": alias, "original_relative_path": relative})
    public_summary = {
        "coverage": "12 completed turns on 2026-11-02; selected historical references in source bundle; earlier history is incomplete",
        "counts": dict(source_kinds), "total_activity_records": len(activity),
        "command_status_counts": dict(source_command_status), "command_exit_code_counts": dict(source_exit),
        "tool_status_counts": dict(source_tool_status), "turns": public_turns,
        "no_additional_input_window_count": len(public_windows),
        "attribution": "User-initiated development and testing with scoped approvals, access renewals and subsequent feedback",
        "timing_limits": "Turn timestamps and individual execution durations are retained. Individual command timestamps were not supplied. Elapsed turn time includes waits."
    }
    redactions = {
        "policy": "Structured allowlist export; raw strings are withheld rather than relying on token substitution",
        "preserved": ["All 1083 activity records and original ordering", "All 754 command statuses, exit codes and supplied durations",
                      "All 145 tool statuses and supplied durations", "All 12 turn boundaries and 14 user-message positions",
                      "Five source-selected no-additional-input windows and their initiating context",
                      "13 file-change records with per-file change type and diff-availability flags",
                      "11 commit hashes/timestamps and numeric per-file change statistics",
                      "All 60 retained test-suite records and static-analysis limitations"],
        "withheld_counts": {
            "command_strings_present_in_source": sum(r["kind"] == "command_execution" and r["command"] is not None for r in activity),
            "command_strings_already_omitted_by_source": sum(r["kind"] == "command_execution" and r["command"] is None for r in activity),
            "output_objects_present_in_source": sum(r["kind"] == "command_execution" and r["output"] is not None for r in activity),
            "assistant_message_texts": 144, "user_texts_replaced_by_reviewed_paraphrases": 14,
            "full_reference_reports": 5, "raw_machine_test_report_copies": 42,
            "desktop_metadata_files_excluded": sum(p.name == ".DS_Store" for p in source.rglob("*") if p.is_file())},
        "removed_categories": ["Issue and branch identifiers", "Internal URLs, host/account names and access instructions",
                               "Credentials or credential-bearing arguments if present in raw text", "Personal and working-directory paths",
                               "Human identities, reviewer lists and email addresses", "Chat, turn and item identifiers",
                               "Raw commands, console outputs, code/diff content and free-text report bodies"],
        "secret_assertion": "Credential-related instructions and account details were observed; this export does not claim that a live secret value was found",
        "source_manifest_entries_verified": len(original_manifest["files"]),
        "original_manifest_sha256": before["manifest.json"],
        "source_file_count_excluding_desktop_metadata": len(source_files),
        "public_copy_is_not_lossless": True,
        "raw_reference_reports_not_publicly_reproduced": True
    }
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "activity-log.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in public))
    for name, value in [("turn-summary.json", public_summary), ("no-additional-input-windows.json", public_windows),
                        ("commit-metadata.json", public_commits), ("test-results.json", public_tests),
                        ("SOURCE_HASHES.json", {"source_manifest_sha256": before["manifest.json"], "files": source_entries}),
                        ("REDACTION_LOG.json", redactions)]:
        write_json(dest / name, value)
    shutil.copyfile(Path(__file__), dest / "export_sanitized_work_log.py")
    readme = """# Subscriber rebalance — public development work-log extract

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
"""
    (dest / "README.md").write_text(readme)
    for relative, expected in before.items():
        require(digest(source / relative) == expected, "Source changed during export")
    forbidden = {
        "issue_key": r"[A-Z][A-Z0-9]{1,9}-[0-9]{2,}",
        "email": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        "url": r"https?://", "personal_path": r"/Users/|/home/",
        "uuid": r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        "oci_identifier": r"ocid1\.", "private_host": r"oraclecloud\.com|oraclecorp\.com|ocir\.io"
    }
    for path in dest.iterdir():
        # The reusable script contains the literal scanner rules, not source data.
        if path.name == "export_sanitized_work_log.py":
            continue
        text = path.read_text()
        for category, pattern in forbidden.items():
            require(not re.search(pattern, text), "Privacy scan failed: " + category + " in " + path.name)
    (dest / "CHECKSUMS.sha256").write_text("".join(f"{digest(p)}  {p.name}\n" for p in sorted(dest.iterdir()) if p.is_file()))
    if args.private_source_map:
        require(args.private_source_map.resolve().parent != dest, "Private source map must be outside the public packet")
        write_json(args.private_source_map, {"source_manifest_sha256": before["manifest.json"], "mapping": private_map})
    print(json.dumps({"destination": str(dest), "records": len(public), "commands": source_kinds["command_execution"],
                      "failed_commands": source_command_status["failed"], "no_additional_input_windows": len(public_windows),
                      "source_manifest_verified": len(original_manifest["files"]), "source_files_unchanged": len(before),
                      "output_files": len(list(dest.iterdir()))}))


if __name__ == "__main__":
    main()
