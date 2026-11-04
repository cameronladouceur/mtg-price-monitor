# MTG price monitor

Windows-based scanner for listed prices of Magic Play and Draft Booster Boxes and named Commander precons. Retailer feeds are checked sequentially. The local dashboard displays the lowest offers, pack counts, and price per pack.

## Data boundary

This repository contains code and reference pack counts. Selected store URLs, Amazon watchlists, price histories, thresholds, scan logs, and email settings stay private. `.gitignore` uses an allowlist to prevent those files from being committed accidentally.

The scan runtime trial is manual and runs with `--live --preview`. It makes the normal sequential feed requests, then reports only elapsed time and aggregate counts. It does not update price benchmarks or send alerts. It does not persist its temporary scan files as GitHub artifacts.

The trial requires a repository secret called `SCAN_CONFIG_ZLIB_B64`. It is a zlib-compressed, Base64-encoded UTF-8 JSON object containing `selected_sources` (the contents of `SELECTED_MAGIC_SOURCES.json`) and `retailer_exclusions` (the contents of `RETAILER_EXCLUSIONS.json`). This configuration is written only into the temporary runner workspace. Compression keeps it within Windows' environment-variable limit.

The Amazon command-line binary and private watchlist are absent from this public repo, so the trial reports Amazon as unavailable. Best Buy is skipped by preview mode. The first trial therefore measures the selected collection feeds, not the complete local notification workload.

The workflow has no schedule. No cloud notification or private history storage is configured yet.
