from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .app_usage import build_inventory_records, is_likely_user_facing_app, read_userassist_entries
from .config import default_config_text, load_config
from .db import AppInventoryRecord, FileMoveRecord, fetch_unused_apps, open_database, record_file_move, upsert_app_inventory
from .file_rules import execute_planned_moves, plan_moves
from .windows_apps import list_installed_apps


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="storage-genius", description="Windows storage automation helper.")
    parser.add_argument("--config", default="config.json", help="Path to the JSON config file.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init-config", help="Write a starter config file.")
    init_parser.add_argument("--force", action="store_true", help="Overwrite an existing config file.")

    cleanup_parser = subparsers.add_parser("cleanup", help="Move large files according to cleanup rules.")
    cleanup_parser.add_argument("--dry-run", action="store_true", help="Preview moves without changing files.")

    subparsers.add_parser("scan-apps", help="Refresh the installed app inventory and usage signals.")

    report_parser = subparsers.add_parser("report", help="Show apps that appear unused.")
    report_parser.add_argument("--days", type=int, default=None, help="Override the unused-days cutoff from config.")
    report_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    report_parser.add_argument("--all", action="store_true", help="Include component-like packages in the report.")

    run_once_parser = subparsers.add_parser("run-once", help="Run cleanup, app scan, and unused-app reporting.")
    run_once_parser.add_argument("--dry-run", action="store_true", help="Preview file moves without changing files.")

    return parser


def _write_text(path: Path, content: str, force: bool) -> int:
    if path.exists() and not force:
        print(f"Config already exists at {path}. Use --force to overwrite it.")
        return 1
    path.write_text(content, encoding="utf-8")
    print(f"Wrote starter config to {path}")
    return 0


def _run_cleanup(config_path: Path, dry_run: bool) -> int:
    config = load_config(config_path)
    connection = open_database(config.database_path)
    now = datetime.now(timezone.utc)
    total_bytes = 0
    total_moves = 0

    for rule in config.cleanup_rules:
        planned_moves = plan_moves(rule, now=now)
        execute_planned_moves(planned_moves, dry_run=dry_run)

        for move in planned_moves:
            total_bytes += move.size_bytes
            total_moves += 1
            if not dry_run:
                record_file_move(
                    connection,
                    FileMoveRecord(
                        rule_name=move.rule_name,
                        source_path=str(move.source_path),
                        destination_path=str(move.destination_path),
                        size_bytes=move.size_bytes,
                        moved_at=now,
                    ),
                )

            action = "Would move" if dry_run else "Moved"
            print(f"{action}: {move.source_path} -> {move.destination_path} ({move.size_bytes / 1024 / 1024:.1f} MB)")

    print(f"Cleanup complete. Files matched: {total_moves}. Total size: {total_bytes / 1024 / 1024:.1f} MB.")
    return 0


def _run_app_scan(config_path: Path) -> tuple[int, list[AppInventoryRecord], Path]:
    config = load_config(config_path)
    connection = open_database(config.database_path)
    observed_at = datetime.now(timezone.utc)
    installed_apps = list_installed_apps()
    usage_entries = read_userassist_entries()
    records = build_inventory_records(installed_apps, usage_entries, config.app_audit, observed_at)

    for record in records:
        upsert_app_inventory(connection, record)

    print(f"App scan complete. Inventory updated for {len(records)} apps using {len(usage_entries)} usage signals.")
    return 0, records, config.database_path


def _unused_app_rows(config_path: Path, days_override: int | None):
    config = load_config(config_path)
    connection = open_database(config.database_path)
    days = days_override if days_override is not None else config.app_audit.unused_days
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return fetch_unused_apps(connection, cutoff.isoformat()), days


def _run_report(config_path: Path, days_override: int | None, json_output: bool, include_all: bool) -> int:
    rows, days = _unused_app_rows(config_path, days_override)
    filtered_rows = rows if include_all else [row for row in rows if is_likely_user_facing_app(row["display_name"])]
    if json_output:
        serializable = [dict(row) for row in filtered_rows]
        print(json.dumps({"unused_days": days, "apps": serializable}, indent=2))
        return 0

    label = "All apps" if include_all else "Likely user-facing apps"
    print(f"{label} with no tracked usage in the last {days} days: {len(filtered_rows)}")
    for row in filtered_rows:
        last_used = row["last_used"] or "never observed"
        uninstall_hint = row["uninstall_string"] or "no uninstall command recorded"
        print(f"- {row['display_name']} | last used: {last_used} | uninstall: {uninstall_hint}")
    return 0


def _run_once(config_path: Path, dry_run: bool) -> int:
    cleanup_code = _run_cleanup(config_path, dry_run=dry_run)
    if cleanup_code != 0:
        return cleanup_code

    scan_code, _, _ = _run_app_scan(config_path)
    if scan_code != 0:
        return scan_code

    return _run_report(config_path, days_override=None, json_output=False, include_all=False)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config_path = Path(args.config).resolve()

    if args.command == "init-config":
        return _write_text(config_path, default_config_text(), args.force)
    if args.command == "cleanup":
        return _run_cleanup(config_path, dry_run=args.dry_run)
    if args.command == "scan-apps":
        code, _, _ = _run_app_scan(config_path)
        return code
    if args.command == "report":
        return _run_report(config_path, days_override=args.days, json_output=args.json, include_all=args.all)
    if args.command == "run-once":
        return _run_once(config_path, dry_run=args.dry_run)

    parser.error("Unknown command")
    return 2
