from __future__ import annotations

import codecs
import struct
import winreg
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import AppAuditConfig
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


def build_inventory_records(
    apps: list[InstalledApp], usage_entries: list[UsageEntry], config: AppAuditConfig, observed_at: datetime
) -> list[AppInventoryRecord]:
    records: list[AppInventoryRecord] = []

    for app in apps:
        if should_ignore_app(app, config, observed_at):
            continue

        last_used, match_reason = _match_last_used(app, usage_entries)
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
