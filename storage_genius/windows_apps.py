from __future__ import annotations

import re
import winreg
from dataclasses import dataclass
from datetime import date


UNINSTALL_LOCATIONS = (
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
)


@dataclass(slots=True)
class InstalledApp:
    display_name: str
    publisher: str | None
    install_location: str | None
    display_icon: str | None
    uninstall_string: str | None
    install_date: date | None


def _read_string(key: winreg.HKEYType, name: str) -> str | None:
    try:
        value, _ = winreg.QueryValueEx(key, name)
    except FileNotFoundError:
        return None
    if isinstance(value, str):
        return value.strip() or None
    return None


def _parse_install_date(raw: str | None) -> date | None:
    if not raw:
        return None
    if not re.fullmatch(r"\d{8}", raw):
        return None
    try:
        return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
    except ValueError:
        return None


def list_installed_apps() -> list[InstalledApp]:
    apps: list[InstalledApp] = []
    seen: set[tuple[str, str | None, str | None]] = set()

    for hive, subkey_path in UNINSTALL_LOCATIONS:
        try:
            root = winreg.OpenKey(hive, subkey_path)
        except FileNotFoundError:
            continue

        index = 0
        while True:
            try:
                child_name = winreg.EnumKey(root, index)
            except OSError:
                break

            index += 1
            try:
                child = winreg.OpenKey(root, child_name)
            except OSError:
                continue

            display_name = _read_string(child, "DisplayName")
            if not display_name:
                continue

            app = InstalledApp(
                display_name=display_name,
                publisher=_read_string(child, "Publisher"),
                install_location=_read_string(child, "InstallLocation"),
                display_icon=_read_string(child, "DisplayIcon"),
                uninstall_string=_read_string(child, "UninstallString"),
                install_date=_parse_install_date(_read_string(child, "InstallDate")),
            )
            key = (app.display_name.lower(), app.publisher, app.uninstall_string)
            if key in seen:
                continue
            seen.add(key)
            apps.append(app)

    return sorted(apps, key=lambda item: item.display_name.lower())
