from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .app_usage import build_inventory_records, is_likely_user_facing_app, read_userassist_entries
from .config import default_config_text, load_config
from .db import (
    ACTION_STATES,
    AppInventoryRecord,
    ActionQueueRecord,
    FileMoveRecord,
    HotspotFindingRecord,
    ScanRunRecord,
    create_action_queue_record,
    create_scan_run,
    fetch_action_logs,
    fetch_action_queue_record,
    fetch_queue_summary,
    fetch_unused_apps,
    finalize_scan_run,
    insert_hotspot_findings,
    list_action_queue_records,
    open_database,
    record_file_move,
    upsert_app_inventory,
)
from .file_rules import execute_planned_moves, plan_moves
from .hotspots import scan_hotspots
from .queue_actions import approve_action, dismiss_action, execute_approved_actions, undo_action
from .reports import write_hotspot_report
from .windows_apps import list_installed_apps


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="storage-genius", description="Windows storage automation helper.")
    parser.add_argument("--config", default="config.json", help="Path to the JSON config file.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init-config", help="Write a starter config file.")
    init_parser.add_argument("--force", action="store_true", help="Overwrite an existing config file.")

    cleanup_parser = subparsers.add_parser("cleanup", help="Move large files according to cleanup rules.")
    cleanup_parser.add_argument("--dry-run", action="store_true", help="Preview moves without changing files.")

    hotspot_parser = subparsers.add_parser("scan-hotspots", help="Scan C-drive storage hotspots and generate a report.")
    hotspot_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    hotspot_parser.add_argument("--html-only", action="store_true", help="Only print the generated HTML report path.")

    queue_parser = subparsers.add_parser("queue", help="Inspect and manage queued actions.")
    queue_subparsers = queue_parser.add_subparsers(dest="queue_command", required=True)
    queue_list_parser = queue_subparsers.add_parser("list", help="List queued actions.")
    queue_list_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    queue_show_parser = queue_subparsers.add_parser("show", help="Show one queued action.")
    queue_show_parser.add_argument("action_id", type=int)
    queue_show_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    queue_approve_parser = queue_subparsers.add_parser("approve", help="Approve one queued action.")
    queue_approve_parser.add_argument("action_id", type=int)
    queue_dismiss_parser = queue_subparsers.add_parser("dismiss", help="Dismiss one queued action.")
    queue_dismiss_parser.add_argument("action_id", type=int)
    queue_subparsers.add_parser("execute", help="Execute all approved actions.")
    queue_undo_parser = queue_subparsers.add_parser("undo", help="Undo one executed action.")
    queue_undo_parser.add_argument("action_id", type=int)

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
    try:
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
                print(
                    f"{action}: {move.source_path} -> {move.destination_path} ({move.size_bytes / 1024 / 1024:.1f} MB)"
                )

        print(f"Cleanup complete. Files matched: {total_moves}. Total size: {total_bytes / 1024 / 1024:.1f} MB.")
        return 0
    finally:
        connection.close()


def _run_hotspot_scan(config_path: Path, json_output: bool, html_only: bool) -> int:
    config = load_config(config_path)
    connection = open_database(config.database_path)
    try:
        started_at = datetime.now(timezone.utc)
        scan_run_id = create_scan_run(connection, ScanRunRecord(scan_type="hotspot", started_at=started_at.isoformat()))
        result = scan_hotspots(config.hotspot_scan)
        queue_summary = fetch_queue_summary(connection)
        report_path = write_hotspot_report(
            config.report_directory,
            result,
            keep_count=config.hotspot_scan.html_reports_to_keep,
            queue_summary=queue_summary,
        )
        insert_hotspot_findings(
            connection,
            [
                HotspotFindingRecord(
                    scan_run_id=scan_run_id,
                    root_path=finding.root_path,
                    path=finding.path,
                    item_type=finding.item_type,
                    category=finding.category,
                    size_bytes=finding.size_bytes,
                    reclaimable_bytes=finding.reclaimable_bytes,
                    action_type_hint=finding.action_type_hint,
                    confidence=finding.confidence,
                    details_json=finding.details_json,
                )
                for finding in result.findings
            ],
        )
        finalize_scan_run(
            connection,
            scan_run_id,
            ScanRunRecord(
                scan_type="hotspot",
                started_at=started_at.isoformat(),
                completed_at=datetime.now(timezone.utc).isoformat(),
                roots_json=json.dumps(result.roots_scanned),
                report_path=str(report_path),
                total_size_bytes=result.total_size_bytes,
                total_reclaimable_bytes=result.total_reclaimable_bytes,
            ),
        )

        serializable = {
            "roots_scanned": result.roots_scanned,
            "report_path": str(report_path),
            "findings": [
                {
                    "path": finding.path,
                    "item_type": finding.item_type,
                    "category": finding.category,
                    "size_bytes": finding.size_bytes,
                    "reclaimable_bytes": finding.reclaimable_bytes,
                    "action_type_hint": finding.action_type_hint,
                    "confidence": finding.confidence,
                }
                for finding in result.findings
            ],
            "total_size_bytes": result.total_size_bytes,
            "total_reclaimable_bytes": result.total_reclaimable_bytes,
        }

        if json_output:
            print(json.dumps(serializable, indent=2))
            return 0

        if html_only:
            print(report_path)
            return 0

        print(f"Hotspot scan complete. Findings: {len(result.findings)}.")
        print(f"Observed size: {result.total_size_bytes / 1024 / 1024:.1f} MB.")
        print(f"Predicted savings: {result.total_reclaimable_bytes / 1024 / 1024:.1f} MB.")
        print(f"HTML report: {report_path}")
        for finding in result.findings[:10]:
            print(
                f"- {finding.category} | {finding.path} | "
                f"{finding.size_bytes / 1024 / 1024:.1f} MB | {finding.action_type_hint} | {finding.confidence}"
            )
        return 0
    finally:
        connection.close()


