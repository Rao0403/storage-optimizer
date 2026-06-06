from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from storage_genius.config import HotspotScanConfig
from storage_genius.hotspots import scan_hotspots
from storage_genius.reports import write_hotspot_report


class HotspotScannerTests(unittest.TestCase):
    def test_classifies_large_archive_in_downloads(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            downloads = Path(root_dir) / "Downloads"
            downloads.mkdir()
            archive = downloads / "game.iso"
            archive.write_bytes(b"x" * 2 * 1024 * 1024)

            config = HotspotScanConfig(
                roots=[Path(root_dir)],
                large_file_threshold_mb=1,
                max_depth=3,
            )
            result = scan_hotspots(config)

            self.assertEqual(len(result.findings), 2)
            file_finding = next(item for item in result.findings if item.item_type == "file")
            self.assertEqual(file_finding.category, "downloads/installers")
            self.assertEqual(file_finding.action_type_hint, "move_file")

    def test_respects_excluded_paths(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            cache_dir = Path(root_dir) / "cache"
            cache_dir.mkdir()
            (cache_dir / "artifact.bin").write_bytes(b"x" * 2 * 1024 * 1024)

            config = HotspotScanConfig(
                roots=[Path(root_dir)],
                exclude_paths=[cache_dir],
                large_file_threshold_mb=1,
                max_depth=3,
            )
            result = scan_hotspots(config)
            self.assertEqual(result.findings, [])

    def test_directory_classification_and_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            pip_cache = Path(root_dir) / ".cache" / "pip"
            pip_cache.mkdir(parents=True)
            (pip_cache / "wheel.whl").write_bytes(b"x" * 2 * 1024 * 1024)

            config = HotspotScanConfig(
                roots=[Path(root_dir)],
                large_file_threshold_mb=1,
                max_depth=4,
            )
            result = scan_hotspots(config)

            directory_finding = next(
                item for item in result.findings if item.item_type == "directory" and item.category == "developer caches"
            )
            self.assertEqual(directory_finding.category, "developer caches")
            self.assertEqual(directory_finding.confidence, "high")
            self.assertGreater(directory_finding.reclaimable_bytes, 0)

    def test_report_generation(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir, tempfile.TemporaryDirectory() as report_dir:
            downloads = Path(root_dir) / "Downloads"
            downloads.mkdir()
            archive = downloads / "archive.iso"
            archive.write_bytes(b"x" * 2 * 1024 * 1024)

            config = HotspotScanConfig(
                roots=[Path(root_dir)],
                large_file_threshold_mb=1,
                max_depth=3,
            )
            result = scan_hotspots(config)
            report_path = write_hotspot_report(Path(report_dir), result, keep_count=2)
            self.assertTrue(report_path.exists())
            content = report_path.read_text(encoding="utf-8")
            self.assertIn("StorageGenius Hotspot Report", content)
            self.assertIn("downloads/installers".title(), content)

    def test_json_payload_is_serializable(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            downloads = Path(root_dir) / "Downloads"
            downloads.mkdir()
            archive = downloads / "archive.iso"
            archive.write_bytes(b"x" * 2 * 1024 * 1024)

            config = HotspotScanConfig(
                roots=[Path(root_dir)],
                large_file_threshold_mb=1,
                max_depth=3,
            )
            result = scan_hotspots(config)
            payload = [
                {
                    "path": finding.path,
                    "category": finding.category,
                    "size_bytes": finding.size_bytes,
                }
                for finding in result.findings
            ]
            self.assertIsInstance(json.dumps(payload), str)


if __name__ == "__main__":
    unittest.main()
