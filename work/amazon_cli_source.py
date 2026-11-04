"""Run the pinned, unmodified amz-cli binary for selected Amazon products.

amz-cli (Apache-2.0): https://github.com/tamnd/amz-cli
The external tool fetches and parses Amazon; this file only validates its JSON
before passing eligible offers to our existing comparison and alert rules.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / "work" / "vendor" / "amz-cli-bin" / "amz.exe"
WATCHLIST = ROOT / "amazon_watchlist.json"
COOLDOWN = ROOT / "work" / "amazon_cli_cooldown.json"
CURSOR = ROOT / "work" / "amazon_watch_cursor.json"
PRODUCTS_PER_SCAN = 12
BLOCK_CODES = {1, 4, 5, 6, 7, 8, 9}


def _amount(value: object) -> Decimal | None:
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result > 0 else None
    except (TypeError, ValueError, InvalidOperation):
        return None


def inspect_product(product: dict, expected_asin: str) -> tuple[dict | None, str | None]:
    """Return an eligible listing or the reason this watched ASIN was skipped."""
    if product.get("asin") != expected_asin or product.get("marketplace") != "us":
        return None, "returned product or marketplace did not match"
    offer = product.get("offer") or {}
    price = offer.get("price") or {}
    if offer.get("in_stock") is not True:
        return None, "out of stock" if offer.get("in_stock") is False else "stock not verified"
    if price.get("currency") != "USD":
        return None, "USD price missing"
    if re.search(r"pre[ -]?order|used|renewed", str(offer.get("availability") or "") + " " +
                 str(offer.get("condition") or ""), re.I):
        return None, "preorder or used condition"
    amount = _amount(price.get("value"))
    title = product.get("title")
    if not amount or not isinstance(title, str) or not title.strip():
        return None, "positive price or title missing"
    url = f"https://www.amazon.com/dp/{expected_asin}"
    seller = str((offer.get("sold_by") or {}).get("name") or "").strip()
    site = "Amazon" if re.fullmatch(r"Amazon(?:\.com)?(?: Services LLC)?", seller, re.I) else \
        f"Amazon Marketplace ({seller})" if seller else "Amazon Marketplace"
    return {"title": title.strip(), "price": str(amount), "url": url,
            "site": site, "seller": seller or None}, None


def pause_after_block(status: dict, exit_code: int) -> None:
    until = datetime.now(timezone.utc) + timedelta(days=1)
    COOLDOWN.write_text(json.dumps({"until_utc": until.isoformat(), "exit_code": exit_code}) + "\n",
                        encoding="utf-8")
    status["error"] = f"Amazon access stopped by amz-cli (exit {exit_code}); paused 24 hours"
    status["skipped_until_utc"] = until.isoformat()


def fetch_amazon_products(idle_guard=None) -> tuple[list[dict], dict]:
    """Check selected ASINs sequentially, then back off for a day on a block."""
    status = {"site": "Amazon", "format": "amz-cli/0.3.0", "pages": 0,
              "complete": False, "stock_verified": True, "checks": []}
    if not BINARY.is_file():
        return [], {**status, "error": "amz-cli binary is missing"}
    if COOLDOWN.exists():
        try:
            until = datetime.fromisoformat(json.loads(COOLDOWN.read_text(encoding="utf-8"))["until_utc"])
            if datetime.now(timezone.utc) < until:
                return [], {**status, "skipped_until_utc": until.isoformat()}
        except (ValueError, KeyError, TypeError, OSError):
            pass
    watch = json.loads(WATCHLIST.read_text(encoding="utf-8"))
    asins = list(dict.fromkeys(x for x in watch["asins"]
                               if isinstance(x, str) and len(x) == 10 and x.isalnum()))
    if not asins:
        return [], {**status, "error": "Amazon watchlist is empty"}
    try:
        cursor = int(json.loads(CURSOR.read_text(encoding="utf-8"))["next_index"]) % len(asins)
    except (OSError, ValueError, KeyError, TypeError):
        cursor = 0
    batch = [asins[(cursor + offset) % len(asins)]
             for offset in range(min(PRODUCTS_PER_SCAN, len(asins)))]
    status["watchlist_total"] = len(asins)
    status["scheduled_count"] = len(batch)
    cards = []
    for index, asin in enumerate(batch):
        check = {"asin": asin, "url": f"https://www.amazon.com/dp/{asin}"}
        if idle_guard and not idle_guard():
            raise InterruptedError("computer became active; scan stopped without changing benchmarks")
        if index:
            time.sleep(3)
        try:
            run = subprocess.run(
                [str(BINARY), "product", asin, "--depth", "quick", "-o", "jsonl",
                 "--marketplace", "us", "--rate", "3s", "--timeout", "18s",
                 "--retries", "0", "--no-cache", "--data-dir", str(ROOT / "work" / "vendor" / "amz-data")],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired) as error:
            status["error"] = f"amz-cli could not run: {type(error).__name__}"
            status["checks"].append({**check, "result": "error", "reason": status["error"]})
            break
        if run.returncode in BLOCK_CODES:
            pause_after_block(status, run.returncode)
            status["checks"].append({**check, "result": "blocked", "reason": status["error"]})
            break
        if run.returncode == 3:
            status["pages"] += 1
            status["checks"].append({**check, "result": "excluded", "reason": "product not found"})
            continue
        if run.returncode:
            status["error"] = f"amz-cli exited {run.returncode}: {run.stderr.strip()[:180]}"
            status["checks"].append({**check, "result": "error", "reason": status["error"]})
            break
        status["pages"] += 1
        try:
            product = json.loads(run.stdout)
            card, reason = inspect_product(product, asin)
            if card:
                cards.append(card)
                status["checks"].append({**check, "result": "eligible", "title": card["title"]})
            else:
                status["checks"].append({**check, "result": "excluded", "reason": reason})
        except (json.JSONDecodeError, TypeError):
            status["error"] = f"amz-cli returned invalid JSON for {asin}"
            status["checks"].append({**check, "result": "error", "reason": status["error"]})
            break
    else:
        status["complete"] = True
        cursor = (cursor + len(batch)) % len(asins)
        CURSOR.write_text(json.dumps({"next_index": cursor}) + "\n", encoding="utf-8")
    if status.get("skipped_until_utc"):
        processed = sum(check["result"] in ("eligible", "excluded") for check in status["checks"])
        if processed:
            cursor = (cursor + processed) % len(asins)
            CURSOR.write_text(json.dumps({"next_index": cursor}) + "\n", encoding="utf-8")
    status["next_index"] = cursor
    status["products"] = len(cards)
    return cards, status
