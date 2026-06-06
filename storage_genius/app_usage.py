from __future__ import annotations

import codecs
import json
import os
import struct
import urllib.error
import urllib.parse
import urllib.request
import winreg
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import ActivityWatchConfig, AppAuditConfig
from .db import AppInventoryRecord, build_app_id
from .windows_apps import InstalledApp


USER_ASSIST_PATH = r"Software\Microsoft\Windows\CurrentVersion\Explorer\UserAssist"
COMPONENT_NAME_FRAGMENTS = (
    "security update",
    "update for",
    "redistributable",
    "runtime",
    "targeting pack",
    "shared framework",
    "apphost pack",
    "host fx resolver",
    "sdk",
    "extension sdk",
    "contracts",
    "intellisense",
    "bootstrapper",
    "development libraries",
    "standard library",
    "test suite",
    "utility scripts",
    "headers",
    "sources",
    "library",
    "libraries",
    "resource package",
    "package",
    "module",
    "collection",
    "singleton",
    "profiler",
    "verifier",
    "helper",
    "clickonce",
    "desktop target",
    "app certification kit",
    "windows software development kit",
    "tools for .net",
)


@dataclass(slots=True)
class UsageEntry:
    raw_name: str
    normalized_name: str
    run_count: int
    last_run: datetime | None


@dataclass(slots=True)
class ActivityWatchEntry:
    app_name: str
    last_used: datetime


def _decode_rot13(text: str) -> str:
    return codecs.decode(text, "rot_13")


def _filetime_to_datetime(filetime: int) -> datetime | None:
    if filetime <= 0:
        return None
    epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
    return epoch + timedelta(microseconds=filetime / 10)


def _normalize_userassist_name(name: str) -> str:
    decoded = _decode_rot13(name)
    if decoded.startswith("UEME_") and ":" in decoded:
        decoded = decoded.split(":", 1)[1]
    return decoded.replace("\\\\", "\\").strip()


def read_userassist_entries() -> list[UsageEntry]:
    entries: list[UsageEntry] = []
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, USER_ASSIST_PATH)
    except FileNotFoundError:
        return entries

    key_index = 0
    while True:
        try:
            guid_name = winreg.EnumKey(root, key_index)
        except OSError:
            break
        key_index += 1

        try:
            count_key = winreg.OpenKey(root, guid_name + r"\Count")
        except FileNotFoundError:
            continue

        value_index = 0
        while True:
            try:
                value_name, value_data, _ = winreg.EnumValue(count_key, value_index)
            except OSError:
                break
            value_index += 1

            if not isinstance(value_data, bytes) or len(value_data) < 68:
                continue

            run_count = struct.unpack_from("<I", value_data, 4)[0]
            last_run_raw = struct.unpack_from("<Q", value_data, 60)[0]
            normalized_name = _normalize_userassist_name(value_name)
            entries.append(
                UsageEntry(
                    raw_name=value_name,
                    normalized_name=normalized_name,
                    run_count=run_count,
                    last_run=_filetime_to_datetime(last_run_raw),
                )
            )

    deduped: dict[str, UsageEntry] = {}
    for entry in entries:
        existing = deduped.get(entry.normalized_name.lower())
        if existing is None:
            deduped[entry.normalized_name.lower()] = entry
            continue
        if (entry.last_run or datetime.min.replace(tzinfo=timezone.utc)) > (
            existing.last_run or datetime.min.replace(tzinfo=timezone.utc)
        ):
            deduped[entry.normalized_name.lower()] = entry
    return list(deduped.values())


def _path_candidate(raw_value: str | None) -> Path | None:
    if not raw_value:
        return None
    trimmed = raw_value.split(",", 1)[0].strip().strip('"')
    if not trimmed:
        return None
    path = Path(trimmed)
    if path.drive or str(path).startswith("\\"):
        return path
    return None


