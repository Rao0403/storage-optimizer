from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(slots=True)
class FileMoveRecord:
    rule_name: str
    source_path: str
    destination_path: str
    size_bytes: int
    moved_at: datetime


@dataclass(slots=True)
class AppInventoryRecord:
    app_id: str
    display_name: str
    publisher: str | None
    install_location: str | None
    display_icon: str | None
    uninstall_string: str | None
    install_date: str | None
    last_used: str | None
    last_seen: str
    match_reason: str | None
    usage_score: int
    usage_confidence: str
    last_used_source: str | None
    candidate_action: str
    estimated_installed_size_bytes: int | None


@dataclass(slots=True)
class ScanRunRecord:
    scan_type: str
    started_at: str
    completed_at: str | None = None
    roots_json: str | None = None
    report_path: str | None = None
    total_size_bytes: int = 0
    total_reclaimable_bytes: int = 0


@dataclass(slots=True)
class HotspotFindingRecord:
    scan_run_id: int
    root_path: str
    path: str
    item_type: str
    category: str
    size_bytes: int
    reclaimable_bytes: int
    action_type_hint: str
    confidence: str
    details_json: str


@dataclass(slots=True)
class ActionQueueRecord:
    action_type: str
    state: str
    payload_json: str
    created_at: str
    human_summary: str
    id: int | None = None
    updated_at: str | None = None
    approved_at: str | None = None
    executed_at: str | None = None
    undone_at: str | None = None
    rollback_payload_json: str | None = None
    failure_message: str | None = None


ACTION_STATES = ("pending", "approved", "executed", "failed", "rolled_back", "dismissed")
ACTION_TYPES = ("move_file", "delete_cache", "relocate_app")


def open_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    initialize_database(connection)
    return connection


def initialize_database(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS file_moves (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          rule_name TEXT NOT NULL,
          source_path TEXT NOT NULL,
          destination_path TEXT NOT NULL,
          size_bytes INTEGER NOT NULL,
          moved_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS app_inventory (
          app_id TEXT PRIMARY KEY,
          display_name TEXT NOT NULL,
          publisher TEXT,
          install_location TEXT,
          display_icon TEXT,
          uninstall_string TEXT,
          install_date TEXT,
          last_used TEXT,
          last_seen TEXT NOT NULL,
          match_reason TEXT,
          usage_score INTEGER NOT NULL DEFAULT 0,
          usage_confidence TEXT NOT NULL DEFAULT 'low',
          last_used_source TEXT,
          candidate_action TEXT NOT NULL DEFAULT 'review',
          estimated_installed_size_bytes INTEGER
        );

        CREATE TABLE IF NOT EXISTS scan_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          scan_type TEXT NOT NULL,
          started_at TEXT NOT NULL,
          completed_at TEXT,
          roots_json TEXT,
          report_path TEXT,
          total_size_bytes INTEGER NOT NULL DEFAULT 0,
          total_reclaimable_bytes INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS hotspot_findings (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          scan_run_id INTEGER NOT NULL,
          root_path TEXT NOT NULL,
          path TEXT NOT NULL,
          item_type TEXT NOT NULL,
          category TEXT NOT NULL,
          size_bytes INTEGER NOT NULL,
          reclaimable_bytes INTEGER NOT NULL,
          action_type_hint TEXT NOT NULL,
          confidence TEXT NOT NULL,
          details_json TEXT NOT NULL,
          FOREIGN KEY(scan_run_id) REFERENCES scan_runs(id)
        );

        CREATE TABLE IF NOT EXISTS action_queue (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          action_type TEXT NOT NULL,
          state TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          rollback_payload_json TEXT,
          human_summary TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT,
          approved_at TEXT,
          executed_at TEXT,
          undone_at TEXT,
          failure_message TEXT
        );

        CREATE TABLE IF NOT EXISTS action_execution_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          action_id INTEGER NOT NULL,
          happened_at TEXT NOT NULL,
          status TEXT NOT NULL,
          message TEXT NOT NULL,
          details_json TEXT NOT NULL,
          FOREIGN KEY(action_id) REFERENCES action_queue(id)
        );

        CREATE TABLE IF NOT EXISTS action_undo_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          action_id INTEGER NOT NULL,
          happened_at TEXT NOT NULL,
          status TEXT NOT NULL,
          message TEXT NOT NULL,
          details_json TEXT NOT NULL,
          FOREIGN KEY(action_id) REFERENCES action_queue(id)
        );
        """
    )
    _ensure_column(connection, "app_inventory", "usage_score", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(connection, "app_inventory", "usage_confidence", "TEXT NOT NULL DEFAULT 'low'")
    _ensure_column(connection, "app_inventory", "last_used_source", "TEXT")
    _ensure_column(connection, "app_inventory", "candidate_action", "TEXT NOT NULL DEFAULT 'review'")
    _ensure_column(connection, "app_inventory", "estimated_installed_size_bytes", "INTEGER")
    connection.commit()


def _ensure_column(connection: sqlite3.Connection, table_name: str, column_name: str, definition: str) -> None:
    cursor = connection.execute(f"PRAGMA table_info({table_name})")
    existing_columns = {row["name"] for row in cursor.fetchall()}
    if column_name not in existing_columns:
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def record_file_move(connection: sqlite3.Connection, record: FileMoveRecord) -> None:
    connection.execute(
        """
        INSERT INTO file_moves(rule_name, source_path, destination_path, size_bytes, moved_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            record.rule_name,
            record.source_path,
            record.destination_path,
            record.size_bytes,
            record.moved_at.isoformat(),
        ),
    )
    connection.commit()


