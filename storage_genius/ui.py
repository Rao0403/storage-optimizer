from __future__ import annotations

import json
import shutil
from contextlib import redirect_stderr, redirect_stdout
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .app_usage import is_likely_user_facing_app
from .cli import _run_app_scan, _run_hotspot_scan, _scan_dev_caches, _scan_relocation
from .config import load_config
from .db import (
    fetch_app_report_rows,
    fetch_queue_summary,
    list_action_queue_records,
    open_database,
)
from .queue_actions import approve_action, dismiss_action, execute_approved_actions, undo_action


WEB_ROOT = Path(__file__).with_name("web")


def _parse_json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _queue_row_to_dict(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "action_type": row["action_type"],
        "state": row["state"],
        "human_summary": row["human_summary"],
        "payload": _parse_json(row["payload_json"], {}),
        "rollback_payload": _parse_json(row["rollback_payload_json"], None),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "approved_at": row["approved_at"],
        "executed_at": row["executed_at"],
        "undone_at": row["undone_at"],
        "failure_message": row["failure_message"],
    }


def _latest_scan(connection, scan_type: str) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM scan_runs WHERE scan_type = ? ORDER BY id DESC LIMIT 1",
        (scan_type,),
    ).fetchone()
    if row is None:
        return None
    payload = dict(row)
    payload["roots"] = _parse_json(payload.pop("roots_json"), [])
    return payload


def _latest_findings(connection, scan_type: str, category: str | None = None) -> list[dict[str, Any]]:
    query = """
        SELECT h.*, s.started_at AS scan_started_at, s.report_path
        FROM hotspot_findings h
        JOIN scan_runs s ON s.id = h.scan_run_id
        WHERE s.id = (
          SELECT id FROM scan_runs WHERE scan_type = ? ORDER BY id DESC LIMIT 1
        )
    """
    parameters: list[Any] = [scan_type]
    if category is not None:
        query += " AND h.category = ?"
        parameters.append(category)
    query += " ORDER BY h.reclaimable_bytes DESC, h.size_bytes DESC"
    rows = connection.execute(query, parameters).fetchall()

    findings = []
    for row in rows:
        item = dict(row)
        item["details"] = _parse_json(item.pop("details_json"), {})
        findings.append(item)
    return findings


def _drive_status(root: str) -> dict[str, Any] | None:
    try:
        usage = shutil.disk_usage(root)
    except OSError:
        return None
    return {
        "root": root,
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "free_percent": round(usage.free / usage.total * 100, 1) if usage.total else 0,
    }


def _build_summary(connection) -> dict[str, Any]:
    latest_hotspot = _latest_scan(connection, "hotspot")
    latest_dev_cache = _latest_scan(connection, "dev_caches")
    latest_apps = connection.execute("SELECT MAX(last_seen) AS last_seen FROM app_inventory").fetchone()["last_seen"]
    queue_summary = fetch_queue_summary(connection)
    app_count = connection.execute("SELECT COUNT(*) AS item_count FROM app_inventory").fetchone()["item_count"]
    review_count = connection.execute(
        "SELECT COUNT(*) AS item_count FROM app_inventory WHERE candidate_action = 'review_uninstall'"
    ).fetchone()["item_count"]

    categories = connection.execute(
        """
        SELECT category, SUM(size_bytes) AS size_bytes, SUM(reclaimable_bytes) AS reclaimable_bytes,
               COUNT(*) AS finding_count
        FROM hotspot_findings
        WHERE scan_run_id = (
          SELECT id FROM scan_runs WHERE scan_type = 'hotspot' ORDER BY id DESC LIMIT 1
        )
        GROUP BY category
        ORDER BY size_bytes DESC
        """
    ).fetchall()

    return {
        "drives": [drive for drive in (_drive_status("C:\\"), _drive_status("D:\\")) if drive],
        "queue": queue_summary,
        "apps": {"count": app_count, "review_count": review_count, "last_seen": latest_apps},
        "latest_scans": {
            "hotspot": latest_hotspot,
            "dev_caches": latest_dev_cache,
            "relocation": _latest_scan(connection, "relocation"),
        },
        "hotspots": {
            "finding_count": sum(row["finding_count"] for row in categories),
            "observed_bytes": latest_hotspot["total_size_bytes"] if latest_hotspot else 0,
            "reclaimable_bytes": latest_hotspot["total_reclaimable_bytes"] if latest_hotspot else 0,
            "categories": [dict(row) for row in categories],
        },
    }


def _app_payload(connection) -> list[dict[str, Any]]:
    rows = fetch_app_report_rows(connection)
    return [
        {
            "app_id": row["app_id"],
            "display_name": row["display_name"],
            "publisher": row["publisher"],
            "install_location": row["install_location"],
            "last_used": row["last_used"],
            "last_used_source": row["last_used_source"],
            "usage_score": row["usage_score"],
            "usage_confidence": row["usage_confidence"],
            "candidate_action": row["candidate_action"],
            "estimated_installed_size_bytes": row["estimated_installed_size_bytes"] or 0,
        }
        for row in rows
        if is_likely_user_facing_app(row["display_name"])
    ]


