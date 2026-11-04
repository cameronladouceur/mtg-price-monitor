"""Watch new first-party Magic products in Best Buy's clearance listing.

The live reader follows the visible-browser approach in
XanderRobbins/GianPokemonTracker's checkers/bestbuy_browser.py and the
same-origin batch price read in imoonkey/openweb's bestbuy-read.ts. It makes
one sequential pass over result pages, opens no product pages, and never
accepts partial lists.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from urllib.parse import urljoin

from inspect_bestbuy_saved import CardParser


ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = (
    "https://www.bestbuy.com/site/searchpage.jsp?"
    "browsedCategory=pcmcat1737149086844&id=pcat17071&"
    "qp=currentoffers_facet%3DCurrent+Deals~Clearance&"
    "st=pcmcat1737149086844_categoryid%24pcmcat1604992984556"
)
STATE = ROOT / "bestbuy_clearance_seen.json"
OUTBOX = ROOT / "bestbuy_clearance_email_outbox.json"
SEND_SCRIPT = ROOT / "work" / "send_brevo_bestbuy_email.ps1"
EMAIL_STATUS = ROOT / "bestbuy_email_delivery_status.json"
EXCLUDED = re.compile(r"\b(?:playmat|deck protector|card sleeve|binder|deck box|dice|accessor(?:y|ies))\b", re.I)


def windows_idle_minutes() -> float:
    class LastInputInfo(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    last = LastInputInfo()
    last.cbSize = ctypes.sizeof(last)
    if not hasattr(ctypes, "windll") or not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(last)):
        return 0.0
    ctypes.windll.kernel32.GetTickCount.restype = ctypes.c_uint
    return ((ctypes.windll.kernel32.GetTickCount() - last.dwTime) & 0xFFFFFFFF) / 60_000.0


def save_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path, fallback: dict) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else fallback


def parse_listing(html: str) -> tuple[list[dict], dict]:
    parser = CardParser()
    parser.feed(html)
    page_text = " ".join(" ".join(parser.page_text_parts).split())
    counts = [(int(a), int(b)) for a, b in re.findall(r"\b(\d+)\s+of\s+(\d+)\b", page_text)]
    shown, expected = counts[-1] if counts else (None, None)
    cards = parser.cards
    valid = bool(cards) and all(card["sku"] and card["url"] and card["price"] is not None and card["title"] for card in cards)
    complete = bool(valid and shown == expected == len(cards)
                    and len({card["sku"] for card in cards}) == len(cards)
                    and "trouble loading more products" not in page_text.lower())
    products = [
        {key: card[key] for key in ("sku", "title", "price", "url", "sold_out", "available")}
        for card in cards
        if card["title"].lower().startswith("wizards of the coast - magic: the gathering")
        and not EXCLUDED.search(card["title"])
    ]
    status = {"site": "Best Buy clearance", "format": "browser-html", "pages": 1,
              "complete": complete, "shown": shown, "expected": expected,
              "products": len(cards), "sealed_products": len(products)}
    if not complete:
        status["error"] = "incomplete Best Buy listing; no availability email or seen-state update"
    return products, status


def fetch_live(idle_check=None, *, headless: bool = False) -> tuple[list[dict], dict]:
    """Read paginated SKU tiles and one batch price response per page.

    The batch endpoint and same-origin browser fetch come from imoonkey/openweb's
    bestbuy-read adapter; no product page or checkout request is made.
    """
    from playwright.sync_api import sync_playwright

    if idle_check and not idle_check():
        raise InterruptedError("computer became active before Best Buy read")
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(Path.home() / "AppData" / "Local" / "ms-playwright"))
    seen = {}
    expected = None
    pages = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless,
                                             args=[] if headless else ["--start-minimized"], timeout=15000)
        try:
            page = browser.new_page(locale="en-US", viewport={"width": 1440, "height": 900})
            page.set_default_timeout(8000)
            for number in range(1, 11):
                if idle_check and not idle_check():
                    raise InterruptedError("computer became active during Best Buy read")
                if number > 1:
                    page.wait_for_timeout(2000)
                url = SOURCE_URL if number == 1 else SOURCE_URL + f"&cp={number}"
                response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
                if response is None or response.status != 200:
                    raise RuntimeError(f"Best Buy result page {number} HTTP {response.status if response else 'no response'}")
                page.wait_for_selector("li[data-product-id]", timeout=10000)
                ids = page.locator("li[data-product-id]").evaluate_all(
                    "els => [...new Set(els.map(e => e.getAttribute('data-product-id')).filter(Boolean))]")
                text = page.locator("body").inner_text(timeout=8000)
                counts = [(int(a), int(b)) for a, b in re.findall(r"\b(\d+)\s+of\s+(\d+)\b", text)]
                if counts:
                    expected = max(expected or 0, max(b for _, b in counts))
                if not ids:
                    raise RuntimeError(f"Best Buy result page {number} had no SKU tiles")
                data = page.evaluate("""async skus => {
                  const abort = new AbortController();
                  const timer = setTimeout(() => abort.abort(), 10000);
                  try {
                    const r = await fetch('/api/3.0/priceBlocks?' + new URLSearchParams({skus:skus.join(',')}),
                      {credentials:'include', signal:abort.signal});
                    const type = r.headers.get('content-type') || '';
                    return {status:r.status, type, data:type.includes('json') ? await r.json() : null};
                  } catch(e) { return {error:String(e)}; }
                  finally { clearTimeout(timer); }
                }""", ids)
                rows = data.get("data")
                if data.get("status") != 200 or not isinstance(rows, list) or len(rows) != len(ids):
                    raise RuntimeError(f"Best Buy batch prices incomplete on page {number}: "
                                       f"HTTP {data.get('status')}, {len(rows) if isinstance(rows, list) else 0}/{len(ids)} rows, "
                                       f"{data.get('error') or data.get('type')}")
                returned_ids = set()
                for row in rows:
                    sku = row.get("sku") if isinstance(row, dict) else None
                    if not isinstance(sku, dict) or sku.get("error"):
                        raise RuntimeError(f"Best Buy batch price error on page {number}")
                    sku_id = str(sku.get("skuId") or "")
                    name = (sku.get("names") or {}).get("short")
                    price = (sku.get("price") or {}).get("currentPrice")
                    if sku_id not in ids or not name or price is None:
                        raise RuntimeError(f"Best Buy batch price missing SKU/name/price on page {number}")
                    product_url = sku.get("url") or f"/site/-/{sku_id}.p?skuId={sku_id}"
                    button_data = sku.get("buttonState") or {}
                    button = button_data.get("buttonState")
                    sold_out = button in ("SOLD_OUT", "NOT_AVAILABLE", "COMING_SOON", "PRE_ORDER")
                    seen[sku_id] = {"sku": sku_id, "title": name, "price": float(price),
                                    "url": urljoin("https://www.bestbuy.com", product_url),
                                    "sold_out": sold_out,
                                    "available": bool(not sold_out and (button_data.get("purchasable") is True
                                                                        or button in ("ADD_TO_CART", "BUY_NOW")))}
                    returned_ids.add(sku_id)
                if returned_ids != set(ids):
                    raise RuntimeError(f"Best Buy batch prices did not match result page {number} SKUs")
                pages += 1
                if expected is not None and len(seen) >= expected:
                    break
                if len(ids) < 16:
                    break
        finally:
            browser.close()
    complete = bool(expected is not None and len(seen) == expected)
    products = [item for item in seen.values()
                if item["title"].lower().startswith("wizards of the coast - magic: the gathering")
                and not EXCLUDED.search(item["title"])]
    status = {"site": "Best Buy clearance", "format": "openweb-priceBlocks/browser",
              "pages": pages, "complete": complete, "products": len(seen),
              "expected": expected, "sealed_products": len(products), "source_url": SOURCE_URL}
    if not complete:
        status["error"] = "incomplete Best Buy listing; no availability email or seen-state update"
    return products, status


def probe_batch_read() -> list[dict]:
    """One diagnostic of OpenWeb's same-origin batch pricing method per result page."""
    from playwright.sync_api import sync_playwright

    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(Path.home() / "AppData" / "Local" / "ms-playwright"))
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False, args=["--start-minimized"], timeout=15000)
        try:
            page = browser.new_page(locale="en-US", viewport={"width": 1440, "height": 900})
            for number in (1, 2):
                url = SOURCE_URL if number == 1 else SOURCE_URL + "&cp=2"
                response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2200)
                ids = page.locator("li[data-product-id]").evaluate_all(
                    "els => [...new Set(els.map(e => e.getAttribute('data-product-id')).filter(Boolean))]")
                result = {"page": number, "http_status": response.status if response else None,
                          "tile_count": len(ids), "sku_count": len(ids)}
                if ids:
                    data = page.evaluate("""async skus => {
                      const abort = new AbortController();
                      const timer = setTimeout(() => abort.abort(), 8000);
                      try {
                        const r = await fetch('/api/3.0/priceBlocks?skus=' + encodeURIComponent(skus.join(',')),
                          {credentials:'include', signal:abort.signal});
                        const type = r.headers.get('content-type') || '';
                        return {status:r.status, type, data:type.includes('json') ? await r.json() : null};
                      } catch(e) { return {error:String(e)}; }
                      finally { clearTimeout(timer); }
                    }""", ids)
                    result["batch_http_status"] = data.get("status")
                    result["batch_content_type"] = data.get("type")
                    result["batch_error"] = data.get("error")
                    rows = data.get("data") if isinstance(data.get("data"), list) else []
                    result["batch_count"] = len(rows)
                    result["sample"] = [{"sku": (x.get("sku") or {}).get("skuId"),
                                          "name": ((x.get("sku") or {}).get("names") or {}).get("short"),
                                          "price": ((x.get("sku") or {}).get("price") or {}).get("currentPrice")}
                                         for x in rows[:2] if isinstance(x, dict) and isinstance(x.get("sku"), dict)]
                results.append(result)
                if number == 1:
                    page.wait_for_timeout(2000)
        finally:
            browser.close()
    return results