def _queue_row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "action_type": row["action_type"],
        "state": row["state"],
        "human_summary": row["human_summary"],
        "payload": json.loads(row["payload_json"]),
        "rollback_payload": json.loads(row["rollback_payload_json"]) if row["rollback_payload_json"] else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "approved_at": row["approved_at"],
        "executed_at": row["executed_at"],
        "undone_at": row["undone_at"],
        "failure_message": row["failure_message"],
    }


def _run_queue_command(config_path: Path, args: argparse.Namespace) -> int:
    config = load_config(config_path)
    connection = open_database(config.database_path)
    try:
        if args.queue_command == "list":
            rows = list_action_queue_records(connection)
            if args.json:
                print(json.dumps([_queue_row_to_dict(row) for row in rows], indent=2))
                return 0
            print(f"Queued actions: {len(rows)}")
            for row in rows:
                print(f"- #{row['id']} | {row['state']} | {row['action_type']} | {row['human_summary']}")
            return 0

        if args.queue_command == "show":
            row = fetch_action_queue_record(connection, args.action_id)
            if row is None:
                raise ValueError(f"Action {args.action_id} does not exist.")
            payload = _queue_row_to_dict(row)
            payload["execution_logs"] = [dict(item) for item in fetch_action_logs(connection, "action_execution_log", args.action_id)]
            payload["undo_logs"] = [dict(item) for item in fetch_action_logs(connection, "action_undo_log", args.action_id)]
            if args.json:
                print(json.dumps(payload, indent=2))
                return 0
            print(f"Action #{row['id']} | {row['state']} | {row['action_type']}")
            print(row["human_summary"])
            print(json.dumps(payload["payload"], indent=2))
            return 0

        if args.queue_command == "approve":
            result = approve_action(connection, args.action_id)
            print(result.message)
            return 0

        if args.queue_command == "dismiss":
            result = dismiss_action(connection, args.action_id)
            print(result.message)
            return 0

        if args.queue_command == "execute":
            results = execute_approved_actions(connection)
            if not results:
                print("No approved actions to execute.")
                return 0
            for result in results:
                print(f"#{result.action_id} | {result.state} | {result.message}")
            return 0

        if args.queue_command == "undo":
            result = undo_action(connection, args.action_id)
            print(result.message)
            return 0

        raise ValueError(f"Unsupported queue command: {args.queue_command}")
    finally:
        connection.close()


def _run_app_scan(config_path: Path) -> tuple[int, list[AppInventoryRecord], Path]:
    config = load_config(config_path)
    connection = open_database(config.database_path)
    try:
        observed_at = datetime.now(timezone.utc)
        installed_apps = list_installed_apps()
        usage_entries = read_userassist_entries()
        records = build_inventory_records(installed_apps, usage_entries, config.app_audit, observed_at)

        for record in records:
            upsert_app_inventory(connection, record)

        print(f"App scan complete. Inventory updated for {len(records)} apps using {len(usage_entries)} usage signals.")
        return 0, records, config.database_path
    finally:
        connection.close()


def _unused_app_rows(config_path: Path, days_override: int | None):
    config = load_config(config_path)
    connection = open_database(config.database_path)
    try:
        days = days_override if days_override is not None else config.app_audit.unused_days
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        return fetch_unused_apps(connection, cutoff.isoformat()), days
    finally:
        connection.close()


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
    if args.command == "scan-hotspots":
        return _run_hotspot_scan(config_path, json_output=args.json, html_only=args.html_only)
    if args.command == "queue":
        return _run_queue_command(config_path, args)
    if args.command == "scan-apps":
        code, _, _ = _run_app_scan(config_path)
        return code
    if args.command == "report":
        return _run_report(config_path, days_override=args.days, json_output=args.json, include_all=args.all)
    if args.command == "run-once":
        return _run_once(config_path, dry_run=args.dry_run)

    parser.error("Unknown command")
    return 2
