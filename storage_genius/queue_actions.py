from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .db import (
    fetch_action_queue_record,
    insert_action_execution_log,
    insert_action_undo_log,
    list_actions_by_state,
    update_action_queue_record,
)


@dataclass(slots=True)
class ActionExecutionResult:
    action_id: int
    state: str
    message: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def approve_action(connection: sqlite3.Connection, action_id: int) -> ActionExecutionResult:
    record = fetch_action_queue_record(connection, action_id)
    if record is None:
        raise ValueError(f"Action {action_id} does not exist.")
    if record["state"] != "pending":
        raise ValueError(f"Action {action_id} is not pending.")

    timestamp = _now_iso()
    update_action_queue_record(connection, action_id, state="approved", updated_at=timestamp, approved_at=timestamp)
    return ActionExecutionResult(action_id=action_id, state="approved", message="Action approved.")


def dismiss_action(connection: sqlite3.Connection, action_id: int) -> ActionExecutionResult:
    record = fetch_action_queue_record(connection, action_id)
    if record is None:
        raise ValueError(f"Action {action_id} does not exist.")
    if record["state"] not in {"pending", "approved"}:
        raise ValueError(f"Action {action_id} cannot be dismissed from state {record['state']}.")

    timestamp = _now_iso()
    update_action_queue_record(connection, action_id, state="dismissed", updated_at=timestamp)
    return ActionExecutionResult(action_id=action_id, state="dismissed", message="Action dismissed.")


def execute_approved_actions(connection: sqlite3.Connection) -> list[ActionExecutionResult]:
    results: list[ActionExecutionResult] = []
    for record in list_actions_by_state(connection, "approved"):
        results.append(_execute_action(connection, record["id"]))
    return results


def _execute_action(connection: sqlite3.Connection, action_id: int) -> ActionExecutionResult:
    record = fetch_action_queue_record(connection, action_id)
    if record is None:
        raise ValueError(f"Action {action_id} does not exist.")
    if record["state"] != "approved":
        raise ValueError(f"Action {action_id} is not approved.")

    handler_map = {
        "move_file": _execute_move_file,
        "delete_cache": _execute_delete_cache,
        "relocate_app": _execute_relocate_app,
    }
    payload = json.loads(record["payload_json"])
    timestamp = _now_iso()

    try:
        rollback_payload, message = handler_map[record["action_type"]](payload)
        update_action_queue_record(
            connection,
            action_id,
            state="executed",
            updated_at=timestamp,
            approved_at=record["approved_at"],
            executed_at=timestamp,
            rollback_payload_json=json.dumps(rollback_payload, sort_keys=True),
            failure_message=None,
        )
        insert_action_execution_log(connection, action_id, timestamp, "executed", message, rollback_payload)
        return ActionExecutionResult(action_id=action_id, state="executed", message=message)
    except Exception as exc:  # noqa: BLE001
        update_action_queue_record(
            connection,
            action_id,
            state="failed",
            updated_at=timestamp,
            approved_at=record["approved_at"],
            failure_message=str(exc),
        )
        insert_action_execution_log(connection, action_id, timestamp, "failed", str(exc), {"action_type": record["action_type"]})
        return ActionExecutionResult(action_id=action_id, state="failed", message=str(exc))


def undo_action(connection: sqlite3.Connection, action_id: int) -> ActionExecutionResult:
    record = fetch_action_queue_record(connection, action_id)
    if record is None:
        raise ValueError(f"Action {action_id} does not exist.")
    if record["state"] != "executed":
        raise ValueError(f"Action {action_id} is not executed.")

    rollback_payload = json.loads(record["rollback_payload_json"] or "{}")
    handler_map = {
        "move_file": _undo_move_file,
        "delete_cache": _undo_delete_cache,
        "relocate_app": _undo_relocate_app,
    }
    timestamp = _now_iso()

    try:
        message, restorable = handler_map[record["action_type"]](rollback_payload)
        if restorable:
            update_action_queue_record(connection, action_id, state="rolled_back", updated_at=timestamp, undone_at=timestamp)
        insert_action_undo_log(
            connection,
            action_id,
            timestamp,
            "rolled_back" if restorable else "not_restorable",
            message,
            rollback_payload,
        )
        return ActionExecutionResult(
            action_id=action_id,
            state="rolled_back" if restorable else "executed",
            message=message,
        )
    except Exception as exc:  # noqa: BLE001
        insert_action_undo_log(connection, action_id, timestamp, "failed", str(exc), rollback_payload)
        raise


