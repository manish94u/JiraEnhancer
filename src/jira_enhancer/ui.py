from __future__ import annotations

import html
import json
import math
from datetime import datetime
from typing import Any
from urllib.parse import quote_plus


def _escape(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _render_detail_fields(fields: list[tuple[str, Any]]) -> str:
    return "".join(
        f'<div class="detail-field"><span class="label">{_escape(label)}</span><div class="value">{_escape(value)}</div></div>'
        for label, value in fields
    )


def _render_run_fields(run: dict[str, Any] | None) -> str:
    if not run:
        return '<div class="detail-field"><span class="label">Next step</span><div class="value">Create Run to start a new enrichment, or open Advanced run controls, paste an existing Run ID, and Refresh Run.</div></div>'
    items = list((run.get("status_payload") or {}).get("items", []))
    return _render_detail_fields(
        [
            ("Run ID", run.get("run_id", "")),
            ("Requestor", run.get("requestor", "")),
            ("Scope Type", run.get("scope_type", "")),
            ("Scope Value", run.get("scope_value", "")),
            ("Status", run.get("status", "")),
            ("Item Count", len(items)),
            ("Created", run.get("created_at", "")),
            ("Updated", run.get("updated_at", "")),
        ]
    )


def _render_item_fields(item: dict[str, Any] | None) -> str:
    if not item:
        return '<div class="detail-field"><span class="label">Next step</span><div class="value">Load a run first. Once Jira items appear, select one here to review the generated draft.</div></div>'
    draft = item.get("draft") or {}
    payload = draft.get("payload") or {}
    context = payload.get("context_brief") or {}
    rl_decision = item.get("rl_prompt_decision") or {}
    rl_meta = rl_decision.get("metadata") or {}
    beta_points = rl_meta.get("candidate_beta_distribution") or []
    beta_summary = " | ".join(
        f"{point.get('prompt_id', '')}: α={float(point.get('alpha', 1.0)):.2f}, β={float(point.get('beta', 1.0)):.2f}, E={float(point.get('expected', 0.5)):.2f}"
        for point in beta_points
    )
    ranking = rl_meta.get("candidate_ranking") or []
    ranking_summary = " | ".join(
        f"#{entry.get('rank', '')} {entry.get('prompt_id', '')} ({float(entry.get('score', 0.0)):.3f})"
        for entry in ranking
    )
    trajectory = rl_meta.get("confidence_trajectory") or []
    trajectory_summary = " | ".join(
        f"attempt {entry.get('attempt', '?')}: c={entry.get('confidence', '?')} lag={entry.get('lag', '?')}"
        for entry in trajectory
    )
    selected_pack = rl_meta.get("selected_prompt_pack") or {}
    return _render_detail_fields(
        [
            ("Issue Key", item.get("issue_key", "")),
            ("Jira Summary", item.get("jira_summary", "")),
            ("Status", item.get("status", "")),
            ("Approval Required", str(item.get("approval_required", False))),
            ("Confidence", item.get("confidence", "")),
            ("Readiness Score", item.get("readiness_score", "")),
            ("Draft Version", draft.get("draft_version", "")),
            ("Draft Status", draft.get("status", "")),
            ("Summary", payload.get("summary", "")),
            ("Suggested Description", payload.get("suggested_description", "")),
            ("Problem Statement", context.get("problem_statement", "")),
            ("Acceptance Focus", context.get("acceptance_focus", "")),
            ("Dependency Focus", context.get("dependency_focus", "")),
            ("Edge Case Focus", context.get("edge_case_focus", "")),
            ("Evidence Context", context.get("evidence_context", "")),
            ("Open Questions", " | ".join(payload.get("fallback_questions", []))),
            ("Recommended Next Step", payload.get("recommended_next_step", "")),
            (
                "Guidance Analysis",
                " | ".join(payload.get("user_input_details", [])) or payload.get("user_input_summary", ""),
            ),
            ("RL Prompt Selected", rl_decision.get("prompt_id", "")),
            ("RL Prompt Title", selected_pack.get("title", "")),
            ("RL Prompt Rationale", selected_pack.get("rationale", "")),
            ("RL Candidate Ranking", ranking_summary),
            ("RL Beta Distribution", beta_summary),
            ("RL Confidence Trajectory", trajectory_summary),
        ]
    )


def _render_item_options(run: dict[str, Any] | None, selected_issue_key: str) -> str:
    options = ['<option value="">Select a Jira from this run</option>']
    items = list((run or {}).get("status_payload", {}).get("items", []))
    for item in items:
        issue_key = str(item.get("issue_key", ""))
        summary = str(item.get("jira_summary", ""))
        selected = " selected" if issue_key and issue_key == selected_issue_key else ""
        options.append(f'<option value="{_escape(issue_key)}"{selected}>{_escape(f"{issue_key} - {summary}".strip())}</option>')
    return "".join(options)


def _ui_query(run_id: str, issue_key: str, scope_type: str, scope_value: str, requestor: str, status_message: str = "") -> str:
    parts = [
        f"runId={quote_plus(run_id or '')}",
        f"issueKey={quote_plus(issue_key or '')}",
        f"scopeType={quote_plus(scope_type or '')}",
        f"scopeValue={quote_plus(scope_value or '')}",
        f"requestor={quote_plus(requestor or '')}",
    ]
    if status_message:
        parts.append(f"status={quote_plus(status_message)}")
    return "&".join(parts)


def render_index_page(
    confluence_base_url: str = "",
    *,
    initial_run: dict[str, Any] | None = None,
    initial_item: dict[str, Any] | None = None,
    status_message: str = "Ready.",
    scope_type: str = "issue",
    scope_value: str = "DEMO-18324",
    requestor: str = "reviewer@example.org",
    user_input_value: str = "",
    prompt_input_value: str = "",
    storage_root: str = "",
    rl_enabled: bool | None = None,
    rl_policy: str = "default",
    rl_stats: list[dict[str, Any]] | None = None,
    rl_decisions: list[dict[str, Any]] | None = None,
) -> str:
    safe_confluence_url = confluence_base_url.replace("\\", "\\\\").replace('"', '\\"')
    selected_issue_key = str((initial_item or {}).get("issue_key") or (initial_run or {}).get("items", [""])[0] or "")
    run_id_value = str((initial_run or {}).get("run_id", ""))
    refresh_query = _ui_query(run_id_value, selected_issue_key, scope_type, scope_value, requestor, status_message)
    refresh_href = f"/ui?{refresh_query}" if run_id_value else ""
    auto_refresh_tag = ""
    if initial_run and str(initial_run.get("status", "")) == "running" and refresh_href:
        auto_refresh_tag = f'<meta http-equiv="refresh" content="3;url={_escape(refresh_href)}">'
    item_json = json.dumps(initial_item, indent=2) if initial_item else "Load a run first, then select a Jira item to inspect its draft."
    run_json = json.dumps(initial_run, indent=2) if initial_run else "Create Run to start, or use Advanced run controls to refresh an existing Run ID."
    generated_prompt = str(((initial_item or {}).get("draft") or {}).get("payload", {}).get("codex_user_input_prompt", "")).strip()
    effective_user_input = user_input_value
    effective_prompt_input = prompt_input_value
    last_run_label = f"{run_id_value} / {selected_issue_key or scope_value} / {str((initial_run or {}).get('status') or 'loaded')}"
    last_run_chip = (
        f'<span class="app-chip" title="{_escape(last_run_label)}">Last {_escape(last_run_label)}</span>' if run_id_value else ""
    )
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  __AUTO_REFRESH_TAG__
  <title>Agentic Jira Enhancer</title>
  <style>
    :root {
      --bg: #edf3f8;
      --panel: #ffffff;
      --ink: #172033;
      --muted: #667085;
      --accent: #2f5fda;
      --accent-2: #b86b12;
      --success: #087f5b;
      --line: #d4dce8;
      --danger: #c4322b;
      --surface: rgba(255, 255, 255, 0.82);
      --surface-soft: rgba(255, 255, 255, 0.58);
      --neutral-line: rgba(92, 105, 124, 0.26);
      --neutral-soft: rgba(92, 105, 124, 0.10);
      --accent-line: rgba(47, 95, 218, 0.30);
      --accent-soft: rgba(47, 95, 218, 0.11);
      --amber-line: rgba(217, 119, 6, 0.28);
      --amber-soft: rgba(217, 119, 6, 0.10);
      --success-line: rgba(5, 150, 105, 0.26);
      --success-soft: rgba(5, 150, 105, 0.09);
      --danger-line: rgba(217, 45, 32, 0.28);
      --danger-soft: rgba(217, 45, 32, 0.08);
      --shadow: 0 18px 42px rgba(15, 23, 42, 0.12);
      --label: #465266;
      --label-strong: #334155;
      --section-line: rgba(70, 82, 102, 0.24);
      --font-xs: 13px;
      --font-sm: 14px;
      --font-md: 16px;
      --font-lg: 17px;
    }

    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at 20% -10%, rgba(47, 95, 218, 0.10), transparent 28%),
        linear-gradient(180deg, #f7faff 0%, var(--bg) 54%, #e6edf5 100%);
      color: var(--ink);
      font-size: var(--font-md);
    }
    .shell {
      max-width: 1280px;
      margin: 0 auto;
      padding: 10px 20px 32px;
    }
    .app-nav {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      margin-bottom: 10px;
      padding: 7px 10px;
      border: 1px solid rgba(47, 95, 218, 0.26);
      border-radius: 14px;
      background:
        linear-gradient(120deg, rgba(31, 79, 201, 0.16), rgba(255, 255, 255, 0.90) 38%, rgba(8, 127, 91, 0.08)),
        rgba(255, 255, 255, 0.92);
      box-shadow: 0 14px 34px rgba(15, 23, 42, 0.14);
      position: sticky;
      top: 6px;
      z-index: 10;
      backdrop-filter: blur(16px);
    }
    .app-brand {
      display: flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
      padding: 0 4px 0 0;
      color: var(--ink);
      font-weight: 800;
    }
    .app-brand-copy {
      display: grid;
      gap: 2px;
      min-width: 0;
    }
    .app-brand-copy strong,
    .app-brand-copy span {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .app-brand-copy span {
      color: var(--muted);
      font-size: var(--font-xs);
      font-weight: 600;
    }
    .app-brand-copy strong {
      font-size: 18px;
      letter-spacing: 0;
      line-height: 1.1;
    }
    .app-brand-mark {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 30px;
      height: 30px;
      border-radius: 10px;
      background:
        radial-gradient(circle at 30% 20%, rgba(255, 255, 255, 0.42), transparent 28%),
        linear-gradient(135deg, #1f4fc9, #2f5fda 58%, #087f5b);
      color: #fff;
      font-size: var(--font-xs);
      line-height: 1;
      flex-shrink: 0;
      box-shadow: 0 8px 18px rgba(47, 95, 218, 0.30);
    }
    .app-links {
      display: inline-flex;
      gap: 3px;
      flex-wrap: wrap;
      justify-content: flex-end;
      align-items: center;
      padding: 3px;
      border: 1px solid rgba(47, 95, 218, 0.18);
      border-radius: 12px;
      background: rgba(232, 240, 252, 0.92);
      box-shadow: inset 0 1px 2px rgba(15, 23, 42, 0.06);
    }
    .app-link {
      display: inline-flex;
      align-items: center;
      min-height: 28px;
      border: 1px solid transparent;
      border-radius: 8px;
      padding: 5px 9px;
      color: var(--muted);
      font-size: var(--font-sm);
      font-weight: 700;
      text-decoration: none;
    }
    .app-link:hover {
      border-color: var(--accent-line);
      color: var(--accent);
      background: rgba(255, 255, 255, 0.78);
      transform: translateY(-1px);
    }
    .app-link.active {
      border-color: rgba(47, 95, 218, 0.42);
      background: var(--accent);
      color: #ffffff;
      box-shadow: 0 8px 18px rgba(47, 95, 218, 0.22);
    }
    .app-meta {
      display: flex;
      align-items: center;
      gap: 6px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }
    .app-chip {
      max-width: 220px;
      border: 1px solid rgba(190, 203, 220, 0.92);
      border-radius: 999px;
      padding: 4px 8px;
      background: rgba(255, 255, 255, 0.72);
      color: var(--muted);
      font-size: var(--font-xs);
      font-weight: 700;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      box-shadow: 0 1px 2px rgba(15, 23, 42, 0.05);
    }
    .app-chip.good {
      border-color: var(--success-line);
      color: var(--success);
      background: var(--success-soft);
    }
    .app-chip.warn {
      border-color: var(--amber-line);
      color: var(--accent-2);
      background: var(--amber-soft);
    }
    @media (max-width: 720px) {
      .shell {
        padding: 8px 10px 24px;
      }
      .app-nav {
        align-items: center;
        display: grid;
        grid-template-columns: 1fr auto;
        gap: 6px 8px;
        margin-bottom: 8px;
        padding: 6px 8px;
        top: 4px;
      }
      .app-brand-mark {
        width: 28px;
        height: 28px;
      }
      .app-brand-copy strong {
        font-size: 16px;
      }
      .app-brand-copy span,
      .app-meta {
        display: none;
      }
      .app-links {
        justify-content: flex-end;
        padding: 2px;
      }
      .app-link {
        min-height: 26px;
        padding: 4px 7px;
        font-size: 12px;
      }
    }
    .grid {
      display: grid;
      grid-template-columns: minmax(320px, 360px) minmax(0, 1fr);
      gap: 16px;
      align-items: start;
    }
    @media (max-width: 960px) {
      .grid { grid-template-columns: 1fr; }
      .command-panel {
        position: static;
        max-height: none;
      }
      .review-workspace {
        min-height: 0;
      }
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 12px;
      box-shadow: var(--shadow);
      padding: 14px;
    }
    .command-panel {
      position: sticky;
      top: 70px;
      max-height: calc(100vh - 86px);
      overflow: auto;
      background: rgba(248, 251, 255, 0.72);
      border-color: rgba(202, 212, 226, 0.72);
      box-shadow: 0 10px 24px rgba(15, 23, 42, 0.06);
    }
    .review-workspace {
      min-height: calc(100vh - 112px);
      padding: 18px;
      border-color: #cbd6e5;
      box-shadow: 0 22px 52px rgba(15, 23, 42, 0.13);
    }
    .panel h2, .panel h3 {
      margin-top: 0;
      margin-bottom: 10px;
      line-height: 1.15;
    }
    .panel h2 {
      font-size: 22px;
    }
    .panel h3 {
      font-size: 19px;
    }
    .detail-panel {
      display: grid;
      gap: 12px;
    }
    .detail-panel h2 {
      margin-bottom: 0;
      font-size: 24px;
    }
    .detail-panel h3 {
      margin-bottom: 0;
      font-size: 19px;
    }
    .workflow-steps {
      display: flex;
      align-items: center;
      gap: 6px;
      margin-bottom: 2px;
      padding: 4px 0 10px;
      border-bottom: 1px solid rgba(70, 82, 102, 0.16);
      background: transparent;
    }
    .workflow-step {
      display: inline-flex;
      align-items: flex-start;
      justify-content: flex-start;
      gap: 6px;
      min-width: 0;
      border: 0;
      border-radius: 8px;
      padding: 6px 7px;
      background: transparent;
      color: #778196;
      font-size: 12px;
      font-weight: 700;
      text-align: left;
      line-height: 1.15;
      overflow: hidden;
      white-space: normal;
      flex: 1;
    }
    .workflow-step:not(:last-child) .workflow-label::after {
      content: "→";
      color: rgba(102, 112, 133, 0.42);
      font-weight: 700;
      margin-left: 6px;
    }
    .workflow-step::before {
      content: "";
      width: 8px;
      height: 8px;
      border-radius: 999px;
      background: rgba(102, 112, 133, 0.30);
      flex-shrink: 0;
      margin-top: 3px;
      box-shadow: 0 0 0 3px rgba(102, 112, 133, 0.08);
    }
    .workflow-step-text {
      display: grid;
      gap: 2px;
      min-width: 0;
    }
    .workflow-label {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .workflow-time {
      min-height: 12px;
      color: rgba(102, 112, 133, 0.72);
      font-size: 10px;
      font-weight: 650;
      letter-spacing: 0;
    }
    .workflow-step.done {
      background: rgba(5, 150, 105, 0.07);
      color: var(--success);
    }
    .workflow-step.done::before {
      background: var(--success);
      box-shadow: 0 0 0 3px var(--success-soft);
    }
    .workflow-step.current,
    .workflow-step.active {
      background: rgba(47, 95, 218, 0.10);
      color: var(--accent);
      font-weight: 850;
    }
    .workflow-step.current::before,
    .workflow-step.active::before {
      background: var(--accent);
      box-shadow: 0 0 0 3px rgba(47, 95, 218, 0.16);
    }
    .workflow-step.error {
      background: var(--danger-soft);
      color: var(--danger);
    }
    .workflow-step.error::before {
      background: var(--danger);
      box-shadow: 0 0 0 3px rgba(217, 45, 32, 0.12);
    }
    .workflow-step.future {
      color: rgba(102, 112, 133, 0.50);
      opacity: 0.72;
    }
    .workflow-step.future::before {
      background: rgba(102, 112, 133, 0.20);
      box-shadow: none;
    }
    .field-group {
      border: 0;
      border-radius: 8px;
      padding: 0;
      background: transparent;
      display: grid;
      gap: 8px;
    }
    .step-section {
      display: grid;
      gap: 10px;
      padding-top: 14px;
      border-top: 1px solid rgba(70, 82, 102, 0.15);
    }
    .step-section:first-of-type {
      border-top: 0;
      padding-top: 0;
    }
    .step-section.hidden,
    .action-hidden {
      display: none;
    }
    .step-section-title {
      margin: 0;
      color: var(--label-strong);
      font-size: var(--font-xs);
      font-weight: 800;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }
    .field-group-title {
      margin: 0;
      color: var(--ink);
      font-size: 15px;
      font-weight: 700;
      line-height: 1.2;
    }
    .advanced-drawer {
      border: 0;
      border-top: 1px solid rgba(70, 82, 102, 0.15);
      border-radius: 0;
      background: transparent;
      padding: 0;
    }
    .advanced-drawer summary {
      cursor: pointer;
      color: var(--label);
      font-size: var(--font-xs);
      font-weight: 800;
      padding: 9px 0;
    }
    .advanced-drawer-body {
      display: grid;
      gap: 10px;
      padding: 0 0 10px;
    }
    .detail-context {
      border: 1px solid rgba(47, 95, 218, 0.34);
      border-radius: 12px;
      padding: 14px;
      background: linear-gradient(180deg, rgba(247, 250, 255, 0.96), rgba(237, 244, 252, 0.90));
      display: grid;
      gap: 8px;
      box-shadow: 0 12px 28px rgba(15, 23, 42, 0.08);
    }
    .context-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }
    .context-flow {
      color: var(--accent);
      font-size: var(--font-xs);
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
    .context-object {
      color: var(--ink);
      font-size: 20px;
      font-weight: 800;
      line-height: 1.25;
      word-break: break-word;
    }
    .context-status {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 3px 8px;
      color: var(--muted);
      background: var(--surface);
      font-size: 12px;
      font-weight: 700;
      white-space: nowrap;
    }
    .context-status.running {
      border-color: var(--amber-line);
      color: var(--accent-2);
      background: var(--amber-soft);
    }
    .context-status.ready {
      border-color: var(--success-line);
      color: var(--success);
      background: var(--success-soft);
    }
    .context-status.error {
      border-color: var(--danger-line);
      color: var(--danger);
      background: var(--danger-soft);
    }
    .context-metrics {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      padding-top: 4px;
    }
    .context-metrics.hidden {
      display: none;
    }
    .stack { display: grid; gap: 12px; }
    label {
      display: grid;
      gap: 5px;
      font-size: var(--font-sm);
      color: var(--label);
      font-weight: 650;
    }
    input, select, textarea, button {
      font: inherit;
    }
    input[type="checkbox"] {
      width: auto;
      margin: 0;
    }
    input, select, textarea {
      width: 100%;
      padding: 9px 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.9);
      color: var(--ink);
    }
    input:focus, select:focus, textarea:focus {
      outline: 2px solid rgba(47, 95, 218, 0.16);
      border-color: rgba(47, 95, 218, 0.38);
    }
    textarea {
      min-height: 84px;
      resize: vertical;
    }
    .row {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    .run-actions,
    .review-actions {
      display: grid;
      grid-template-columns: 1fr;
      gap: 8px;
    }
    .run-actions button,
    .review-actions button {
      width: 100%;
      border-radius: 8px;
      padding: 9px 12px;
      white-space: normal;
    }
    .review-actions .final-action {
      padding: 10px 14px;
    }
    .epic-actions {
      display: grid;
      grid-template-columns: 1fr;
      gap: 8px;
    }
    .epic-actions button {
      width: 100%;
      border-radius: 8px;
      padding: 9px 12px;
      white-space: normal;
    }
    .epic-actions .create-action {
      padding: 10px 14px;
    }
    .checkbox-row {
      display: flex;
      align-items: center;
      gap: 10px;
      color: var(--muted);
      font-size: var(--font-sm);
    }
    button {
      border: 0;
      border-radius: 8px;
      padding: 9px 13px;
      cursor: pointer;
      background: var(--accent);
      color: white;
      font-weight: 700;
      font-size: var(--font-sm);
      box-shadow: 0 8px 16px rgba(47, 95, 218, 0.18);
      transition:
        background-color 150ms ease,
        border-color 150ms ease,
        box-shadow 150ms ease,
        color 150ms ease,
        transform 150ms ease;
      will-change: transform;
    }
    button:hover:not(:disabled) {
      background: #244fc2;
      box-shadow: 0 12px 24px rgba(47, 95, 218, 0.24);
      transform: translateY(-1px);
    }
    button:active:not(:disabled) {
      box-shadow: 0 5px 12px rgba(47, 95, 218, 0.18);
      transform: translateY(0);
    }
    button:disabled {
      cursor: not-allowed;
      opacity: 0.56;
      box-shadow: none;
      transform: none;
    }
    button.secondary {
      background: rgba(255, 255, 255, 0.58);
      color: var(--accent);
      border: 1px solid rgba(47, 95, 218, 0.22);
      box-shadow: none;
    }
    button.secondary:hover:not(:disabled) {
      background: #ffffff;
      border-color: rgba(47, 95, 218, 0.42);
      color: #244fc2;
      box-shadow: 0 10px 20px rgba(47, 95, 218, 0.16);
    }
    button.final-action {
      background: var(--danger);
      box-shadow: 0 8px 16px rgba(196, 50, 43, 0.18);
    }
    button.final-action:hover:not(:disabled) {
      background: #a92722;
      box-shadow: 0 12px 24px rgba(196, 50, 43, 0.24);
    }
    button.ghost {
      background: transparent;
      color: var(--ink);
      border: 1px solid rgba(70, 82, 102, 0.20);
      box-shadow: none;
    }
    button.ghost:hover:not(:disabled) {
      background: #ffffff;
      border-color: var(--accent-line);
      color: var(--accent);
      box-shadow: 0 10px 20px rgba(15, 23, 42, 0.10);
    }
    .flow-section-title {
      border-top: 1px solid var(--line);
      padding-top: 12px;
      margin-top: 2px;
    }
    .status {
      border-left: 3px solid rgba(47, 95, 218, 0.50);
      padding: 7px 0 7px 10px;
      background: transparent;
      border-radius: 0;
      white-space: pre-wrap;
      word-break: break-word;
      font-size: var(--font-sm);
      color: var(--label);
    }
    .status.error {
      border-left-color: var(--danger);
      background: transparent;
      color: var(--danger);
    }
    .workspace-section {
      border: 0;
      border-radius: 0;
      padding: 0;
      background: transparent;
      display: grid;
      gap: 8px;
    }
    .workspace-section.primary {
      border-bottom: 1px solid var(--neutral-line);
      padding-bottom: 12px;
      background: transparent;
    }
    .workspace-section-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }
    .workspace-section-title {
      margin: 0;
      color: var(--label-strong);
      font-size: var(--font-xs);
      font-weight: 800;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }
    .debug-view {
      border: 0;
      border-top: 1px solid var(--neutral-line);
      border-radius: 0;
      background: transparent;
      padding: 8px 0 0;
      margin-top: 2px;
    }
    .debug-view summary {
      cursor: pointer;
      color: var(--muted);
      font-size: var(--font-xs);
      font-weight: 700;
      width: fit-content;
    }
    .debug-view summary:hover {
      color: var(--accent);
    }
    .debug-view pre {
      margin-top: 10px;
    }
    .debug-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      margin-top: 10px;
    }
    @media (max-width: 720px) {
      .debug-grid { grid-template-columns: 1fr; }
    }
    .debug-label {
      color: var(--label);
      font-size: 11px;
      font-weight: 800;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      margin-bottom: 6px;
    }
    .warning-box {
      border-left: 4px solid var(--accent-2);
      padding: 12px 14px;
      background: var(--amber-soft);
      border-radius: 10px;
      white-space: normal;
      display: grid;
      gap: 8px;
    }
    .warning-box.hidden {
      display: none;
    }
    .warning-title {
      font-size: var(--font-xs);
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--label-strong);
      font-weight: 800;
    }
    .warning-list {
      display: grid;
      gap: 8px;
    }
    .warning-item {
      border: 1px solid var(--amber-line);
      border-radius: 12px;
      padding: 10px 12px;
      background: var(--surface);
    }
    .warning-item strong {
      display: inline;
      margin-right: 6px;
    }
    .modal-backdrop {
      position: fixed;
      inset: 0;
      background: rgba(24, 34, 48, 0.42);
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 20px;
      z-index: 20;
    }
    .modal-backdrop.hidden {
      display: none;
    }
    .modal {
      width: min(560px, 100%);
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 12px;
      box-shadow: var(--shadow);
      padding: 20px;
      display: grid;
      gap: 12px;
    }
    .modal h3 {
      margin: 0;
    }
    .modal-copy {
      color: var(--muted);
      line-height: 1.5;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .detail-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }
    .detail-grid.run-grid {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    @media (max-width: 720px) {
      .detail-grid { grid-template-columns: 1fr; }
    }
    .detail-field {
      border: 0;
      border-top: 1px solid var(--section-line);
      border-radius: 0;
      padding: 10px 0;
      background: transparent;
    }
    .detail-field .label {
      display: block;
      font-size: var(--font-xs);
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--label);
      font-weight: 800;
      margin-bottom: 6px;
    }
    .detail-field .value {
      font-size: var(--font-md);
      line-height: 1.4;
      word-break: break-word;
    }
    .detail-field.wide {
      grid-column: 1 / -1;
    }
    .detail-field.primary-detail {
      border: 1px solid var(--accent-line);
      border-radius: 8px;
      padding: 10px 12px;
      background: var(--accent-soft);
    }
    .content-stack {
      grid-column: 1 / -1;
      display: grid;
      gap: 10px;
    }
    .content-block {
      border-top: 1px solid var(--section-line);
      padding-top: 12px;
      display: grid;
      gap: 6px;
    }
    .content-block.primary {
      border: 1px solid var(--accent-line);
      border-radius: 8px;
      padding: 10px 12px;
      background: var(--accent-soft);
    }
    .content-label {
      color: var(--label-strong);
      font-size: var(--font-xs);
      font-weight: 800;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }
    .content-value {
      color: var(--ink);
      font-size: var(--font-md);
      line-height: 1.45;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .metadata-details {
      grid-column: 1 / -1;
      border: 1px solid var(--neutral-line);
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.38);
      padding: 0;
    }
    .metadata-details summary {
      cursor: pointer;
      color: var(--label);
      font-size: var(--font-xs);
      font-weight: 800;
      padding: 9px 10px;
    }
    .metadata-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      padding: 0 10px 10px;
    }
    .empty-state {
      grid-column: 1 / -1;
      border: 1px dashed rgba(102, 112, 133, 0.34);
      border-radius: 10px;
      padding: 16px;
      background: rgba(255, 255, 255, 0.60);
      display: grid;
      gap: 5px;
    }
    .empty-mark {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 28px;
      height: 28px;
      border-radius: 8px;
      margin-bottom: 3px;
      background: var(--accent-soft);
      color: var(--accent);
      font-weight: 800;
      font-size: var(--font-sm);
    }
    .empty-state .empty-title {
      color: var(--ink);
      font-size: 19px;
      font-weight: 700;
      line-height: 1.35;
    }
    .empty-state .empty-copy {
      color: var(--muted);
      font-size: var(--font-sm);
      line-height: 1.45;
    }
    .story-review-meta {
      border: 1px solid var(--neutral-line);
      border-radius: 8px;
      padding: 8px 10px;
      background: rgba(255, 255, 255, 0.42);
      color: var(--muted);
      font-size: var(--font-xs);
      font-weight: 700;
    }
    .metric-badge {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 5px 8px;
      background: var(--surface);
      color: var(--muted);
      font-size: var(--font-xs);
      font-weight: 700;
      line-height: 1;
    }
    .metric-badge.ready {
      border-color: var(--success-line);
      background: var(--success-soft);
      color: var(--success);
    }
    .metric-badge.review {
      border-color: var(--amber-line);
      background: var(--amber-soft);
      color: var(--accent-2);
    }
    .metric-badge.risk {
      border-color: var(--danger-line);
      background: var(--danger-soft);
      color: var(--danger);
    }
    .tabs {
      display: flex;
      gap: 4px;
      flex-wrap: nowrap;
      margin-bottom: 12px;
      padding: 4px;
      border: 1px solid rgba(47, 95, 218, 0.22);
      border-radius: 12px;
      background: #e9f0fa;
      box-shadow: inset 0 1px 2px rgba(15, 23, 42, 0.06);
    }
    .tab-btn {
      flex: 1;
      position: relative;
      border: 1px solid transparent;
      border-radius: 8px;
      background: transparent;
      color: var(--label);
      padding: 9px 10px;
      font-weight: 800;
      font-size: var(--font-sm);
      box-shadow: none;
      transition:
        background-color 150ms ease,
        border-color 150ms ease,
        box-shadow 150ms ease,
        color 150ms ease,
        transform 150ms ease;
    }
    .tab-btn:hover:not(.active) {
      background: rgba(255, 255, 255, 0.72);
      color: var(--accent);
      box-shadow: none;
      transform: translateY(-1px);
    }
    .tab-btn.active {
      background: var(--accent);
      color: #ffffff;
      border-color: rgba(31, 79, 201, 0.62);
      box-shadow: 0 9px 18px rgba(47, 95, 218, 0.26);
      transform: translateY(-1px);
    }
    .tab-btn.active::after {
      content: "";
      position: absolute;
      left: 50%;
      bottom: -5px;
      width: 28px;
      height: 3px;
      border-radius: 999px;
      background: var(--accent);
      transform: translateX(-50%);
    }
    .tab-panel.hidden {
      display: none;
    }
    .hidden {
      display: none;
    }
    .item-card {
      border: 0;
      border-left: 3px solid rgba(47, 95, 218, 0.28);
      border-radius: 0;
      padding: 4px 0 4px 10px;
      background: transparent;
    }
    .item-card strong { display: block; margin-bottom: 6px; }
    .pill {
      display: inline-block;
      padding: 4px 10px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
      font-size: var(--font-xs);
      font-weight: 700;
      margin-right: 6px;
    }
    pre {
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: var(--font-xs);
      line-height: 1.35;
      background: var(--surface-soft);
      border: 1px solid var(--neutral-line);
      border-radius: 10px;
      padding: 10px 12px;
      color: #263442;
      max-height: 420px;
      overflow: auto;
    }
    .hint {
      color: var(--muted);
      font-size: var(--font-sm);
      line-height: 1.5;
    }
  </style>
