from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from storage_genius.app_usage import ActivityWatchEntry, UsageEntry, build_inventory_records, fetch_activitywatch_entries
from storage_genius.config import ActivityWatchConfig, AppAuditConfig
from storage_genius.windows_apps import InstalledApp


class AppUsageScoringTests(unittest.TestCase):
    def test_activitywatch_outranks_userassist_when_newer(self) -> None:
        observed_at = datetime(2026, 6, 6, tzinfo=timezone.utc)
        app = InstalledApp(
            display_name="Visual Studio Code",
            publisher="Microsoft",
            install_location=None,
            display_icon=None,
            uninstall_string=None,
            install_date=date(2025, 1, 1),
        )
        usage_entries = [
            UsageEntry(
                raw_name="code.exe",
                normalized_name="C:\\Users\\Aryan\\AppData\\Local\\Programs\\Microsoft VS Code\\Code.exe",
                run_count=10,
                last_run=observed_at - timedelta(days=60),
            )
        ]
        activitywatch_entries = [ActivityWatchEntry(app_name="Visual Studio Code", last_used=observed_at - timedelta(days=2))]
        records = build_inventory_records(
            [app],
            usage_entries,
            activitywatch_entries,
            AppAuditConfig(ignored_name_fragments=[]),
            observed_at,
        )
        self.assertEqual(records[0].last_used_source, "activitywatch")
        self.assertGreaterEqual(records[0].usage_score, 65)

    def test_unreachable_activitywatch_returns_no_entries(self) -> None:
        config = ActivityWatchConfig(enabled=True, base_url="http://localhost:5600", lookback_days=30)
        with patch("urllib.request.urlopen", side_effect=OSError("unreachable")):
            entries = fetch_activitywatch_entries(config, datetime.now(timezone.utc))
        self.assertEqual(entries, [])

    def test_large_unused_user_facing_app_becomes_review_candidate(self) -> None:
        observed_at = datetime(2026, 6, 6, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as workspace:
            install_location = Path(workspace) / "Notion"
            install_location.mkdir()
            (install_location / "blob.bin").write_bytes(b"x" * 2 * 1024 * 1024)
            app = InstalledApp(
                display_name="Notion",
                publisher="Notion Labs",
                install_location=str(install_location),
                display_icon=None,
                uninstall_string=None,
                install_date=date(2025, 1, 1),
            )
            records = build_inventory_records(
                [app],
                [],
                [],
                AppAuditConfig(minimum_candidate_size_mb=1, ignored_name_fragments=[]),
                observed_at,
            )
            self.assertEqual(records[0].candidate_action, "review_uninstall")
            self.assertEqual(records[0].usage_confidence, "low")

    def test_component_filter_still_ignores_runtime_packages(self) -> None:
        observed_at = datetime(2026, 6, 6, tzinfo=timezone.utc)
        app = InstalledApp(
            display_name="Microsoft .NET Runtime - 8.0",
            publisher="Microsoft",
            install_location=None,
            display_icon=None,
            uninstall_string=None,
            install_date=date(2025, 1, 1),
        )
        records = build_inventory_records(
            [app],
            [],
            [],
            AppAuditConfig(ignored_name_fragments=[]),
            observed_at,
        )
        self.assertEqual(records[0].usage_score, 0)


if __name__ == "__main__":
    unittest.main()
