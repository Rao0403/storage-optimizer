from __future__ import annotations

import json
import threading
import unittest
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from storage_genius.config import default_config_text, load_config
from storage_genius.db import ActionQueueRecord, create_action_queue_record, open_database
from storage_genius.ui import StorageGeniusServer, build_dashboard_payload


class UiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        payload = json.loads(default_config_text())
        payload["database_path"] = str(self.root / "storage.db")
        payload["report_directory"] = str(self.root / "reports")
        payload["hotspot_scan"]["roots"] = [str(self.root)]
        payload["hotspot_scan"]["exclude_paths"] = []
        self.config_path = self.root / "config.json"
        self.config_path.write_text(json.dumps(payload), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_dashboard_payload_contains_summary_and_queue(self) -> None:
        config = load_config(self.config_path)
        connection = open_database(config.database_path)
        try:
            create_action_queue_record(
                connection,
                ActionQueueRecord(
                    action_type="delete_cache",
                    state="pending",
                    payload_json=json.dumps({"target_path": str(self.root / "cache")}),
                    created_at=datetime.now(timezone.utc).isoformat(),
                    human_summary="Delete test cache",
                ),
            )
        finally:
            connection.close()

        payload = build_dashboard_payload(self.config_path)

        self.assertIn("summary", payload)
        self.assertEqual(payload["summary"]["queue"]["pending"], 1)
        self.assertEqual(payload["queue"][0]["human_summary"], "Delete test cache")

    def test_local_server_serves_dashboard_and_json_api(self) -> None:
        server = StorageGeniusServer(("127.0.0.1", 0), self.config_path)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base_url = f"http://127.0.0.1:{server.server_port}"
            with urllib.request.urlopen(f"{base_url}/", timeout=5) as response:
                page = response.read().decode("utf-8")
            with urllib.request.urlopen(f"{base_url}/api/summary", timeout=5) as response:
                summary = json.loads(response.read().decode("utf-8"))

            self.assertIn("StorageGenius | Local storage console", page)
            self.assertTrue(summary["ok"])
            self.assertIn("queue", summary["summary"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