</head>
<body>
  <div class="shell">
    <nav class="app-nav" aria-label="Primary">
      <div class="app-brand">
        <span class="app-brand-mark">AJ</span>
        <span class="app-brand-copy">
          <strong>Agentic Jira Enhancer</strong>
        </span>
      </div>
      <div class="app-meta" role="status" aria-live="polite" aria-label="Runtime status">
        __LAST_RUN_CHIP__
      </div>
      <div class="app-links" aria-label="Sections">
        <a class="app-link active" href="/ui">Console</a>
        <a class="app-link" href="/ui/rl">Prompt Learning</a>
      </div>
    </nav>
    <div class="grid">
      <section class="panel stack command-panel">
        <div class="tabs" id="flowTabs" role="tablist" aria-label="Workflow">
          <button id="enrichmentFlowTab" class="tab-btn active" data-target="enrichmentFlowPanel" type="button" role="tab" aria-selected="true" aria-controls="enrichmentFlowPanel">Enrich Jira</button>
          <button id="epicStoryFlowTab" class="tab-btn" data-target="epicStoryFlowPanel" type="button" role="tab" aria-selected="false" aria-controls="epicStoryFlowPanel">Break Down Epic</button>
        </div>

        <div id="enrichmentFlowPanel" class="tab-panel stack" role="tabpanel" aria-labelledby="enrichmentFlowTab">
          <ol id="enrichWorkflowSteps" class="workflow-steps" aria-label="Enrich workflow steps">
            <li class="workflow-step current" data-step="configure" aria-current="step"><span class="workflow-step-text"><span class="workflow-label">Configure</span><span class="workflow-time"></span></span></li>
            <li class="workflow-step" data-step="run" aria-disabled="true"><span class="workflow-step-text"><span class="workflow-label">Run</span><span class="workflow-time"></span></span></li>
            <li class="workflow-step" data-step="review" aria-disabled="true"><span class="workflow-step-text"><span class="workflow-label">Review</span><span class="workflow-time"></span></span></li>
            <li class="workflow-step" data-step="writeback" aria-disabled="true"><span class="workflow-step-text"><span class="workflow-label">Write</span><span class="workflow-time"></span></span></li>
          </ol>
          <form method="post" action="/ui/create-run" class="stack">
          <div class="step-section">
            <div class="step-section-title">Configure</div>
          <div class="field-group">
            <div class="field-group-title">Run target</div>
            <label>
              Scope Type
              <select id="scopeType" name="scopeType">
                <option value="issue" __SCOPE_ISSUE_SELECTED__>Issue</option>
                <option value="sprint" __SCOPE_SPRINT_SELECTED__>Sprint</option>
                <option value="epic" __SCOPE_EPIC_SELECTED__>Epic</option>
                <option value="release" __SCOPE_RELEASE_SELECTED__>Release</option>
              </select>
            </label>
            <label>
              Scope Value
              <input id="scopeValue" name="scopeValue" value="__SCOPE_VALUE__" />
            </label>
            <label>
              Requestor
              <input id="requestor" name="requestor" value="__REQUESTOR__" />
            </label>
          </div>
          <div class="run-actions" id="enrichRunActions">
            <button id="createRunBtn" type="submit" onclick="if (window.createRun) { window.createRun(); return false; }">Create Run</button>
          </div>
          </div>
          </form>

          <details class="advanced-drawer">
            <summary>Advanced run controls</summary>
            <div class="advanced-drawer-body">
              <div class="field-group">
                <div class="field-group-title">Existing run</div>
                <label>
                  Run ID
                  <input id="runId" name="runId" value="__RUN_ID__" />
                </label>
                <label>
                  Issue Key
                  <input id="issueKey" name="issueKey" value="__ISSUE_KEY__" />
                </label>
              </div>
              <div class="run-actions">
                <button id="refreshRunBtn" type="button" class="ghost" onclick="if (document.getElementById('runId').value.trim()) { window.location.href = '/ui?' + ['runId=' + encodeURIComponent(document.getElementById('runId').value.trim()), 'issueKey=' + encodeURIComponent(document.getElementById('issueKey').value.trim()), 'scopeType=' + encodeURIComponent(document.getElementById('scopeType').value), 'scopeValue=' + encodeURIComponent(document.getElementById('scopeValue').value.trim()), 'requestor=' + encodeURIComponent(document.getElementById('requestor').value.trim())].join('&'); return false; } if (window.refreshRun) { window.refreshRun(); } return false;">Refresh Run</button>
                <button id="replayBtn" type="button" class="ghost" onclick="if (window.replayExistingRun) { window.replayExistingRun(); } return false;">Replay Run</button>
              </div>
            </div>
          </details>

          <div id="enrichReviewSection" class="step-section hidden">
          <div class="step-section-title">Review</div>
          <form method="post" action="/ui/decision" class="stack">
          <div class="field-group">
            <div class="field-group-title">Reviewer inputs</div>
            <label>
              Reviewer
              <input id="reviewer" name="reviewer" value="approver@example.org" />
            </label>
            <label>
              Decision
              <select id="decision" name="decision">
                <option value="approve">Approve</option>
                <option value="reject">Reject</option>
                <option value="regenerate">Regenerate</option>
                <option value="user_input">User Input</option>
                <option value="prompt">Prompt</option>
              </select>
            </label>
            <label>
              Reviewer Notes
              <textarea id="reviewerNotes" name="reviewerNotes" placeholder="Optional reviewer notes"></textarea>
            </label>
          </div>

          <details class="advanced-drawer">
            <summary>Advanced review inputs</summary>
            <div class="advanced-drawer-body">
              <div class="field-group">
                <div class="field-group-title">Prompt controls</div>
                <label>
                  User Input
                  <textarea id="userInput" name="userInput" placeholder="Provide detailed user input. You can paste Confluence page links/ids and Bitbucket PR links/refs here. For epic runs, this can be applied to every Jira in the run.">__USER_INPUT_VALUE__</textarea>
                </label>
                <label>
                  Prompt
                  <textarea id="promptInput" name="promptInput" placeholder="Generate or enter a Jira-specific prompt here. Submit Decision with `Prompt` selected to execute it.">__PROMPT_INPUT_VALUE__</textarea>
                </label>
              </div>
              <div class="item-card">
                <strong>Generate Prompt</strong>
                <div class="hint">Generate Jira-specific Codex input from the selected Jira context, prior preferences, and already captured evidence. This fills the `Prompt` box so `Submit Decision` can execute it when `Prompt` is selected.</div>
                <textarea id="generatedPrompt" class="hidden">__GENERATED_PROMPT__</textarea>
                <div class="row">
                  <button id="generatePromptBtn" class="ghost" type="button" onclick="if (window.generatePromptFromSelection) { window.generatePromptFromSelection(); } return false;">Generate Prompt</button>
                </div>
              </div>
              <label class="checkbox-row">
                <input id="applyAllItems" name="applyAllItems" type="checkbox" />
                <span>Apply to all Jiras in run</span>
              </label>
            </div>
          </details>

          <div class="field-group">
            <div class="field-group-title">Review action</div>
          <div class="review-actions">
            <button id="approveBtn" type="submit" class="secondary" onclick="if (window.submitDecision) { window.submitDecision(); return false; }">Approve Draft</button>
          </div>
          </div>
          </form>
          </div>
          <div id="enrichWritebackSection" class="step-section hidden">
            <div class="step-section-title">Write back</div>
            <div class="field-group">
              <div class="field-group-title">Final action</div>
              <div class="review-actions">
                <button id="writebackBtn" class="final-action" type="submit" formaction="/ui/writeback" formmethod="post" title="Write Back" onclick="if (window.writebackRun) { window.writebackRun(); return false; }">Write to Jira</button>
              </div>
            </div>
          </div>
        </div>

        <div id="epicStoryFlowPanel" class="tab-panel stack hidden" role="tabpanel" aria-labelledby="epicStoryFlowTab" hidden>
          <ol id="epicWorkflowSteps" class="workflow-steps" aria-label="Epic workflow steps">
            <li class="workflow-step current" data-step="configure" aria-current="step"><span class="workflow-step-text"><span class="workflow-label">Configure</span><span class="workflow-time"></span></span></li>
            <li class="workflow-step" data-step="suggest" aria-disabled="true"><span class="workflow-step-text"><span class="workflow-label">Suggest</span><span class="workflow-time"></span></span></li>
            <li class="workflow-step" data-step="review" aria-disabled="true"><span class="workflow-step-text"><span class="workflow-label">Review</span><span class="workflow-time"></span></span></li>
            <li class="workflow-step" data-step="create" aria-disabled="true"><span class="workflow-step-text"><span class="workflow-label">Create</span><span class="workflow-time"></span></span></li>
          </ol>
          <form method="post" action="/ui/epic-stories/suggest" class="stack">
          <div class="step-section">
            <div class="step-section-title">Configure</div>
          <div class="field-group">
            <div class="field-group-title">Epic target</div>
            <label>
              Epic Key
              <input id="epicKey" name="epicKey" value="__SCOPE_VALUE__" placeholder="e.g. DEMO-18000" />
            </label>
            <label>
              Project Key
              <input id="projectKey" name="projectKey" value="DEMO" placeholder="e.g. DEMO" />
            </label>
            <label>
              Requestor
              <input id="epicRequestor" name="requestor" value="__REQUESTOR__" />
            </label>
          </div>
          <textarea id="storiesPayload" name="storiesPayload" class="hidden">[]</textarea>
          </div>
          <div class="step-section">
            <div class="step-section-title">Run</div>
            <div class="epic-actions">
              <button id="suggestStoriesBtn" type="submit" class="ghost" onclick="if (window.suggestEpicStories) { window.suggestEpicStories(); return false; }">Suggest Stories</button>
            </div>
          </div>
          <div id="epicReviewActionsSection" class="step-section hidden">
            <div class="step-section-title">Review and create</div>
          <div class="field-group">
            <div class="field-group-title">Story actions</div>
          <div class="epic-actions">
            <button id="selectAllStoriesBtn" type="button" class="ghost" onclick="if (window.selectAllEpicStories) { window.selectAllEpicStories(); } return false;">Select All Stories</button>
            <button id="createStoriesBtn" class="create-action" type="submit" formaction="/ui/epic-stories/create" formmethod="post" onclick="if (window.createEpicStories) { window.createEpicStories(); return false; }">Create Stories</button>
          </div>
          </div>
          </div>
          </form>
        </div>

        <div id="statusBox" class="status" role="status" aria-live="polite" aria-atomic="true">__STATUS_MESSAGE__</div>
      </section>

      <section class="panel stack detail-panel review-workspace">
        <div class="detail-context" id="detailContext">
          <div class="context-row">
            <span id="contextFlow" class="context-flow">Enrich Jira</span>
            <span id="contextStatus" class="context-status">Idle</span>
          </div>
          <div id="contextObject" class="context-object">No run loaded</div>
          <div id="contextMetrics" class="context-metrics hidden"></div>
        </div>
        <h2 id="detailPanelTitle">Run Details</h2>
        <div id="runFieldsPanel" class="workspace-section primary">
          <div class="workspace-section-head">
            <div class="workspace-section-title">Summary</div>
          </div>
          <div id="runWarnings" class="warning-box hidden"></div>
          <div id="runFields" class="detail-grid run-grid">__RUN_FIELDS__</div>
        </div>
        <label>
          <span id="itemSelectLabel">Jira</span>
          <select id="itemSelect" onchange="if (window.handleItemSelectionChange) { window.handleItemSelectionChange(); }">
            __ITEM_OPTIONS__
          </select>
        </label>
        <div id="storyReviewMeta" class="story-review-meta hidden"></div>
        <h3 id="selectedItemHeading">Selected Item</h3>
        <div id="itemFieldsPanel" class="workspace-section">
          <div class="workspace-section-head">
            <div class="workspace-section-title">Details</div>
          </div>
          <div id="itemWarnings" class="warning-box hidden"></div>
          <div id="itemFields" class="detail-grid">__ITEM_FIELDS__</div>
        </div>
        <details id="debugJsonPanel" class="debug-view" aria-label="Debug JSON">
          <summary>Debug JSON</summary>
          <div class="debug-grid">
            <div>
              <div class="debug-label">Run</div>
              <pre id="runSummary">__RUN_JSON__</pre>
            </div>
            <div>
              <div class="debug-label">Item</div>
              <pre id="itemDetail">__ITEM_JSON__</pre>
            </div>
          </div>
        </details>
      </section>
    </div>
  </div>

  <div id="authModal" class="modal-backdrop hidden" aria-hidden="true">
    <div class="modal" role="dialog" aria-modal="true" aria-labelledby="authModalTitle">
      <h3 id="authModalTitle">Confluence Authentication Required</h3>
      <div id="authModalMessage" class="modal-copy">The project runtime needs a Confluence-authenticated MCP session before it can read the supplied page.</div>
      <div class="row">
        <button id="authOpenBtn" type="button">Open Confluence</button>
        <button id="authRetryBtn" class="secondary" type="button">Retry</button>
        <button id="authDismissBtn" class="ghost" type="button">Dismiss</button>
      </div>
    </div>
  </div>

  <script>
    const API_TIMEOUT_MS = 30000;
    const RUN_POLL_INTERVAL_MS = 2000;
    const CONSOLE_STATE_STORAGE_KEY = "jiraEnhancer.consoleState";
    const EPIC_STATE_STORAGE_KEY = "jiraEnhancer.epicBreakdownState";
    const CONFLUENCE_BASE_URL = "__CONFLUENCE_BASE_URL__";
    const els = {
      scopeType: document.getElementById("scopeType"),
      scopeValue: document.getElementById("scopeValue"),
      requestor: document.getElementById("requestor"),
      runId: document.getElementById("runId"),
      issueKey: document.getElementById("issueKey"),
      epicKey: document.getElementById("epicKey"),
      projectKey: document.getElementById("projectKey"),
      epicRequestor: document.getElementById("epicRequestor"),
      storiesPayload: document.getElementById("storiesPayload"),
      reviewer: document.getElementById("reviewer"),
      decision: document.getElementById("decision"),
      reviewerNotes: document.getElementById("reviewerNotes"),
      userInput: document.getElementById("userInput"),
      promptInput: document.getElementById("promptInput"),
      applyAllItems: document.getElementById("applyAllItems"),
      createRunBtn: document.getElementById("createRunBtn"),
      refreshRunBtn: document.getElementById("refreshRunBtn"),
      enrichReviewSection: document.getElementById("enrichReviewSection"),
      enrichWritebackSection: document.getElementById("enrichWritebackSection"),
      generatePromptBtn: document.getElementById("generatePromptBtn"),
      approveBtn: document.getElementById("approveBtn"),
      replayBtn: document.getElementById("replayBtn"),
      writebackBtn: document.getElementById("writebackBtn"),
      suggestStoriesBtn: document.getElementById("suggestStoriesBtn"),
      selectAllStoriesBtn: document.getElementById("selectAllStoriesBtn"),
      createStoriesBtn: document.getElementById("createStoriesBtn"),
      epicReviewActionsSection: document.getElementById("epicReviewActionsSection"),
      statusBox: document.getElementById("statusBox"),
      contextFlow: document.getElementById("contextFlow"),
      contextObject: document.getElementById("contextObject"),
      contextStatus: document.getElementById("contextStatus"),
      contextMetrics: document.getElementById("contextMetrics"),
      detailPanelTitle: document.getElementById("detailPanelTitle"),
      enrichWorkflowSteps: document.getElementById("enrichWorkflowSteps"),
      epicWorkflowSteps: document.getElementById("epicWorkflowSteps"),
      runSummary: document.getElementById("runSummary"),
      runWarnings: document.getElementById("runWarnings"),
      runFields: document.getElementById("runFields"),
      itemSelectLabel: document.getElementById("itemSelectLabel"),
      itemSelect: document.getElementById("itemSelect"),
      storyReviewMeta: document.getElementById("storyReviewMeta"),
      selectedItemHeading: document.getElementById("selectedItemHeading"),
      itemWarnings: document.getElementById("itemWarnings"),
      itemFields: document.getElementById("itemFields"),
      itemDetail: document.getElementById("itemDetail"),
      generatedPrompt: document.getElementById("generatedPrompt"),
      authModal: document.getElementById("authModal"),
      authModalMessage: document.getElementById("authModalMessage"),
      authOpenBtn: document.getElementById("authOpenBtn"),
      authRetryBtn: document.getElementById("authRetryBtn"),
      authDismissBtn: document.getElementById("authDismissBtn"),
    };
    let authModalRetryAction = null;
    let runPollTimer = null;
    let currentRun = null;
    let currentItem = null;
    let selectedRunIssueKey = "";
    let detailMode = "run";
    let activeFlow = "enrich";
    let lastRenderedRunSignature = "";
    let lastRenderedItemSignature = "";
    let flowStatuses = {
      enrich: {
        message: document.getElementById("statusBox").textContent || "Ready.",
        isError: document.getElementById("statusBox").classList.contains("error"),
      },
      epic: {
        message: "No epic breakdown started.",
        isError: false,
      },
    };
    let currentEpicResult = null;
    let longOpTimer = null;
    let longOpFlow = "";

    function coalesce(value, fallback = "") {
      return value === null || value === undefined ? fallback : value;
    }

    function getNested(value, path, fallback = "") {
      let current = value;
      for (const key of path) {
        if (current === null || current === undefined) {
          return fallback;
        }
        current = current[key];
      }
      return current === null || current === undefined ? fallback : current;
    }

    function looksLikeJiraKey(value) {
      return /^[A-Z][A-Z0-9]+-\\d+$/i.test(String(value || "").trim());
    }

    function currentEnrichIssueKey() {
      const candidates = [selectedRunIssueKey, els.issueKey.value, els.itemSelect.value];
      return candidates.map((value) => String(value || "").trim()).find((value) => looksLikeJiraKey(value)) || "";
    }

    function payloadSignature(value) {
      try {
        return JSON.stringify(value);
      } catch (error) {
        return String(Date.now());
      }
    }

    function escapeHtml(value) {
      return String(coalesce(value, ""))
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
    }

    function renderFields(container, fields) {
      container.innerHTML = fields.map((field) => `
        <div class="detail-field">
          <span class="label">${escapeHtml(field.label)}</span>
          <div class="value">${escapeHtml(field.value)}</div>
        </div>
      `).join("");
    }

    function renderEmptyState(container, title, copy = "") {
      container.innerHTML = `
        <div class="empty-state">
          <span class="empty-mark">·</span>
          <div class="empty-title">${escapeHtml(title)}</div>
          ${copy ? `<div class="empty-copy">${escapeHtml(copy)}</div>` : ""}
        </div>
      `;
    }

    function detailFieldHtml(label, value) {
      return `
        <div class="detail-field">
          <span class="label">${escapeHtml(label)}</span>
          <div class="value">${escapeHtml(value)}</div>
        </div>
      `;
    }

    function contentBlockHtml(label, value, className = "") {
      const classes = `content-block ${className}`.trim();
      return `
        <section class="${classes}">
          <div class="content-label">${escapeHtml(label)}</div>
          <div class="content-value">${escapeHtml(value)}</div>
        </section>
      `;
    }

    function detailFieldsHtml(fields) {
      return fields.map((field) => detailFieldHtml(field.label, field.value)).join("");
    }

    function scoreTone(value, readyAt = 9, reviewAt = 7) {
      const numeric = Number(value);
      if (!Number.isFinite(numeric)) {
        return "";
      }
      if (numeric >= readyAt) {
        return "ready";
      }
      if (numeric >= reviewAt) {
        return "review";
      }
      return "risk";
    }

    function metricBadgeHtml(label, value, tone = "") {
      const className = `metric-badge ${tone}`.trim();
      return `<span class="${className}">${escapeHtml(label)}: ${escapeHtml(coalesce(value, ""))}</span>`;
    }

    function setElementHidden(element, hidden) {
      if (element) {
        element.classList.toggle("hidden", Boolean(hidden));
      }
    }

    function setActionHidden(element, hidden) {
      if (element) {
        element.classList.toggle("action-hidden", Boolean(hidden));
      }
    }

    function updateEnrichActionVisibility(item = currentItem) {
      const runId = els.runId.value.trim();
      const issueKey = selectedRunIssueKey || els.issueKey.value.trim();
      const hasSelectedItem = Boolean(runId && issueKey && item);
      const itemStatus = String(coalesce(item && item.status, "")).toLowerCase();
      const draftStatus = String(getNested(item, ["draft", "status"], "")).toLowerCase();
      const isWritten = itemStatus.includes("written");
      const isApproved = itemStatus.includes("approved") || itemStatus.includes("writeback");
      const isWaiting = ["pending", "queued", "running", "processing", "initializing"].some((status) => itemStatus.includes(status));
      const isFailed = itemStatus.includes("failed") || itemStatus.includes("error");
      const hasDraft = Boolean(getNested(item, ["draft", "payload"], null)) || draftStatus.includes("ready") || itemStatus.includes("ready");
      const canReview = hasSelectedItem && hasDraft && !isWaiting && !isFailed && !isApproved && !isWritten;
      const canWriteBack = hasSelectedItem && isApproved && !isWritten;
      setElementHidden(els.enrichReviewSection, !canReview);
      setElementHidden(els.enrichWritebackSection, !canWriteBack);
      setActionHidden(els.approveBtn, !canReview);
      setActionHidden(els.writebackBtn, !canWriteBack);
      setActionHidden(els.generatePromptBtn, !canReview);
      setActionHidden(els.replayBtn, !runId);
    }

    function updateEpicActionVisibility(stories = null) {
      let rows = Array.isArray(stories) ? stories : [];
      if (!stories) {
        try {
          rows = parseStoriesPayload();
        } catch (error) {
          rows = [];
        }
      }
      const hasStories = rows.length > 0;
      const hasApproved = rows.some((story) => Boolean(story.approved));
      setElementHidden(els.epicReviewActionsSection, !hasStories);
      setActionHidden(els.createStoriesBtn, !hasApproved);
    }

    function renderStoryReviewMeta(stories) {
      const rows = Array.isArray(stories) ? stories : [];
      if (!rows.length) {
        els.storyReviewMeta.classList.add("hidden");
        els.storyReviewMeta.textContent = "";
        return;
      }
      const selected = rows.filter((story) => Boolean(story.approved)).length;
      const ready = rows.filter((story) => story.meets_threshold || Number(story.readiness_score || 0) >= 9 || Number(story.confidence || 0) >= 9).length;
      els.storyReviewMeta.classList.remove("hidden");
      els.storyReviewMeta.textContent = `Selected ${selected} of ${rows.length} stories | Ready ${ready} of ${rows.length}`;
    }

    function renderWarnings(container, warnings) {
      const list = Array.isArray(warnings) ? warnings : [];
      if (!list.length) {
        container.classList.add("hidden");
        container.innerHTML = "";
        return;
      }
      container.classList.remove("hidden");
      container.innerHTML = `
        <div class="warning-title">Warnings</div>
        <div class="warning-list">
          ${list.map((warning) => `
            <div class="warning-item">
              <div><strong>${escapeHtml(warning.code || "warning")}</strong>${escapeHtml(warning.source || "")}${warning.ref ? ` · ${escapeHtml(warning.ref)}` : ""}</div>
              <div>${escapeHtml(warning.message || "")}</div>
            </div>
          `).join("")}
        </div>
      `;
    }

    function openAuthModal(message, authUrl = "", retryAction = null) {
      els.authModalMessage.textContent = message;
      authModalRetryAction = retryAction;
      els.authOpenBtn.dataset.url = authUrl || CONFLUENCE_BASE_URL;
      els.authOpenBtn.disabled = !(els.authOpenBtn.dataset.url);
      els.authModal.classList.remove("hidden");
      els.authModal.setAttribute("aria-hidden", "false");
    }

    function closeAuthModal() {
      els.authModal.classList.add("hidden");
      els.authModal.setAttribute("aria-hidden", "true");
      authModalRetryAction = null;
    }

    function maybePromptForConfluenceAuth(warnings, retryAction = null) {
      const warning = (Array.isArray(warnings) ? warnings : []).find((entry) => entry.code === "confluence_auth_required");
      if (!warning) {
        return;
      }
      const detail = warning.detail ? `\n\nDetails: ${warning.detail}` : "";
      openAuthModal(warning.message + detail, warning.auth_url || "", retryAction);
    }

    function persistConsoleState() {
      try {
        const state = {
          activeFlow,
          scopeType: els.scopeType.value,
          scopeValue: els.scopeValue.value.trim(),
          requestor: els.requestor.value.trim(),
          runId: els.runId.value.trim(),
          issueKey: els.issueKey.value.trim(),
          selectedRunIssueKey,
          enrichStatus: flowStatuses.enrich,
        };
        window.localStorage.setItem(CONSOLE_STATE_STORAGE_KEY, JSON.stringify(state));
      } catch (error) {
        return;
      }
    }

    function restoreConsoleState() {
      try {
        const raw = window.localStorage.getItem(CONSOLE_STATE_STORAGE_KEY);
        if (!raw) {
          return "";
        }
        const state = JSON.parse(raw);
        if (!state || typeof state !== "object") {
          return "";
        }
        const pageHasRun = Boolean(els.runId.value.trim());
        els.scopeType.value = state.scopeType || els.scopeType.value;
        els.scopeValue.value = state.scopeValue || els.scopeValue.value;
        els.requestor.value = state.requestor || els.requestor.value;
        if (!pageHasRun) {
          els.runId.value = state.runId || els.runId.value;
          els.issueKey.value = state.issueKey || els.issueKey.value;
          selectedRunIssueKey = state.selectedRunIssueKey || state.issueKey || selectedRunIssueKey;
          if (state.enrichStatus && typeof state.enrichStatus === "object") {
            flowStatuses.enrich = {
              message: state.enrichStatus.message || flowStatuses.enrich.message,
              isError: Boolean(state.enrichStatus.isError),
            };
          }
        }
        return state.activeFlow === "epic" ? "epic" : "enrich";
      } catch (error) {
        return "";
      }
    }

    function selectedEpicStoryIndex() {
      const selected = Number(els.itemSelect.value || 0);
      return Number.isFinite(selected) ? selected : 0;
    }

    function persistEpicState() {
      try {
        const state = {
          activeFlow,
          epicKey: els.epicKey.value.trim(),
          projectKey: els.projectKey.value.trim(),
          requestor: els.epicRequestor.value.trim(),
          storiesPayload: els.storiesPayload.value || "[]",
          currentEpicResult,
          selectedIndex: selectedEpicStoryIndex(),
          status: flowStatuses.epic,
        };
        window.localStorage.setItem(EPIC_STATE_STORAGE_KEY, JSON.stringify(state));
      } catch (error) {
        return;
      }
    }

    function restoreEpicState() {
      try {
        const raw = window.localStorage.getItem(EPIC_STATE_STORAGE_KEY);
        if (!raw) {
          return "";
        }
        const state = JSON.parse(raw);
        if (!state || typeof state !== "object") {
          return "";
        }
        els.epicKey.value = state.epicKey || els.epicKey.value;
        els.projectKey.value = state.projectKey || els.projectKey.value;
        els.epicRequestor.value = state.requestor || els.epicRequestor.value;
        els.storiesPayload.value = state.storiesPayload || "[]";
        currentEpicResult = state.currentEpicResult || null;
        if (state.status && typeof state.status === "object") {
          flowStatuses.epic = {
            message: state.status.message || "Epic breakdown restored.",
            isError: Boolean(state.status.isError),
          };
        }
        return state.activeFlow === "epic" ? "epic" : "";
      } catch (error) {
        return "";
      }
    }

    function activateTabs(rootId) {
      const root = document.getElementById(rootId);
      if (!root) {
        return;
      }
      const buttons = Array.from(root.querySelectorAll(".tab-btn"));
      buttons.forEach((button) => {
        button.addEventListener("click", () => {
          buttons.forEach((candidate) => candidate.classList.remove("active"));
          buttons.forEach((candidate) => candidate.setAttribute("aria-selected", "false"));
          button.classList.add("active");
          button.setAttribute("aria-selected", "true");
          const targetId = button.dataset.target;
          const panelIds = buttons.map((candidate) => candidate.dataset.target);
          panelIds.forEach((panelId) => {
            const panel = document.getElementById(panelId);
            const hidden = panelId !== targetId;
            panel.classList.toggle("hidden", hidden);
            panel.hidden = hidden;
          });
          if (rootId === "flowTabs" && targetId === "epicStoryFlowPanel") {
            activeFlow = "epic";
            renderEpicIdleDetails();
            renderActiveStatus();
            persistConsoleState();
            persistEpicState();
          } else if (rootId === "flowTabs" && targetId === "enrichmentFlowPanel") {
            activeFlow = "enrich";
            lastRenderedRunSignature = "";
            lastRenderedItemSignature = "";
            if (currentRun) {
              renderRun(currentRun);
              if (currentEnrichIssueKey()) {
                loadItem();
              }
            } else {
              renderEmptyRunDetails();
            }
            renderActiveStatus();
            persistConsoleState();
            persistEpicState();
          }
        });
      });
    }

    function renderActiveStatus() {
      const status = flowStatuses[activeFlow] || flowStatuses.enrich;
      els.statusBox.textContent = status.message;
      els.statusBox.className = status.isError ? "status error" : "status";
    }

    function setStatus(message, isError = false, flow = activeFlow) {
      const targetFlow = flow || activeFlow;
      flowStatuses[targetFlow] = { message, isError };
      if (targetFlow === activeFlow) {
        renderActiveStatus();
      }
      if (targetFlow === "enrich") {
        persistConsoleState();
      }
      if (targetFlow === "epic") {
        persistEpicState();
      }
    }

    function clearLongOpProgress() {
      if (longOpTimer) {
        window.clearInterval(longOpTimer);
        longOpTimer = null;
      }
      if (longOpFlow) {
        setFlowBusy(longOpFlow, false);
        longOpFlow = "";
      }
    }

    function startLongOpProgress(baseMessage, onTick = null, flow = activeFlow) {
      clearLongOpProgress();
      longOpFlow = flow;
      setFlowBusy(flow, true);
      const startedAt = Date.now();
      setStatus(baseMessage, false, flow);
      if (onTick) {
        onTick(0);
      }
      longOpTimer = window.setInterval(() => {
        const elapsedSec = Math.floor((Date.now() - startedAt) / 1000);
        setStatus(`${baseMessage} (${elapsedSec}s elapsed)...`, false, flow);
        if (onTick) {
          onTick(elapsedSec);
        }
      }, 2000);
    }

    function setFlowBusy(flow, busy) {
      const buttonIds = flow === "epic"
        ? ["suggestStoriesBtn", "selectAllStoriesBtn", "createStoriesBtn"]
        : ["createRunBtn", "refreshRunBtn", "generatePromptBtn", "approveBtn", "replayBtn", "writebackBtn"];
      buttonIds.forEach((id) => {
        if (els[id]) {
          els[id].disabled = busy;
        }
      });
    }

    function formatList(values) {
      const list = Array.isArray(values) ? values.filter(Boolean) : [];
      return list.length ? list.join(" | ") : "";
    }

    function updateWorkflowSteps(flow, currentStep, state = "current") {
      const root = flow === "epic" ? els.epicWorkflowSteps : els.enrichWorkflowSteps;
      if (!root) {
        return;
      }
      const steps = Array.from(root.querySelectorAll(".workflow-step"));
      const currentIndex = Math.max(0, steps.findIndex((step) => step.dataset.step === currentStep));
      const timeLabel = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      steps.forEach((step, index) => {
        step.classList.remove("active", "current", "done", "error", "future");
        step.removeAttribute("aria-current");
        step.setAttribute("aria-disabled", index > currentIndex ? "true" : "false");
        if (index < currentIndex) {
          step.classList.add("done");
        } else if (index === currentIndex) {
          step.classList.add(state === "error" ? "error" : "current");
          step.setAttribute("aria-current", "step");
        } else {
          step.classList.add("future");
        }
        if (index <= currentIndex) {
          const timeEl = step.querySelector(".workflow-time");
          if (timeEl && !timeEl.textContent.trim()) {
            timeEl.textContent = timeLabel;
          }
        }
      });
    }

    function enrichWorkflowStepForOperation(operation) {
      const value = String(operation || "").toLowerCase();
      if (value.includes("writing")) {
        return "writeback";
      }
      if (value.includes("submitting") || value.includes("decision")) {
        return "review";
      }
      if (value.includes("creating") || value.includes("loading") || value.includes("replaying") || value.includes("running")) {
        return "run";
      }
      return "configure";
    }

    function epicWorkflowStepForOperation(operation) {
      const value = String(operation || "").toLowerCase();
      if (value.includes("creating")) {
        return "create";
      }
      if (value.includes("generating") || value.includes("suggest")) {
        return "suggest";
      }
      if (value.includes("review") || value.includes("payload") || value.includes("selected") || value.includes("ready")) {
        return "review";
      }
      return "configure";
    }

    function formatStatusLabel(status) {
      const value = String(status || "idle").toLowerCase();
      if (value.includes("fail") || value.includes("error")) {
        return "Failed";
      }
      if (value.includes("running") || value.includes("generating") || value.includes("creating") || value.includes("processing") || value.includes("submitting") || value.includes("writing") || value.includes("replaying") || value.includes("loading")) {
        return "Running";
      }
      if (value.includes("suggest")) {
        return "Suggested";
      }
      if (value.includes("ready") || value.includes("complete") || value.includes("loaded") || value.includes("created") || value.includes("exists")) {
        return "Ready";
      }
      if (value.includes("idle")) {
        return "Idle";
      }
      const label = value.replace(/_/g, " ");
      return label.charAt(0).toUpperCase() + label.slice(1);
    }

    function statusTone(status) {
      const value = String(status || "").toLowerCase();
      if (value.includes("fail") || value.includes("error")) {
        return "error";
      }
      if (value.includes("running") || value.includes("generating") || value.includes("creating") || value.includes("processing")) {
        return "running";
      }
      if (value.includes("ready") || value.includes("suggest") || value.includes("complete") || value.includes("loaded")) {
        return "ready";
      }
      return "";
    }

    function isEpicLongOperationStatus(status) {
      const value = String(status || "").toLowerCase();
      return value.includes("generating") || value.includes("creating");
    }

    function setDetailContext(flow, objectLabel, status) {
      els.contextFlow.textContent = flow;
      els.contextObject.textContent = objectLabel || "No context loaded";
      els.contextStatus.textContent = formatStatusLabel(status);
      els.contextStatus.className = `context-status ${statusTone(status)}`.trim();
    }

    function setDetailMetrics(metrics = []) {
      if (!els.contextMetrics) {
        return;
      }
      const visibleMetrics = metrics.filter((metric) => coalesce(metric.value, "") !== "");
      if (!visibleMetrics.length) {
        els.contextMetrics.classList.add("hidden");
        els.contextMetrics.innerHTML = "";
        return;
      }
      els.contextMetrics.classList.remove("hidden");
      els.contextMetrics.innerHTML = visibleMetrics
        .map((metric) => metricBadgeHtml(metric.label, metric.value, metric.tone || ""))
        .join("");
    }

    function setEnrichItemMetrics(item) {
      setDetailMetrics([
        { label: "Confidence", value: coalesce(item && item.confidence, ""), tone: scoreTone(item && item.confidence) },
        { label: "Readiness", value: coalesce(item && item.readiness_score, ""), tone: scoreTone(item && item.readiness_score) },
        { label: "Approval", value: item && item.approval_required ? "required" : "not required", tone: item && item.approval_required ? "review" : "ready" },
        { label: "Draft", value: getNested(item, ["draft", "status"], "") },
      ]);
    }

    function parseStoriesPayload() {
      const parsed = JSON.parse(els.storiesPayload.value || "[]");
      return Array.isArray(parsed) ? parsed : [];
    }

    function writeStoriesPayload(stories) {
      els.storiesPayload.value = JSON.stringify(stories, null, 2);
      persistEpicState();
    }

    function renderEpicRawJson(result, stories) {
      const rows = Array.isArray(stories) ? stories : [];
      const rawPayload = rows.length
        ? {
            ...result,
            stories_payload: rows,
          }
        : result;
      els.runSummary.textContent = JSON.stringify(rawPayload, null, 2);
    }

    function setRunDetailMode() {
      detailMode = "run";
      els.detailPanelTitle.textContent = "Run Details";
      els.itemSelectLabel.textContent = "Jira";
      els.selectedItemHeading.textContent = "Selected Item";
      els.storyReviewMeta.classList.add("hidden");
      els.storyReviewMeta.textContent = "";
    }

    function renderEmptyRunDetails() {
      currentItem = null;
      setRunDetailMode();
      updateWorkflowSteps("enrich", "configure");
      updateEnrichActionVisibility(null);
      setDetailContext("Enrich Jira", "No run loaded", "idle");
      setDetailMetrics([]);
      renderWarnings(els.runWarnings, []);
      renderEmptyState(
        els.runFields,
        "Start with a run",
        "Choose a scope and click Create Run. To reopen previous work, expand Advanced run controls, enter the Run ID, then click Refresh Run.",
      );
      els.runSummary.textContent = "Create Run to start, or use Advanced run controls to refresh an existing Run ID.";
      els.itemSelect.innerHTML = '<option value="">Select a Jira from this run</option>';
      renderWarnings(els.itemWarnings, []);
      renderEmptyState(
        els.itemFields,
        "No Jira selected",
        "Create or refresh a run first. When Jira items appear in the dropdown, select one to review the generated draft.",
      );
      els.itemDetail.textContent = "Load a run first, then select a Jira item to inspect its draft.";
    }

    function syncEpicDetailsFromPayload() {
      const stories = parseStoriesPayload();
      renderEpicBreakdownDetails(currentEpicResult || {}, stories);
      return stories;
    }

    function renderEpicSummaryFields(result, stories) {
      const rows = Array.isArray(stories) ? stories : [];
      if (activeFlow !== "epic") {
        return;
      }
      updateWorkflowSteps("epic", rows.length ? "review" : epicWorkflowStepForOperation(result.status || "suggested"));
      setDetailContext(
        "Break Down Epic",
        `Epic ${els.epicKey.value.trim() || "not selected"}`,
        result.status || "suggested",
      );
      setDetailMetrics([
        { label: "Suggested", value: rows.length },
        { label: "Selected", value: rows.filter((story) => Boolean(story.approved)).length },
        { label: "Project", value: els.projectKey.value.trim() },
      ]);
      renderWarnings(els.runWarnings, []);
      renderFields(els.runFields, [
        { label: "Epic Key", value: els.epicKey.value.trim() },
        { label: "Status", value: result.status || "suggested" },
        { label: "Suggested Stories", value: rows.length },
        { label: "Selected Stories", value: rows.filter((story) => Boolean(story.approved)).length },
      ]);
      renderEpicRawJson(result, rows);
    }

    function renderEpicIdleDetails() {
      let stories = [];
      try {
        stories = parseStoriesPayload();
      } catch (error) {
        stories = [];
      }
      if (stories.length) {
        renderEpicBreakdownDetails(currentEpicResult || { status: "payload_loaded" }, stories);
        return;
      }
      if (currentEpicResult && isEpicLongOperationStatus(currentEpicResult.status)) {
        renderEpicOperationStatus(currentEpicResult.status, currentEpicResult.elapsed_seconds || 0);
        return;
      }
      if (currentEpicResult && currentEpicResult.status) {
        renderEpicBreakdownDetails(currentEpicResult, stories);
        return;
      }
      detailMode = "epicStories";
      clearRunPoll();
      updateWorkflowSteps("epic", "configure");
      updateEpicActionVisibility([]);
      els.detailPanelTitle.textContent = "Epic Breakdown Details";
      els.itemSelectLabel.textContent = "Story";
      els.selectedItemHeading.textContent = "Selected Story";
      renderStoryReviewMeta([]);
      setDetailContext(
        "Break Down Epic",
        "No epic breakdown started",
        "idle",
      );
      setDetailMetrics([]);
      renderWarnings(els.runWarnings, []);
      renderEmptyState(
        els.runFields,
        "Break down an epic",
        "Enter an Epic Key and Project Key on the left, then click Suggest Stories to generate reviewable story drafts.",
      );
      renderEpicRawJson({ status: "idle" }, []);
      els.itemSelect.innerHTML = '<option value="">No stories loaded</option>';
      renderWarnings(els.itemWarnings, []);
      renderEmptyState(
        els.itemFields,
        "No story selected",
        "After story suggestions are generated, pick a story here to review readiness, confidence, and acceptance criteria.",
      );
      els.itemDetail.textContent = "Suggest stories first, then select a story to inspect it.";
    }

    function renderEpicOperationStatus(operation, elapsedSec = 0) {
      currentEpicResult = {
        epic_key: els.epicKey.value.trim(),
        project_key: els.projectKey.value.trim(),
        requestor: els.epicRequestor.value.trim() || els.requestor.value.trim(),
        status: operation,
        elapsed_seconds: elapsedSec,
      };
      persistEpicState();
      if (activeFlow !== "epic") {
        return;
      }
      updateWorkflowSteps("epic", epicWorkflowStepForOperation(operation));
      updateEpicActionVisibility([]);
      detailMode = "epicStories";
      clearRunPoll();
      els.detailPanelTitle.textContent = "Epic Breakdown Details";
      els.itemSelectLabel.textContent = "Story";
      els.selectedItemHeading.textContent = "Selected Story";
      renderStoryReviewMeta([]);
      setDetailContext(
        "Break Down Epic",
        `Epic ${currentEpicResult.epic_key || "not selected"}`,
        operation,
      );
      setDetailMetrics([
        { label: "Elapsed", value: `${elapsedSec}s` },
        { label: "Stories", value: "pending", tone: "review" },
      ]);
      renderWarnings(els.runWarnings, []);
      renderFields(els.runFields, [
        { label: "Epic Key", value: currentEpicResult.epic_key },
        { label: "Project Key", value: currentEpicResult.project_key },
        { label: "Requestor", value: currentEpicResult.requestor },
        { label: "Status", value: operation },
        { label: "Elapsed", value: `${elapsedSec}s` },
        { label: "Suggested Stories", value: "pending" },
      ]);
      renderEpicRawJson(currentEpicResult, []);
      els.itemSelect.innerHTML = '<option value="">Waiting for story suggestions</option>';
      renderWarnings(els.itemWarnings, []);
      renderFields(els.itemFields, [
        { label: "Status", value: "Story suggestions are being generated." },
      ]);
      els.itemDetail.textContent = "Story suggestions are being generated.";
    }

    function renderEpicFailureStatus(message, storiesToPreserve = null) {
      const preservedStories = Array.isArray(storiesToPreserve) ? storiesToPreserve : [];
      currentEpicResult = {
        epic_key: els.epicKey.value.trim(),
        project_key: els.projectKey.value.trim(),
        requestor: els.epicRequestor.value.trim() || els.requestor.value.trim(),
        status: "failed",
        error: message,
      };
      if (preservedStories.length) {
        writeStoriesPayload(preservedStories);
      }
      persistEpicState();
      if (activeFlow !== "epic") {
        return;
      }
      updateWorkflowSteps("epic", epicWorkflowStepForOperation(currentEpicResult.status), "error");
      updateEpicActionVisibility(preservedStories);
      detailMode = "epicStories";
      els.detailPanelTitle.textContent = "Epic Breakdown Details";
      els.itemSelectLabel.textContent = "Story";
      els.selectedItemHeading.textContent = "Selected Story";
      renderStoryReviewMeta(preservedStories);
      setDetailContext(
        "Break Down Epic",
        `Epic ${currentEpicResult.epic_key || "not selected"}`,
        "failed",
      );
      setDetailMetrics([]);
      renderWarnings(els.runWarnings, []);
      renderFields(els.runFields, [
        { label: "Epic Key", value: currentEpicResult.epic_key },
        { label: "Project Key", value: currentEpicResult.project_key },
        { label: "Status", value: "Failed" },
        { label: "Error", value: message },
      ]);
      renderEpicRawJson(currentEpicResult, preservedStories);
      renderWarnings(els.itemWarnings, []);
      if (preservedStories.length) {
        renderEpicBreakdownDetails(currentEpicResult, preservedStories, Number(els.itemSelect.value || 0));
        updateWorkflowSteps("epic", epicWorkflowStepForOperation(currentEpicResult.status), "error");
        renderFields(els.runFields, [
          { label: "Epic Key", value: currentEpicResult.epic_key },
          { label: "Project Key", value: currentEpicResult.project_key },
          { label: "Status", value: "Failed" },
          { label: "Error", value: message },
          { label: "Suggested Stories", value: preservedStories.length },
          { label: "Selected Stories", value: preservedStories.filter((story) => Boolean(story.approved)).length },
        ]);
        renderEpicRawJson(currentEpicResult, preservedStories);
        updateEpicActionVisibility(preservedStories);
        setStatus(`${message}. The selected stories are still loaded; retry Create Stories after checking Jira for partial creates.`, true, "epic");
        return;
      }
      renderFields(els.itemFields, [
        { label: "Status", value: "Epic operation failed." },
      ]);
      els.itemDetail.textContent = message;
    }

    function renderSelectedEpicStory(index) {
      const stories = parseStoriesPayload();
      const story = stories[index];
      if (activeFlow !== "epic") {
        return;
      }
      updateWorkflowSteps("epic", "review");
      setDetailContext(
        "Break Down Epic",
        story ? `Epic ${els.epicKey.value.trim() || "not selected"} / Story ${index + 1}` : `Epic ${els.epicKey.value.trim() || "not selected"}`,
        currentEpicResult && currentEpicResult.status ? currentEpicResult.status : "suggested",
      );
      renderWarnings(els.itemWarnings, []);
      if (!story) {
        setDetailMetrics([]);
        renderStoryReviewMeta(stories);
        renderEmptyState(
          els.itemFields,
          "Choose a story to review",
          "Select one of the suggested stories above to inspect readiness, confidence, description, and acceptance criteria.",
        );
        els.itemDetail.textContent = "Select a suggested story to inspect it.";
        return;
      }
      renderStoryReviewMeta(stories);
      const readinessTone = story.meets_threshold ? "ready" : scoreTone(story.readiness_score);
      const confidenceTone = scoreTone(story.confidence);
      const thresholdTone = story.meets_threshold ? "ready" : "review";
      setDetailMetrics([
        { label: "Readiness", value: coalesce(story.readiness_score, "?"), tone: readinessTone },
        { label: "Confidence", value: coalesce(story.confidence, "?"), tone: confidenceTone },
        { label: "Story Points", value: coalesce(story.story_points, "?") },
        { label: "Threshold", value: story.meets_threshold ? "met" : "needs review", tone: thresholdTone },
      ]);
      els.itemFields.innerHTML = `
        <div class="detail-field">
          <span class="label">Selected</span>
          <label class="checkbox-row">
            <input id="selectedStoryApproved" type="checkbox" ${story.approved ? "checked" : ""} />
            <span>Create this story</span>
          </label>
        </div>
        <div class="content-stack">
          ${contentBlockHtml("Summary", story.summary || "", "primary")}
          ${contentBlockHtml("Description", story.description || "")}
          ${contentBlockHtml("Acceptance Criteria", formatList(story.acceptance_criteria))}
          <details class="metadata-details">
            <summary>Secondary story details</summary>
            <div class="metadata-grid">
              ${detailFieldHtml("Dependencies", formatList(story.dependencies))}
              ${detailFieldHtml("Edge Cases", formatList(story.edge_cases))}
            </div>
          </details>
        </div>
      `;
      const checkbox = document.getElementById("selectedStoryApproved");
      if (checkbox) {
        checkbox.addEventListener("change", () => {
          const currentStories = parseStoriesPayload();
          if (currentStories[index]) {
            currentStories[index].approved = checkbox.checked;
            writeStoriesPayload(currentStories);
            renderStoryReviewMeta(currentStories);
            renderEpicSummaryFields(currentEpicResult || {}, currentStories);
            updateEpicActionVisibility(currentStories);
            els.itemDetail.textContent = JSON.stringify(currentStories[index], null, 2);
            persistEpicState();
          }
        });
      }
      els.itemDetail.textContent = JSON.stringify(story, null, 2);
      persistEpicState();
    }

    function selectAllEpicStories() {
      try {
        const stories = parseStoriesPayload();
        if (!stories.length) {
          setStatus("Suggest stories before selecting all.", true, "epic");
          return;
        }
        const approvedStories = stories.map((story) => ({ ...story, approved: true }));
        const selectedIndex = Number(els.itemSelect.value || 0);
        writeStoriesPayload(approvedStories);
        renderEpicBreakdownDetails(currentEpicResult || { status: "payload_loaded" }, approvedStories, selectedIndex);
        updateEpicActionVisibility(approvedStories);
        setStatus(`Selected all ${approvedStories.length} stories for creation.`, false, "epic");
      } catch (error) {
        setStatus("Stories payload must be valid JSON before selecting all.", true, "epic");
      }
    }

    function renderEpicBreakdownDetails(result, stories, selectedIndex = 0) {
      currentEpicResult = result;
      if (activeFlow !== "epic") {
        return;
      }
      detailMode = "epicStories";
      clearRunPoll();
      const rows = Array.isArray(stories) ? stories : [];
      updateEpicActionVisibility(rows);
      updateWorkflowSteps("epic", rows.length ? "review" : epicWorkflowStepForOperation(result.status || "suggested"));
      els.detailPanelTitle.textContent = "Epic Breakdown Details";
      els.itemSelectLabel.textContent = "Story";
      els.selectedItemHeading.textContent = "Selected Story";
      renderEpicSummaryFields(result, rows);
      renderStoryReviewMeta(rows);
      els.itemSelect.innerHTML = '<option value="">Select a suggested story</option>';
      rows.forEach((story, index) => {
        const option = document.createElement("option");
        option.value = String(index);
        const readiness = coalesce(story.readiness_score, "?");
        const confidence = coalesce(story.confidence, "?");
        const summary = story.summary || story.key || `Story ${index + 1}`;
        const selectedLabel = story.approved ? "selected | " : "";
        option.textContent = `${index + 1}. ${selectedLabel}${summary} | Readiness ${readiness} | Confidence ${confidence}`;
        if (index === selectedIndex) {
          option.selected = true;
        }
        els.itemSelect.appendChild(option);
      });
      if (rows.length > 0) {
        els.itemSelect.value = String(Math.min(selectedIndex, rows.length - 1));
        renderSelectedEpicStory(Number(els.itemSelect.value));
      } else {
        setDetailMetrics([]);
        renderStoryReviewMeta([]);
        renderWarnings(els.itemWarnings, []);
        renderEmptyState(
          els.itemFields,
          "No stories were generated",
          "Click Suggest Stories again after confirming the Epic Key and Project Key. If this repeats, check Debug JSON for the API response.",
        );
        els.itemDetail.textContent = "No stories were generated. Confirm the epic details and run Suggest Stories again.";
      }
      persistEpicState();
    }

    function updateGeneratedPrompt(item) {
      const prompt = getNested(item, ["draft", "payload", "codex_user_input_prompt"], "");
      const generated = prompt || "";
      if (els.generatedPrompt) {
        els.generatedPrompt.value = generated;
      }
    }

    function bindConsoleStatePersistence() {
      [els.scopeType, els.scopeValue, els.requestor, els.runId, els.issueKey].forEach((input) => {
        input.addEventListener("change", persistConsoleState);
      });
    }

    async function apiRequest(path, options = {}) {
      const timeoutMs = Number.isFinite(options.timeoutMs) ? options.timeoutMs : API_TIMEOUT_MS;
      const timeoutMessage = options.timeoutMessage || "Request timed out. Please retry and check server logs if it keeps happening.";
      const fetchOptions = { ...options };
      delete fetchOptions.timeoutMs;
      delete fetchOptions.timeoutMessage;
      const controller = new AbortController();
      const timer = timeoutMs > 0 ? window.setTimeout(() => controller.abort(), timeoutMs) : null;
      try {
        const response = await fetch(path, {
          headers: { "Content-Type": "application/json" },
          ...fetchOptions,
          signal: controller.signal,
        });
        const text = await response.text();
        const payload = text ? JSON.parse(text) : {};
        if (!response.ok) {
          throw new Error(payload.error || payload.message || text || `HTTP ${response.status}`);
        }
        return payload;
      } catch (error) {
        if (error.name === "AbortError") {
          throw new Error(timeoutMessage);
        }
        throw error;
      } finally {
        if (timer) {
          window.clearTimeout(timer);
        }
      }
    }

    function clearRunPoll() {
      if (runPollTimer) {
        window.clearTimeout(runPollTimer);
        runPollTimer = null;
      }
    }

    function scheduleRunPoll() {
      clearRunPoll();
      runPollTimer = window.setTimeout(() => {
        refreshRun({ silent: true });
      }, RUN_POLL_INTERVAL_MS);
    }

    function isBulkRun() {
      return getNested(currentRun, ["scope_type"], "") === "epic";
    }

    function syncBulkMode() {
      const bulk = isBulkRun();
      els.applyAllItems.disabled = false;
      els.applyAllItems.title = bulk
        ? "Select this to apply the action to every Jira in the epic run. Leave it unchecked to target only the selected Jira."
        : "";
    }

    function currentRunContextLabel() {
      const runId = els.runId.value.trim() || getNested(currentRun, ["run_id"], "");
      const issueKey = currentEnrichIssueKey();
      if (!runId) {
        return "No run loaded";
      }
      return `Run ${runId}${issueKey ? ` / Jira ${issueKey}` : ""}`;
    }

    function renderEnrichOperationStatus(operation, elapsedSec = 0) {
      if (activeFlow !== "enrich") {
        return;
      }
      updateWorkflowSteps("enrich", enrichWorkflowStepForOperation(operation));
      setRunDetailMode();
      setDetailContext("Enrich Jira", currentRunContextLabel(), operation);
      setDetailMetrics([
        { label: "Elapsed", value: `${elapsedSec}s` },
        { label: "Run", value: els.runId.value.trim() || "pending", tone: "review" },
      ]);
      renderWarnings(els.runWarnings, []);
      renderFields(els.runFields, [
        { label: "Operation", value: formatStatusLabel(operation) },
        { label: "Elapsed", value: `${elapsedSec}s` },
        { label: "Run ID", value: els.runId.value.trim() || "pending" },
        { label: "Issue Key", value: selectedRunIssueKey || els.issueKey.value.trim() || "" },
      ]);
      els.runSummary.textContent = JSON.stringify({
        flow: "enrich",
        operation,
        elapsed_seconds: elapsedSec,
        run_id: els.runId.value.trim(),
        issue_key: selectedRunIssueKey || els.issueKey.value.trim(),
      }, null, 2);
    }

    function renderEnrichFailureStatus(operation, message) {
      if (activeFlow !== "enrich") {
        return;
      }
      updateWorkflowSteps("enrich", enrichWorkflowStepForOperation(operation), "error");
      setDetailContext("Enrich Jira", currentRunContextLabel(), "failed");
      setDetailMetrics([]);
      renderWarnings(els.runWarnings, []);
      renderFields(els.runFields, [
        { label: "Operation", value: formatStatusLabel(operation) },
        { label: "Status", value: "Failed" },
        { label: "Error", value: message },
      ]);
    }

    function renderRun(run) {
      const runSignature = payloadSignature({
        run_id: run.run_id || "",
        status: run.status || "",
        status_payload: run.status_payload || {},
      });
      currentRun = run;
      syncBulkMode();
      persistConsoleState();
      const statusPayload = run.status_payload || {};
      const items = Array.isArray(statusPayload.items) ? statusPayload.items : [];
      if (activeFlow !== "enrich") {
        return;
      }
      updateWorkflowSteps("enrich", run.status === "running" ? "run" : "review");
      if (run.status === "running") {
        scheduleRunPoll();
      } else {
        clearRunPoll();
      }
      setDetailContext(
        "Enrich Jira",
        run.run_id
          ? `Run ${run.run_id}${selectedRunIssueKey ? ` / Jira ${selectedRunIssueKey}` : ""}`
          : "No run loaded",
        run.status || "loaded",
      );
      setDetailMetrics([
        { label: "Items", value: items.length },
        { label: "Updated", value: run.updated_at || "" },
      ]);
      if (runSignature === lastRenderedRunSignature) {
        return;
      }
      lastRenderedRunSignature = runSignature;
      currentItem = null;
      setRunDetailMode();
      updateEnrichActionVisibility(null);
      maybePromptForConfluenceAuth(statusPayload.warnings || [], refreshRun);
      renderWarnings(els.runWarnings, statusPayload.warnings || []);
      renderFields(els.runFields, [
        { label: "Run ID", value: run.run_id || "" },
        { label: "Status", value: run.status || "" },
        { label: "Item Count", value: items.length },
        { label: "Updated", value: run.updated_at || "" },
      ]);
      els.runSummary.textContent = JSON.stringify(run, null, 2);
      const availableKeys = items.map((item) => item.issue_key);
      const requestedSelection = currentEnrichIssueKey();
      const currentSelection = availableKeys.includes(requestedSelection) ? requestedSelection : "";
      els.itemSelect.innerHTML = '<option value="">Select a Jira from this run</option>';
      items.forEach((item) => {
        const option = document.createElement("option");
        option.value = item.issue_key;
        option.textContent = `${item.issue_key} - ${item.jira_summary || ""}`.trim();
        if (item.issue_key === currentSelection) {
          option.selected = true;
        }
        els.itemSelect.appendChild(option);
      });
      if (currentSelection) {
        els.itemSelect.value = currentSelection;
        els.issueKey.value = currentSelection;
        selectedRunIssueKey = currentSelection;
      } else if (items.length > 0) {
        els.itemSelect.value = items[0].issue_key;
        els.issueKey.value = items[0].issue_key;
        selectedRunIssueKey = items[0].issue_key;
      } else {
        els.issueKey.value = "";
        selectedRunIssueKey = "";
        renderWarnings(els.itemWarnings, []);
        renderEmptyState(
          els.itemFields,
          "No Jira items in this run",
          "Refresh Run to check again. If it stays empty, create a new run with a scope that resolves to at least one Jira.",
        );
        els.itemDetail.textContent = "No Jira items are available. Refresh the run or create a new run with a different scope.";
      }
    }

    async function createRun() {
      startLongOpProgress(
        "Creating run",
        (elapsedSec) => renderEnrichOperationStatus("creating_run", elapsedSec),
        "enrich",
      );
      try {
        currentRun = null;
        currentItem = null;
        updateEnrichActionVisibility(null);
        syncBulkMode();
        const payload = await apiRequest("/api/v1/runs", {
          method: "POST",
          body: JSON.stringify({
            scopeType: els.scopeType.value,
            scopeValue: els.scopeValue.value.trim(),
            requestor: els.requestor.value.trim(),
          }),
        });
        els.runId.value = payload.runId;
        if (payload.items && payload.items.length === 1) {
          els.issueKey.value = payload.items[0];
        }
        persistConsoleState();
        setStatus(`Run created: ${payload.runId}. Processing in background...`, false, "enrich");
        await refreshRun({ silent: true });
        clearLongOpProgress();
      } catch (error) {
        clearLongOpProgress();
        renderEnrichFailureStatus("creating_run", error.message);
        setStatus(error.message, true, "enrich");
      }
    }

    async function refreshRun(options = {}) {
      const runId = els.runId.value.trim();
      if (!runId) {
        if (!options.silent) {
          setStatus("Enter a run id first.", true, "enrich");
        }
        return;
      }
      const trackProgress = !options.silent;
      if (!options.silent) {
        startLongOpProgress(
          `Loading run ${runId}`,
          (elapsedSec) => renderEnrichOperationStatus("loading_run", elapsedSec),
          "enrich",
        );
      }
      try {
        const run = await apiRequest(`/api/v1/runs/${encodeURIComponent(runId)}`);
        renderRun(run);
        if (trackProgress) {
          clearLongOpProgress();
        }
        if (activeFlow === "enrich") {
          if (!options.silent) {
            setStatus(run.status === "running" ? `Run ${runId} is still processing...` : `Loaded run ${runId}.`, false, "enrich");
          } else if (run.status === "running") {
            setStatus(`Run ${runId} is still processing...`, false, "enrich");
          } else if (run.status === "failed") {
            setStatus(`Run ${runId} failed.`, true, "enrich");
          } else {
            setStatus(`Run ${runId} is ready.`, false, "enrich");
          }
        }
        const issueKey = currentEnrichIssueKey();
        if (activeFlow === "enrich" && issueKey) {
          els.issueKey.value = issueKey;
          await loadItem();
        }
      } catch (error) {
        if (trackProgress) {
          clearLongOpProgress();
          renderEnrichFailureStatus("loading_run", error.message);
        }
        clearRunPoll();
        if (!options.silent) {
          setStatus(error.message, true, "enrich");
        }
      }
    }

    async function loadItem() {
      if (activeFlow !== "enrich") {
        return;
      }
      const runId = els.runId.value.trim();
      const issueKey = currentEnrichIssueKey();
      if (!runId || !issueKey) {
        return;
      }
      els.issueKey.value = issueKey;
      selectedRunIssueKey = issueKey;
      if (els.itemSelect.value !== issueKey) {
        const option = Array.from(els.itemSelect.options).find((candidate) => candidate.value === issueKey);
        if (option) {
          els.itemSelect.value = issueKey;
        }
      }
      persistConsoleState();
      try {
        const item = await apiRequest(`/api/v1/runs/${encodeURIComponent(runId)}/items/${encodeURIComponent(issueKey)}`);
        if (activeFlow !== "enrich") {
          return;
        }
        const itemSignature = payloadSignature({
          issue_key: item.issue_key || "",
          status: item.status || "",
          jira_summary: item.jira_summary || "",
          confidence: item.confidence,
          readiness_score: item.readiness_score,
          approval_required: item.approval_required,
          policy_flags: item.policy_flags || [],
          warnings: item.warnings || [],
          draft: item.draft || {},
          rl_prompt_decision: item.rl_prompt_decision || {},
        });
        if (itemSignature === lastRenderedItemSignature) {
          currentItem = item;
          setDetailContext("Enrich Jira", `Run ${runId} / Jira ${issueKey}`, item.status || getNested(currentRun, ["status"], "loaded"));
          setEnrichItemMetrics(item);
          updateEnrichActionVisibility(item);
          return;
        }
        lastRenderedItemSignature = itemSignature;
        currentItem = item;
        updateEnrichActionVisibility(item);
        updateWorkflowSteps("enrich", "review");
        setDetailContext("Enrich Jira", `Run ${runId} / Jira ${issueKey}`, item.status || getNested(currentRun, ["status"], "loaded"));
        setEnrichItemMetrics(item);
        const draftPayload = getNested(item, ["draft", "payload"], {});
        const itemWarnings = item.warnings || draftPayload.warnings || [];
        const rlDecision = item.rl_prompt_decision || {};
        const rlMeta = rlDecision.metadata || {};
        const ranking = Array.isArray(rlMeta.candidate_ranking) ? rlMeta.candidate_ranking : [];
        const betaPoints = Array.isArray(rlMeta.candidate_beta_distribution) ? rlMeta.candidate_beta_distribution : [];
        const trajectory = Array.isArray(rlMeta.confidence_trajectory) ? rlMeta.confidence_trajectory : [];
        const selectedPack = rlMeta.selected_prompt_pack || {};
        const rankingSummary = ranking.map((entry) => `#${entry.rank || ''} ${entry.prompt_id || ''} (${Number(entry.score || 0).toFixed(3)})`).join(" | ");
        const betaSummary = betaPoints.map((point) => `${point.prompt_id || ''}: α=${Number(point.alpha || 1).toFixed(2)}, β=${Number(point.beta || 1).toFixed(2)}, E=${Number(point.expected || 0.5).toFixed(2)}`).join(" | ");
        const betaGraph = betaPoints.map((point) => {
          const expected = Number(point.expected || 0.5);
          const filled = Math.max(1, Math.min(10, Math.round(expected * 10)));
          return `${point.prompt_id || ''} [${"█".repeat(filled)}${"·".repeat(Math.max(0, 10 - filled))}]`;
        }).join(" | ");
        const trajectorySummary = trajectory.map((entry) => `attempt ${entry.attempt ?? "?"}: c=${entry.confidence ?? "?"} lag=${entry.lag ?? "?"}`).join(" | ");
        maybePromptForConfluenceAuth(itemWarnings, loadItem);
        renderWarnings(els.itemWarnings, itemWarnings);
        const contextBrief = draftPayload.context_brief || {};
        const metadataFields = [
          { label: "Issue Key", value: item.issue_key || "" },
          { label: "Jira Summary", value: item.jira_summary || "" },
          { label: "Draft Version", value: getNested(item, ["draft", "draft_version"], "") },
          { label: "Draft Status", value: getNested(item, ["draft", "status"], "") },
          { label: "Problem Statement", value: contextBrief.problem_statement || "" },
          { label: "Acceptance Focus", value: contextBrief.acceptance_focus || "" },
          { label: "Dependency Focus", value: contextBrief.dependency_focus || "" },
          { label: "Edge Case Focus", value: contextBrief.edge_case_focus || "" },
          { label: "Evidence Context", value: contextBrief.evidence_context || "" },
          { label: "Open Questions", value: formatList(draftPayload.fallback_questions) },
          { label: "Guidance Analysis", value: (draftPayload.user_input_details || []).join(" | ") || draftPayload.user_input_summary || "" },
          { label: "RL Prompt Selected", value: rlDecision.prompt_id || "" },
          { label: "RL Prompt Title", value: selectedPack.title || "" },
          { label: "RL Prompt Rationale", value: selectedPack.rationale || "" },
          { label: "RL Candidate Ranking", value: rankingSummary },
          { label: "RL Beta Distribution", value: betaSummary },
          { label: "RL Beta Graph", value: betaGraph },
          { label: "RL Confidence Trajectory", value: trajectorySummary },
        ];
        els.itemFields.innerHTML = `
          <div class="content-stack">
            ${contentBlockHtml("Summary", draftPayload.summary || "", "primary")}
            ${contentBlockHtml("Suggested Description", draftPayload.suggested_description || "")}
            ${contentBlockHtml("Recommended Next Step", draftPayload.recommended_next_step || "")}
          </div>
          <details class="metadata-details">
            <summary>Metadata and prompt learning</summary>
            <div class="metadata-grid">
              ${detailFieldsHtml(metadataFields)}
            </div>
          </details>
        `;
        updateGeneratedPrompt(item);
        els.itemDetail.textContent = JSON.stringify(item, null, 2);
      } catch (error) {
        if (activeFlow !== "enrich") {
          return;
        }
        currentItem = null;
        updateEnrichActionVisibility(null);
        updateWorkflowSteps("enrich", "review", "error");
        setDetailContext("Enrich Jira", `Run ${runId} / Jira ${issueKey}`, "error");
        setDetailMetrics([]);
        renderWarnings(els.itemWarnings, []);
        renderFields(els.itemFields, [
          { label: "Issue Key", value: issueKey },
          { label: "Error", value: error.message },
        ]);
        updateGeneratedPrompt(null);
        els.itemDetail.textContent = error.message;
      }
    }

    async function submitDecision() {
      const runId = els.runId.value.trim();
      const issueKey = els.issueKey.value.trim();
      const applyAll = els.applyAllItems.checked;
      const decision = els.decision.value;
      const selectedInput = decision === "prompt" ? els.promptInput.value : els.userInput.value;
      if (!runId || (!applyAll && !issueKey)) {
        setStatus("Run id is required, and issue key is required unless applying to all Jiras in the run.", true, "enrich");
        return;
      }
      if ((decision === "prompt" || decision === "user_input") && !selectedInput.trim()) {
        setStatus(`Provide ${decision === "prompt" ? "Prompt" : "User Input"} text before submitting ${decision}.`, true, "enrich");
        return;
      }
      const targetLabel = applyAll ? "all Jiras in the run" : issueKey;
      startLongOpProgress(
        `Submitting ${decision} for ${targetLabel}`,
        (elapsedSec) => renderEnrichOperationStatus("submitting_decision", elapsedSec),
        "enrich",
      );
      try {
        await apiRequest(
          applyAll
            ? `/api/v1/runs/${encodeURIComponent(runId)}/approval`
            : `/api/v1/runs/${encodeURIComponent(runId)}/items/${encodeURIComponent(issueKey)}/approval`,
          {
          method: "POST",
          body: JSON.stringify({
            reviewer: els.reviewer.value.trim(),
            decision,
            reviewerNotes: els.reviewerNotes.value,
            userInput: { details: selectedInput },
            promptInput: els.promptInput.value,
          }),
        });
        clearLongOpProgress();
        setStatus(`Decision submitted for ${targetLabel}.`, false, "enrich");
        await refreshRun({ silent: true });
      } catch (error) {
        clearLongOpProgress();
        renderEnrichFailureStatus("submitting_decision", error.message);
        setStatus(error.message, true, "enrich");
      }
    }

    async function writeback() {
      const runId = els.runId.value.trim();
      const issueKey = els.issueKey.value.trim();
      const applyAll = els.applyAllItems.checked;
      if (!runId || (!applyAll && !issueKey)) {
        setStatus("Run id is required, and issue key is required unless applying to all Jiras in the run.", true, "enrich");
        return;
      }
      const targetLabel = applyAll ? "all Jiras in the run" : issueKey;
      startLongOpProgress(
        `Writing back ${targetLabel} to Jira`,
        (elapsedSec) => renderEnrichOperationStatus("writing_back", elapsedSec),
        "enrich",
      );
      try {
        const result = await apiRequest(
          applyAll
            ? `/api/v1/runs/${encodeURIComponent(runId)}/writeback`
            : `/api/v1/runs/${encodeURIComponent(runId)}/items/${encodeURIComponent(issueKey)}/writeback`,
          {
          method: "POST",
          body: JSON.stringify({}),
        });
        clearLongOpProgress();
        setStatus(applyAll ? `Writeback complete for ${targetLabel}.` : `Writeback complete: ${result.status}`, false, "enrich");
        els.itemDetail.textContent = JSON.stringify(result, null, 2);
        await refreshRun({ silent: true });
        updateWorkflowSteps("enrich", "writeback");
      } catch (error) {
        clearLongOpProgress();
        renderEnrichFailureStatus("writing_back", error.message);
        setStatus(error.message, true, "enrich");
      }
    }

    async function replayRun() {
      const runId = els.runId.value.trim();
      if (!runId) {
        setStatus("Run id is required.", true, "enrich");
        return;
      }
      startLongOpProgress(
        `Replaying ${runId}`,
        (elapsedSec) => renderEnrichOperationStatus("replaying_run", elapsedSec),
        "enrich",
      );
      try {
        const result = await apiRequest(`/api/v1/replay/${encodeURIComponent(runId)}`, {
          method: "POST",
          body: JSON.stringify({}),
        });
        clearLongOpProgress();
        els.itemDetail.textContent = JSON.stringify(result, null, 2);
        setStatus(`Replay finished for ${runId}.`, false, "enrich");
      } catch (error) {
        clearLongOpProgress();
        renderEnrichFailureStatus("replaying_run", error.message);
        setStatus(error.message, true, "enrich");
      }
    }

    async function suggestEpicStories() {
      const epicKey = els.epicKey.value.trim();
      const projectKey = els.projectKey.value.trim();
      const requestor = els.epicRequestor.value.trim() || els.requestor.value.trim();
      if (!epicKey || !projectKey) {
        setStatus("Epic key and project key are required.", true, "epic");
        return;
      }
      startLongOpProgress(
        `Generating story suggestions for ${epicKey}`,
        (elapsedSec) => renderEpicOperationStatus("generating_story_suggestions", elapsedSec),
        "epic",
      );
      try {
        const result = await apiRequest(
          `/api/v1/epics/${encodeURIComponent(epicKey)}/stories/suggest`,
          {
            method: "POST",
            body: JSON.stringify({ projectKey, requestor }),
            timeoutMs: 0,
            timeoutMessage: "Epic suggestion request timed out while querying Jira. Please verify live credentials/connectivity and check server logs.",
          },
        );
        const suggested = Array.isArray(result.suggested_stories) ? result.suggested_stories : [];
        const approvalReady = suggested.map((story) => ({ ...story, approved: false }));
        writeStoriesPayload(approvalReady);
        renderEpicBreakdownDetails(result, approvalReady);
        els.promptInput.value = JSON.stringify(result, null, 2);
        clearLongOpProgress();
        setStatus(`Suggested ${suggested.length} stories for ${epicKey}. Select stories in the details panel.`, false, "epic");
      } catch (error) {
        clearLongOpProgress();
        renderEpicFailureStatus(error.message);
        setStatus(error.message, true, "epic");
      }
    }

    async function createEpicStories() {
      const epicKey = els.epicKey.value.trim();
      const projectKey = els.projectKey.value.trim();
      const requestor = els.epicRequestor.value.trim() || els.requestor.value.trim();
      if (!epicKey || !projectKey) {
        setStatus("Epic key and project key are required.", true, "epic");
        return;
      }
      let stories = [];
      try {
        stories = syncEpicDetailsFromPayload();
      } catch (error) {
        setStatus("Stories payload must be valid JSON.", true, "epic");
        return;
      }
      if (!stories.some((story) => Boolean(story.approved))) {
        setStatus("Select at least one story before creating.", true, "epic");
        return;
      }
      startLongOpProgress(
        `Creating approved stories for ${epicKey}`,
        (elapsedSec) => renderEpicOperationStatus("creating_selected_stories", elapsedSec),
        "epic",
      );
      try {
        const result = await apiRequest(
          `/api/v1/epics/${encodeURIComponent(epicKey)}/stories/create`,
          {
            method: "POST",
            body: JSON.stringify({ projectKey, requestor, stories }),
            timeoutMs: 0,
            timeoutMessage: "Epic story creation request timed out while waiting for Jira. Check server logs and retry.",
          },
        );
        els.promptInput.value = JSON.stringify(result, null, 2);
        renderEpicSummaryFields(result, stories);
        if (activeFlow === "epic") {
          renderEpicRawJson(result, stories);
        }
        if (result.status === "already_exists") {
          const existing = Array.isArray(result.existing_stories) ? result.existing_stories : [];
          const payloadStories = existing.map((story) => ({
            key: story.key || "",
            summary: story.summary || "",
            description: story.description || "",
            acceptance_criteria: Array.isArray(story.acceptance_criteria) ? story.acceptance_criteria : [],
            dependencies: Array.isArray(story.dependencies) ? story.dependencies : [],
            edge_cases: Array.isArray(story.edge_cases) ? story.edge_cases : [],
            story_points: story.story_points || 0,
            approved: false,
          }));
          writeStoriesPayload(payloadStories);
          renderEpicBreakdownDetails(result, payloadStories);
          clearLongOpProgress();
          setStatus(`Stories already exist for ${epicKey}. Loaded ${existing.length} existing stories for review.`, false, "epic");
          return;
        }
        clearLongOpProgress();
        if (result.status === "failed" || ((result.created_count || 0) === 0 && (result.skipped_count || 0) > 0)) {
          const failed = Array.isArray(result.skipped) ? result.skipped.find((story) => story.reason === "create_failed") : null;
          const failureMessage = failed && failed.error ? failed.error : `Epic story creation failed. Created=${result.created_count || 0}, skipped=${result.skipped_count || 0}.`;
          renderEpicFailureStatus(failureMessage, stories);
          return;
        }
        renderEpicBreakdownDetails(result, stories);
        setStatus(`Epic story creation complete. Created=${result.created_count || 0}, skipped=${result.skipped_count || 0}.`, false, "epic");
        updateWorkflowSteps("epic", "create");
      } catch (error) {
        clearLongOpProgress();
        renderEpicFailureStatus(error.message, stories);
      }
    }

    function handleItemSelectionChange() {
      if (activeFlow === "epic") {
        renderSelectedEpicStory(Number(els.itemSelect.value));
        return;
      }
      if (activeFlow === "enrich" && els.itemSelect.value) {
        if (!looksLikeJiraKey(els.itemSelect.value)) {
          return;
        }
        els.issueKey.value = els.itemSelect.value;
        loadItem();
      }
    }

    function generatePromptFromSelection() {
      const prompt = (els.generatedPrompt && els.generatedPrompt.value || "").trim();
      if (!prompt) {
        setStatus("No Jira-specific prompt is available yet for the selected item.", true, "enrich");
        return;
      }
      els.promptInput.value = prompt;
      setStatus("Generated Jira-specific prompt and loaded it into Prompt.", false, "enrich");
    }

    function openConfluenceAuth() {
      const url = els.authOpenBtn.dataset.url || CONFLUENCE_BASE_URL;
      if (url) {
        window.open(url, "_blank", "noopener,noreferrer");
      }
    }

    async function retryConfluenceAuth() {
      const retryAction = authModalRetryAction;
      closeAuthModal();
      if (retryAction) {
        await retryAction();
      }
    }

    function initializeUi() {
      window.createRun = createRun;
      window.refreshRun = refreshRun;
      window.submitDecision = submitDecision;
      window.writebackRun = writeback;
      window.replayExistingRun = replayRun;
      window.suggestEpicStories = suggestEpicStories;
      window.selectAllEpicStories = selectAllEpicStories;
      window.createEpicStories = createEpicStories;
      window.handleItemSelectionChange = handleItemSelectionChange;
      window.generatePromptFromSelection = generatePromptFromSelection;
      window.openConfluenceAuth = openConfluenceAuth;
      window.retryConfluenceAuth = retryConfluenceAuth;

      const restoredConsoleFlow = restoreConsoleState();
      const restoredFlow = restoreEpicState();
      bindConsoleStatePersistence();
      els.authOpenBtn.addEventListener("click", openConfluenceAuth);
      els.authRetryBtn.addEventListener("click", retryConfluenceAuth);
      els.authDismissBtn.addEventListener("click", closeAuthModal);
      [els.epicKey, els.projectKey, els.epicRequestor].forEach((input) => {
        input.addEventListener("change", persistEpicState);
      });
      updateEnrichActionVisibility(null);
      updateEpicActionVisibility();
      els.storiesPayload.addEventListener("change", () => {
        try {
          const stories = parseStoriesPayload();
          renderEpicBreakdownDetails(currentEpicResult || { status: "payload_loaded" }, stories);
          updateEpicActionVisibility(stories);
          setStatus("Epic details refreshed from JSON payload.", false, "epic");
        } catch (error) {
          setStatus("Stories payload must be valid JSON.", true, "epic");
        }
      });
      activateTabs("flowTabs");
      activateTabs("runTabs");
      activateTabs("itemTabs");
      if (restoredFlow === "epic" || restoredConsoleFlow === "epic") {
        const epicTab = document.querySelector('#flowTabs .tab-btn[data-target="epicStoryFlowPanel"]');
        if (epicTab) {
          epicTab.click();
        }
      } else if (els.runId.value.trim()) {
        refreshRun({ silent: true });
      }
      window.__jiraEnhancerUiLoaded = true;
    }

    try {
      initializeUi();
    } catch (error) {
      window.__jiraEnhancerUiLoaded = false;
      setStatus(`UI initialization failed: ${error.message}`, true);
    }
  </script>
