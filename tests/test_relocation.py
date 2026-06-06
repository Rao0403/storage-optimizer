from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from storage_genius.config import RelocationConfig
from storage_genius.db import ActionQueueRecord, create_action_queue_record, open_database
from storage_genius.queue_actions import approve_action, execute_approved_actions, undo_action
from storage_genius.relocation import scan_relocation_candidates
from storage_genius.windows_apps import InstalledApp


class RelocationTests(unittest.TestCase):
    def test_scan_relocation_candidates_builds_destination(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            install_location = Path(workspace) / "PortableApp"
            install_location.mkdir()
            (install_location / "payload.bin").write_bytes(b"x" * 2 * 1024 * 1024)

            app = InstalledApp(
                display_name="PortableApp",
                publisher="Portable Inc",
                install_location=str(install_location),
                display_icon=None,
                uninstall_string=None,
                install_date=date(2025, 1, 1),
            )
            config = RelocationConfig(
                enabled=True,
                target_root=Path(workspace) / "relocated",
                minimum_size_gb=0,
                exclude_publishers=[],
                exclude_paths=[],
            )
            candidates = scan_relocation_candidates([app], config)
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0].destination_path, config.target_root / install_location.name)

    def test_scan_relocation_respects_excluded_path(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            install_location = Path(workspace) / "PortableApp"
            install_location.mkdir()
            (install_location / "payload.bin").write_bytes(b"x" * 2 * 1024 * 1024)

            app = InstalledApp(
                display_name="PortableApp",
                publisher="Portable Inc",
                install_location=str(install_location),
                display_icon=None,
                uninstall_string=None,
                install_date=date(2025, 1, 1),
            )
            config = RelocationConfig(
                enabled=True,
                target_root=Path(workspace) / "relocated",
                minimum_size_gb=0,
                exclude_publishers=[],
                exclude_paths=[install_location],
            )
            self.assertEqual(scan_relocation_candidates([app], config), [])

    def test_relocation_action_executes_and_undoes(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            source_path = Path(workspace) / "PortableApp"
            source_path.mkdir()
            (source_path / "payload.bin").write_bytes(b"x" * 1024 * 1024)
            destination_root = Path(workspace) / "relocated"
            destination_path = destination_root / source_path.name

            connection = open_database(Path(workspace) / "storage-genius.db")
            try:
                action_id = create_action_queue_record(
                    connection,
                    ActionQueueRecord(
                        action_type="relocate_app",
                        state="pending",
                        payload_json=json.dumps(
                            {
                                "source_path": str(source_path),
                                "destination_path": str(destination_path),
                                "staging_path": str(destination_path) + ".sg-staging",
                                "backup_path": str(source_path) + ".sg-backup",
                            }
                        ),
                        created_at=datetime.now(timezone.utc).isoformat(),
                        human_summary="Relocate portable app",
                    ),
                )
                approve_action(connection, action_id)
                result = execute_approved_actions(connection)[0]
                self.assertEqual(result.state, "executed")
                self.assertTrue(source_path.exists())
                self.assertTrue(destination_path.exists())
                self.assertEqual(Path(os.path.realpath(source_path)), destination_path.resolve())

                undo_result = undo_action(connection, action_id)
                self.assertEqual(undo_result.state, "rolled_back")
                self.assertTrue(source_path.exists())
                self.assertFalse(destination_path.exists())
            finally:
                connection.close()

    def test_relocation_failure_leaves_source_intact(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            source_path = Path(workspace) / "PortableApp"
            source_path.mkdir()
            (source_path / "payload.bin").write_bytes(b"x" * 1024 * 1024)
            destination_root = Path(workspace) / "relocated"
            destination_path = destination_root / source_path.name
            destination_path.mkdir(parents=True)

            connection = open_database(Path(workspace) / "storage-genius.db")
            try:
                action_id = create_action_queue_record(
                    connection,
                    ActionQueueRecord(
                        action_type="relocate_app",
                        state="pending",
                        payload_json=json.dumps(
                            {
                                "source_path": str(source_path),
                                "destination_path": str(destination_path),
                                "staging_path": str(destination_path) + ".sg-staging",
                                "backup_path": str(source_path) + ".sg-backup",
                            }
                        ),
                        created_at=datetime.now(timezone.utc).isoformat(),
                        human_summary="Relocate portable app with conflicting destination",
                    ),
                )
                approve_action(connection, action_id)
                result = execute_approved_actions(connection)[0]
                self.assertEqual(result.state, "failed")
                self.assertTrue(source_path.exists())
                self.assertTrue(destination_path.exists())
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
