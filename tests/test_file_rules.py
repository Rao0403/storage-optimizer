from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from storage_genius.config import CleanupRule
from storage_genius.file_rules import build_destination_path, plan_moves, should_move_file


class FileRulesTests(unittest.TestCase):
    def test_should_move_large_old_file(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as target_dir:
            source_path = Path(source_dir) / "movie.mkv"
            source_path.write_bytes(b"x" * 2 * 1024 * 1024)
            old_timestamp = (datetime.now(timezone.utc) - timedelta(days=2)).timestamp()
            source_path.touch()
            source_path.chmod(0o666)
            import os

            os.utime(source_path, (old_timestamp, old_timestamp))

            rule = CleanupRule(
                name="test",
                source_dir=Path(source_dir),
                target_dir=Path(target_dir),
                min_size_mb=1,
                keep_recent_hours=24,
            )

            self.assertTrue(should_move_file(source_path, rule, datetime.now(timezone.utc)))

    def test_should_skip_recent_file(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as target_dir:
            source_path = Path(source_dir) / "archive.zip"
            source_path.write_bytes(b"x" * 2 * 1024 * 1024)

            rule = CleanupRule(
                name="test",
                source_dir=Path(source_dir),
                target_dir=Path(target_dir),
                min_size_mb=1,
                keep_recent_hours=24,
            )

            self.assertFalse(should_move_file(source_path, rule, datetime.now(timezone.utc)))

    def test_build_destination_path_avoids_collisions(self) -> None:
        with tempfile.TemporaryDirectory() as target_dir:
            rule = CleanupRule(
                name="test",
                source_dir=Path(target_dir),
                target_dir=Path(target_dir),
                organize_by="month",
            )
            now = datetime(2026, 6, 5, tzinfo=timezone.utc)
            month_dir = Path(target_dir) / "2026-06"
            month_dir.mkdir(parents=True)
            (month_dir / "video.mp4").write_bytes(b"existing")
            planned = build_destination_path(Path("video.mp4"), rule, now)
            self.assertEqual(planned.name, "video-1.mp4")

    def test_plan_moves_only_returns_eligible_files(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as target_dir:
            old_large = Path(source_dir) / "big.iso"
            old_large.write_bytes(b"x" * 2 * 1024 * 1024)
            small = Path(source_dir) / "small.txt"
            small.write_bytes(b"x")

            old_timestamp = (datetime.now(timezone.utc) - timedelta(days=2)).timestamp()
            import os

            os.utime(old_large, (old_timestamp, old_timestamp))
            os.utime(small, (old_timestamp, old_timestamp))

            rule = CleanupRule(
                name="test",
                source_dir=Path(source_dir),
                target_dir=Path(target_dir),
                min_size_mb=1,
                keep_recent_hours=24,
            )

            planned = plan_moves(rule, now=datetime.now(timezone.utc))
            self.assertEqual(len(planned), 1)
            self.assertEqual(planned[0].source_path.name, "big.iso")


if __name__ == "__main__":
    unittest.main()
