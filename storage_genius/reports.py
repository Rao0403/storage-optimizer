from __future__ import annotations

import html
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .hotspots import HotspotFinding, HotspotScanResult
from .dev_cleanup import DevCacheFinding


def _format_bytes(size_bytes: int) -> str:
    value = float(size_bytes)
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size_bytes} B"


def write_hotspot_report(
    report_directory: Path, result: HotspotScanResult, keep_count: int, queue_summary: dict[str, int] | None = None
) -> Path:
    report_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    report_path = report_directory / f"hotspots-{timestamp}.html"

    grouped: dict[str, list[HotspotFinding]] = defaultdict(list)
    for finding in result.findings:
        grouped[finding.category].append(finding)

    sections: list[str] = []
    for category, items in sorted(grouped.items()):
        rows = []
        for item in items[:25]:
            rows.append(
                "<tr>"
                f"<td>{html.escape(item.item_type)}</td>"
                f"<td>{html.escape(item.path)}</td>"
                f"<td>{_format_bytes(item.size_bytes)}</td>"
                f"<td>{_format_bytes(item.reclaimable_bytes)}</td>"
                f"<td>{html.escape(item.action_type_hint)}</td>"
                f"<td>{html.escape(item.confidence)}</td>"
                "</tr>"
            )
        sections.append(
            f"<section><h2>{html.escape(category.title())}</h2>"
            "<table><thead><tr><th>Type</th><th>Path</th><th>Size</th><th>Predicted savings</th><th>Action hint</th><th>Confidence</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></section>"
        )

    report_path.write_text(
        (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>StorageGenius Hotspot Report</title>"
            "<style>"
            "body{font-family:Segoe UI,Arial,sans-serif;margin:32px;background:#f6f3ea;color:#1e2328;}"
            "h1,h2{font-family:Georgia,serif;}"
            ".meta{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:24px 0;}"
            ".card{background:#fffaf1;border:1px solid #dbcdb7;padding:16px;border-radius:12px;}"
            "table{width:100%;border-collapse:collapse;background:white;margin-bottom:24px;}"
            "th,td{padding:10px;border-bottom:1px solid #e8dece;text-align:left;vertical-align:top;}"
            "th{background:#efe5d3;}"
            "code{font-family:Consolas,monospace;font-size:0.95em;}"
            "</style></head><body>"
            "<h1>StorageGenius Hotspot Report</h1>"
            "<div class='meta'>"
            f"<div class='card'><strong>Roots scanned</strong><br>{len(result.roots_scanned)}</div>"
            f"<div class='card'><strong>Findings</strong><br>{len(result.findings)}</div>"
            f"<div class='card'><strong>Total observed size</strong><br>{_format_bytes(result.total_size_bytes)}</div>"
            f"<div class='card'><strong>Predicted savings</strong><br>{_format_bytes(result.total_reclaimable_bytes)}</div>"
            f"<div class='card'><strong>Pending actions</strong><br>{(queue_summary or {}).get('pending', 0)}</div>"
            f"<div class='card'><strong>Executed actions</strong><br>{(queue_summary or {}).get('executed', 0)}</div>"
            "</div>"
            + "".join(sections)
            + "</body></html>"
        ),
        encoding="utf-8",
    )

    reports = sorted(report_directory.glob("hotspots-*.html"), key=lambda item: item.stat().st_mtime, reverse=True)
    for stale_report in reports[keep_count:]:
        try:
            stale_report.unlink()
        except OSError:
            continue

    return report_path


def write_dev_cache_report(report_directory: Path, findings: list[DevCacheFinding], keep_count: int) -> Path:
    report_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    report_path = report_directory / f"dev-caches-{timestamp}.html"

    rows = []
    total_size_bytes = 0
    for finding in findings:
        total_size_bytes += finding.size_bytes
        rows.append(
            "<tr>"
            f"<td>{html.escape(finding.ecosystem)}</td>"
            f"<td>{html.escape(finding.name)}</td>"
            f"<td>{html.escape(str(finding.path))}</td>"
            f"<td>{_format_bytes(finding.size_bytes)}</td>"
            f"<td>{html.escape(finding.reclaim_method)}</td>"
            f"<td>{html.escape(finding.expected_side_effects)}</td>"
            "</tr>"
        )

    report_path.write_text(
        (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>StorageGenius Developer Cache Report</title>"
            "<style>"
            "body{font-family:Segoe UI,Arial,sans-serif;margin:32px;background:#eef4ea;color:#1f2922;}"
            "h1{font-family:Georgia,serif;}"
            ".card{background:#f8fff4;border:1px solid #cfe0bf;padding:16px;border-radius:12px;display:inline-block;margin:0 12px 24px 0;}"
            "table{width:100%;border-collapse:collapse;background:white;}"
            "th,td{padding:10px;border-bottom:1px solid #dde7d6;text-align:left;vertical-align:top;}"
            "th{background:#d9e8cd;}"
            "</style></head><body>"
            "<h1>StorageGenius Developer Cache Report</h1>"
            f"<div class='card'><strong>Findings</strong><br>{len(findings)}</div>"
            f"<div class='card'><strong>Predicted savings</strong><br>{_format_bytes(total_size_bytes)}</div>"
            "<table><thead><tr><th>Ecosystem</th><th>Name</th><th>Path</th><th>Size</th><th>Reclaim method</th><th>Expected side effects</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>"
            "</body></html>"
        ),
        encoding="utf-8",
    )

    reports = sorted(report_directory.glob("dev-caches-*.html"), key=lambda item: item.stat().st_mtime, reverse=True)
    for stale_report in reports[keep_count:]:
        try:
            stale_report.unlink()
        except OSError:
            continue
    return report_path
