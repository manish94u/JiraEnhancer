from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from jira_enhancer.config import AppConfig
from jira_enhancer.store import FilesystemStore
from jira_enhancer.utils import read_json, write_json


class FilesystemStoreTest(unittest.TestCase):
    def test_store_creates_expected_layout_and_roundtrips_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = AppConfig(storage_root=Path(tmpdir))
            store = FilesystemStore(config)
            store.save_run({"run_id": "run-1", "status": "running"})
            self.assertTrue((config.mvp_root / "events" / "outbox").exists())
            self.assertEqual(store.get_run("run-1")["status"], "running")

    def test_locking_is_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = AppConfig(storage_root=Path(tmpdir))
            store = FilesystemStore(config)
            store.create_lock("DEMO-1", "ai_implementation_brief", "run-1")
            with self.assertRaises(RuntimeError):
                store.create_lock("DEMO-1", "ai_implementation_brief", "run-2")

    def test_write_json_supports_concurrent_writers_to_same_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "shared" / "decision.json"
            errors: list[Exception] = []
            barrier = threading.Barrier(2)

            def _writer(value: int) -> None:
                try:
                    barrier.wait(timeout=2)
                    write_json(path, {"value": value})
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

            first = threading.Thread(target=_writer, args=(1,))
            second = threading.Thread(target=_writer, args=(2,))
            first.start()
            second.start()
            first.join()
            second.join()

            self.assertEqual(errors, [])
            self.assertIn(read_json(path)["value"], {1, 2})


if __name__ == "__main__":
    unittest.main()