</body>
</html>
""".replace("__CONFLUENCE_BASE_URL__", safe_confluence_url)\
    .replace("__AUTO_REFRESH_TAG__", auto_refresh_tag)\
    .replace("__SCOPE_ISSUE_SELECTED__", "selected" if scope_type == "issue" else "")\
    .replace("__SCOPE_SPRINT_SELECTED__", "selected" if scope_type == "sprint" else "")\
    .replace("__SCOPE_EPIC_SELECTED__", "selected" if scope_type == "epic" else "")\
    .replace("__SCOPE_RELEASE_SELECTED__", "selected" if scope_type == "release" else "")\
    .replace("__SCOPE_VALUE__", _escape(scope_value))\
    .replace("__REQUESTOR__", _escape(requestor))\
    .replace("__RUN_ID__", _escape(run_id_value))\
    .replace("__ISSUE_KEY__", _escape(selected_issue_key or scope_value))\
    .replace("__STATUS_MESSAGE__", _escape(status_message))\
    .replace("__LAST_RUN_CHIP__", last_run_chip)\
    .replace("__RUN_FIELDS__", _render_run_fields(initial_run))\
    .replace("__ITEM_FIELDS__", _render_item_fields(initial_item))\
    .replace("__RUN_JSON__", _escape(run_json))\
    .replace("__ITEM_JSON__", _escape(item_json))\
    .replace("__ITEM_OPTIONS__", _render_item_options(initial_run, selected_issue_key))\
    .replace("__GENERATED_PROMPT__", _escape(generated_prompt))\
    .replace("__USER_INPUT_VALUE__", _escape(effective_user_input))\
    .replace("__PROMPT_INPUT_VALUE__", _escape(effective_prompt_input))


def render_rl_page(
    *, rl_enabled: bool, policy_name: str, stats: list[dict[str, Any]], decisions: list[dict[str, Any]], storage_root: str = ""
) -> str:
    effective_stats = list(stats)
    sorted_decisions = sorted(decisions, key=lambda row: str(row.get("selected_at", "")), reverse=True)
    if not effective_stats and decisions:
        inferred: dict[str, dict[str, float | str]] = {}
        for decision in sorted_decisions:
            metadata = decision.get("metadata") or {}
            beta_points = metadata.get("candidate_beta_distribution") or []
            for point in beta_points:
                prompt_id = str(point.get("prompt_id", "")).strip()
                if not prompt_id:
                    continue
                bucket = inferred.setdefault(
                    prompt_id,
                    {"prompt_id": prompt_id, "count": 0.0, "alpha": 0.0, "beta": 0.0, "mean_reward": 0.0},
                )
                bucket["count"] = float(bucket.get("count", 0.0)) + 1.0
                bucket["alpha"] = float(bucket.get("alpha", 0.0)) + float(point.get("alpha", 1.0) or 1.0)
                bucket["beta"] = float(bucket.get("beta", 0.0)) + float(point.get("beta", 1.0) or 1.0)
        for prompt_id, bucket in inferred.items():
            seen = max(1.0, float(bucket.get("count", 0.0)))
            alpha = float(bucket.get("alpha", 0.0)) / seen
            beta = float(bucket.get("beta", 0.0)) / seen
            expected = alpha / (alpha + beta) if (alpha + beta) > 0 else 0.5
            effective_stats.append(
                {
                    "prompt_id": prompt_id,
                    "count": int(seen),
                    "mean_reward": round(expected * 2 - 1, 4),
                    "alpha": round(alpha, 4),
                    "beta": round(beta, 4),
                }
            )
        effective_stats.sort(key=lambda row: float(row.get("count", 0)), reverse=True)

    def _expected_probability(row: dict[str, Any]) -> float:
        alpha = float(row.get("alpha", 1) or 1)
        beta = float(row.get("beta", 1) or 1)
        return alpha / (alpha + beta) if (alpha + beta) > 0 else 0.5

    def _beta_graph_cell(row: dict[str, Any]) -> str:
        alpha = float(row.get("alpha", 1) or 1)
        beta = float(row.get("beta", 1) or 1)
        expected = _expected_probability(row)
        variance = (alpha * beta) / (((alpha + beta) ** 2) * (alpha + beta + 1)) if (alpha + beta) > 1 else 0.02
        sigma = max(0.05, math.sqrt(max(variance, 1e-6)))
        svg_w = 180
        svg_h = 56
        pad_x = 10
        baseline = 46
        curve_h = 30

        points: list[str] = []
        peak = 1e-6
        densities: list[tuple[float, float]] = []
        for i in range(60):
            x = i / 59
            density = math.exp(-0.5 * ((x - expected) / sigma) ** 2)
            peak = max(peak, density)
            densities.append((x, density))
        for x, density in densities:
            px = pad_x + x * (svg_w - 2 * pad_x)
            py = baseline - (density / peak) * curve_h
            points.append(f"{px:.2f},{py:.2f}")
        curve_points = " ".join(points)
        mean_x = pad_x + expected * (svg_w - 2 * pad_x)
        return (
            "<div class='beta-cell'>"
            f"<svg width='{svg_w}' height='{svg_h}' viewBox='0 0 {svg_w} {svg_h}' role='img' aria-label='Beta distribution bell curve'>"
            f"<line x1='{pad_x}' y1='{baseline}' x2='{svg_w - pad_x}' y2='{baseline}' stroke='#e4e7ec' stroke-width='1' />"
            f"<polyline points='{curve_points}' fill='none' stroke='#2563eb' stroke-width='2' stroke-linecap='round' />"
            f"<line x1='{mean_x:.2f}' y1='{baseline}' x2='{mean_x:.2f}' y2='{baseline - curve_h - 2}' stroke='#d97706' stroke-width='1.5' stroke-dasharray='3 2' />"
            "</svg>"
            f"<div class='beta-meta'>E[p]={expected:.2f} (alpha={alpha:.1f}, beta={beta:.1f})</div>"
            "</div>"
        )

    def _format_decision_score(value: Any) -> str:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return str(value or "")
        return f"{numeric:.3f}".rstrip("0").rstrip(".")

    def _format_decision_time(value: Any) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return raw
        local_value = parsed.astimezone()
        return local_value.strftime("%b %d, %Y %I:%M %p").replace(" 0", " ")

    decision_rows = "".join(
        (
            f"<tr><td>{_escape(row.get('issue_key', ''))}</td>"
            f"<td>{_escape(row.get('prompt_id', ''))}</td>"
            f"<td title='{_escape(row.get('score', ''))}'>{_escape(_format_decision_score(row.get('score', '')))}</td>"
            f"<td title='{_escape(row.get('selected_at', ''))}'>{_escape(_format_decision_time(row.get('selected_at', '')))}</td>"
            f"<td class='rationale-cell' title='{_escape((row.get('metadata') or {}).get('selected_prompt_pack', {}).get('rationale', ''))}'>"
            f"<span>{_escape((row.get('metadata') or {}).get('selected_prompt_pack', {}).get('rationale', ''))}</span></td></tr>"
        )
        for row in sorted_decisions
    )
    def _expected_reward(row: dict[str, Any]) -> float:
        try:
            return float(row.get("mean_reward"))
        except (TypeError, ValueError):
            alpha = float(row.get("alpha", 1) or 1)
            beta = float(row.get("beta", 1) or 1)
            expected = alpha / (alpha + beta) if (alpha + beta) > 0 else 0.5
            return expected * 2 - 1

    best_prompt = max(effective_stats, key=_expected_reward, default=None)
    best_prompt_label = (
        f"{best_prompt.get('prompt_id', '')} ({_expected_reward(best_prompt):.2f})"
        if best_prompt
        else "No prompt data yet"
    )
    best_prompt_id = str((best_prompt or {}).get("prompt_id", ""))

    def _prompt_status(row: dict[str, Any]) -> str:
        prompt_id = str(row.get("prompt_id", ""))
        count = int(float(row.get("count", 0) or 0))
        if prompt_id and prompt_id == best_prompt_id and count > 0:
            return "Leading"
        if count < 2:
            return "Low confidence"
        return "Exploring"

    prompt_cards = "".join(
        (
            "<article class='prompt-card'>"
            "<div class='prompt-card-main'>"
            f"<div class='prompt-title'>{_escape(row.get('prompt_id', ''))}</div>"
            f"<span class='prompt-status {_escape(_prompt_status(row).lower().replace(' ', '-'))}'>{_escape(_prompt_status(row))}</span>"
            "</div>"
            "<div class='prompt-metrics'>"
            f"<div><span>Usage</span><strong>{_escape(row.get('count', ''))}</strong></div>"
            f"<div><span>Mean reward</span><strong>{_escape(row.get('mean_reward', ''))}</strong></div>"
            f"<div><span>Expected probability</span><strong>{_expected_probability(row):.2f}</strong></div>"
            f"<div><span>Alpha/Beta</span><strong>{_escape(row.get('alpha', ''))} / {_escape(row.get('beta', ''))}</strong></div>"
            "</div>"
            f"{_beta_graph_cell(row)}"
            "</article>"
        )
        for row in effective_stats
    )
    debug_payload = json.dumps(
        {
            "rl_enabled": rl_enabled,
            "policy": policy_name,
            "prompt_stats": effective_stats,
            "recent_decisions": sorted_decisions,
        },
        indent=2,
        default=str,
    )
    has_learning_data = bool(effective_stats or decisions)
    learning_sections = (
        f"""
    <section class='panel'>
      <h2>Prompt Statistics (Beta Distribution)</h2>
      <div class='prompt-card-list' aria-label='Beta Distribution Graph'>
        {prompt_cards}
      </div>
    </section>
    <section class='panel secondary-panel'>
      <h2>Recent Prompt Decisions</h2>
      <div class='table-wrap'>
        <table class='decision-table'>
          <tr><th>Issue</th><th>Prompt Selected</th><th>Score</th><th>Selected At</th><th>Rationale</th></tr>
          {decision_rows or "<tr><td class='empty-row' colspan='5'>No RL decisions yet.</td></tr>"}
        </table>
      </div>
      <details class='debug-view'>
        <summary>Debug JSON</summary>
        <pre>{_escape(debug_payload)}</pre>
      </details>
    </section>"""
        if has_learning_data
        else f"""
    <section class='panel'>
      <div class='empty-card empty-learning'>
        <div class='empty-title'>No RL decisions recorded yet</div>
        <div class='empty-copy'>Run enrichment with RL enabled to populate prompt learning data.</div>
      </div>
      <details class='debug-view'>
        <summary>Debug JSON</summary>
        <pre>{_escape(debug_payload)}</pre>
      </details>
    </section>"""
    )
    return f"""<!doctype html>