def record_scan(products: list[dict], status: dict, *, seed: bool = False,
                observed_at: str | None = None) -> list[dict]:
    """Record all seen SKUs; email once when each is first confirmed available."""
    if not status.get("complete"):
        return []
    now = observed_at or datetime.now(timezone.utc).isoformat()
    state = read_json(STATE, {"version": 1, "seen": {}})
    seen = state.setdefault("seen", {})
    alerted = state.setdefault("alerted", {})
    first_scan = not STATE.exists()
    newly_available = []
    for item in products:
        if item["sku"] not in seen:
            seen[item["sku"]] = {"first_seen_utc": now, "title": item["title"], "url": item["url"]}
        if not item.get("available") or item.get("sold_out") or item["sku"] in alerted:
            continue
        alerted[item["sku"]] = {"first_available_utc": now, "baseline": bool(seed or first_scan)}
        if not (seed or first_scan):
            newly_available.append(item)
    state["last_success_utc"] = now
    state["last_card_count"] = status["products"]
    state["last_sealed_count"] = len(products)
    if newly_available:
        outbox = read_json(OUTBOX, {"version": 1, "pending": []})
        pending = outbox.setdefault("pending", [])
        pending_skus = {item["sku"] for item in pending}
        for item in newly_available:
            if item["sku"] not in pending_skus:
                pending.append({"id": uuid4().hex, "sku": item["sku"], "title": item["title"],
                                "price": item["price"], "available": True,
                                "url": item["url"], "observed_at_utc": now})
        save_json(OUTBOX, outbox)
    save_json(STATE, state)
    return newly_available


