"""One manual cloud trial; keep private feed config and scan output out of Actions logs."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import zlib


ROOT = Path(__file__).resolve().parents[1]


def prepare_config() -> None:
    encoded = os.environ.get("SCAN_CONFIG_ZLIB_B64", "")
    if not encoded:
        raise ValueError("SCAN_CONFIG_ZLIB_B64 repository secret is missing")
    data = json.loads(zlib.decompress(base64.b64decode(encoded, validate=True)))
    sources = data.get("selected_sources")
    exclusions = data.get("retailer_exclusions")
    if not isinstance(sources, dict) or not isinstance(sources.get("sources"), list):
        raise ValueError("selected_sources must contain a sources list")
    if not isinstance(exclusions, dict) or not isinstance(exclusions.get("site_ids"), list):
        raise ValueError("retailer_exclusions must contain a site_ids list")
    for name, value in (("SELECTED_MAGIC_SOURCES.json", sources),
                        ("RETAILER_EXCLUSIONS.json", exclusions)):
        (ROOT / name).write_text(json.dumps(value), encoding="utf-8")


def main() -> int:
    prepare_config()
    started = time.monotonic()
    try:
        result = subprocess.run(
            [sys.executable, "work/scan_price_benchmarks.py", "--live", "--preview"],
            cwd=ROOT, capture_output=True, text=True, timeout=85 * 60,
        )
    except subprocess.TimeoutExpired:
        print("Scan timed out after 85 minutes; no alerts were sent.")
        return 1
    elapsed = time.monotonic() - started
    print(f"Scan runtime: {elapsed:.1f} seconds ({elapsed / 60:.1f} minutes)")
    print(f"Exit status: {result.returncode}")
    log_path = ROOT / "price_scan_log.jsonl"
    if log_path.exists():
        lines = log_path.read_text(encoding="utf-8").splitlines()
        if lines:
            run = json.loads(lines[-1])
            statuses = run.get("sources") or run.get("statuses") or []
            print(f"Sources checked: {len(statuses)}")
            print(f"Category pages requested: {sum(int(row.get('pages') or 0) for row in statuses)}")
            print(f"Qualifying box offers: {run.get('box_offer_count', 0)}")
            print(f"Qualifying precon offers: {run.get('precon_offer_count', 0)}")
            print(f"Incomplete sources: {sum(row.get('complete') is False for row in statuses)}")
            for index, row in enumerate(statuses):
                if row.get("complete") is not False:
                    continue
                error = row.get("error") or ""
                http = row.get("http_status") or re.search(r"HTTP Error (\d{3})", error)
                if hasattr(http, "group"):
                    http = http.group(1)
                kind = "http " + str(http) if http else error.split(":", 1)[0][:40]
                print(f"Incomplete source {index}: {row.get('result', 'unknown')}; {kind or 'no error'}")
    if result.returncode:
        print("Scan failed. The full error remains in the temporary runner workspace.")
    return result.returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, TypeError) as error:
        print(f"Trial configuration error: {error}")
        raise SystemExit(2)
