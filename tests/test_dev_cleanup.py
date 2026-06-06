from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from storage_genius.config import DevCleanupConfig, DevCleanupToolConfig
from storage_genius.db import ActionQueueRecord, create_action_queue_record, list_actions_by_state, open_database
from storage_genius.dev_cleanup import scan_dev_caches
from storage_genius.queue_actions import approve_action, execute_approved_actions


class DevCleanupTests(unittest.TestCase):
    def test_scan_dev_caches_detects_configured_paths(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            cache_path = Path(workspace) / "pip-cache"
            cache_path.mkdir()
            (cache_path / "package.whl").write_bytes(b"x" * 2 * 1024 * 1024)

            config = DevCleanupConfig(
                enabled=True,
                node=DevCleanupToolConfig(enabled=False),
                python=DevCleanupToolConfig(enabled=False),
                extra_paths=[cache_path],
                exclude_paths=[],
            )
            findings = scan_dev_caches(config)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].path, cache_path)
            self.assertGreater(findings[0].size_bytes, 0)

    def test_scan_dev_caches_respects_exclusions(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            cache_path = Path(workspace) / "npm-cache"
            cache_path.mkdir()
            (cache_path / "package.tgz").write_bytes(b"x" * 2 * 1024 * 1024)

            config = DevCleanupConfig(
                enabled=True,
                node=DevCleanupToolConfig(enabled=False),
                python=DevCleanupToolConfig(enabled=False),
                extra_paths=[cache_path],
                exclude_paths=[cache_path],
            )
            self.assertEqual(scan_dev_caches(config), [])

    def test_delete_cache_executes_only_after_approval(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            cache_path = Path(workspace) / "pip-cache"
            cache_path.mkdir()
            (cache_path / "artifact.whl").write_bytes(b"x" * 1024)

            connection = open_database(Path(workspace) / "storage-genius.db")
            try:
                action_id = create_action_queue_record(
                    connection,
                    ActionQueueRecord(
                        action_type="delete_cache",
                        state="pending",
                        payload_json=json.dumps({"target_path": str(cache_path)}),
                        created_at=datetime.now(timezone.utc).isoformat(),
                        human_summary="Delete cache",
                    ),
                )
                self.assertEqual(execute_approved_actions(connection), [])
                self.assertTrue(cache_path.exists())

                approve_action(connection, action_id)
                results = execute_approved_actions(connection)
                self.assertEqual(results[0].state, "executed")
                self.assertFalse(cache_path.exists())
                self.assertEqual(len(list_actions_by_state(connection, "approved")), 0)
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
