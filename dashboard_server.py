"""Local-only HTML dashboard for MTG box lows and manual scans."""

from __future__ import annotations

import argparse
from collections import deque
import json
import re
import subprocess
import sys
import threading
import urllib.request
import webbrowser
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from work.commander_precons import catalog_rows


ROOT = Path(__file__).resolve().parent
PAGE = ROOT / "PRICE_DASHBOARD.html"
BENCHMARKS = ROOT / "price_benchmarks.json"
PRECONS = ROOT / "commander_precons.json"
PRECON_IMAGES = ROOT / "precon_images.json"
PRECON_ART = ROOT / "precon_art"
LATEST = ROOT / "latest_price_scan.json"
ALERTS = ROOT / "new_price_alerts.jsonl"
SCAN_LOG = ROOT / "price_scan_log.jsonl"
IMAGES = ROOT / "box_images.json"
PACK_COUNTS = ROOT / "box_pack_counts.json"
RELEASE_DATES = ROOT / "box_release_dates.json"
THRESHOLDS = ROOT / "price_thresholds.json"
EMAIL_OUTBOX = ROOT / "threshold_email_outbox.json"
EMAIL_STATUS = ROOT / "email_delivery_status.json"
EMAIL_STORE = Path.home() / "AppData" / "Local" / "MTGBoxScraper"
SCRIPT = ROOT / "work" / "scan_price_benchmarks.py"
HOST = "127.0.0.1"
PORT = 8765

scan_lock = threading.Lock()
settings_lock = threading.Lock()
scan_process: subprocess.Popen | None = None
last_scan_finished: str | None = None
last_scan_error: str | None = None


def read_json(path: Path, fallback: dict) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else fallback


def save_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def normalized_threshold(value: object) -> str | None:
    if value is None or value == "":
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("Enter a valid dollar amount") from None
    if not amount.is_finite() or amount <= 0 or amount > 100000 or amount.as_tuple().exponent < -2:
        raise ValueError("Enter an amount from $0.01 to $100,000 with at most two decimal places")
    return str(amount.quantize(Decimal("0.01")))


def read_alerts() -> list[dict]:
    if not ALERTS.exists():
        return []
    rows = []
    for line in ALERTS.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(rows[-100:]))


def read_scan_log(offset: int = 0, limit: int = 20) -> dict:
    if not SCAN_LOG.exists():
        return {"records": [], "total": 0}
    recent = deque(maxlen=offset + limit)
    total = 0
    with SCAN_LOG.open("r", encoding="utf-8") as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                recent.append(row)
                total += 1
    return {"records": list(reversed(recent))[offset:offset + limit], "total": total}


