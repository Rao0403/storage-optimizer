from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _expand_path(value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(value))).resolve()


@dataclass(slots=True)
class CleanupRule:
    name: str
    source_dir: Path
    target_dir: Path
    min_size_mb: int = 500
    keep_recent_hours: int = 24
    include_extensions: list[str] = field(default_factory=list)
    exclude_extensions: list[str] = field(default_factory=list)
    organize_by: str = "month"


@dataclass(slots=True)
class AppAuditConfig:
    unused_days: int = 30
    minimum_install_age_days: int = 45
    ignored_name_fragments: list[str] = field(default_factory=list)
    minimum_candidate_size_mb: int = 500
    score_thresholds: dict[str, int] = field(
        default_factory=lambda: {
            "review_uninstall": 30,
            "likely_active": 65,
        }
    )


@dataclass(slots=True)
class ActivityWatchConfig:
    enabled: bool = False
    base_url: str = "http://localhost:5600"
    lookback_days: int = 30


@dataclass(slots=True)
class HotspotScanConfig:
    roots: list[Path]
    exclude_paths: list[Path] = field(default_factory=list)
    large_file_threshold_mb: int = 250
    max_depth: int = 4
    html_reports_to_keep: int = 10


@dataclass(slots=True)
class DevCleanupToolConfig:
    enabled: bool = True


@dataclass(slots=True)
class DevCleanupConfig:
    enabled: bool = True
    node: DevCleanupToolConfig = field(default_factory=DevCleanupToolConfig)
    python: DevCleanupToolConfig = field(default_factory=DevCleanupToolConfig)
    extra_paths: list[Path] = field(default_factory=list)
    exclude_paths: list[Path] = field(default_factory=list)


@dataclass(slots=True)
class Config:
    database_path: Path
    cleanup_rules: list[CleanupRule]
    app_audit: AppAuditConfig
    report_directory: Path
    hotspot_scan: HotspotScanConfig
    dev_cleanup: DevCleanupConfig
    activitywatch: ActivityWatchConfig


DEFAULT_CONFIG: dict[str, Any] = {
    "database_path": r"%LOCALAPPDATA%\StorageGenius\storage-genius.db",
    "report_directory": r"%USERPROFILE%\Documents\StorageGenius",
    "cleanup_rules": [
        {
            "name": "large-downloads",
            "source_dir": r"%USERPROFILE%\Downloads",
            "target_dir": r"D:\StorageGeniusBackup\DownloadsArchive",
            "min_size_mb": 500,
            "keep_recent_hours": 24,
            "include_extensions": [],
            "exclude_extensions": [".tmp", ".crdownload", ".part"],
            "organize_by": "month",
        }
    ],
    "app_audit": {
        "unused_days": 30,
        "minimum_install_age_days": 45,
        "minimum_candidate_size_mb": 500,
        "score_thresholds": {
            "review_uninstall": 30,
            "likely_active": 65,
        },
        "ignored_name_fragments": [
            "Security Update",
            "Update for",
            "Microsoft Visual C++",
            "Windows SDK",
            "Driver",
            "Redistributable",
        ],
    },
    "hotspot_scan": {
        "roots": [
            r"%USERPROFILE%",
            r"%LOCALAPPDATA%",
            r"%APPDATA%",
            r"%ProgramData%",
            r"%TEMP%",
            r"%SystemRoot%\Temp",
            r"C:\Program Files",
            r"C:\Program Files (x86)",
        ],
        "exclude_paths": [
            r"%LOCALAPPDATA%\StorageGenius",
        ],
        "large_file_threshold_mb": 250,
        "max_depth": 4,
        "html_reports_to_keep": 10,
    },
    "dev_cleanup": {
        "enabled": True,
        "node": {
            "enabled": True,
        },
        "python": {
            "enabled": True,
        },
        "extra_paths": [],
        "exclude_paths": [
            r"%LOCALAPPDATA%\StorageGenius",
        ],
    },
    "activitywatch": {
        "enabled": False,
        "base_url": "http://localhost:5600",
        "lookback_days": 30,
    },
}


