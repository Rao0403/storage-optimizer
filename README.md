# StorageGenius

Windows-first storage automation for people who let `C:` fill up and only notice when the machine starts complaining.

The first version focuses on two jobs:

1. Move large files out of `Downloads` automatically based on configurable rules.
2. Track installed apps and flag the ones that appear unused for 30+ days.

V2 starts by adding a dedicated hotspot scanner for `C:` so later cleanup and relocation decisions are based on measured storage pressure instead of guesswork.

## Why this shape

The large-file cleanup is deterministic and safe enough to automate now.

The uninstall side is intentionally advisory in v1. Windows uninstall metadata is messy, and app-usage tracking is never perfect. This project records signals, highlights candidates, and lets you decide what to remove instead of deleting software blindly.

## Features in v1

- JSON config with one or more cleanup rules.
- Safe `--dry-run` preview for file moves.
- Destination folder organization by month or extension.
- SQLite inventory for moved files and installed apps.
- Installed app inventory from the Windows uninstall registry.
- Usage signals from the current user's `UserAssist` registry data.
- Unused-app report based on your configured day threshold.

## V2 feature 1

- `scan-hotspots` command focused on `C:` pressure.
- Classification for large personal files, installers, archives, temp files, app caches, developer caches, and app install folders.
- SQLite history for hotspot scans and findings.
- Static HTML report generation under `report_directory`.
- Predicted savings and action hints for each finding.

## V2 feature 2

- SQLite-backed action queue with explicit states: `pending`, `approved`, `executed`, `failed`, `rolled_back`, `dismissed`.
- Execution and undo logs for every queued action.
- Queue CLI for review, approval, execution, dismissal, and undo.
- Reversible `move_file` actions are fully supported now; `delete_cache` and `relocate_app` action types are reserved for later feature slices.
- Hotspot HTML reports now include queue state counts.

## V2 feature 3

- `scan-dev-caches` and `recommend dev-caches` commands for Node and Python cache cleanup.
- Detection for npm, pnpm, Yarn, pip, Conda, and Anaconda package caches.
- Static HTML report for developer-cache findings with reclaim methods and side effects.
- Optional queue creation for `delete_cache` actions so cleanup still follows review and approval.

## V2 feature 4

- Scored app usage model instead of a single last-used heuristic.
- Optional ActivityWatch enrichment through the local REST API.
- Estimated install size, usage confidence, and candidate action stored with each app.
- `report apps` groups results into uninstall candidates, uncertain inactive apps, and likely active apps.

## Project layout

```text
storage_genius/
  __main__.py
  cli.py
  config.py
  db.py
  file_rules.py
  app_usage.py
  windows_apps.py
tests/
```

## Quick start

1. Create a config:

```powershell
python -m storage_genius init-config
```

2. Edit `config.json` and confirm the backup path points to a real folder on `D:`.

3. Preview the first cleanup run:

```powershell
python -m storage_genius --config config.json cleanup --dry-run
```

4. Scan apps and usage:

```powershell
python -m storage_genius --config config.json scan-apps
```

5. See unused-app candidates:

```powershell
python -m storage_genius --config config.json report
```

6. Run the full cycle:

```powershell
python -m storage_genius --config config.json run-once --dry-run
```

Remove `--dry-run` once the moves look correct.

7. Generate a hotspot report:

```powershell
python -m storage_genius --config config.json scan-hotspots
```

8. Generate JSON instead:

```powershell
python -m storage_genius --config config.json scan-hotspots --json
```

9. Inspect queued actions:

```powershell
python -m storage_genius --config config.json queue list
python -m storage_genius --config config.json queue show 1
python -m storage_genius --config config.json queue approve 1
python -m storage_genius --config config.json queue execute
python -m storage_genius --config config.json queue undo 1
```

10. Scan and queue developer-cache recommendations:

```powershell
python -m storage_genius --config config.json scan-dev-caches
python -m storage_genius --config config.json recommend dev-caches --enqueue
```

11. Refresh scored app usage and report it:

```powershell
python -m storage_genius --config config.json scan-apps
python -m storage_genius --config config.json report apps
python -m storage_genius --config config.json report apps --json
```

## Config example

Use [`config.example.json`](./config.example.json) as a starting point.

Key fields:

- `cleanup_rules[].source_dir`: the folder to scan, typically `%USERPROFILE%\Downloads`.
- `cleanup_rules[].target_dir`: the archive path on `D:`.
- `cleanup_rules[].min_size_mb`: files at or above this size are eligible.
- `cleanup_rules[].keep_recent_hours`: avoids moving files you just downloaded.
- `cleanup_rules[].include_extensions`: optional allowlist.
- `cleanup_rules[].exclude_extensions`: useful for temporary and in-progress downloads.
- `app_audit.unused_days`: report apps not seen recently.
- `app_audit.minimum_install_age_days`: ignore apps you installed recently.
- `hotspot_scan.roots`: folders to inspect for SSD pressure.
- `hotspot_scan.exclude_paths`: folders the scanner should skip.
- `hotspot_scan.large_file_threshold_mb`: minimum size for file and directory findings.
- `hotspot_scan.max_depth`: recursive depth for directory sizing.
- `hotspot_scan.html_reports_to_keep`: how many old hotspot reports to retain.
- `dev_cleanup.enabled`: master switch for developer-cache recommendations.
- `dev_cleanup.node.enabled`: include Node cache paths.
- `dev_cleanup.python.enabled`: include Python cache paths.
- `dev_cleanup.extra_paths`: opt-in extra cache folders to include.
- `dev_cleanup.exclude_paths`: cache paths the recommendation layer should skip.
- `activitywatch.enabled`: opt in to local ActivityWatch enrichment.
- `activitywatch.base_url`: local ActivityWatch server URL.
- `activitywatch.lookback_days`: how much recent ActivityWatch history to consider.
- `app_audit.minimum_candidate_size_mb`: minimum installed size for uninstall suggestions.
- `app_audit.score_thresholds`: score cutoffs for uninstall review and likely-active grouping.

## Important limitations

- `UserAssist` mostly reflects GUI launches for the current Windows user. It will miss some terminal-launched tools and background-only apps.
- App matching is heuristic. Review the report before uninstalling anything.
- Cleanup currently scans only the top level of each configured folder, not nested folders.
- The hotspot scanner is intentionally conservative about directory classification. It is designed to surface pressure, not delete anything automatically.

## Good next additions

If you want this to become a serious SSD hygiene tool, add these next:

- Disk-pressure mode: when free space on `C:` drops below a threshold, tighten cleanup rules automatically.
- Large-folder scanner: find the heaviest folders under `%USERPROFILE%`, `%LOCALAPPDATA%`, Steam libraries, and IDE caches.
- Temporary-file cleanup packs: browser caches, Windows temp, package-manager caches, old installers.
- Duplicate-file finder using hashes, with a review queue instead of direct deletion.
- Screenshot and screen-recording archiver: those folders quietly grow forever.
- Recycle Bin watcher and old-file purge policy.
- Compression suggestions for cold archives on `D:`.
- Scheduled Task registration so scans happen daily without manual runs.
- Exported HTML or CSV reports so you can see storage trends over time.

## Long-term architecture direction

The current code already supports this split cleanly:

- rule-based file automation
- app inventory and usage observation
- SQLite state/history

That makes it straightforward to add:

- a background tray app
- notifications for cleanup suggestions
- a small local dashboard
- one-click “review and approve” actions
