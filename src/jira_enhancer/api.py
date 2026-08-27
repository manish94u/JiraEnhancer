from __future__ import annotations

import json
import logging
import re
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .bootstrap import build_orchestrator
from .codex_orchestrator import CodexOrchestratorBackend
from .ui import render_index_page, render_rl_page


_CLIENT_DISCONNECT_ERRORS = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)
logger = logging.getLogger(__name__)


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, indent=2).encode("utf-8")
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        handler.send_header("Pragma", "no-cache")
        handler.send_header("Expires", "0")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
    except _CLIENT_DISCONNECT_ERRORS:
        return


def _html_response(handler: BaseHTTPRequestHandler, status: int, body: str) -> None:
    encoded = body.encode("utf-8")
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        handler.send_header("Pragma", "no-cache")
        handler.send_header("Expires", "0")
        handler.send_header("Content-Length", str(len(encoded)))
        handler.end_headers()
        handler.wfile.write(encoded)
    except _CLIENT_DISCONNECT_ERRORS:
        return


def make_handler(storage_root: str, broker_base_url: str | None = None):
    orchestrator = build_orchestrator(storage_root, broker_base_url=broker_base_url)
    confluence_base_url = orchestrator.store.config.confluence_base_url or ""
    broker_backend = CodexOrchestratorBackend(storage_root)

    def _load_rl_dashboard_payload() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        rl_root = orchestrator.store.config.mvp_root / "rl"
        rl_data_root = rl_root / "data"
        stats: list[dict[str, Any]] = []
        decisions: list[dict[str, Any]] = []
        state_path = rl_data_root / "bandit_state.json"
        if not state_path.exists():
            state_path = rl_root / "bandit_state.json"
        if state_path.exists():
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            prompt_stats = dict(payload.get("prompt_stats", {}))
            for prompt_id, row in prompt_stats.items():
                stats.append(
                    {
                        "prompt_id": prompt_id,
                        "count": row.get("count", 0),
                        "mean_reward": row.get("mean_reward", 0),
                        "alpha": row.get("alpha", 1),
                        "beta": row.get("beta", 1),
                    }
                )
            stats.sort(key=lambda row: float(row.get("count", 0)), reverse=True)
        decisions_dir = rl_data_root / "decisions"
        if not decisions_dir.exists():
            decisions_dir = rl_root / "decisions"
        if decisions_dir.exists():
            for path in sorted(decisions_dir.glob("*.json"), reverse=True)[:50]:
                try:
                    decisions.append(json.loads(path.read_text(encoding="utf-8")))
                except Exception:  # noqa: BLE001
                    continue
        return stats, decisions

    def _render_ui_page(
        *,
        run_id: str = "",
        issue_key: str = "",
        status_message: str = "Ready.",
        scope_type: str = "issue",
        scope_value: str = "DEMO-18324",
        requestor: str = "reviewer@example.org",
        user_input_value: str = "",
        prompt_input_value: str = "",
    ) -> str:
        initial_run: dict[str, Any] | None = None
        initial_item: dict[str, Any] | None = None
        resolved_scope_type = scope_type
        resolved_scope_value = scope_value
        resolved_requestor = requestor
        if run_id:
            initial_run = orchestrator.get_run(run_id)
            resolved_scope_type = str(initial_run.get("scope_type") or resolved_scope_type)
            resolved_scope_value = str(initial_run.get("scope_value") or resolved_scope_value)
            resolved_requestor = str(initial_run.get("requestor") or resolved_requestor)
            if not issue_key:
                status_items = list((initial_run.get("status_payload") or {}).get("items", []))
                if status_items:
                    issue_key = str(status_items[0].get("issue_key", ""))
            if issue_key:
                initial_item = orchestrator.get_run_item(run_id, issue_key)
        rl_stats, rl_decisions = _load_rl_dashboard_payload()
        return render_index_page(
            confluence_base_url=confluence_base_url,
            initial_run=initial_run,
            initial_item=initial_item,
            status_message=status_message,
            scope_type=resolved_scope_type,
            scope_value=resolved_scope_value,
            requestor=resolved_requestor,
            user_input_value=user_input_value,
            prompt_input_value=prompt_input_value,
            storage_root=storage_root,
            rl_enabled=bool(orchestrator.store.config.rl_enabled),
            rl_policy=str(orchestrator.store.config.rl_policy),
            rl_stats=rl_stats,
            rl_decisions=rl_decisions,
        )

    class RequestHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            try:
                parsed = urlparse(self.path)
                logger.info("HTTP GET %s", parsed.path)
                if parsed.path in {"/", "/ui"}:
                    query = parse_qs(parsed.query)
                    run_id = query.get("runId", [""])[0]
                    issue_key = query.get("issueKey", [""])[0]
                    scope_type = query.get("scopeType", ["issue"])[0]
                    scope_value = query.get("scopeValue", ["DEMO-18324"])[0]
                    requestor = query.get("requestor", ["reviewer@example.org"])[0]
                    status_message = query.get("status", ["Ready."])[0]
                    return _html_response(
                        self,
                        HTTPStatus.OK,
                        _render_ui_page(
                            run_id=run_id,
                            issue_key=issue_key,
                            status_message=status_message,
                            scope_type=scope_type,
                            scope_value=scope_value,
                            requestor=requestor,
                        ),
                    )
                if parsed.path == "/ui/rl":
                    stats, decisions = _load_rl_dashboard_payload()
                    return _html_response(
                        self,
                        HTTPStatus.OK,
                        render_rl_page(
                            rl_enabled=bool(orchestrator.store.config.rl_enabled),
                            policy_name=str(orchestrator.store.config.rl_policy),
                            stats=stats,
                            decisions=decisions,
                            storage_root=storage_root,
                        ),
                    )
                if re.fullmatch(r"/api/v1/runs/[^/]+", parsed.path):
                    run_id = parsed.path.rsplit("/", 1)[-1]
                    return _json_response(self, HTTPStatus.OK, orchestrator.get_run(run_id))
                match = re.fullmatch(r"/api/v1/runs/([^/]+)/items/([^/]+)", parsed.path)
                if match:
                    run_id, issue_key = match.groups()
                    return _json_response(self, HTTPStatus.OK, orchestrator.get_run_item(run_id, issue_key))
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "not_found"})
            except Exception as exc:  # noqa: BLE001
                logger.exception("GET failed for path=%s", self.path)
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(exc)})

        def do_POST(self) -> None:  # noqa: N802
            try:
                parsed = urlparse(self.path)
                logger.info("HTTP POST %s", parsed.path)
                length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(length) if length else b"{}"
                if parsed.path == "/ui/create-run":
                    form = parse_qs(raw_body.decode("utf-8"))
                    scope_type = form.get("scopeType", ["issue"])[0]
                    scope_value = form.get("scopeValue", [""])[0]
                    requestor = form.get("requestor", [""])[0]
                    logger.info(
                        "UI create-run requested scope_type=%s scope_value=%s requestor=%s",
                        scope_type,
                        scope_value,
                        requestor,
                    )
                    result = orchestrator.start_run(scope_type, scope_value, requestor)
                    logger.info(
                        "Run created run_id=%s item_count=%s; starting background processing",
                        result.get("runId", ""),
                        len(result.get("items", [])),
                    )
                    threading.Thread(
                        target=_run_in_background,
                        args=(orchestrator, result["runId"]),
                        daemon=True,
                    ).start()
                    first_issue_key = result["items"][0] if result.get("items") else ""
                    return _html_response(
                        self,
                        HTTPStatus.OK,
                        _render_ui_page(
                            run_id=result["runId"],
                            issue_key=first_issue_key,
                            status_message=f"Run created: {result['runId']}. Processing in background...",
                            scope_type=scope_type,
                            scope_value=scope_value,
                            requestor=requestor,
                            user_input_value="",
                            prompt_input_value="",
                        ),
                    )
                if parsed.path == "/ui/decision":
                    form = parse_qs(raw_body.decode("utf-8"))
                    run_id = form.get("runId", [""])[0]
                    issue_key = form.get("issueKey", [""])[0]
                    reviewer = form.get("reviewer", [""])[0]
                    decision = form.get("decision", ["approve"])[0]
                    reviewer_notes = form.get("reviewerNotes", [""])[0]
                    user_input_text = form.get("userInput", [""])[0]
                    prompt_input_text = form.get("promptInput", [""])[0]
                    apply_all = form.get("applyAllItems", [""])[0].lower() in {"on", "true", "1", "yes"}
                    selected_input_text = prompt_input_text if decision == "prompt" else user_input_text
                    user_input = {"details": selected_input_text} if selected_input_text.strip() else None
                    if apply_all:
                        orchestrator.record_approval_for_run(
                            run_id,
                            reviewer=reviewer,
                            decision=decision,
                            reviewer_notes=reviewer_notes,
                            user_input=user_input,
                        )
                        first_issue_key = issue_key
                        if not first_issue_key:
                            run = orchestrator.get_run(run_id)
                            status_items = list((run.get("status_payload") or {}).get("items", []))
                            if status_items:
                                first_issue_key = str(status_items[0].get("issue_key", ""))
                        return _html_response(
                            self,
                            HTTPStatus.OK,
                            _render_ui_page(
                                run_id=run_id,
                                issue_key=first_issue_key,
                                status_message=f"Decision submitted for all Jiras in run: {decision}.",
                                user_input_value=user_input_text,
                                prompt_input_value=prompt_input_text,
                            ),
                        )
                    orchestrator.record_approval(
                        run_id,
                        issue_key,
                        reviewer=reviewer,
                        decision=decision,
                        reviewer_notes=reviewer_notes,
                        user_input=user_input,
                    )
                    return _html_response(
                        self,
                        HTTPStatus.OK,
                        _render_ui_page(
                            run_id=run_id,
                            issue_key=issue_key,
                            status_message=f"Decision submitted for {issue_key}: {decision}.",
                            user_input_value=user_input_text,
                            prompt_input_value=prompt_input_text,
                        ),
                    )
                if parsed.path == "/ui/writeback":
                    form = parse_qs(raw_body.decode("utf-8"))
                    run_id = form.get("runId", [""])[0]
                    issue_key = form.get("issueKey", [""])[0]
                    user_input_text = form.get("userInput", [""])[0]
                    prompt_input_text = form.get("promptInput", [""])[0]
                    apply_all = form.get("applyAllItems", [""])[0].lower() in {"on", "true", "1", "yes"}
                    if apply_all:
                        orchestrator.writeback_run(run_id)
                        first_issue_key = issue_key
                        if not first_issue_key:
                            run = orchestrator.get_run(run_id)
                            status_items = list((run.get("status_payload") or {}).get("items", []))
                            if status_items:
                                first_issue_key = str(status_items[0].get("issue_key", ""))
                        return _html_response(
                            self,
                            HTTPStatus.OK,
                            _render_ui_page(
                                run_id=run_id,
                                issue_key=first_issue_key,
                                status_message="Writeback complete for all Jiras in run.",
                                user_input_value=user_input_text,
                                prompt_input_value=prompt_input_text,
                            ),
                        )
                    result = orchestrator.writeback(run_id, issue_key)
                    return _html_response(
                        self,
                        HTTPStatus.OK,
                        _render_ui_page(
                            run_id=run_id,
                            issue_key=issue_key,
                            status_message=f"Writeback complete: {result['status']}.",
                            user_input_value=user_input_text,
                            prompt_input_value=prompt_input_text,
                        ),
                    )
                if parsed.path == "/ui/epic-stories/suggest":
                    form = parse_qs(raw_body.decode("utf-8"))
                    epic_key = form.get("epicKey", [""])[0]
                    project_key = form.get("projectKey", [""])[0]
                    requestor = form.get("requestor", [""])[0]
                    result = orchestrator.suggest_epic_stories(epic_key, project_key, requestor)
                    return _html_response(
                        self,
                        HTTPStatus.OK,
                        _render_ui_page(
                            scope_type="epic",
                            scope_value=epic_key,
                            requestor=requestor,
                            status_message=f"Story suggestions generated for {epic_key}.",
                            prompt_input_value=json.dumps(result, indent=2),
                        ),
                    )
                if parsed.path == "/ui/epic-stories/create":
                    form = parse_qs(raw_body.decode("utf-8"))
                    epic_key = form.get("epicKey", [""])[0]
                    project_key = form.get("projectKey", [""])[0]
                    requestor = form.get("requestor", [""])[0]
                    stories_payload = form.get("storiesPayload", ["[]"])[0]
                    stories = json.loads(stories_payload)
                    result = orchestrator.create_epic_stories_on_approval(epic_key, project_key, requestor, stories)
                    return _html_response(
                        self,
                        HTTPStatus.OK,
                        _render_ui_page(
                            scope_type="epic",
                            scope_value=epic_key,
                            requestor=requestor,
                            status_message=f"Epic stories created for {epic_key}: {result.get('created_count', 0)}.",
                            prompt_input_value=json.dumps(result, indent=2),
                        ),
                    )

                payload = json.loads(raw_body.decode("utf-8"))
                if parsed.path == "/api/v1/runs":
                    logger.info(
                        "API create-run requested scope_type=%s scope_value=%s requestor=%s",
                        payload.get("scopeType", ""),
                        payload.get("scopeValue", ""),
                        payload.get("requestor", ""),
                    )
                    result = orchestrator.start_run(payload["scopeType"], payload["scopeValue"], payload["requestor"])
                    logger.info(
                        "Run created run_id=%s item_count=%s; starting background processing",
                        result.get("runId", ""),
                        len(result.get("items", [])),
                    )
                    threading.Thread(
                        target=_run_in_background,
                        args=(orchestrator, result["runId"]),
                        daemon=True,
                    ).start()
                    return _json_response(self, HTTPStatus.ACCEPTED, result)
                if parsed.path == "/api/v1/confluence/get-page":
                    result = broker_backend.get_confluence_page(payload["pageRef"])
                    return _json_response(self, HTTPStatus.OK, result)
                if parsed.path == "/api/v1/analysis/analyze-issue":
                    result = broker_backend.analyze_issue(
                        payload["issue"],
                        payload.get("evidence", []),
                        payload.get("user_input_text", ""),
                    )
                    return _json_response(self, HTTPStatus.OK, result)
                match = re.fullmatch(r"/api/v1/epics/([^/]+)/stories/suggest", parsed.path)
                if match:
                    epic_key = match.group(1)
                    started = time.perf_counter()
                    result = orchestrator.suggest_epic_stories(
                        epic_key,
                        project_key=payload.get("projectKey", ""),
                        requestor=payload.get("requestor", ""),
                    )
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    logger.info(
                        "HTTP POST %s completed epic_key=%s suggested_count=%s elapsed_ms=%s",
                        parsed.path,
                        epic_key,
                        len(result.get("suggested_stories", [])),
                        elapsed_ms,
                    )
                    return _json_response(self, HTTPStatus.OK, result)
                match = re.fullmatch(r"/api/v1/epics/([^/]+)/stories/create", parsed.path)
                if match:
                    epic_key = match.group(1)
                    started = time.perf_counter()
                    result = orchestrator.create_epic_stories_on_approval(
                        epic_key,
                        project_key=payload.get("projectKey", ""),
                        requestor=payload.get("requestor", ""),
                        stories=list(payload.get("stories", [])),
                    )
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    logger.info(
                        "HTTP POST %s completed epic_key=%s status=%s created_count=%s elapsed_ms=%s",
                        parsed.path,
                        epic_key,
                        result.get("status", ""),
                        result.get("created_count", 0),
                        elapsed_ms,
                    )
                    return _json_response(self, HTTPStatus.OK, result)
                match = re.fullmatch(r"/api/v1/runs/([^/]+)/approval", parsed.path)
                if match:
                    run_id = match.group(1)
                    result = orchestrator.record_approval_for_run(
                        run_id,
                        reviewer=payload["reviewer"],
                        decision=payload["decision"],
                        reviewer_notes=payload.get("reviewerNotes", ""),
                        user_input=payload.get("userInput"),
                    )
                    return _json_response(self, HTTPStatus.OK, result)
                match = re.fullmatch(r"/api/v1/runs/([^/]+)/items/([^/]+)/approval", parsed.path)
                if match:
                    run_id, issue_key = match.groups()
                    result = orchestrator.record_approval(
                        run_id,
                        issue_key,
                        reviewer=payload["reviewer"],
                        decision=payload["decision"],
                        reviewer_notes=payload.get("reviewerNotes", ""),
                        user_input=payload.get("userInput"),
                    )
                    return _json_response(self, HTTPStatus.OK, result)
                match = re.fullmatch(r"/api/v1/runs/([^/]+)/items/([^/]+)/interaction-answer", parsed.path)
                if match:
                    run_id, issue_key = match.groups()
                    result = orchestrator.submit_interaction_answer(
                        run_id,
                        issue_key,
                        reviewer=payload.get("reviewer", ""),
                        answer=payload.get("answer", ""),
                    )
                    return _json_response(self, HTTPStatus.OK, result)
                match = re.fullmatch(r"/api/v1/runs/([^/]+)/writeback", parsed.path)
                if match:
                    run_id = match.group(1)
                    return _json_response(self, HTTPStatus.OK, orchestrator.writeback_run(run_id))
                match = re.fullmatch(r"/api/v1/runs/([^/]+)/items/([^/]+)/writeback", parsed.path)
                if match:
                    run_id, issue_key = match.groups()
                    return _json_response(self, HTTPStatus.OK, orchestrator.writeback(run_id, issue_key))
                match = re.fullmatch(r"/api/v1/replay/([^/]+)", parsed.path)
                if match:
                    run_id = match.group(1)
                    return _json_response(self, HTTPStatus.OK, orchestrator.replay(run_id))
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "not_found"})
            except Exception as exc:  # noqa: BLE001
                logger.exception("POST failed for path=%s", self.path)
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(exc)})

        def log_message(self, fmt: str, *args: object) -> None:
            return None

    return RequestHandler


def _run_in_background(orchestrator, run_id: str) -> None:
    try:
        logger.info("Background run started run_id=%s", run_id)
        orchestrator.continue_run(run_id)
        logger.info("Background run finished run_id=%s", run_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Background run failed run_id=%s", run_id)
        orchestrator.fail_run(run_id, str(exc))


def serve(storage_root: str, host: str = "127.0.0.1", port: int = 8080) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    broker_host = "127.0.0.1" if host == "0.0.0.0" else host
    broker_base_url = f"http://{broker_host}:{port}"
    logger.info("Starting Jira Enhancer server host=%s port=%s storage_root=%s", host, port, storage_root)
    server = ThreadingHTTPServer((host, port), make_handler(storage_root, broker_base_url=broker_base_url))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