def default_config_text() -> str:
    return json.dumps(DEFAULT_CONFIG, indent=2) + "\n"


def _load_cleanup_rule(raw: dict[str, Any]) -> CleanupRule:
    return CleanupRule(
        name=raw["name"],
        source_dir=_expand_path(raw["source_dir"]),
        target_dir=_expand_path(raw["target_dir"]),
        min_size_mb=int(raw.get("min_size_mb", 500)),
        keep_recent_hours=int(raw.get("keep_recent_hours", 24)),
        include_extensions=[str(item).lower() for item in raw.get("include_extensions", [])],
        exclude_extensions=[str(item).lower() for item in raw.get("exclude_extensions", [])],
        organize_by=str(raw.get("organize_by", "month")).lower(),
    )


def _load_hotspot_scan_config(raw: dict[str, Any]) -> HotspotScanConfig:
    return HotspotScanConfig(
        roots=[_expand_path(item) for item in raw.get("roots", DEFAULT_CONFIG["hotspot_scan"]["roots"])],
        exclude_paths=[_expand_path(item) for item in raw.get("exclude_paths", [])],
        large_file_threshold_mb=int(raw.get("large_file_threshold_mb", 250)),
        max_depth=int(raw.get("max_depth", 4)),
        html_reports_to_keep=int(raw.get("html_reports_to_keep", 10)),
    )


def _load_dev_cleanup_config(raw: dict[str, Any]) -> DevCleanupConfig:
    node_raw = raw.get("node", {})
    python_raw = raw.get("python", {})
    return DevCleanupConfig(
        enabled=bool(raw.get("enabled", True)),
        node=DevCleanupToolConfig(enabled=bool(node_raw.get("enabled", True))),
        python=DevCleanupToolConfig(enabled=bool(python_raw.get("enabled", True))),
        extra_paths=[_expand_path(item) for item in raw.get("extra_paths", [])],
        exclude_paths=[_expand_path(item) for item in raw.get("exclude_paths", [])],
    )


def load_config(path: Path) -> Config:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cleanup_rules = [_load_cleanup_rule(item) for item in payload["cleanup_rules"]]
    app_raw = payload.get("app_audit", {})
    app_audit = AppAuditConfig(
        unused_days=int(app_raw.get("unused_days", 30)),
        minimum_install_age_days=int(app_raw.get("minimum_install_age_days", 45)),
        minimum_candidate_size_mb=int(app_raw.get("minimum_candidate_size_mb", 500)),
        score_thresholds={
            "review_uninstall": int(app_raw.get("score_thresholds", {}).get("review_uninstall", 30)),
            "likely_active": int(app_raw.get("score_thresholds", {}).get("likely_active", 65)),
        },
        ignored_name_fragments=[str(item).lower() for item in app_raw.get("ignored_name_fragments", [])],
    )
    hotspot_scan = _load_hotspot_scan_config(payload.get("hotspot_scan", {}))
    dev_cleanup = _load_dev_cleanup_config(payload.get("dev_cleanup", {}))
    activitywatch_raw = payload.get("activitywatch", {})
    activitywatch = ActivityWatchConfig(
        enabled=bool(activitywatch_raw.get("enabled", False)),
        base_url=str(activitywatch_raw.get("base_url", "http://localhost:5600")).rstrip("/"),
        lookback_days=int(activitywatch_raw.get("lookback_days", 30)),
    )
    database_path = _expand_path(payload.get("database_path", DEFAULT_CONFIG["database_path"]))
    report_directory = _expand_path(payload.get("report_directory", DEFAULT_CONFIG["report_directory"]))
    return Config(
        database_path=database_path,
        cleanup_rules=cleanup_rules,
        app_audit=app_audit,
        report_directory=report_directory,
        hotspot_scan=hotspot_scan,
        dev_cleanup=dev_cleanup,
        activitywatch=activitywatch,
    )
