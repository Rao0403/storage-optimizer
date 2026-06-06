from __future__ import annotations

import os
import winreg
from dataclasses import dataclass
from pathlib import Path

from .config import RelocationConfig
from .windows_apps import InstalledApp


PROTECTED_ROOTS = {
    r"c:\windows",
    r"c:\program files\common files",
    r"c:\program files (x86)\common files",
    r"c:\program files\windowsapps",
}


@dataclass(slots=True)
class RelocationCandidate:
    app_name: str
    publisher: str | None
    install_location: Path
    size_bytes: int
    destination_path: Path
    risk_flags: list[str]
    confidence: str


def _safe_directory_stats(path: Path) -> tuple[int, int]:
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


def _service_paths() -> list[str]:
    results: list[str] = []
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services")
    except FileNotFoundError:
        return results

    index = 0
    while True:
        try:
            service_name = winreg.EnumKey(root, index)
        except OSError:
            break
        index += 1
        try:
            service_key = winreg.OpenKey(root, service_name)
            image_path, _ = winreg.QueryValueEx(service_key, "ImagePath")
        except (FileNotFoundError, OSError):
            continue
        if isinstance(image_path, str):
            cleaned = image_path.strip().strip('"').split(" ", 1)[0].lower()
            results.append(cleaned)
    return results


def _is_service_backed(path: Path, service_paths: list[str]) -> bool:
    path_lower = str(path).lower()
    return any(service_path.startswith(path_lower) for service_path in service_paths)


def _is_supported_install_location(path: Path, config: RelocationConfig) -> tuple[bool, list[str]]:
    lower = str(path).lower()
    risk_flags: list[str] = []
    if not path.exists() or not path.is_dir():
        return False, ["missing-install-location"]
    if path.drive.upper() != "C:":
        return False, ["not-on-c-drive"]
    if any(lower == root or lower.startswith(root + "\\") for root in PROTECTED_ROOTS):
        return False, ["protected-path"]
    if any(lower == str(item).lower() or lower.startswith(str(item).lower() + "\\") for item in config.exclude_paths):
        return False, ["excluded-path"]
    if "windowsapps" in lower:
        return False, ["windows-store-path"]
    if lower.endswith(config.backup_suffix.lower()) or lower.endswith(config.staging_suffix.lower()):
        return False, ["managed-suffix"]
    if "program files" in lower:
        risk_flags.append("program-files-path")
    return True, risk_flags


def scan_relocation_candidates(apps: list[InstalledApp], config: RelocationConfig) -> list[RelocationCandidate]:
    if not config.enabled:
        return []

    service_paths = _service_paths()
    minimum_size_bytes = config.minimum_size_gb * 1024 * 1024 * 1024
    candidates: list[RelocationCandidate] = []
    seen_paths: set[str] = set()

    for app in apps:
        install_location = Path(app.install_location).resolve() if app.install_location else None
        if install_location is None:
            continue
        normalized_path = str(install_location).lower()
        if normalized_path in seen_paths:
            continue
        seen_paths.add(normalized_path)

        supported, risk_flags = _is_supported_install_location(install_location, config)
        if not supported:
            continue
        if app.publisher and app.publisher.lower() in config.exclude_publishers:
            continue
        if _is_service_backed(install_location, service_paths):
            continue

        size_bytes, file_count = _safe_directory_stats(install_location)
        if size_bytes < minimum_size_bytes or file_count == 0:
            continue

        destination_path = config.target_root / install_location.name
        candidates.append(
            RelocationCandidate(
                app_name=app.display_name,
                publisher=app.publisher,
                install_location=install_location,
                size_bytes=size_bytes,
                destination_path=destination_path,
                risk_flags=risk_flags,
                confidence="high" if not risk_flags else "medium",
            )
        )

    candidates.sort(key=lambda item: item.size_bytes, reverse=True)
    return candidates