def scan_worker(process: subprocess.Popen) -> None:
    global scan_process, last_scan_finished, last_scan_error
    output, _ = process.communicate()
    with scan_lock:
        last_scan_finished = datetime.now(timezone.utc).isoformat()
        last_scan_error = None if process.returncode == 0 else (output or "Scan failed")[-1200:]
        scan_process = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        print("dashboard:", format % args, flush=True)

    def send_bytes(self, status: int, body: bytes, content_type: str,
                   cache_control: str = "no-store") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; img-src 'self' https://images.ctfassets.net https://media.wizards.com https://cdn.shopify.com https://m.media-amazon.com; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status: int, value: dict) -> None:
        self.send_bytes(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self.send_bytes(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif re.fullmatch(r"/precon-art/[0-9a-f]{16}\.(?:jpg|png|webp)", self.path):
            filename = self.path.rsplit("/", 1)[-1]
            path = PRECON_ART / filename
            if not path.is_file():
                self.send_json(404, {"error": "Image not found"})
                return
            content_type = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}[path.suffix]
            self.send_bytes(200, path.read_bytes(), content_type, "public, max-age=86400")
        elif self.path == "/api/state":
            try:
                with scan_lock:
                    running = scan_process is not None
                    finished = last_scan_finished
                    error = last_scan_error
                self.send_json(200, {
                    "benchmarks": read_json(BENCHMARKS, {"boxes": {}}),
                    "precons": read_json(PRECONS, {"decks": catalog_rows()}),
                    "precon_images": read_json(PRECON_IMAGES, {}),
                    "latest": read_json(LATEST, {}),
                    "images": read_json(IMAGES, {}),
                    "pack_counts": read_json(PACK_COUNTS, {"boxes": {}}),
                    "release_dates": read_json(RELEASE_DATES, {"boxes": {}}),
                    "thresholds": read_json(THRESHOLDS, {"boxes": {}, "precons": {}}),
                    "pending_threshold_emails": len(read_json(EMAIL_OUTBOX, {"pending": []}).get("pending", [])),
                    "email_delivery_ready": ((EMAIL_STORE / "brevo_smtp_credential.xml").is_file()
                                             and (EMAIL_STORE / "brevo_sender.txt").is_file()),
                    "email_delivery_status": read_json(EMAIL_STATUS, {}),
                    "alerts": read_alerts(),
                    "running": running,
                    "last_scan_finished": finished,
                    "last_scan_error": error,
                })
            except (OSError, ValueError) as error:
                self.send_json(500, {"error": str(error)})
        elif re.fullmatch(r"/api/scan-log(?:\?offset=\d{1,5})?", self.path):
            offset = int(self.path.partition("offset=")[2] or "0")
            self.send_json(200, read_scan_log(offset))
        else:
            self.send_json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        global scan_process, last_scan_error
        if self.path not in ("/api/scan", "/api/thresholds"):
            self.send_json(404, {"error": "Not found"})
            return
        if (self.headers.get("X-Local-Dashboard") != "1"
                or self.headers.get("Origin") not in (None, f"http://{HOST}:{PORT}")):
            self.send_json(403, {"error": "Local dashboard request required"})
            return
        if self.path == "/api/thresholds":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > 4096:
                    raise ValueError("Invalid request size")
                body = json.loads(self.rfile.read(length))
                key = body.get("product_key")
                box_keys = read_json(BENCHMARKS, {"boxes": {}}).get("boxes", {})
                precon_keys = read_json(PRECONS, {"decks": catalog_rows()}).get("decks", {})
                if not isinstance(key, str) or (key not in box_keys and key not in precon_keys):
                    raise ValueError("Unknown product")
                group = "precons" if key in precon_keys else "boxes"
                amount = normalized_threshold(body.get("threshold"))
                with settings_lock:
                    settings = read_json(THRESHOLDS, {"version": 1, "boxes": {}, "precons": {}})
                    settings.setdefault(group, {})
                    if amount is None:
                        settings[group].pop(key, None)
                    else:
                        settings[group][key] = amount
                    save_json(THRESHOLDS, settings)
                self.send_json(200, {"product_key": key, "threshold": amount})
            except (ValueError, OSError, KeyError, AttributeError, TypeError) as error:
                self.send_json(400, {"error": str(error)})
            return
        with scan_lock:
            if scan_process is not None:
                self.send_json(409, {"error": "A scan is already running"})
                return
            try:
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                process = subprocess.Popen(
                    [sys.executable, "-u", str(SCRIPT), "--live"], cwd=ROOT,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, creationflags=flags)
            except OSError as error:
                self.send_json(500, {"error": str(error)})
                return
            scan_process = process
            last_scan_error = None
        threading.Thread(target=scan_worker, args=(process,), daemon=True).start()
        self.send_json(202, {"started": True})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--open", action="store_true", help="open the page in the default browser")
    args = parser.parse_args()
    url = f"http://{HOST}:{PORT}/"
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError:
        try:
            with urllib.request.urlopen(url + "api/state", timeout=2) as response:
                if response.status != 200:
                    raise OSError("Port is in use by another service")
        except OSError as error:
            raise SystemExit(f"Could not start dashboard on {url}: {error}")
        print("MTG price dashboard is already running:", url, flush=True)
        if args.open:
            webbrowser.open(url)
        return
    print("MTG price dashboard:", url, flush=True)
    if args.open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
