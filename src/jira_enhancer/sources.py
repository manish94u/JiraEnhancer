from __future__ import annotations

import base64
import atexit
import json
import logging
import os
import re
import select
import subprocess
import threading
from importlib import resources
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urlparse
from urllib.request import Request, urlopen

from .config import AppConfig


ISSUE_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")
logger = logging.getLogger(__name__)


def _load_json(filename: str) -> dict[str, Any]:
    text = resources.files("jira_enhancer.data").joinpath(filename).read_text(encoding="utf-8")
    return json.loads(text)


class JiraSource(Protocol):
    def get_issue(self, issue_key: str) -> dict[str, Any]:
        ...

    def resolve_scope(self, scope_type: str, scope_value: str) -> list[str]:
        ...

    def write_description(self, issue_key: str, description: str) -> dict[str, Any]:
        ...

    def create_issue(
        self,
        project_key: str,
        summary: str,
        description: str,
        issue_type: str = "Story",
        additional_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...


class ConfluenceSource(Protocol):
    def get_page(self, page_id: str) -> dict[str, Any]:
        ...


class BitbucketSource(Protocol):
    def get_pr(self, pr_ref: str) -> dict[str, Any]:
        ...


def _default_request_json(method: str, url: str, headers: dict[str, str], payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url=url, method=method, headers=headers, data=body)
    timeout_sec = float(os.getenv("JIRA_ENHANCER_HTTP_TIMEOUT_SEC", "20"))
    try:
        with urlopen(request, timeout=timeout_sec) as response:
            content = response.read()
    except HTTPError as exc:
        error_body = ""
        if exc.fp is not None:
            try:
                error_body = exc.fp.read().decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                error_body = ""
        message = f"{method} {url} failed with {exc.code}"
        if error_body:
            message = f"{message}: {error_body}"
        raise RuntimeError(message) from exc
    except TimeoutError as exc:
        raise RuntimeError(f"{method} {url} timed out after {timeout_sec:.0f}s waiting for response") from exc
    except URLError as exc:
        raise RuntimeError(f"{method} {url} failed: {exc.reason}") from exc
    if not content:
        return {}
    return json.loads(content.decode("utf-8"))


def _basic_auth_header(username: str, token: str) -> str:
    encoded = base64.b64encode(f"{username}:{token}".encode("utf-8")).decode("ascii")
    return f"Basic {encoded}"


def _auth_header(username: str | None, token: str) -> str:
    if username:
        return _basic_auth_header(username, token)
    return f"Bearer {token}"


def _parse_confluence_ref(ref: str) -> tuple[str, str] | None:
    ref = (ref or "").strip()
    if not ref:
        return None
    if ref.isdigit():
        return ("id", ref)
    parsed = urlparse(ref)
    query_page_id = parse_qs(parsed.query).get("pageId")
    if query_page_id:
        return ("id", query_page_id[0])
    match = re.search(r"/pages/(?:viewpage.action\?pageId=)?(\d+)", ref)
    if match:
        return ("id", match.group(1))
    display_match = re.search(r"/display/([^/]+)/([^/?#]+)", parsed.path or ref)
    if display_match:
        space_key, title_slug = display_match.groups()
        title = unquote(title_slug).replace("+", " ")
        return ("display", f"{space_key}|{title}")
    return None


class RestJsonClient:
    def __init__(self, base_url: str, auth_header: str, request_json=_default_request_json) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth_header = auth_header
        self.request_json = request_json

    def get(self, path: str) -> dict[str, Any]:
        return self.request_json("GET", f"{self.base_url}{path}", self._headers())

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request_json("POST", f"{self.base_url}{path}", self._headers(), payload=payload)

    def put(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request_json("PUT", f"{self.base_url}{path}", self._headers(), payload=payload)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self.auth_header,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }


class McpToolCaller(Protocol):
    def __call__(self, command: str, args: list[str], tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        ...


class HttpMcpToolClient:
    def __init__(self, endpoint: str, timeout_seconds: float = 20.0, protocol_version: str = "2025-03-26") -> None:
        self.endpoint = endpoint.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.protocol_version = protocol_version
        self._lock = threading.RLock()
        self._session_id = ""
        self._initialized = False
        self._next_request_id = 1

    def __call__(self, command: str, args: list[str], tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        del command, args
        with self._lock:
            if not self._initialized:
                self._initialize()
            result = self._post_request(
                "tools/call",
                {"name": tool_name, "arguments": arguments},
                expect_result=True,
            )
            return self._extract_tool_payload(result)

    def _initialize(self) -> None:
        self._post_request(
            "initialize",
            {
                "protocolVersion": self.protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "jira_enhancer", "version": "1.0"},
            },
            expect_result=True,
        )
        self._post_notification("notifications/initialized", {})
        self._initialized = True

    def _post_notification(self, method: str, params: dict[str, Any]) -> None:
        request = self._build_request(
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
            }
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                self._capture_session_id(response.headers)
                response.read()
        except HTTPError as exc:
            raise RuntimeError(self._format_http_error(f"MCP notification {method} failed", exc)) from exc
        except URLError as exc:
            raise RuntimeError(f"MCP notification {method} failed: {exc.reason}") from exc

    def _post_request(self, method: str, params: dict[str, Any], expect_result: bool) -> dict[str, Any]:
        request_id = self._allocate_request_id()
        request = self._build_request(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                self._capture_session_id(response.headers)
                content_type = response.headers.get("Content-Type", "")
                body = response.read()
        except HTTPError as exc:
            raise RuntimeError(self._format_http_error(f"MCP request {method} failed", exc)) from exc
        except URLError as exc:
            raise RuntimeError(f"MCP request {method} failed: {exc.reason}") from exc
        payload = self._decode_http_response(body, content_type, request_id)
        if "error" in payload:
            raise RuntimeError(f"MCP request {method} failed: {payload['error']}")
        result = payload.get("result", {})
        if expect_result:
            return result
        return result

    def _build_request(self, payload: dict[str, Any]) -> Request:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": self.protocol_version,
        }
        if self._session_id:
            headers["MCP-Session-Id"] = self._session_id
        return Request(
            url=self.endpoint,
            method="POST",
            headers=headers,
            data=json.dumps(payload).encode("utf-8"),
        )

    def _capture_session_id(self, headers: Any) -> None:
        session_id = headers.get("MCP-Session-Id", "")
        if session_id:
            self._session_id = session_id

    def _decode_http_response(self, body: bytes, content_type: str, request_id: int) -> dict[str, Any]:
        if not body:
            return {}
        text = body.decode("utf-8")
        if "text/event-stream" in content_type.lower():
            return self._parse_sse_response(text, request_id)
        return json.loads(text)

    @staticmethod
    def _parse_sse_response(text: str, request_id: int) -> dict[str, Any]:
        data_lines: list[str] = []
        for raw_line in text.splitlines():
            if raw_line.startswith("data:"):
                data_lines.append(raw_line[len("data:") :].strip())
            elif not raw_line.strip() and data_lines:
                payload = json.loads("\n".join(data_lines))
                if payload.get("id") == request_id:
                    return payload
                data_lines = []
        if data_lines:
            payload = json.loads("\n".join(data_lines))
            if payload.get("id") == request_id:
                return payload
        raise RuntimeError("MCP HTTP response did not contain the expected JSON-RPC result")

    @staticmethod
    def _extract_tool_payload(result: dict[str, Any]) -> dict[str, Any]:
        if result.get("isError"):
            raise RuntimeError(f"MCP tool call failed: {result}")
        for item in result.get("content", []):
            if item.get("type") != "text":
                continue
            text = item.get("text", "").strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
            if isinstance(payload, dict) and payload.get("error"):
                raise RuntimeError(str(payload["error"]))
            return payload if isinstance(payload, dict) else {"value": payload}
        return {}

    @staticmethod
    def _format_http_error(message: str, exc: HTTPError) -> str:
        error_body = ""
        if exc.fp is not None:
            try:
                error_body = exc.fp.read().decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                error_body = ""
        if error_body:
            return f"{message} with {exc.code}: {error_body}"
        return f"{message} with {exc.code}"

    def _allocate_request_id(self) -> int:
        request_id = self._next_request_id
        self._next_request_id += 1
        return request_id


class McpStdioToolClient:
    def __init__(self, startup_timeout_seconds: float = 300.0, request_timeout_seconds: float = 60.0) -> None:
        self.startup_timeout_seconds = startup_timeout_seconds
        self.request_timeout_seconds = request_timeout_seconds
        self._lock = threading.RLock()
        self._process: subprocess.Popen[bytes] | None = None
        self._command: tuple[str, ...] | None = None
        self._next_request_id = 1
        self._initialized = False
        atexit.register(self.close)

    def __call__(self, command: str, args: list[str], tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            process = self._ensure_process(command, args)
            request_id = self._allocate_request_id()
            try:
                result = self._request(
                    process,
                    request_id,
                    "tools/call",
                    {"name": tool_name, "arguments": arguments},
                    timeout_seconds=self.request_timeout_seconds,
                )
                return self._extract_tool_payload(result)
            except Exception:
                self._terminate(self._process)
                self._process = None
                self._command = None
                self._initialized = False
                raise

    def close(self) -> None:
        with self._lock:
            self._terminate(self._process)
            self._process = None
            self._command = None
            self._initialized = False

    def _ensure_process(self, command: str, args: list[str]) -> subprocess.Popen[bytes]:
        command_tuple = tuple([command, *args])
        if self._process is not None and self._process.poll() is None and self._command == command_tuple and self._initialized:
            return self._process
        self._terminate(self._process)
        self._process = subprocess.Popen(
            list(command_tuple),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._command = command_tuple
        self._initialized = False
        self._initialize(self._process)
        self._initialized = True
        return self._process

    def _initialize(self, process: subprocess.Popen[bytes]) -> None:
        request_id = self._allocate_request_id()
        try:
            self._request(
                process,
                request_id,
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "jira_enhancer", "version": "1.0"},
                },
                timeout_seconds=self.startup_timeout_seconds,
            )
        except Exception as exc:
            raise RuntimeError(self._format_process_error(process, f"MCP initialize failed: {exc}")) from exc
        self._notify(process, "notifications/initialized", {})

    def _allocate_request_id(self) -> int:
        request_id = self._next_request_id
        self._next_request_id += 1
        return request_id

    def _request(
        self,
        process: subprocess.Popen[bytes],
        request_id: int,
        method: str,
        params: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        self._send_message(
            process,
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            },
        )
        while True:
            response = self._read_message(process, timeout_seconds, method)
            if response.get("id") == request_id:
                if "error" in response:
                    raise RuntimeError(f"MCP request {method} failed: {response['error']}")
                return response.get("result", {})

    def _notify(self, process: subprocess.Popen[bytes], method: str, params: dict[str, Any]) -> None:
        self._send_message(
            process,
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
            },
        )

    def _send_message(self, process: subprocess.Popen[bytes], payload: dict[str, Any]) -> None:
        if process.stdin is None:
            raise RuntimeError("MCP process stdin is not available")
        body = json.dumps(payload).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        process.stdin.write(header + body)
        process.stdin.flush()

    def _read_message(self, process: subprocess.Popen[bytes], timeout_seconds: float, phase: str) -> dict[str, Any]:
        if process.stdout is None:
            raise RuntimeError("MCP process stdout is not available")
        header = self._read_until(process, process.stdout, b"\r\n\r\n", timeout_seconds, phase)
        content_length = 0
        for line in header.decode("ascii").split("\r\n"):
            if line.lower().startswith("content-length:"):
                content_length = int(line.split(":", 1)[1].strip())
                break
        if content_length <= 0:
            raise RuntimeError(self._format_process_error(process, f"MCP response missing Content-Length header during {phase}"))
        body = self._read_exact(process, process.stdout, content_length, timeout_seconds, phase)
        return json.loads(body.decode("utf-8"))

    def _read_until(self, process: subprocess.Popen[bytes], stream: Any, marker: bytes, timeout_seconds: float, phase: str) -> bytes:
        buffer = bytearray()
        while marker not in buffer:
            buffer.extend(self._read_exact(process, stream, 1, timeout_seconds, phase))
        return bytes(buffer)

    def _read_exact(self, process: subprocess.Popen[bytes], stream: Any, size: int, timeout_seconds: float, phase: str) -> bytes:
        chunks = bytearray()
        while len(chunks) < size:
            ready, _, _ = select.select([stream], [], [], timeout_seconds)
            if not ready:
                raise RuntimeError(self._format_process_error(process, f"Timed out waiting for MCP server response during {phase}"))
            chunk = stream.read(size - len(chunks))
            if not chunk:
                raise RuntimeError(self._format_process_error(process, f"MCP server closed the connection unexpectedly during {phase}"))
            chunks.extend(chunk)
        return bytes(chunks)

    def _format_process_error(self, process: subprocess.Popen[bytes], message: str) -> str:
        details = [message]
        if self._command:
            details.append(f"command={' '.join(self._command)}")
        stderr_tail = self._read_available_stderr(process)
        if stderr_tail:
            details.append(f"stderr={stderr_tail}")
        else:
            details.append("stderr=<empty>")
        return " | ".join(details)

    def _read_available_stderr(self, process: subprocess.Popen[bytes]) -> str:
        if process.stderr is None:
            return ""
        chunks: list[bytes] = []
        while True:
            ready, _, _ = select.select([process.stderr], [], [], 0)
            if not ready:
                break
            chunk = process.stderr.read1(4096)
            if not chunk:
                break
            chunks.append(chunk)
        if not chunks:
            return ""
        text = b"".join(chunks).decode("utf-8", errors="replace").strip()
        if len(text) > 1200:
            text = text[-1200:]
        return " ".join(text.split())

    def _extract_tool_payload(self, result: dict[str, Any]) -> dict[str, Any]:
        if result.get("isError"):
            raise RuntimeError(f"MCP tool call failed: {result}")
        for item in result.get("content", []):
            if item.get("type") != "text":
                continue
            text = item.get("text", "").strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
            if isinstance(payload, dict) and payload.get("error"):
                raise RuntimeError(str(payload["error"]))
            return payload if isinstance(payload, dict) else {"value": payload}
        return {}

    def _terminate(self, process: subprocess.Popen[bytes] | None) -> None:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)


