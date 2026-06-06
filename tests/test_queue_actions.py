from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from storage_genius.db import (
    ActionQueueRecord,
    create_action_queue_record,
    fetch_action_logs,
    fetch_action_queue_record,
    open_database,
)
from storage_genius.queue_actions import approve_action, execute_approved_actions, undo_action


class QueueActionTests(unittest.TestCase):
    def test_move_file_action_can_execute_and_undo(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            source_path = Path(workspace) / "source.txt"
            destination_path = Path(workspace) / "archive" / "source.txt"
            source_path.write_text("payload", encoding="utf-8")

            connection = open_database(Path(workspace) / "storage-genius.db")
            try:
                action_id = create_action_queue_record(
                    connection,
                    ActionQueueRecord(
                        action_type="move_file",
                        state="pending",
                        payload_json=json.dumps(
                            {
                                "source_path": str(source_path),
                                "destination_path": str(destination_path),
                            }
                        ),
                        created_at=datetime.now(timezone.utc).isoformat(),
                        human_summary="Move test file",
                    ),
                )
                approve_action(connection, action_id)
                results = execute_approved_actions(connection)
                self.assertEqual(results[0].state, "executed")
                self.assertFalse(source_path.exists())
                self.assertTrue(destination_path.exists())

                undo_result = undo_action(connection, action_id)
                self.assertEqual(undo_result.state, "rolled_back")
                self.assertTrue(source_path.exists())
                self.assertFalse(destination_path.exists())
            finally:
                connection.close()

    def test_execute_rejects_non_approved_action(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            connection = open_database(Path(workspace) / "storage-genius.db")
            try:
                action_id = create_action_queue_record(
                    connection,
                    ActionQueueRecord(
                        action_type="move_file",
                        state="pending",
                        payload_json=json.dumps({"source_path": "a", "destination_path": "b"}),
                        created_at=datetime.now(timezone.utc).isoformat(),
                        human_summary="Invalid state test",
                    ),
                )
                with self.assertRaises(ValueError):
                    undo_action(connection, action_id)
            finally:
                connection.close()

    def test_failed_execution_is_logged(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            connection = open_database(Path(workspace) / "storage-genius.db")
            try:
                action_id = create_action_queue_record(
                    connection,
                    ActionQueueRecord(
                        action_type="move_file",
                        state="pending",
                        payload_json=json.dumps(
                            {
                                "source_path": str(Path(workspace) / "missing.txt"),
                                "destination_path": str(Path(workspace) / "archive" / "missing.txt"),
                            }
                        ),
                        created_at=datetime.now(timezone.utc).isoformat(),
                        human_summary="Missing source test",
                    ),
                )
                approve_action(connection, action_id)
                result = execute_approved_actions(connection)[0]
                self.assertEqual(result.state, "failed")
                row = fetch_action_queue_record(connection, action_id)
                self.assertEqual(row["state"], "failed")
                execution_logs = fetch_action_logs(connection, "action_execution_log", action_id)
                self.assertEqual(execution_logs[0]["status"], "failed")
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
