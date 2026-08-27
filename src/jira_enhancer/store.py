from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .config import AppConfig
from .utils import read_json, stable_hash, write_json


class FilesystemStore:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._ensure_layout()

    def _ensure_layout(self) -> None:
        for relative in [
            "runs",
            "drafts",
            "approvals",
            "evidence",
            "events/inbox",
            "events/outbox",
            "events/dead-letter",
            "locks",
            "writeback_operations",
            "mock_jira",
        ]:
            (self.config.mvp_root / relative).mkdir(parents=True, exist_ok=True)

    def _path(self, *parts: str) -> Path:
        return self.config.mvp_root.joinpath(*parts)

    def save_run(self, payload: dict[str, Any]) -> None:
        write_json(self._path("runs", payload["run_id"], "run.json"), payload)

    def get_run(self, run_id: str) -> dict[str, Any]:
        return read_json(self._path("runs", run_id, "run.json"))

    def save_status(self, run_id: str, payload: dict[str, Any]) -> None:
        write_json(self._path("runs", run_id, "status.json"), payload)

    def get_status(self, run_id: str) -> dict[str, Any]:
        return read_json(self._path("runs", run_id, "status.json"))

    def save_run_item(self, run_id: str, issue_key: str, payload: dict[str, Any]) -> None:
        write_json(self._path("runs", run_id, "items", f"{issue_key}.json"), payload)

    def get_run_item(self, run_id: str, issue_key: str) -> dict[str, Any]:
        return read_json(self._path("runs", run_id, "items", f"{issue_key}.json"))

    def save_draft(self, run_id: str, issue_key: str, version: int, payload: dict[str, Any]) -> None:
        write_json(self._path("drafts", run_id, issue_key, f"draft.v{version}.json"), payload)

    def get_latest_draft(self, run_id: str, issue_key: str) -> dict[str, Any]:
        draft_dir = self._path("drafts", run_id, issue_key)
        versions = sorted(draft_dir.glob("draft.v*.json"))
        if not versions:
            raise FileNotFoundError(f"No draft exists for run {run_id} / {issue_key}")
        return read_json(versions[-1])

    def save_approval_request(self, run_id: str, issue_key: str, payload: dict[str, Any]) -> None:
        write_json(self._path("approvals", run_id, issue_key, "request.json"), payload)

    def save_approval_decision(self, run_id: str, issue_key: str, payload: dict[str, Any]) -> None:
        write_json(self._path("approvals", run_id, issue_key, "decision.json"), payload)

    def get_approval_decision(self, run_id: str, issue_key: str) -> dict[str, Any]:
        return read_json(self._path("approvals", run_id, issue_key, "decision.json"))

    def save_evidence(self, run_id: str, source_type: str, name: str, payload: dict[str, Any]) -> None:
        write_json(self._path("evidence", run_id, source_type, f"{name}.json"), payload)

    def save_event(self, event_type: str, run_id: str, payload: dict[str, Any], issue_key: str | None = None) -> Path:
        suffix = f"__{issue_key}" if issue_key else ""
        path = self._path("events", "outbox", f"{event_type}__{run_id}{suffix}.json")
        write_json(path, payload)
        return path

    def list_dead_letters(self, run_id: str) -> list[Path]:
        return sorted(self._path("events", "dead-letter").glob(f"*__{run_id}*.json"))

    def save_dead_letter(self, event_file: Path) -> Path:
        target = self._path("events", "dead-letter", event_file.name)
        event_file.replace(target)
        return target

    def create_lock(self, issue_key: str, write_target: str, run_id: str) -> Path:
        lock_name = f"{issue_key}__{write_target}.lock"
        path = self._path("locks", lock_name)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            raise RuntimeError(f"Lock already held for {issue_key} / {write_target}")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"run_id": run_id}, handle, indent=2, sort_keys=True)
        return path

    def release_lock(self, issue_key: str, write_target: str) -> None:
        path = self._path("locks", f"{issue_key}__{write_target}.lock")
        if path.exists():
            path.unlink()

    def save_writeback(self, run_id: str, issue_key: str, payload: dict[str, Any]) -> None:
        write_json(self._path("runs", run_id, f"writeback.{issue_key}.json"), payload)

    def get_writeback_operation(self, idempotency_key: str) -> dict[str, Any] | None:
        path = self._path("writeback_operations", f"{idempotency_key}.json")
        if not path.exists():
            return None
        return read_json(path)

    def reserve_writeback_operation(self, idempotency_key: str, payload: dict[str, Any]) -> bool:
        path = self._path("writeback_operations", f"{idempotency_key}.json")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        return True

    def save_writeback_operation(self, idempotency_key: str, payload: dict[str, Any]) -> None:
        write_json(self._path("writeback_operations", f"{idempotency_key}.json"), payload)

    def save_mock_jira_issue(self, issue_key: str, payload: dict[str, Any]) -> None:
        write_json(self._path("mock_jira", f"{issue_key}.json"), payload)

    def get_mock_jira_issue(self, issue_key: str) -> dict[str, Any] | None:
        path = self._path("mock_jira", f"{issue_key}.json")
        if not path.exists():
            return None
        return read_json(path)

    @staticmethod
    def snapshot_hash(payload: dict[str, Any]) -> str:
        return stable_hash(payload)
