"""Polite Target search adapter for listed MTG booster box prices.

Adapted from the public RedSky search pattern in piekstra/target-cli (MIT/Apache-2.0).
https://github.com/piekstra/target-cli
The search response does not verify stock; offers remain listed-price leads.
"""

from __future__ import annotations

import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from decimal import Decimal, InvalidOperation


ENDPOINT = "https://redsky.target.com/redsky_aggregations/v1/web/plp_search_v2"
DEFAULT_KEY = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
QUERY = "magic the gathering booster box"
PAGE_SIZE = 28
# Target returned HTTP 435 when a broader probe reached the fifth page.
# Check one page per two-hour idle scan rather than repeatedly hitting that limit.
MAX_PAGES = 1


def cards_from_products(products: list[dict]) -> list[dict]:
    cards = []
    seen = set()
    for product in products:
        tcin = str(product.get("tcin") or "")
        item = product.get("item") or {}
        title = html.unescape(str((item.get("product_description") or {}).get("title") or "")).strip()
        price = (product.get("price") or {}).get("current_retail")
        url = str((item.get("enrichment") or {}).get("buy_url") or "")
        street_date = str((item.get("mmbv_content") or {}).get("street_date") or "")
        ribbons = " ".join(str(value) for value in item.get("ribbons") or []).lower()
        if (not tcin.isdigit() or tcin in seen or not title
                or not re.search(r"\b(?:play|draft)\s+booster\s+(?:box|display)\b", title, re.I)
                or re.search(r"\bscene box\b", title, re.I)
                or urllib.parse.urlsplit(url).hostname not in ("target.com", "www.target.com")
                or "preorder" in ribbons or "pre-order" in ribbons):
            continue
        if street_date:
            try:
                if date.fromisoformat(street_date[:10]) > date.today():
                    continue
            except ValueError:
                continue
        try:
            amount = Decimal(str(price))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if not amount.is_finite() or amount <= 0:
            continue
        seen.add(tcin)
        cards.append({"title": title, "url": url, "price": str(amount), "tcin": tcin})
    return cards


def fetch_target_search(idle_guard=None) -> tuple[list[dict], dict]:
    """Search one page per scan, stopping immediately on any failure."""
    status = {"site": "Target", "format": "target-redsky-search", "pages": 0,
              "complete": False, "query": QUERY, "stock_verified": False}
    key = os.environ.get("TARGET_REDSKY_KEY") or DEFAULT_KEY
    store = os.environ.get("TARGET_STORE_ID") or "3991"
    cards = []
    seen = set()
    total_pages = None
    for page_number in range(MAX_PAGES):
        if idle_guard and not idle_guard():
            raise InterruptedError("computer became active; scan stopped without changing benchmarks")
        params = {"key": key, "keyword": QUERY, "channel": "WEB",
                  "count": PAGE_SIZE, "offset": page_number * PAGE_SIZE,
                  "page": "/s/" + QUERY, "platform": "desktop",
                  "pricing_store_id": store, "store_id": store,
                  "visitor_id": "0193A1B2C3D4E5F6"}
        url = ENDPOINT + "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={
            "User-Agent": "MTGBoxScraper/1.0 (personal price monitor)",
            "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ValueError("Target search response exceeded 2 MB")
            search = json.loads(raw)["data"]["search"]
            products = search["products"]
            if not isinstance(products, list):
                raise ValueError("Target search products is not a list")
            metadata = search.get("search_response", {}).get("metadata", {})
            if total_pages is None:
                total_pages = int(metadata.get("total_pages", 1))
                status["total_pages"] = total_pages
                status["limited_to_first_page"] = total_pages > 1
            for card in cards_from_products(products):
                if card["tcin"] not in seen:
                    seen.add(card["tcin"])
                    cards.append(card)
            status["pages"] += 1
            if not products or page_number + 1 >= total_pages:
                status["complete"] = True
                break
        except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as error:
            status["error"] = f"{type(error).__name__}: {error}"
            break
    status["products"] = len(cards)
    return cards, status


def cached_target_search() -> tuple[list[dict], dict]:
    """Offline previews never make an implicit Target request."""
    return [], {"site": "Target", "format": "target-redsky-search", "pages": 0,
                "complete": False, "products": 0, "cached": True,
                "stock_verified": False, "offline_preview_skipped": True}
