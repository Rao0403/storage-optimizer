from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from storage_genius.cli import main


class HotspotCliTests(unittest.TestCase):
    def test_scan_hotspots_json_output(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            workspace_path = Path(workspace)
            report_dir = workspace_path / "reports"
            downloads = workspace_path / "Downloads"
            downloads.mkdir()
            (downloads / "large.iso").write_bytes(b"x" * 2 * 1024 * 1024)

            config_path = workspace_path / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "database_path": str(workspace_path / "storage-genius.db"),
                        "report_directory": str(report_dir),
                        "cleanup_rules": [
                            {
                                "name": "large-downloads",
                                "source_dir": str(downloads),
                                "target_dir": str(workspace_path / "archive"),
                            }
                        ],
                        "app_audit": {
                            "unused_days": 30,
                            "minimum_install_age_days": 45,
                            "ignored_name_fragments": [],
                        },
                        "hotspot_scan": {
                            "roots": [str(workspace_path)],
                            "exclude_paths": [],
                            "large_file_threshold_mb": 1,
                            "max_depth": 3,
                            "html_reports_to_keep": 2,
                        },
                    }
                ),
                encoding="utf-8",
            )

            stream = io.StringIO()
            with redirect_stdout(stream):
                exit_code = main(["--config", str(config_path), "scan-hotspots", "--json"])

            payload = json.loads(stream.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["findings"][0]["category"], "downloads/installers")
            self.assertTrue(Path(payload["report_path"]).exists())


if __name__ == "__main__":
    unittest.main()