<html lang='en'>
<head>
  <meta charset='utf-8'>
  <meta name='viewport' content='width=device-width, initial-scale=1'>
  <title>RL Dashboard</title>
</head>
<body>
  <style>
    :root {{
      --bg: #f7f9fc;
      --line: #e4e7ec;
      --panel: #ffffff;
      --ink: #182230;
      --muted: #667085;
      --accent: #2563eb;
      --accent-2: #d97706;
      --success: #059669;
      --surface: rgba(255, 255, 255, 0.82);
      --surface-soft: rgba(255, 255, 255, 0.58);
      --neutral-line: rgba(102, 112, 133, 0.18);
      --neutral-soft: rgba(102, 112, 133, 0.08);
      --accent-line: rgba(37, 99, 235, 0.22);
      --accent-soft: rgba(37, 99, 235, 0.09);
      --amber-line: rgba(217, 119, 6, 0.28);
      --amber-soft: rgba(217, 119, 6, 0.10);
      --success-line: rgba(5, 150, 105, 0.26);
      --success-soft: rgba(5, 150, 105, 0.09);
      --shadow: 0 10px 24px rgba(16, 24, 40, 0.06);
      --font-xs: 13px;
      --font-sm: 14px;
      --font-md: 16px;
      --font-lg: 17px;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      background: linear-gradient(180deg, #fbfcff 0%, var(--bg) 45%, #eef4fb 100%);
      font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: var(--font-md);
    }}
    .shell {{
      max-width: 1200px;
      margin: 0 auto;
      padding: 10px 20px 32px;
    }}
    .app-nav {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      margin-bottom: 10px;
      padding: 7px 10px;
      border: 1px solid rgba(37, 99, 235, 0.26);
      border-radius: 14px;
      background:
        linear-gradient(120deg, rgba(37, 99, 235, 0.16), rgba(255, 255, 255, 0.90) 38%, rgba(5, 150, 105, 0.08)),
        rgba(255, 255, 255, 0.92);
      box-shadow: 0 14px 34px rgba(15, 23, 42, 0.14);
    }}
    .app-brand {{
      display: flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
      padding: 0 4px 0 0;
      color: var(--ink);
      font-weight: 800;
    }}
    .app-brand-copy {{
      display: grid;
      gap: 2px;
      min-width: 0;
    }}
    .app-brand-copy strong,
    .app-brand-copy span {{
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .app-brand-copy span {{
      color: var(--muted);
      font-size: var(--font-xs);
      font-weight: 600;
    }}
    .app-brand-copy strong {{
      font-size: 18px;
      letter-spacing: 0;
      line-height: 1.1;
    }}
    .app-brand-mark {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 30px;
      height: 30px;
      border-radius: 10px;
      background:
        radial-gradient(circle at 30% 20%, rgba(255, 255, 255, 0.42), transparent 28%),
        linear-gradient(135deg, #1f4fc9, var(--accent) 58%, var(--success));
      color: #fff;
      font-size: var(--font-xs);
      line-height: 1;
      flex-shrink: 0;
      box-shadow: 0 8px 18px rgba(37, 99, 235, 0.30);
    }}
    .app-links {{
      display: inline-flex;
      gap: 3px;
      flex-wrap: wrap;
      justify-content: flex-end;
      align-items: center;
      padding: 3px;
      border: 1px solid rgba(37, 99, 235, 0.18);
      border-radius: 12px;
      background: rgba(232, 240, 252, 0.92);
      box-shadow: inset 0 1px 2px rgba(15, 23, 42, 0.06);
    }}
    .app-link {{
      display: inline-flex;
      align-items: center;
      min-height: 28px;
      border: 1px solid transparent;
      border-radius: 8px;
      padding: 5px 9px;
      color: var(--muted);
      font-size: var(--font-sm);
      font-weight: 700;
      text-decoration: none;
    }}
    .app-link:hover {{
      border-color: var(--accent-line);
      color: var(--accent);
      background: rgba(255, 255, 255, 0.78);
      transform: translateY(-1px);
    }}
    .app-link.active {{
      border-color: rgba(37, 99, 235, 0.42);
      background: var(--accent);
      color: #ffffff;
      box-shadow: 0 8px 18px rgba(37, 99, 235, 0.22);
    }}
    .app-meta {{
      display: flex;
      align-items: center;
      gap: 6px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }}
    .app-chip {{
      max-width: 220px;
      border: 1px solid rgba(190, 203, 220, 0.92);
      border-radius: 999px;
      padding: 4px 8px;
      background: rgba(255, 255, 255, 0.72);
      color: var(--muted);
      font-size: var(--font-xs);
      font-weight: 700;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      box-shadow: 0 1px 2px rgba(15, 23, 42, 0.05);
    }}
    .app-chip.good {{
      border-color: var(--success-line);
      color: var(--success);
      background: var(--success-soft);
    }}
    .app-chip.warn {{
      border-color: var(--amber-line);
      color: var(--accent-2);
      background: var(--amber-soft);
    }}
    @media (max-width: 720px) {{
      .shell {{
        padding: 8px 10px 24px;
      }}
      .app-nav {{
        display: grid;
        grid-template-columns: 1fr auto;
        gap: 6px 8px;
        margin-bottom: 8px;
        padding: 6px 8px;
      }}
      .app-brand-mark {{
        width: 28px;
        height: 28px;
      }}
      .app-brand-copy strong {{
        font-size: 16px;
      }}
      .app-brand-copy span,
      .app-meta {{
        display: none;
      }}
      .app-links {{
        justify-content: flex-end;
        padding: 2px;
      }}
      .app-link {{
        min-height: 26px;
        padding: 4px 7px;
        font-size: 12px;
      }}
    }}
    h2 {{
      margin: 0 0 12px;
      font-size: 25px;
      line-height: 1.15;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 10px;
      box-shadow: var(--shadow);
      padding: 16px;
      margin-bottom: 18px;
    }}
    .secondary-panel {{
      box-shadow: none;
    }}
    .status-strip {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
    }}
    @media (max-width: 960px) {{
      .status-strip {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    @media (max-width: 560px) {{
      .status-strip {{ grid-template-columns: 1fr; }}
    }}
    .status-pill {{
      border: 1px solid var(--accent-line);
      border-radius: 10px;
      padding: 8px 10px;
      background: var(--surface-soft);
      color: var(--muted);
      font-size: var(--font-sm);
      min-width: 0;
    }}
    .status-pill span {{
      display: block;
      margin-bottom: 4px;
    }}
    .status-pill strong {{
      display: block;
      color: var(--ink);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .table-wrap {{
      overflow-x: auto;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: var(--surface-soft);
    }}
    .prompt-card-list {{
      display: grid;
      gap: 10px;
    }}
    .prompt-card {{
      display: grid;
      grid-template-columns: minmax(220px, 1.2fr) minmax(360px, 2fr) minmax(190px, 0.8fr);
      gap: 12px;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: var(--surface-soft);
      padding: 12px;
    }}
    @media (max-width: 920px) {{
      .prompt-card {{ grid-template-columns: 1fr; }}
    }}
    .prompt-card-main {{
      display: grid;
      gap: 8px;
      min-width: 0;
    }}
    .prompt-title {{
      font-weight: 700;
      word-break: break-word;
    }}
    .prompt-status {{
      width: fit-content;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 4px 9px;
      color: var(--muted);
      background: var(--surface);
      font-size: var(--font-xs);
      font-weight: 700;
    }}
    .prompt-status.leading {{
      border-color: var(--success-line);
      color: var(--success);
      background: var(--success-soft);
    }}
    .prompt-status.exploring {{
      border-color: var(--amber-line);
      color: var(--accent-2);
      background: var(--amber-soft);
    }}
    .prompt-status.low-confidence {{
      border-color: var(--neutral-line);
      color: var(--muted);
      background: var(--neutral-soft);
    }}
    .prompt-metrics {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
    }}
    @media (max-width: 700px) {{
      .prompt-metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    .prompt-metrics div {{
      border: 1px solid var(--neutral-line);
      border-radius: 10px;
      padding: 8px 9px;
      background: var(--surface-soft);
      min-width: 0;
    }}
    .prompt-metrics span {{
      display: block;
      margin-bottom: 4px;
      color: var(--muted);
      font-size: var(--font-xs);
    }}
    .prompt-metrics strong {{
      display: block;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-size: var(--font-sm);
    }}
    table {{
      width: 100%;
      min-width: 860px;
      border-collapse: separate;
      border-spacing: 0;
      table-layout: auto;
    }}
    th, td {{
      border-bottom: 1px solid var(--line);
      padding: 9px 10px;
      text-align: left;
      vertical-align: top;
      word-break: break-word;
      overflow-wrap: anywhere;
      font-size: var(--font-sm);
    }}
    th {{
      color: var(--muted);
      background: var(--surface-soft);
      font-size: var(--font-xs);
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    tr:last-child td {{
      border-bottom: 0;
    }}
    .beta-cell {{
      min-width: 190px;
    }}
    .beta-cell svg {{
      display: block;
      width: 100%;
      max-width: 180px;
      height: auto;
    }}
    .beta-meta {{
      font-size: var(--font-xs);
      color: var(--muted);
      margin-top: 4px;
    }}
    .why-cell {{
      max-width: 48ch;
      white-space: normal;
    }}
    .decision-table {{
      min-width: 760px;
    }}
    .decision-table th,
    .decision-table td {{
      padding: 7px 9px;
      font-size: var(--font-sm);
    }}
    .rationale-cell {{
      max-width: 52ch;
    }}
    .rationale-cell span {{
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
      line-height: 1.35;
    }}
    .empty-row {{
      color: var(--muted);
    }}
    .empty-card {{
      border: 1px dashed rgba(102, 112, 133, 0.34);
      border-radius: 10px;
      padding: 16px;
      background: rgba(255, 255, 255, 0.60);
      color: var(--muted);
    }}
    .empty-card .empty-title {{
      color: var(--ink);
      font-size: 19px;
      font-weight: 700;
      margin-bottom: 6px;
    }}
    .empty-card .empty-copy {{
      color: var(--muted);
      font-size: var(--font-sm);
      line-height: 1.45;
    }}
    .empty-learning {{
      margin-bottom: 10px;
    }}
    .debug-view {{
      border: 1px solid var(--neutral-line);
      border-radius: 10px;
      background: var(--surface-soft);
      padding: 10px 12px;
      margin-top: 10px;
    }}
    .debug-view summary {{
      cursor: pointer;
      color: var(--muted);
      font-size: var(--font-sm);
      font-weight: 700;
    }}
    .debug-view pre {{
      max-height: 360px;
      overflow: auto;
      margin: 10px 0 0;
      padding: 10px 12px;
      border: 1px solid var(--neutral-line);
      border-radius: 10px;
      background: var(--surface-soft);
      color: #263442;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: var(--font-xs);
      line-height: 1.35;
      white-space: pre-wrap;
      word-break: break-word;
    }}
  </style>
  <div class='shell'>
    <nav class='app-nav' aria-label='Primary'>
      <div class='app-brand'>
        <span class='app-brand-mark'>AJ</span>
        <span class='app-brand-copy'>
          <strong>Agentic Jira Enhancer</strong>
          <span>Prompt Learning</span>
        </span>
      </div>
      <div class='app-meta' aria-label='Runtime status'>
        <span class='app-chip'>Policy {_escape(policy_name)}</span>
        <span class='app-chip'>Decisions {_escape(len(decisions))}</span>
      </div>
      <div class='app-links'>
        <a class='app-link' href='/ui'>Console</a>
        <a class='app-link active' href='/ui/rl'>Prompt Learning</a>
      </div>
    </nav>
    <section class='panel'>
      <h2>Prompt Learning</h2>
      <div class='status-strip'>
        <div class='status-pill'><span>Policy</span><strong>{_escape(policy_name)}</strong></div>
        <div class='status-pill'><span>Total Prompt Decisions</span><strong>{_escape(len(decisions))}</strong></div>
        <div class='status-pill'><span>Prompt Variants Tracked</span><strong>{_escape(len(effective_stats))}</strong></div>
        <div class='status-pill'><span>Best Current Prompt</span><strong title='{_escape(best_prompt_label)}'>{_escape(best_prompt_label)}</strong></div>
      </div>
    </section>
    {learning_sections}
  </div>
</body>
</html>"""
