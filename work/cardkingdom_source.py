"""Read Card Kingdom's public sealed-product retail price list once per scan.

Endpoint and field meanings follow mtgban/go-cardkingdom's MIT-licensed
public client. This uses the retailer's feed without product-page requests.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlsplit


SOURCE_URL = "https://api.cardkingdom.com/api/sealed_pricelist"
USER_AGENT = "MTGBoxScraper/1.0 (personal price comparison)"


def parse_feed(payload: dict) -> tuple[list[dict], dict]:
    meta = payload.get("meta")
    rows = payload.get("data")
    if not isinstance(meta, dict) or not isinstance(rows, list) or len(rows) < 100:
        raise ValueError("Card Kingdom sealed price list is incomplete")
    base = str(meta.get("base_url") or "")
    if urlsplit(base).hostname != "www.cardkingdom.com":
        raise ValueError("Card Kingdom sealed price list has an unexpected product base URL")
    cards = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        title = str(item.get("name") or "").strip()
        path = str(item.get("url") or "")
        try:
            quantity = int(item.get("qty_retail") or 0)
            price = Decimal(str(item.get("price_retail") or "0"))
        except (ValueError, TypeError, InvalidOperation):
            continue
        url = urljoin(base, path)
        if (quantity <= 0 or not title or not path or path.startswith(("//", "http:"))
                or urlsplit(url).hostname != "www.cardkingdom.com"
                or not price.is_finite() or price <= 0):
            continue
        cards.append({"title": title, "url": url, "price": str(price)})
    status = {"site": "Card Kingdom", "format": "sealed-pricelist/json", "pages": 1,
              "complete": True, "products": len(rows), "available_products": len(cards),
              "feed_created_at": meta.get("created_at")}
    return cards, status


def fetch_feed() -> tuple[list[dict], dict]:
    try:
        request = urllib.request.Request(SOURCE_URL, headers={"User-Agent": USER_AGENT,
                                                            "Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("Card Kingdom sealed price list exceeded 2 MB cap")
        return parse_feed(json.loads(raw))
    except (urllib.error.URLError, OSError, ValueError) as error:
        return [], {"site": "Card Kingdom", "format": "sealed-pricelist/json", "pages": 0,
                    "complete": False, "products": 0, "error": f"{type(error).__name__}: {error}"}
