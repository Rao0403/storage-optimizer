from __future__ import annotations

import json
import shutil
import sqlite3
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


def _execute_relocate_app(payload: dict) -> tuple[dict, str]:
    raise NotImplementedError("Relocation execution is not available until the relocation feature lands.")


def _undo_relocate_app(payload: dict) -> tuple[str, bool]:
    raise NotImplementedError("Relocation undo is not available until the relocation feature lands.")
