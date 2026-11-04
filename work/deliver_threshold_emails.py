"""Deliver queued threshold alerts through the user's local Brevo SMTP credential."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from threshold_outbox import ROOT, mark_sent, pending_alerts, save_json


STORE = Path.home() / "AppData" / "Local" / "MTGBoxScraper"
CREDENTIAL = STORE / "brevo_smtp_credential.xml"
SENDER = STORE / "brevo_sender.txt"
STATUS = ROOT / "email_delivery_status.json"
SCRIPT = ROOT / "work" / "send_brevo_threshold_email.ps1"


def configured() -> bool:
    return CREDENTIAL.is_file() and SENDER.is_file()


def deliver() -> tuple[int, int]:
    if not configured():
        return 0, 0
    sent = 0
    failed = 0
    for event in pending_alerts():
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", str(SCRIPT), "-EventId", event["id"]],
                cwd=ROOT, capture_output=True, text=True, timeout=40,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout or "SMTP send failed").strip()[-500:])
            if not mark_sent(event["id"]):
                raise RuntimeError("Email sent, but the pending alert could not be marked sent")
            sent += 1
            save_json(STATUS, {"last_success_utc": datetime.now(timezone.utc).isoformat(),
                               "last_error": None})
        except (OSError, subprocess.TimeoutExpired, RuntimeError) as error:
            failed += 1
            save_json(STATUS, {"last_error": str(error)[-500:],
                               "last_failure_utc": datetime.now(timezone.utc).isoformat()})
            print(f"Brevo email for {event['product_key']} failed: {error}")
            break
    return sent, failed


if __name__ == "__main__":
    sent, failed = deliver()
    print(json.dumps({"sent": sent, "failed": failed, "pending": len(pending_alerts())}))
    if failed:
        raise SystemExit(1)
