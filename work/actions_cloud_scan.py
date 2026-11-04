"""Run the scanner against state checked out from a separate private repository."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "private-state"
STATE_FILES = (
    "SELECTED_MAGIC_SOURCES.json",
    "RETAILER_EXCLUSIONS.json",
    "price_benchmarks.json",
    "price_thresholds.json",
    "threshold_email_outbox.json",
    "commander_precons.json",
    "latest_price_scan.json",
    "price_scan_log.jsonl",
    "new_price_alerts.jsonl",
    "cloud_source_cooldowns.json",
)
REQUIRED = STATE_FILES[:4]


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=90 * 60)


def save_state(names: tuple[str, ...], message: str) -> None:
    for name in names:
        source = ROOT / name
        if source.is_file():
            shutil.copy2(source, STATE / name)
    staged_files = tuple(name for name in names if (STATE / name).is_file())
    stage = run("git", "-C", str(STATE), "add", "--", *staged_files)
    if stage.returncode:
        raise RuntimeError("Could not stage private scan state")
    diff = run("git", "-C", str(STATE), "diff", "--cached", "--quiet")
    if diff.returncode == 0:
        return
    if diff.returncode != 1:
        raise RuntimeError("Could not inspect private scan state")
    for key, value in (("user.name", "mtg-price-monitor"),
                       ("user.email", "actions@users.noreply.github.com")):
        if run("git", "-C", str(STATE), "config", key, value).returncode:
            raise RuntimeError("Could not set private state commit identity")
    if run("git", "-C", str(STATE), "commit", "-m", message).returncode:
        raise RuntimeError("Could not commit private scan state")
    if run("git", "-C", str(STATE), "push", "origin", "HEAD:main").returncode:
        raise RuntimeError("Could not push private scan state")


def main() -> int:
    if not (STATE / ".git").exists():
        raise RuntimeError("Private state checkout is missing")
    for name in REQUIRED:
        if not (STATE / name).is_file():
            raise RuntimeError(f"Private state is missing {name}")
    for name in STATE_FILES:
        source = STATE / name
        if source.is_file():
            shutil.copy2(source, ROOT / name)

    log_path = ROOT / "price_scan_log.jsonl"
    prior_log_lines = len(log_path.read_text(encoding="utf-8").splitlines()) if log_path.exists() else 0
    started = time.monotonic()
    scan = run(sys.executable, "work/scan_price_benchmarks.py", "--live", "--cloud")
    print(f"Cloud scan runtime: {time.monotonic() - started:.1f} seconds")
    print(f"Scanner exit status: {scan.returncode}")
    record = None
    if log_path.exists():
        lines = log_path.read_text(encoding="utf-8").splitlines()
        if len(lines) > prior_log_lines:
            record = json.loads(lines[-1])
            print(f"Sources checked: {record.get('source_count', 0)}")
            print(f"Incomplete or blocked sources: {record.get('issue_count', 0)}")
            print(f"Qualifying box offers: {record.get('box_offer_count', 0)}")
            print(f"Qualifying precon offers: {record.get('precon_offer_count', 0)}")
            print(f"New lows recorded: {record.get('new_low_count', 0)}")
    if scan.returncode or record is None or record.get("outcome") != "complete":
        print("Scan failed; private price state was not updated.")
        return scan.returncode or 1

    save_state(STATE_FILES, "Save cloud scan state")
    print("Updated private scan history.")
    if os.environ.get("CLOUD_SEND_EMAILS") == "true":
        from cloud_threshold_email import send_pending
        try:
            sent, _ = send_pending()
        except Exception:
            save_state(("threshold_email_outbox.json",), "Save email delivery progress")
            print("Cloud email delivery failed; pending alerts remain in private state.")
            return 1
        if sent:
            save_state(("threshold_email_outbox.json",), "Mark threshold emails delivered")
            print(f"Sent {sent} threshold email alerts.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"Cloud scan failed: {error}")
        raise SystemExit(1)