class MockJiraSource:
    def __init__(self) -> None:
        self.issues = _load_json("jira_issues.json")
        for issue in self.issues.values():
            issue.setdefault("_revision", 0)
        self.scopes = _load_json("scopes.json")

    def get_issue(self, issue_key: str) -> dict[str, Any]:
        if issue_key not in self.issues:
            raise KeyError(f"Unknown issue key: {issue_key}")
        return dict(self.issues[issue_key])

    def resolve_scope(self, scope_type: str, scope_value: str) -> list[str]:
        if scope_type == "issue":
            self.get_issue(scope_value)
            return [scope_value]
        mapping_name = f"{scope_type}s"
        mapping = self.scopes.get(mapping_name, {})
        if scope_value not in mapping:
            known_values = ", ".join(sorted(mapping.keys())) if mapping else "<none configured>"
            raise ValueError(f"Unknown {scope_type}: {scope_value}. Known {mapping_name}: {known_values}")
        return list(mapping[scope_value])

    def write_description(self, issue_key: str, description: str) -> dict[str, Any]:
        issue = self.get_issue(issue_key)
        issue["description"] = description
        issue["_revision"] = int(issue.get("_revision", 0)) + 1
        self.issues[issue_key] = issue
        return {"jira_update_id": f"mock-update-{issue_key.lower()}", "issue": issue}

    def create_issue(
        self,
        project_key: str,
        summary: str,
        description: str,
        issue_type: str = "Story",
        additional_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        additional_fields = additional_fields or {}
        prefix = (project_key or "MOCK").upper()
        next_number = 1
        for key in self.issues.keys():
            if key.startswith(f"{prefix}-"):
                try:
                    next_number = max(next_number, int(key.split("-", 1)[1]) + 1)
                except Exception:  # noqa: BLE001
                    continue
        issue_key = f"{prefix}-{next_number}"
        issue = {
            "key": issue_key,
            "summary": summary,
            "description": description,
            "acceptance_criteria": additional_fields.get("acceptance_criteria", []),
            "dependencies": additional_fields.get("dependencies", []),
            "edge_cases": additional_fields.get("edge_cases", []),
            "comments": [],
            "worklogs": [],
            "links": {"confluence_pages": [], "bitbucket_prs": []},
            "epic": additional_fields.get("epic", ""),
            "release": additional_fields.get("release", ""),
            "sprint": additional_fields.get("sprint", ""),
            "story_points": additional_fields.get("story_points", 0),
            "issue_type": issue_type,
            "_revision": 0,
        }
        self.issues[issue_key] = issue
        return {"jira_update_id": f"mock-create-{issue_key.lower()}", "issue": dict(issue)}


class MockConfluenceSource:
    def __init__(self) -> None:
        self.pages = _load_json("confluence_pages.json")

    def get_page(self, page_id: str) -> dict[str, Any]:
        resolved = _parse_confluence_ref(page_id)
        if resolved is None:
            raise KeyError(f"Unknown Confluence page: {page_id}")
        ref_type, value = resolved
        if ref_type == "id":
            if value not in self.pages:
                raise KeyError(f"Unknown Confluence page: {page_id}")
            return dict(self.pages[value])
        _, title = value.split("|", 1)
        normalized = title.strip().lower()
        for page in self.pages.values():
            if str(page.get("title", "")).strip().lower() == normalized:
                return dict(page)
        raise KeyError(f"Unknown Confluence page: {page_id}")


class MockBitbucketSource:
    def __init__(self) -> None:
        self.prs = _load_json("bitbucket_prs.json")

    def get_pr(self, pr_ref: str) -> dict[str, Any]:
        pr_id = pr_ref.split("/")[-1]
        if pr_id not in self.prs:
            raise KeyError(f"Unknown PR id: {pr_ref}")
        payload = dict(self.prs[pr_id])
        payload["ref"] = pr_ref
        return payload


class LiveJiraSource:
    def __init__(self, config: AppConfig, request_json=_default_request_json) -> None:
        if not config.jira_base_url or not config.jira_token:
            raise ValueError("Live Jira mode requires JIRA_BASE_URL and JIRA_TOKEN")
        auth_header = _auth_header(config.jira_username, config.jira_token)
        self.client = RestJsonClient(config.jira_base_url, auth_header, request_json=request_json)
        self.config = config

    def get_issue(self, issue_key: str) -> dict[str, Any]:
        raw_issue = self.client.get(f"/rest/api/2/issue/{quote(issue_key)}")
        remote_links = self.client.get(f"/rest/api/2/issue/{quote(issue_key)}/remotelink")
        return self._normalize_issue(raw_issue, remote_links)

    def resolve_scope(self, scope_type: str, scope_value: str) -> list[str]:
        logger.info("LiveJiraSource.resolve_scope start scope_type=%s scope_value=%s", scope_type, scope_value)
        if scope_type == "issue":
            return [scope_value]
        if scope_type == "sprint":
            payload = self.client.get(f"/rest/agile/1.0/sprint/{quote(scope_value)}/issue")
            return [issue["key"] for issue in payload.get("issues", [])]
        if scope_type == "epic":
            epic_value = scope_value.strip()
            if ISSUE_KEY_PATTERN.fullmatch(epic_value):
                issue_keys = self._search_issue_keys(f'parent = "{epic_value}"')
                if issue_keys:
                    return issue_keys
                return self._search_issue_keys(f'"Epic Link" = "{epic_value}"')
            return self._search_issue_keys(f'{self._format_jql_field(self.config.jira_epic_jql_field)} = "{scope_value}"')
        jql_field = self.config.jira_release_jql_field
        formatted_field = self._format_jql_field(jql_field)
        return self._search_issue_keys(f"{formatted_field} = \"{scope_value}\"")

    def _search_issue_keys(self, jql: str) -> list[str]:
        logger.info("LiveJiraSource._search_issue_keys jql=%s", jql)
        payload = self.client.post(
            "/rest/api/2/search",
            {
                "jql": jql,
                "fields": ["summary"],
                "maxResults": 100,
            },
        )
        return [issue["key"] for issue in payload.get("issues", [])]

    @staticmethod
    def _format_jql_field(field_name: str) -> str:
        if re.fullmatch(r"(?:cf\[\d+\]|customfield_\d+|[A-Za-z_][A-Za-z0-9_]*)", field_name):
            return field_name
        return f'"{field_name}"'

    def write_description(self, issue_key: str, description: str) -> dict[str, Any]:
        self.client.put(f"/rest/api/2/issue/{quote(issue_key)}", {"fields": {"description": description}})
        updated = self.get_issue(issue_key)
        return {"jira_update_id": f"live-update-{issue_key.lower()}", "issue": updated}

    def create_issue(
        self,
        project_key: str,
        summary: str,
        description: str,
        issue_type: str = "Story",
        additional_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        additional_fields = additional_fields or {}
        fields: dict[str, Any] = {
            "project": {"key": project_key},
            "summary": summary,
            "description": description,
            "issuetype": {"name": issue_type},
        }
        epic_key = str(additional_fields.get("epic", "")).strip()
        if epic_key:
            fields["customfield_10014"] = epic_key
        assignee = str(additional_fields.get("assignee", "")).strip()
        if assignee:
            fields["assignee"] = {"name": assignee}
        payload = self.client.post("/rest/api/2/issue", {"fields": fields})
        created_key = str(payload.get("key", "")).strip()
        if created_key and additional_fields.get("story_points") is not None:
            self.client.put(
                f"/rest/api/2/issue/{quote(created_key)}",
                {"fields": {self.config.jira_story_points_field: additional_fields.get("story_points")}},
            )
        created_issue = self.get_issue(created_key) if created_key else {"summary": summary, "description": description}
        return {"jira_update_id": f"live-create-{created_key.lower()}", "issue": created_issue}

    def _normalize_issue(self, raw_issue: dict[str, Any], remote_links: list[dict[str, Any]] | dict[str, Any]) -> dict[str, Any]:
        fields = raw_issue.get("fields", {})
        confluence_pages, bitbucket_prs = self._extract_remote_refs(remote_links if isinstance(remote_links, list) else remote_links.get("values", []))
        return {
            "key": raw_issue["key"],
            "version": raw_issue.get("version", ""),
            "updated": fields.get("updated", ""),
            "summary": fields.get("summary", ""),
            "description": self._extract_text(fields.get("description")),
            "acceptance_criteria": self._coerce_list(fields.get(self.config.jira_acceptance_criteria_field)),
            "dependencies": self._coerce_list(fields.get(self.config.jira_dependencies_field)),
            "edge_cases": self._coerce_list(fields.get(self.config.jira_edge_cases_field)),
            "comments": [self._extract_text(comment.get("body")) for comment in fields.get("comment", {}).get("comments", [])],
            "worklogs": [self._extract_text(worklog.get("comment")) for worklog in fields.get("worklog", {}).get("worklogs", [])],
            "links": {
                "confluence_pages": confluence_pages,
                "bitbucket_prs": bitbucket_prs,
            },
            "epic": self._extract_text(fields.get(self.config.jira_epic_field)),
            "release": self._extract_text(fields.get(self.config.jira_release_field)),
            "sprint": self._extract_sprint(fields.get(self.config.jira_sprint_field)),
        }

    @staticmethod
    def _extract_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            if "content" in value:
                return " ".join(LiveJiraSource._extract_text(item) for item in value["content"])
            if "text" in value:
                return str(value["text"])
        if isinstance(value, list):
            return " ".join(LiveJiraSource._extract_text(item) for item in value)
        return str(value)

    @staticmethod
    def _coerce_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            result = []
            for item in value:
                text = LiveJiraSource._extract_text(item)
                if text:
                    result.append(text)
            return result
        text = LiveJiraSource._extract_text(value)
        if not text:
            return []
        return [part.strip() for part in re.split(r"[\n;]+", text) if part.strip()]

    @staticmethod
    def _extract_sprint(value: Any) -> str:
        if isinstance(value, list) and value:
            return LiveJiraSource._extract_text(value[-1])
        return LiveJiraSource._extract_text(value)

    @staticmethod
    def _extract_remote_refs(remote_links: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
        confluence_pages: list[str] = []
        bitbucket_prs: list[str] = []
        for link in remote_links:
            url = link.get("object", {}).get("url") or link.get("globalId", "")
            if not url:
                continue
            page_id = LiveJiraSource._parse_confluence_page_id(url)
            if page_id:
                confluence_pages.append(page_id)
            pr_ref = LiveJiraSource._parse_bitbucket_pr_ref(url)
            if pr_ref:
                bitbucket_prs.append(pr_ref)
        return confluence_pages, bitbucket_prs

    @staticmethod
    def _parse_confluence_page_id(url: str) -> str | None:
        parsed = urlparse(url)
        query_page_id = parse_qs(parsed.query).get("pageId")
        if query_page_id:
            return query_page_id[0]
        match = re.search(r"/pages/(?:viewpage.action\?pageId=)?(\d+)", url)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def _parse_bitbucket_pr_ref(url: str) -> str | None:
        match = re.search(r"/projects/([^/]+)/repos/([^/]+)/pull-requests/(\d+)", url)
        if not match:
            return None
        project, repo, pr_id = match.groups()
        return f"{project}/{repo}/{pr_id}"


class LiveConfluenceSource:
    def __init__(
        self,
        config: AppConfig,
        request_json=_default_request_json,
        mcp_call_tool: McpToolCaller | None = None,
    ) -> None:
        if not config.confluence_base_url:
            raise ValueError("Live Confluence mode requires a Confluence base URL")
        self.config = config
        self.request_json = request_json
        self.mcp_call_tool = mcp_call_tool or self._default_mcp_client(config)

    @staticmethod
    def _default_mcp_client(config: AppConfig) -> McpToolCaller:
        if config.confluence_mcp_endpoint:
            return HttpMcpToolClient(
                endpoint=config.confluence_mcp_endpoint,
                timeout_seconds=float(config.confluence_mcp_request_timeout_sec),
            )
        return McpStdioToolClient(
            startup_timeout_seconds=float(config.confluence_mcp_startup_timeout_sec),
            request_timeout_seconds=float(config.confluence_mcp_request_timeout_sec),
        )

    def get_page(self, page_id: str) -> dict[str, Any]:
        if not self.config.confluence_mcp_command:
            raise RuntimeError("Live Confluence mode requires a configured Confluence MCP command")
        resolved = _parse_confluence_ref(page_id)
        if resolved is None:
            raise RuntimeError(f"Unsupported Confluence reference: {page_id}")
        ref_type, value = resolved
        arguments: dict[str, Any]
        if ref_type == "id":
            arguments = {"page_id": value, "convert_to_markdown": True, "include_metadata": True}
        else:
            space_key, title = value.split("|", 1)
            arguments = {
                "space_key": space_key,
                "title": title,
                "convert_to_markdown": True,
                "include_metadata": True,
            }
        payload = self.mcp_call_tool(
            self.config.confluence_mcp_command,
            list(self.config.confluence_mcp_args),
            "confluence_get_page",
            arguments,
        )
        metadata = payload.get("metadata", {})
        content = payload.get("content", {})
        if isinstance(metadata, dict) and metadata.get("id"):
            page_value = content.get("value", "") if isinstance(content, dict) else ""
            return {
                "id": metadata.get("id", page_id),
                "title": metadata.get("title", ""),
                "summary": str(page_value)[:1000],
                "version": metadata.get("version"),
                "url": metadata.get("url", ""),
            }
        if payload.get("id") or payload.get("title"):
            return {
                "id": payload.get("id", page_id),
                "title": payload.get("title", ""),
                "summary": str(payload.get("summary", payload.get("text", "")))[:1000],
                "version": payload.get("version"),
                "url": payload.get("url", ""),
            }
        raise RuntimeError(f"Confluence MCP tool returned an unexpected payload for {page_id}")

    def close(self) -> None:
        closer = getattr(self.mcp_call_tool, "close", None)
        if callable(closer):
            closer()


class CodexBrokerConfluenceSource:
    def __init__(self, config: AppConfig, request_json=_default_request_json) -> None:
        if not config.codex_broker_base_url:
            raise ValueError("Codex broker mode requires CODEX_BROKER_BASE_URL")
        self.base_url = config.codex_broker_base_url.rstrip("/")
        self.request_json = request_json

    def get_page(self, page_id: str) -> dict[str, Any]:
        return self.request_json(
            "POST",
            f"{self.base_url}/api/v1/confluence/get-page",
            {
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            payload={"pageRef": page_id},
        )


class LiveBitbucketSource:
    def __init__(self, config: AppConfig, request_json=_default_request_json) -> None:
        if not config.bitbucket_base_url or not config.bitbucket_token:
            raise ValueError("Live Bitbucket mode requires BITBUCKET_BASE_URL and BITBUCKET_TOKEN")
        auth_header = _auth_header(config.bitbucket_username, config.bitbucket_token)
        self.client = RestJsonClient(config.bitbucket_base_url, auth_header, request_json=request_json)

    def get_pr(self, pr_ref: str) -> dict[str, Any]:
        project, repo, pr_id = pr_ref.split("/", 2)
        pr_payload = self.client.get(f"/rest/api/1.0/projects/{quote(project)}/repos/{quote(repo)}/pull-requests/{quote(pr_id)}")
        changes_payload = self.client.get(
            f"/rest/api/1.0/projects/{quote(project)}/repos/{quote(repo)}/pull-requests/{quote(pr_id)}/changes?limit=100"
        )
        return {
            "id": pr_payload.get("id", pr_id),
            "title": pr_payload.get("title", ""),
            "repository": repo,
            "project": project,
            "branch": pr_payload.get("fromRef", {}).get("displayId", ""),
            "changed_files": [change.get("path", {}).get("toString", "") for change in changes_payload.get("values", [])],
            "reviewers": [reviewer.get("user", {}).get("name", "") for reviewer in pr_payload.get("reviewers", [])],
            "ref": pr_ref,
        }


def build_sources(config: AppConfig, request_json=_default_request_json) -> tuple[JiraSource, ConfluenceSource, BitbucketSource]:
    if config.connector_mode == "live":
        confluence: ConfluenceSource
        if config.codex_broker_base_url and not config.use_codex_exec:
            confluence = CodexBrokerConfluenceSource(config, request_json=request_json)
        else:
            confluence = LiveConfluenceSource(config, request_json=request_json)
        return (
            LiveJiraSource(config, request_json=request_json),
            confluence,
            LiveBitbucketSource(config, request_json=request_json),
        )
    return MockJiraSource(), MockConfluenceSource(), MockBitbucketSource()
