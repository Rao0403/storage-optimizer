from __future__ import annotations

import hashlib
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
          match_reason TEXT
        );
        """
    )
    connection.commit()


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
          uninstall_string, install_date, last_used, last_seen, match_reason
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
          match_reason = excluded.match_reason
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