def _safe_path_size(path: Path | None) -> int | None:
    if path is None or not path.exists() or not path.is_dir():
        return None
    total = 0
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
                            total += entry.stat().st_size
                        elif entry.is_dir():
                            stack.append(Path(entry.path))
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def _match_last_used(app: InstalledApp, usage_entries: list[UsageEntry]) -> tuple[datetime | None, str | None]:
    install_location = _path_candidate(app.install_location)
    display_icon = _path_candidate(app.display_icon)
    display_name = app.display_name.lower()
    best: tuple[datetime | None, str | None] = (None, None)

    for entry in usage_entries:
        normalized_lower = entry.normalized_name.lower()
        entry_path = _path_candidate(entry.normalized_name)
        reason: str | None = None

        if install_location and entry_path:
            try:
                entry_path.relative_to(install_location)
                reason = "install-location"
            except ValueError:
                pass

        if reason is None and display_icon and entry_path and display_icon.name.lower() == entry_path.name.lower():
            reason = "display-icon"

        if reason is None and display_name and display_name in normalized_lower:
            reason = "display-name"

        if reason is None:
            continue

        if best[0] is None or (entry.last_run and entry.last_run > best[0]):
            best = (entry.last_run, reason)

    return best


def should_ignore_app(app: InstalledApp, config: AppAuditConfig, today: datetime) -> bool:
    name = app.display_name.lower()
    if any(fragment in name for fragment in config.ignored_name_fragments):
        return True
    if app.install_date is None:
        return False
    age_days = (today.date() - app.install_date).days
    return age_days < config.minimum_install_age_days


def _read_startup_markers() -> list[str]:
    startup_entries: list[str] = []
    run_keys = (
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"),
    )
    for hive, subkey in run_keys:
        try:
            key = winreg.OpenKey(hive, subkey)
        except FileNotFoundError:
            continue
        index = 0
        while True:
            try:
                _, value, _ = winreg.EnumValue(key, index)
            except OSError:
                break
            index += 1
            if isinstance(value, str):
                startup_entries.append(value.lower())
    return startup_entries


def _parse_iso8601(value: str) -> datetime | None:
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def fetch_activitywatch_entries(config: ActivityWatchConfig, observed_at: datetime) -> list[ActivityWatchEntry]:
    if not config.enabled:
        return []

    try:
        with urllib.request.urlopen(f"{config.base_url}/api/0/buckets/", timeout=5) as response:
            buckets = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return []

    cutoff = observed_at - timedelta(days=config.lookback_days)
    matches: dict[str, datetime] = {}

    for bucket_id, metadata in buckets.items():
        bucket_type = str(metadata.get("type", "")).lower()
        if "currentwindow" not in bucket_type and "aw-watcher-window" not in bucket_id.lower():
            continue
        try:
            with urllib.request.urlopen(
                f"{config.base_url}/api/0/buckets/{urllib.parse.quote(bucket_id, safe='')}/events",
                timeout=10,
            ) as response:
                events = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            continue

        for event in events[-2000:]:
            timestamp = _parse_iso8601(str(event.get("timestamp", "")))
            if timestamp is None or timestamp < cutoff:
                continue
            app_name = str(event.get("data", {}).get("app", "")).strip()
            if not app_name:
                continue
            current = matches.get(app_name.lower())
            if current is None or timestamp > current:
                matches[app_name.lower()] = timestamp

    return [ActivityWatchEntry(app_name=name, last_used=timestamp) for name, timestamp in matches.items()]


def _match_activitywatch_last_used(app: InstalledApp, entries: list[ActivityWatchEntry]) -> datetime | None:
    display_name = app.display_name.lower()
    best: datetime | None = None
    for entry in entries:
        event_name = entry.app_name.lower()
        if display_name in event_name or event_name in display_name:
            if best is None or entry.last_used > best:
                best = entry.last_used
    return best


def _has_startup_marker(app: InstalledApp, startup_markers: list[str]) -> bool:
    display_name = app.display_name.lower()
    install_location = (app.install_location or "").lower()
    display_icon = (app.display_icon or "").lower()
    return any(display_name in marker or (install_location and install_location in marker) or (display_icon and display_icon in marker) for marker in startup_markers)