def _execute_move_file(payload: dict) -> tuple[dict, str]:
    source_path = Path(payload["source_path"])
    destination_path = Path(payload["destination_path"])
    destination_path.parent.mkdir(parents=True, exist_ok=True)

    if destination_path.exists() and not source_path.exists():
        return (
            {
                "source_path": str(source_path),
                "destination_path": str(destination_path),
                "restorable": True,
                "already_moved": True,
            },
            "Move already applied.",
        )
    if not source_path.exists():
        raise FileNotFoundError(f"Source path not found: {source_path}")
    if destination_path.exists():
        raise FileExistsError(f"Destination path already exists: {destination_path}")

    source_path.replace(destination_path)
    return (
        {
            "source_path": str(source_path),
            "destination_path": str(destination_path),
            "restorable": True,
        },
        f"Moved {source_path} to {destination_path}.",
    )


def _undo_move_file(payload: dict) -> tuple[str, bool]:
    source_path = Path(payload["source_path"])
    destination_path = Path(payload["destination_path"])

    if source_path.exists() and not destination_path.exists():
        return "Move already undone.", True
    if not destination_path.exists():
        raise FileNotFoundError(f"Moved file missing at {destination_path}")
    if source_path.exists():
        raise FileExistsError(f"Cannot restore because source already exists: {source_path}")

    source_path.parent.mkdir(parents=True, exist_ok=True)
    destination_path.replace(source_path)
    return f"Restored {source_path}.", True


def _execute_delete_cache(payload: dict) -> tuple[dict, str]:
    target_path = Path(payload["target_path"])
    if not target_path.exists():
        return (
            {
                "target_path": str(target_path),
                "restorable": False,
                "missing_before_delete": True,
            },
            f"Cache path already absent: {target_path}",
        )

    if target_path.is_dir():
        shutil.rmtree(target_path)
    else:
        target_path.unlink()

    return (
        {
            "target_path": str(target_path),
            "restorable": False,
        },
        f"Deleted cache path {target_path}.",
    )


def _undo_delete_cache(payload: dict) -> tuple[str, bool]:
    return (f"Cache deletion at {payload.get('target_path', 'unknown path')} is not restorable.", False)


def _directory_stats(path: Path) -> tuple[int, int]:
    total_bytes = 0
    total_files = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_file():
                            total_bytes += entry.stat().st_size
                            total_files += 1
                        elif entry.is_dir():
                            stack.append(Path(entry.path))
                    except OSError:
                        continue
        except OSError:
            continue
    return total_bytes, total_files


def _create_junction(link_path: Path, target_path: Path) -> None:
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link_path), str(target_path)],
        check=True,
        capture_output=True,
        text=True,
    )


def _remove_junction(link_path: Path) -> None:
    os.rmdir(link_path)


def _execute_relocate_app(payload: dict) -> tuple[dict, str]:
    source_path = Path(payload["source_path"])
    destination_path = Path(payload["destination_path"])
    staging_path = Path(payload.get("staging_path", str(destination_path) + payload.get("staging_suffix", ".sg-staging")))
    backup_path = Path(payload.get("backup_path", str(source_path) + payload.get("backup_suffix", ".sg-backup")))

    if not source_path.exists() or not source_path.is_dir():
        raise FileNotFoundError(f"Source app path not found: {source_path}")
    if destination_path.exists() or staging_path.exists() or backup_path.exists():
        raise FileExistsError("Destination, staging, or backup path already exists.")

    source_stats = _directory_stats(source_path)
    destination_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        shutil.copytree(source_path, staging_path)
        staging_stats = _directory_stats(staging_path)
        if staging_stats != source_stats:
            raise RuntimeError("Copied app payload does not match source contents.")

        source_path.rename(backup_path)
        shutil.move(str(staging_path), str(destination_path))
        _create_junction(source_path, destination_path)
        resolved = Path(os.path.realpath(source_path))
        if resolved != destination_path.resolve():
            raise RuntimeError("Junction validation failed.")
        shutil.rmtree(backup_path)
    except Exception:  # noqa: BLE001
        if source_path.exists() and source_path.is_dir() and os.path.realpath(source_path) == str(destination_path):
            try:
                _remove_junction(source_path)
            except OSError:
                pass
        if backup_path.exists() and not source_path.exists():
            backup_path.rename(source_path)
        if staging_path.exists():
            shutil.rmtree(staging_path, ignore_errors=True)
        if destination_path.exists():
            shutil.rmtree(destination_path, ignore_errors=True)
        raise

    return (
        {
            "source_path": str(source_path),
            "destination_path": str(destination_path),
            "backup_path": str(backup_path),
            "restorable": True,
        },
        f"Relocated app from {source_path} to {destination_path}.",
    )


def _undo_relocate_app(payload: dict) -> tuple[str, bool]:
    source_path = Path(payload["source_path"])
    destination_path = Path(payload["destination_path"])

    if not destination_path.exists():
        raise FileNotFoundError(f"Relocated app payload missing at {destination_path}")
    if source_path.exists() and os.path.realpath(source_path) != str(destination_path):
        raise FileExistsError(f"Cannot restore because source path is occupied: {source_path}")

    if source_path.exists():
        _remove_junction(source_path)
    shutil.move(str(destination_path), str(source_path))
    return f"Restored relocated app back to {source_path}.", True
