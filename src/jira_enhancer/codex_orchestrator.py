from __future__ import annotations

import json
import re
from dataclasses import replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .config import AppConfig
from .models import EvidenceRecord
from .services import CodexExecSemanticReasoner, HeuristicSemanticReasoner, ModelSemanticReasoner
from .sources import build_sources


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class CodexOrchestratorBackend:
    def __init__(self, storage_root: str | Path) -> None:
        config = AppConfig.from_runtime(Path(storage_root))
        self.config = replace(config, codex_broker_base_url="")
        _, self.confluence, _ = build_sources(self.config)
        self.reasoner = HeuristicSemanticReasoner()
        if self.config.use_codex_exec:
            self.reasoner = CodexExecSemanticReasoner(
                codex_command=self.config.codex_exec_command,
                timeout_sec=self.config.codex_exec_timeout_sec,
                workdir=str(Path(storage_root)),
                fallback=self.reasoner,
            )
        elif self.config.reasoning_mode == "model" and self.config.llm_base_url and self.config.llm_api_key:
            self.reasoner = ModelSemanticReasoner(
                base_url=self.config.llm_base_url,
                api_key=self.config.llm_api_key,
                model=self.config.llm_model,
                fallback=self.reasoner,
            )

    def get_confluence_page(self, page_ref: str) -> dict[str, Any]:
        return self.confluence.get_page(page_ref)

    def analyze_issue(self, issue: dict[str, Any], evidence_payload: list[dict[str, Any]], user_input_text: str = "") -> dict[str, Any]:
        evidence = [
            EvidenceRecord(
                run_id="codex-orchestrator",
                issue_key=issue.get("issue_key", ""),
                source_type=item.get("source_type", ""),
                source_ref=item.get("source_ref", ""),
                relevance_score=float(item.get("relevance_score", 1.0)),
                selected_for_draft=bool(item.get("selected_for_draft", True)),
                content={
                    "title": item.get("title", ""),
                    "summary": item.get("summary", ""),
                    "repository": item.get("repository", ""),
                    "branch": item.get("branch", ""),
                    "changed_files": item.get("changed_files", []),
                },
            )
            for item in evidence_payload
        ]
        return self.reasoner.analyze_issue(issue, evidence, user_input_text)


def make_codex_orchestrator_handler(storage_root: str):
    backend = CodexOrchestratorBackend(storage_root)

    class RequestHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path in {"/healthz", "/api/v1/healthz"}:
                return _json_response(self, HTTPStatus.OK, {"status": "ok"})
            return _json_response(self, HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            try:
                length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(length) if length else b"{}"
                payload = json.loads(raw_body.decode("utf-8"))
                if self.path == "/api/v1/confluence/get-page":
                    return _json_response(self, HTTPStatus.OK, backend.get_confluence_page(payload["pageRef"]))
                if self.path == "/api/v1/analysis/analyze-issue":
                    return _json_response(
                        self,
                        HTTPStatus.OK,
                        backend.analyze_issue(
                            payload["issue"],
                            payload.get("evidence", []),
                            payload.get("user_input_text", ""),
                        ),
                    )
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "not_found"})
            except Exception as exc:  # noqa: BLE001
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(exc)})

        def log_message(self, fmt: str, *args: object) -> None:
            return None

    return RequestHandler


def serve_codex_orchestrator(storage_root: str, host: str = "127.0.0.1", port: int = 8090) -> None:
    server = ThreadingHTTPServer((host, port), make_codex_orchestrator_handler(storage_root))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