def build_app_id(display_name: str, publisher: str | None, uninstall_string: str | None) -> str:
    digest = hashlib.sha256()
    digest.update(display_name.lower().encode("utf-8"))
    digest.update(b"|")
    digest.update((publisher or "").lower().encode("utf-8"))
    digest.update(b"|")
    digest.update((uninstall_string or "").lower().encode("utf-8"))
    return digest.hexdigest()


def upsert_app_inventory(connection: sqlite3.Connection, record: AppInventoryRecord) -> None:
    connection.execute(
        """
        INSERT INTO app_inventory(
          app_id, display_name, publisher, install_location, display_icon,
          uninstall_string, install_date, last_used, last_seen, match_reason,
          usage_score, usage_confidence, last_used_source, candidate_action, estimated_installed_size_bytes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(app_id) DO UPDATE SET
          display_name = excluded.display_name,
          publisher = excluded.publisher,
          install_location = excluded.install_location,
          display_icon = excluded.display_icon,
          uninstall_string = excluded.uninstall_string,
          install_date = excluded.install_date,
          last_used = CASE
            WHEN excluded.last_used IS NOT NULL THEN excluded.last_used
            ELSE app_inventory.last_used
          END,
          last_seen = excluded.last_seen,
          match_reason = excluded.match_reason,
          usage_score = excluded.usage_score,
          usage_confidence = excluded.usage_confidence,
          last_used_source = excluded.last_used_source,
          candidate_action = excluded.candidate_action,
          estimated_installed_size_bytes = excluded.estimated_installed_size_bytes
        """,
        (
            record.app_id,
            record.display_name,
            record.publisher,
            record.install_location,
            record.display_icon,
            record.uninstall_string,
            record.install_date,
            record.last_used,
            record.last_seen,
            record.match_reason,
            record.usage_score,
            record.usage_confidence,
            record.last_used_source,
            record.candidate_action,
            record.estimated_installed_size_bytes,
        ),
    )
    connection.commit()


def fetch_unused_apps(connection: sqlite3.Connection, cutoff_iso: str) -> list[sqlite3.Row]:
    cursor = connection.execute(
        """
        SELECT *
        FROM app_inventory
        WHERE COALESCE(last_used, '') < ?
        ORDER BY COALESCE(last_used, ''), display_name COLLATE NOCASE
        """,
        (cutoff_iso,),
    )
    return list(cursor.fetchall())


def fetch_app_report_rows(connection: sqlite3.Connection, min_score: int | None = None) -> list[sqlite3.Row]:
    if min_score is None:
        cursor = connection.execute(
            """
            SELECT *
            FROM app_inventory
            ORDER BY usage_score ASC, COALESCE(estimated_installed_size_bytes, 0) DESC, display_name COLLATE NOCASE
            """
        )
    else:
        cursor = connection.execute(
            """
            SELECT *
            FROM app_inventory
            WHERE usage_score >= ?
            ORDER BY usage_score ASC, COALESCE(estimated_installed_size_bytes, 0) DESC, display_name COLLATE NOCASE
            """,
            (min_score,),
        )
    return list(cursor.fetchall())


def create_scan_run(connection: sqlite3.Connection, record: ScanRunRecord) -> int:
    cursor = connection.execute(
        """
        INSERT INTO scan_runs(
          scan_type, started_at, completed_at, roots_json, report_path, total_size_bytes, total_reclaimable_bytes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.scan_type,
            record.started_at,
            record.completed_at,
            record.roots_json,
            record.report_path,
            record.total_size_bytes,
            record.total_reclaimable_bytes,
        ),
    )
    connection.commit()
    return int(cursor.lastrowid)


def finalize_scan_run(connection: sqlite3.Connection, scan_run_id: int, record: ScanRunRecord) -> None:
    connection.execute(
        """
        UPDATE scan_runs
        SET completed_at = ?, roots_json = ?, report_path = ?, total_size_bytes = ?, total_reclaimable_bytes = ?
        WHERE id = ?
        """,
        (
            record.completed_at,
            record.roots_json,
            record.report_path,
            record.total_size_bytes,
            record.total_reclaimable_bytes,
            scan_run_id,
        ),
    )
    connection.commit()


def insert_hotspot_findings(connection: sqlite3.Connection, findings: list[HotspotFindingRecord]) -> None:
    connection.executemany(
        """
        INSERT INTO hotspot_findings(
          scan_run_id, root_path, path, item_type, category, size_bytes,
          reclaimable_bytes, action_type_hint, confidence, details_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                finding.scan_run_id,
                finding.root_path,
                finding.path,
                finding.item_type,
                finding.category,
                finding.size_bytes,
                finding.reclaimable_bytes,
                finding.action_type_hint,
                finding.confidence,
                finding.details_json,
            )
            for finding in findings
        ],
    )
    connection.commit()


