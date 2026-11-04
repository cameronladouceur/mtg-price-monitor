"""Keep the local dashboard's private Git checkout current and publish thresholds."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
CHECKOUT = ROOT / "work" / "private_state_repo"
THRESHOLDS = ROOT / "price_thresholds.json"


def available() -> bool:
    return (CHECKOUT / ".git").exists()


def git(*args: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(CHECKOUT), *args],
        cwd=ROOT, env={**os.environ, "GCM_INTERACTIVE": "never"},
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise RuntimeError(f"Private Git sync failed at {args[0]}")


def sync() -> None:
    if not available():
        return
    git("pull", "--rebase")
    cloud_thresholds = CHECKOUT / "price_thresholds.json"
    if THRESHOLDS.is_file() and (
        not cloud_thresholds.is_file() or THRESHOLDS.read_bytes() != cloud_thresholds.read_bytes()
    ):
        shutil.copy2(THRESHOLDS, cloud_thresholds)
        git("add", "--", "price_thresholds.json")
        git("-c", "user.name=mtg-price-monitor", "-c",
            "user.email=actions@users.noreply.github.com", "commit", "-m", "Update price thresholds")
    git("push", "origin", "main")