def build_dashboard_payload(config_path: Path) -> dict[str, Any]:
    config = load_config(config_path)
    connection = open_database(config.database_path)
    try:
        return {
            "summary": _build_summary(connection),
            "hotspots": _latest_findings(connection, "hotspot"),
            "dev_caches": _latest_findings(connection, "dev_caches", "developer caches"),
            "relocation": _latest_findings(connection, "relocation", "app relocation candidates"),
            "apps": _app_payload(connection),
            "queue": [_queue_row_to_dict(row) for row in list_action_queue_records(connection)],
        }
    finally:
        connection.close()


def _run_scan(function, *arguments) -> dict[str, Any]:
    output = StringIO()
    errors = StringIO()
    with redirect_stdout(output), redirect_stderr(errors):
        exit_code = function(*arguments)
    return {
        "exit_code": exit_code,
        "output": output.getvalue().strip(),
        "error_output": errors.getvalue().strip(),
    }


def _run_app_inventory_scan(config_path: Path) -> int:
    exit_code, _, _ = _run_app_scan(config_path)
    return exit_code


class StorageGeniusHandler(SimpleHTTPRequestHandler):
    server_version = "StorageGenius/2.0"

    def __init__(self, request, client_address, server):
        super().__init__(request, client_address, server, directory=str(WEB_ROOT))

    @property
    def config_path(self) -> Path:
        return self.server.config_path  # type: ignore[attr-defined]

    def log_message(self, format: str, *args) -> None:
        return

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        return payload

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            return super().do_GET()

        try:
            payload = build_dashboard_payload(self.config_path)
            if path == "/api/dashboard":
                self._send_json({"ok": True, **payload})
                return
            if path == "/api/summary":
                self._send_json({"ok": True, "summary": payload["summary"]})
                return
            if path == "/api/findings":
                self._send_json({"ok": True, "hotspots": payload["hotspots"]})
                return
            if path == "/api/dev-caches":
                self._send_json({"ok": True, "dev_caches": payload["dev_caches"]})
                return
            if path == "/api/apps":
                self._send_json({"ok": True, "apps": payload["apps"]})
                return
            if path == "/api/queue":
                self._send_json({"ok": True, "queue": payload["queue"], "summary": payload["summary"]["queue"]})
                return
            self._send_json({"ok": False, "error": "Unknown API route."}, status=404)
        except Exception as exc:  # noqa: BLE001
            self._send_json({"ok": False, "error": str(exc)}, status=500)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            self._send_json({"ok": False, "error": "POST is only supported for API routes."}, status=404)
            return

        try:
            if path in {"/api/scans/hotspots", "/api/scans/dev-caches", "/api/scans/apps", "/api/scans/relocation"}:
                scan_functions = {
                    "/api/scans/hotspots": (_run_hotspot_scan, (self.config_path, True, False)),
                    "/api/scans/dev-caches": (_scan_dev_caches, (self.config_path, True, False)),
                    "/api/scans/apps": (_run_app_inventory_scan, (self.config_path,)),
                    "/api/scans/relocation": (_scan_relocation, (self.config_path, True, False)),
                }
                function, arguments = scan_functions[path]
                result = _run_scan(function, *arguments)
                if result["exit_code"] != 0:
                    self._send_json({"ok": False, **result}, status=500)
                    return
                self._send_json({"ok": True, "result": result, "dashboard": build_dashboard_payload(self.config_path)})
                return

            parts = [part for part in path.split("/") if part]
            if len(parts) == 4 and parts[0:2] == ["api", "queue"]:
                action_id = int(parts[2])
                command = parts[3]
                config = load_config(self.config_path)
                connection = open_database(config.database_path)
                try:
                    actions = {
                        "approve": approve_action,
                        "dismiss": dismiss_action,
                        "undo": undo_action,
                    }
                    if command not in actions:
                        raise ValueError(f"Unsupported queue action: {command}")
                    result = actions[command](connection, action_id)
                finally:
                    connection.close()
                self._send_json({"ok": True, "message": result.message, "dashboard": build_dashboard_payload(self.config_path)})
                return

            if path == "/api/queue/execute":
                config = load_config(self.config_path)
                connection = open_database(config.database_path)
                try:
                    results = execute_approved_actions(connection)
                    messages = [{"action_id": item.action_id, "state": item.state, "message": item.message} for item in results]
                finally:
                    connection.close()
                self._send_json({"ok": True, "results": messages, "dashboard": build_dashboard_payload(self.config_path)})
                return

            self._send_json({"ok": False, "error": "Unknown API route."}, status=404)
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)
        except Exception as exc:  # noqa: BLE001
            self._send_json({"ok": False, "error": str(exc)}, status=500)


class StorageGeniusServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, server_address, config_path: Path):
        handler = partial(StorageGeniusHandler)
        super().__init__(server_address, handler)
        self.config_path = config_path


def run_ui(config_path: Path, host: str = "127.0.0.1", port: int = 8765) -> int:
    if not WEB_ROOT.exists():
        raise FileNotFoundError(f"UI assets are missing: {WEB_ROOT}")
    server = StorageGeniusServer((host, port), config_path)
    url_host = "127.0.0.1" if host in {"localhost", "::1"} else host
    print(f"StorageGenius dashboard running at http://{url_host}:{server.server_port}")
    print("Press Ctrl+C to stop the local server.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping StorageGenius dashboard.")
    finally:
        server.server_close()
    return 0
