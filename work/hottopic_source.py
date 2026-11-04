"""Hot Topic Magic category discovery with stock confirmed by Quick View.

The filtered category HTML contains a complete JSON-LD product list when its
reported result count fits the page. Only candidate boxes or named precons get
one read-only Quick View request; no cart or checkout call is made.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://www.hottopic.com/toys-collectibles/games/trading-cards/?prefn1=brand&prefv1=Magic"
QUICK_VIEW = "https://www.hottopic.com/on/demandware.store/Sites-hottopic-Site/default/Product-ShowQuickView?pid="
CATEGORY_SAMPLE = ROOT / "work" / "pending_site_samples" / "hottopic_category_live.html"
QUICK_VIEW_SAMPLE = ROOT / "work" / "pending_site_samples" / "hottopic_box_quickview.html"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/145.0 Safari/537.36"


def category_products(page: str) -> list[dict]:
    match = re.search(r'<script\b[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
                      page, re.I | re.S)
    count_match = re.search(r'data-product-count="(\d+)"', page)
    if not match or not count_match:
        raise ValueError("Hot Topic category lacked product data or result count")
    payload = json.loads(match.group(1))
    rows = (payload.get("mainEntity") or {}).get("itemListElement")
    if not isinstance(rows, list) or int(count_match.group(1)) != len(rows):
        raise ValueError("Hot Topic category listing was incomplete")
    products = []
    seen = set()
    for row in rows:
        item = row.get("item") if isinstance(row, dict) else None
        if not isinstance(item, dict):
            raise ValueError("Hot Topic category contained an invalid product")
        product_id = str(item.get("sku") or "")
        offer = item.get("offers") or {}
        title = str(item.get("name") or "").strip()
        url = str(offer.get("url") or item.get("@id") or "")
        parts = urlsplit(url)
        if not product_id.isdigit() or product_id in seen or not title or \
                parts.scheme != "https" or parts.hostname != "www.hottopic.com" or \
                offer.get("priceCurrency") != "USD":
            raise ValueError("Hot Topic category contained invalid product details")
        seen.add(product_id)
        products.append({"id": product_id, "title": title, "url": url})
    return products


def quick_view_card(payload: dict, listing: dict) -> dict | None:
    product = payload.get("product")
    if not isinstance(product, dict) or str(product.get("id")) != listing["id"]:
        raise ValueError("Hot Topic Quick View returned a different product")
    availability = product.get("availability") or {}
    if not isinstance(availability, dict):
        raise ValueError("Hot Topic Quick View lacked availability")
    if (product.get("available") is not True or product.get("readyToOrder") is not True
            or product.get("online") is not True or payload.get("isOutofstockStatus") is True
            or availability.get("status") != "In Stock"):
        return None
    sales = (product.get("price") or {}).get("sales") or {}
    if sales.get("currency") != "USD":
        raise ValueError("Hot Topic Quick View lacked a USD listed price")
    try:
        price = Decimal(str(sales.get("decimalPrice") or sales.get("value")))
    except (InvalidOperation, TypeError):
        raise ValueError("Hot Topic Quick View had an invalid listed price") from None
    if not price.is_finite() or price <= 0:
        raise ValueError("Hot Topic Quick View had an invalid listed price")
    return {"title": listing["title"], "url": listing["url"], "price": str(price)}


def cached_feed(wanted) -> tuple[list[dict], dict]:
    listings = category_products(CATEGORY_SAMPLE.read_text(encoding="utf-8", errors="replace"))
    candidates = [row for row in listings if wanted(row["title"])]
    cards = [card for row in candidates
             if (card := quick_view_card(json.loads(QUICK_VIEW_SAMPLE.read_text(encoding="utf-8")), row))]
    return cards, {"site": "Hot Topic", "format": "category-html/quickview-json",
                   "pages": 1, "products": len(listings), "product_checks": len(candidates),
                   "available_products": len(cards), "complete": True, "cached": True}


def fetch_feed(wanted, idle_check=None) -> tuple[list[dict], dict]:
    status = {"site": "Hot Topic", "format": "category-html/quickview-json",
              "pages": 0, "products": 0, "product_checks": 0, "complete": False,
              "source_url": SOURCE_URL}
    try:
        if idle_check and not idle_check():
            raise InterruptedError("computer became active during Hot Topic read")
        request = urllib.request.Request(SOURCE_URL, headers={"User-Agent": USER_AGENT,
                                                          "Accept": "text/html"})
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(1_500_001)
        if len(raw) > 1_500_000:
            raise ValueError("Hot Topic category exceeded 1.5 MB cap")
        listings = category_products(raw.decode("utf-8", "replace"))
        status["pages"] = 1
        status["products"] = len(listings)
        candidates = [row for row in listings if wanted(row["title"])]
        if len(candidates) > 30:
            raise ValueError("Hot Topic had over 30 candidate products; stopped before stock checks")
        cards = []
        for row in candidates:
            if idle_check and not idle_check():
                raise InterruptedError("computer became active during Hot Topic read")
            time.sleep(2)
            url = QUICK_VIEW + quote(row["id"])
            status["last_request_url"] = url
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                       "Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read(300_001)
            if len(raw) > 300_000:
                raise ValueError("Hot Topic Quick View exceeded 300 KB cap")
            status["product_checks"] += 1
            card = quick_view_card(json.loads(raw), row)
            if card:
                cards.append(card)
        status["available_products"] = len(cards)
        status["complete"] = True
        return cards, status
    except InterruptedError:
        raise
    except (urllib.error.URLError, OSError, ValueError, TypeError) as error:
        status["error"] = f"{type(error).__name__}: {error}"
        return [], status
