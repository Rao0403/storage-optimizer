from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import CleanupRule


@dataclass(slots=True)
class PlannedMove:
    rule_name: str
    source_path: Path
    destination_path: Path
    size_bytes: int


def should_move_file(file_path: Path, rule: CleanupRule, now: datetime) -> bool:
    if not file_path.is_file():
        return False

    suffix = file_path.suffix.lower()
    if rule.include_extensions and suffix not in rule.include_extensions:
        return False
    if suffix in rule.exclude_extensions:
        return False

    size_bytes = file_path.stat().st_size
    if size_bytes < rule.min_size_mb * 1024 * 1024:
        return False

    cutoff = now - timedelta(hours=rule.keep_recent_hours)
    modified_at = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)
    return modified_at <= cutoff


def plan_moves(rule: CleanupRule, now: datetime | None = None) -> list[PlannedMove]:
    timestamp = now or datetime.now(timezone.utc)
    if not rule.source_dir.exists():
        return []

    planned: list[PlannedMove] = []
    for item in rule.source_dir.iterdir():
        if not should_move_file(item, rule, timestamp):
            continue
        destination_path = build_destination_path(item, rule, timestamp)
        planned.append(
            PlannedMove(
                rule_name=rule.name,
                source_path=item,
                destination_path=destination_path,
                size_bytes=item.stat().st_size,
            )
        )
    return planned


def build_destination_path(source_path: Path, rule: CleanupRule, now: datetime) -> Path:
    target_dir = rule.target_dir
    if rule.organize_by == "month":
        target_dir = target_dir / now.strftime("%Y-%m")
    elif rule.organize_by == "extension":
        target_dir = target_dir / (source_path.suffix.lower().lstrip(".") or "no-extension")

    candidate = target_dir / source_path.name
    counter = 1
    while candidate.exists():
        candidate = target_dir / f"{source_path.stem}-{counter}{source_path.suffix}"
        counter += 1
    return candidate


def execute_planned_moves(planned_moves: list[PlannedMove], dry_run: bool) -> list[PlannedMove]:
    if dry_run:
        return planned_moves

    for move in planned_moves:
        move.destination_path.parent.mkdir(parents=True, exist_ok=True)
        move.source_path.replace(move.destination_path)
    return planned_moves