def _score_app(
    app: InstalledApp,
    last_used: datetime | None,
    last_used_source: str | None,
    startup_marked: bool,
    estimated_size_bytes: int | None,
    config: AppAuditConfig,
    observed_at: datetime,
) -> tuple[int, str, str]:
    score = 20 if is_likely_user_facing_app(app.display_name) else 0
    confidence = "low"

    if last_used is not None:
        age_days = (observed_at - last_used).days
        if age_days <= 7:
            score += 55
        elif age_days <= 30:
            score += 40
        elif age_days <= 90:
            score += 22
        elif age_days <= 180:
            score += 8
        else:
            score -= 12
    else:
        score -= 10

    if startup_marked:
        score += 10
    if last_used_source == "activitywatch":
        score += 10
        confidence = "high"
    elif last_used_source in {"userassist-install-location", "userassist-display-icon"}:
        confidence = "medium"
    elif last_used_source == "userassist-display-name":
        confidence = "low"

    if estimated_size_bytes and estimated_size_bytes >= config.minimum_candidate_size_mb * 1024 * 1024 and last_used is None:
        score -= 5

    score = max(0, min(score, 100))
    review_uninstall_threshold = config.score_thresholds.get("review_uninstall", 30)
    likely_active_threshold = config.score_thresholds.get("likely_active", 65)
    size_gate = estimated_size_bytes is not None and estimated_size_bytes >= config.minimum_candidate_size_mb * 1024 * 1024

    if score <= review_uninstall_threshold and size_gate and is_likely_user_facing_app(app.display_name):
        candidate_action = "review_uninstall"
    elif score >= likely_active_threshold:
        candidate_action = "likely_active"
    else:
        candidate_action = "review"

    return score, confidence, candidate_action


def build_inventory_records(
    apps: list[InstalledApp],
    usage_entries: list[UsageEntry],
    activitywatch_entries: list[ActivityWatchEntry],
    config: AppAuditConfig,
    observed_at: datetime,
) -> list[AppInventoryRecord]:
    records: list[AppInventoryRecord] = []
    startup_markers = _read_startup_markers()
    size_cache: dict[str, int | None] = {}

    for app in apps:
        if should_ignore_app(app, config, observed_at):
            continue

        userassist_last_used, match_reason = _match_last_used(app, usage_entries)
        activitywatch_last_used = _match_activitywatch_last_used(app, activitywatch_entries)
        if activitywatch_last_used and (userassist_last_used is None or activitywatch_last_used > userassist_last_used):
            last_used = activitywatch_last_used
            last_used_source = "activitywatch"
        else:
            last_used = userassist_last_used
            last_used_source = f"userassist-{match_reason}" if userassist_last_used and match_reason else None

        install_location = _path_candidate(app.install_location)
        size_key = str(install_location).lower() if install_location else ""
        if size_key and size_key not in size_cache:
            size_cache[size_key] = _safe_path_size(install_location)
        estimated_installed_size_bytes = size_cache.get(size_key)
        startup_marked = _has_startup_marker(app, startup_markers)
        usage_score, usage_confidence, candidate_action = _score_app(
            app,
            last_used,
            last_used_source,
            startup_marked,
            estimated_installed_size_bytes,
            config,
            observed_at,
        )

        records.append(
            AppInventoryRecord(
                app_id=build_app_id(app.display_name, app.publisher, app.uninstall_string),
                display_name=app.display_name,
                publisher=app.publisher,
                install_location=app.install_location,
                display_icon=app.display_icon,
                uninstall_string=app.uninstall_string,
                install_date=app.install_date.isoformat() if app.install_date else None,
                last_used=last_used.isoformat() if last_used else None,
                last_seen=observed_at.isoformat(),
                match_reason=match_reason,
                usage_score=usage_score,
                usage_confidence=usage_confidence,
                last_used_source=last_used_source,
                candidate_action=candidate_action,
                estimated_installed_size_bytes=estimated_installed_size_bytes,
            )
        )

    return records


def is_likely_user_facing_app(display_name: str) -> bool:
    normalized = display_name.lower().strip()
    if not normalized:
        return False
    if normalized.startswith(("vs_", "icecap_", "vcpp_", "winrt ")):
        return False
    if normalized.startswith(("microsoft .net", "microsoft asp.net", "visual c++ library")):
        return False
    return not any(fragment in normalized for fragment in COMPONENT_NAME_FRAGMENTS)