def deliver_emails() -> tuple[int, int]:
    from deliver_threshold_emails import configured

    if not configured():
        return 0, 0
    sent = failed = 0
    outbox = read_json(OUTBOX, {"version": 1, "pending": []})
    for event in list(outbox.get("pending", [])):
        if event.get("available") is not True:
            outbox["pending"] = [item for item in outbox["pending"] if item["id"] != event["id"]]
            save_json(OUTBOX, outbox)
            continue
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", str(SEND_SCRIPT), "-EventId", event["id"]],
                cwd=ROOT, capture_output=True, text=True, timeout=40,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout or "SMTP send failed").strip()[-500:])
            outbox["pending"] = [item for item in outbox["pending"] if item["id"] != event["id"]]
            outbox["last_sent_utc"] = datetime.now(timezone.utc).isoformat()
            save_json(OUTBOX, outbox)
            sent += 1
            save_json(EMAIL_STATUS, {"last_success_utc": outbox["last_sent_utc"], "last_error": None})
        except (OSError, subprocess.TimeoutExpired, RuntimeError) as error:
            failed += 1
            save_json(EMAIL_STATUS, {"last_failure_utc": datetime.now(timezone.utc).isoformat(),
                                     "last_error": str(error)[-500:]})
            break
    return sent, failed


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--live-json", action="store_true")
    group.add_argument("--seed-html", type=Path)
    group.add_argument("--probe-batch", action="store_true")
    parser.add_argument("--idle-only", action="store_true")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()
    if args.probe_batch:
        print(json.dumps(probe_batch_read(), ensure_ascii=False), flush=True)
    elif args.live_json:
        try:
            products, status = fetch_live((lambda: windows_idle_minutes() >= 10) if args.idle_only else None,
                                          headless=args.headless)
        except InterruptedError as error:
            print(str(error), flush=True)
            raise SystemExit(3)
        print(json.dumps({"products": products, "status": status}, ensure_ascii=False), flush=True)
    else:
        html = args.seed_html.read_text(encoding="utf-8", errors="replace")
        products, status = parse_listing(html)
        if not status["complete"]:
            raise SystemExit(f"Cannot seed incomplete Best Buy snapshot: {status}")
        record_scan(products, status, seed=True)
        print(json.dumps({"seeded_products": len(products), "cards": status["products"],
                          "emails_queued": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
