from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .config import DevCleanupConfig


@dataclass(slots=True)
class DevCacheFinding:
    ecosystem: str
    name: str
    path: Path
    size_bytes: int
    reclaim_method: str
    expected_side_effects: str


def _expand(path_value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(path_value))).resolve()


def _safe_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size

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


def _is_excluded(path: Path, excluded: list[Path]) -> bool:
    path_lower = str(path).lower()
    for item in excluded:
        item_lower = str(item).lower()
        if path_lower == item_lower or path_lower.startswith(item_lower + "\\"):
            return True
    return False


def scan_dev_caches(config: DevCleanupConfig) -> list[DevCacheFinding]:
    if not config.enabled:
        return []

    candidates: list[tuple[str, str, Path, str, str]] = []
    if config.node.enabled:
        candidates.extend(
            [
                (
                    "node",
                    "npm-cache",
                    _expand(r"%LOCALAPPDATA%\npm-cache"),
                    "Delete package manager cache contents",
                    "Next install may take longer while the cache is rebuilt.",
                ),
                (
                    "node",
                    "pnpm-store",
                    _expand(r"%LOCALAPPDATA%\pnpm\store"),
                    "Delete PNPM store contents",
                    "PNPM will refetch packages on demand after cleanup.",
                ),
                (
                    "node",
                    "yarn-cache",
                    _expand(r"%LOCALAPPDATA%\Yarn\Cache"),
                    "Delete Yarn cache contents",
                    "Yarn will recreate cache entries during future installs.",
                ),
            ]
        )
    if config.python.enabled:
        candidates.extend(
            [
                (
                    "python",
                    "pip-cache-localappdata",
                    _expand(r"%LOCALAPPDATA%\pip\Cache"),
                    "Delete pip cache contents",
                    "pip will redownload packages when the cache is missing.",
                ),
                (
                    "python",
                    "pip-cache-user",
                    _expand(r"%USERPROFILE%\.cache\pip"),
                    "Delete pip cache contents",
                    "pip will redownload packages when the cache is missing.",
                ),
                (
                    "python",
                    "conda-pkgs-user",
                    _expand(r"%USERPROFILE%\.conda\pkgs"),
                    "Delete Conda package cache",
                    "Conda may need to redownload package archives later.",
                ),
                (
                    "python",
                    "anaconda-pkgs",
                    _expand(r"%USERPROFILE%\anaconda3\pkgs"),
                    "Delete Anaconda package cache",
                    "Anaconda may need to redownload package archives later.",
                ),
            ]
        )

    for extra_path in config.extra_paths:
        candidates.append(
            (
                "custom",
                extra_path.name.lower() or "custom-cache",
                extra_path,
                "Delete configured cache path",
                "The configured tool will recreate cache files when needed.",
            )
        )

    findings: list[DevCacheFinding] = []
    seen_paths: set[str] = set()
    for ecosystem, name, path, reclaim_method, side_effects in candidates:
        normalized = str(path).lower()
        if normalized in seen_paths or _is_excluded(path, config.exclude_paths):
            continue
        seen_paths.add(normalized)
        size_bytes = _safe_size(path)
        if size_bytes <= 0:
            continue
        findings.append(
            DevCacheFinding(
                ecosystem=ecosystem,
                name=name,
                path=path,
                size_bytes=size_bytes,
                reclaim_method=reclaim_method,
                expected_side_effects=side_effects,
            )
        )

    findings.sort(key=lambda item: item.size_bytes, reverse=True)
    return findings


def finding_to_details_json(finding: DevCacheFinding) -> str:
    return json.dumps(
        {
            "ecosystem": finding.ecosystem,
            "reclaim_method": finding.reclaim_method,
            "expected_side_effects": finding.expected_side_effects,
        },
        sort_keys=True,
    )
