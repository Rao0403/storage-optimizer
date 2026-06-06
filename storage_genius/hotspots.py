from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .config import HotspotScanConfig


ARCHIVE_SUFFIXES = {".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".iso"}
INSTALLER_SUFFIXES = {".msi", ".exe", ".iso"}
TEMP_SEGMENTS = {"temp", "tmp", "cache"}
DEVELOPER_CACHE_SEGMENTS = {"npm-cache", ".pnpm-store", "pnpm", "yarn", "pip", ".conda", "conda", "pkgs"}
APP_CACHE_SEGMENTS = {".cache", "cache", "caches", "code cache", "shadercache", "gpucache"}
PROTECTED_NAMES = {"windows", "recovery", "$recycle.bin", "system volume information"}
DESCENDIBLE_NAMES = {
    "downloads",
    "desktop",
    "documents",
    "videos",
    "pictures",
    "music",
    ".cache",
    "cache",
    "caches",
    "temp",
    "tmp",
    "npm-cache",
    "pnpm",
    "yarn",
    "pip",
    ".conda",
    "conda",
    "pkgs",
}


@dataclass(slots=True)
class HotspotFinding:
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
class HotspotScanResult:
    roots_scanned: list[str]
    findings: list[HotspotFinding]
    total_size_bytes: int
    total_reclaimable_bytes: int


def _depth_from_root(root: Path, path: Path) -> int:
    try:
        return len(path.relative_to(root).parts)
    except ValueError:
        return 0


def _is_excluded(path: Path, excluded: list[Path]) -> bool:
    path_lower = str(path).lower()
    for candidate in excluded:
        candidate_lower = str(candidate).lower()
        if path_lower == candidate_lower or path_lower.startswith(candidate_lower + "\\"):
            return True
    return False


def _safe_stat(path: Path):
    try:
        return path.stat()
    except OSError:
        return None


def _directory_size(path: Path, max_depth: int, current_depth: int = 0) -> tuple[int, int]:
    total_bytes = 0
    file_count = 0
    try:
        children = list(path.iterdir())
    except OSError:
        return 0, 0

    for child in children:
        stat_result = _safe_stat(child)
        if stat_result is None:
            continue
        if child.is_file():
            total_bytes += stat_result.st_size
            file_count += 1
        elif child.is_dir() and current_depth < max_depth:
            child_bytes, child_files = _directory_size(child, max_depth, current_depth + 1)
            total_bytes += child_bytes
            file_count += child_files
    return total_bytes, file_count


def _classify_file(path: Path) -> tuple[str, str, str]:
    lower_name = path.name.lower()
    suffix = path.suffix.lower()
    path_text = str(path).lower()

    if "downloads" in path_text and suffix in INSTALLER_SUFFIXES:
        return "downloads/installers", "move_file", "high"
    if suffix in ARCHIVE_SUFFIXES:
        return "archives/isos", "move_file", "high"
    if suffix in {".mp4", ".mov", ".mkv", ".psd", ".blend", ".mp3", ".wav", ".flac"}:
        return "large personal files", "move_file", "medium"
    if any(segment in path_text for segment in ("cache", "temp", "tmp")):
        return "temp files", "delete_cache", "medium"
    if any(segment in lower_name for segment in ("setup", "installer")):
        return "downloads/installers", "move_file", "medium"
    return "large personal files", "move_file", "low"


def _classify_directory(path: Path) -> tuple[str, str, str] | None:
    lower_parts = {part.lower() for part in path.parts}
    lower_name = path.name.lower()

    if lower_name in PROTECTED_NAMES:
        return None
    if lower_name == "downloads":
        return "downloads/installers", "move_file", "medium"
    if lower_name in DEVELOPER_CACHE_SEGMENTS or lower_parts & DEVELOPER_CACHE_SEGMENTS:
        return "developer caches", "delete_cache", "high"
    if lower_name in APP_CACHE_SEGMENTS or lower_parts & APP_CACHE_SEGMENTS:
        return "app caches", "delete_cache", "medium"
    if lower_name in TEMP_SEGMENTS or lower_parts & TEMP_SEGMENTS:
        return "temp files", "delete_cache", "medium"
    if any(part.lower() == "program files" for part in path.parts[:-1]) or lower_name in {
        "applications",
        "steamapps",
        "unity",
    }:
        return "app install folders", "review", "low"
    return None


def _build_details(**payload: object) -> str:
    return json.dumps(payload, sort_keys=True)


def _should_descend(root: Path, entry: Path, depth: int, max_depth: int) -> bool:
    if depth >= max_depth:
        return False

    lower_name = entry.name.lower()
    root_name = root.name.lower()
    if lower_name in DESCENDIBLE_NAMES:
        return True
    if root_name in {"program files", "program files (x86)", "programdata"}:
        return False
    if root_name in {"appdata", "local", "roaming"}:
        return lower_name in DESCENDIBLE_NAMES
    return depth <= 2


def scan_hotspots(config: HotspotScanConfig) -> HotspotScanResult:
    roots_scanned: list[str] = []
    findings: list[HotspotFinding] = []
    totals = {"size": 0, "reclaimable": 0}
    threshold_bytes = config.large_file_threshold_mb * 1024 * 1024

    for root in config.roots:
        if not root.exists() or _is_excluded(root, config.exclude_paths):
            continue
        roots_scanned.append(str(root))
        _scan_path(
            root=root,
            path=root,
            config=config,
            findings=findings,
            threshold_bytes=threshold_bytes,
            total_tracker=totals,
        )

    findings.sort(key=lambda item: item.size_bytes, reverse=True)
    return HotspotScanResult(
        roots_scanned=roots_scanned,
        findings=findings,
        total_size_bytes=totals["size"],
        total_reclaimable_bytes=totals["reclaimable"],
    )


def _scan_path(
    root: Path,
    path: Path,
    config: HotspotScanConfig,
    findings: list[HotspotFinding],
    threshold_bytes: int,
    total_tracker: dict[str, int],
) -> None:
    try:
        children = list(path.iterdir())
    except OSError:
        return

    for entry in children:
        if _is_excluded(entry, config.exclude_paths):
            continue

        depth = _depth_from_root(root, entry)
        if depth > config.max_depth:
            continue

        if entry.is_file():
            stat_result = _safe_stat(entry)
            if stat_result is None or stat_result.st_size < threshold_bytes:
                continue
            category, action_hint, confidence = _classify_file(entry)
            reclaimable = stat_result.st_size if action_hint in {"move_file", "delete_cache"} else 0
            finding = HotspotFinding(
                root_path=str(root),
                path=str(entry),
                item_type="file",
                category=category,
                size_bytes=stat_result.st_size,
                reclaimable_bytes=reclaimable,
                action_type_hint=action_hint,
                confidence=confidence,
                details_json=_build_details(suffix=entry.suffix.lower(), depth=depth),
            )
            findings.append(finding)
            total_tracker["size"] += finding.size_bytes
            total_tracker["reclaimable"] += finding.reclaimable_bytes
            continue

        if not entry.is_dir():
            continue

        classification = _classify_directory(entry)
        if classification is not None:
            category, action_hint, confidence = classification
            size_bytes, file_count = _directory_size(entry, config.max_depth - depth)
            if size_bytes >= threshold_bytes:
                reclaimable = size_bytes if action_hint in {"move_file", "delete_cache"} else 0
                finding = HotspotFinding(
                    root_path=str(root),
                    path=str(entry),
                    item_type="directory",
                    category=category,
                    size_bytes=size_bytes,
                    reclaimable_bytes=reclaimable,
                    action_type_hint=action_hint,
                    confidence=confidence,
                    details_json=_build_details(file_count=file_count, depth=depth),
                )
                findings.append(finding)
                total_tracker["size"] += finding.size_bytes
                total_tracker["reclaimable"] += finding.reclaimable_bytes
            if category not in {"downloads/installers", "app caches"}:
                continue

        if _should_descend(root, entry, depth, config.max_depth):
            _scan_path(root, entry, config, findings, threshold_bytes, total_tracker)