def create_action_queue_record(connection: sqlite3.Connection, record: ActionQueueRecord) -> int:
    if record.action_type not in ACTION_TYPES:
        raise ValueError(f"Unsupported action type: {record.action_type}")
    if record.state not in ACTION_STATES:
        raise ValueError(f"Unsupported action state: {record.state}")

    cursor = connection.execute(
        """
        INSERT INTO action_queue(
          action_type, state, payload_json, rollback_payload_json, human_summary,
          created_at, updated_at, approved_at, executed_at, undone_at, failure_message
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.action_type,
            record.state,
            record.payload_json,
            record.rollback_payload_json,
            record.human_summary,
            record.created_at,
            record.updated_at,
            record.approved_at,
            record.executed_at,
            record.undone_at,
            record.failure_message,
        ),
    )
    connection.commit()
    return int(cursor.lastrowid)


def fetch_action_queue_record(connection: sqlite3.Connection, action_id: int) -> sqlite3.Row | None:
    cursor = connection.execute("SELECT * FROM action_queue WHERE id = ?", (action_id,))
    return cursor.fetchone()


def list_action_queue_records(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    cursor = connection.execute(
        """
        SELECT *
        FROM action_queue
        ORDER BY id ASC
        """
    )
    return list(cursor.fetchall())


def list_actions_by_state(connection: sqlite3.Connection, state: str) -> list[sqlite3.Row]:
    cursor = connection.execute(
        """
        SELECT *
        FROM action_queue
        WHERE state = ?
        ORDER BY id ASC
        """,
        (state,),
    )
    return list(cursor.fetchall())


def update_action_queue_record(
    connection: sqlite3.Connection,
    action_id: int,
    *,
    state: str,
    updated_at: str,
    approved_at: str | None = None,
    executed_at: str | None = None,
    undone_at: str | None = None,
    rollback_payload_json: str | None = None,
    failure_message: str | None = None,
) -> None:
    if state not in ACTION_STATES:
        raise ValueError(f"Unsupported action state: {state}")
    connection.execute(
        """
        UPDATE action_queue
        SET state = ?, updated_at = ?, approved_at = ?, executed_at = ?, undone_at = ?,
            rollback_payload_json = COALESCE(?, rollback_payload_json),
            failure_message = ?
        WHERE id = ?
        """,
        (
            state,
            updated_at,
            approved_at,
            executed_at,
            undone_at,
            rollback_payload_json,
            failure_message,
            action_id,
        ),
    )
    connection.commit()


def insert_action_execution_log(
    connection: sqlite3.Connection, action_id: int, happened_at: str, status: str, message: str, details: dict
) -> None:
    connection.execute(
        """
        INSERT INTO action_execution_log(action_id, happened_at, status, message, details_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (action_id, happened_at, status, message, json.dumps(details, sort_keys=True)),
    )
    connection.commit()


def insert_action_undo_log(
    connection: sqlite3.Connection, action_id: int, happened_at: str, status: str, message: str, details: dict
) -> None:
    connection.execute(
        """
        INSERT INTO action_undo_log(action_id, happened_at, status, message, details_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (action_id, happened_at, status, message, json.dumps(details, sort_keys=True)),
    )
    connection.commit()


def fetch_action_logs(connection: sqlite3.Connection, table_name: str, action_id: int) -> list[sqlite3.Row]:
    if table_name not in {"action_execution_log", "action_undo_log"}:
        raise ValueError("Unsupported log table")
    cursor = connection.execute(
        f"SELECT * FROM {table_name} WHERE action_id = ? ORDER BY id ASC",  # noqa: S608
        (action_id,),
    )
    return list(cursor.fetchall())


def fetch_queue_summary(connection: sqlite3.Connection) -> dict[str, int]:
    cursor = connection.execute(
        """
        SELECT state, COUNT(*) AS item_count
        FROM action_queue
        GROUP BY state
        """
    )
    summary = {state: 0 for state in ACTION_STATES}
    for row in cursor.fetchall():
        summary[row["state"]] = row["item_count"]
    return summary
