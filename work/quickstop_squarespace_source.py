"""Read QuickStop's Magic category from Squarespace's embedded product data.

The selected category page itself contains product title, listed price, sale
price, and stock fields in a ProductList data-context attribute. One GET reads
the category; no product or checkout pages are requested.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit


ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://www.quickstopcards.com/shop/magic-the-gathering"
SAMPLE = ROOT / "work" / "pending_site_samples" / "quickstop_magic_live.html"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/145.0 Safari/537.36"


class ProductListParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.contexts = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "div":
            return
        attr = dict(attrs)
        if attr.get("data-controller") == "ProductList" and attr.get("data-context"):
            self.contexts.append(attr["data-context"])


def _usd_price(value: object) -> Decimal:
    if not isinstance(value, dict) or value.get("currency") != "USD":
        raise ValueError("QuickStop product lacked a USD listed price")
    try:
        price = Decimal(str(value.get("value")))
    except (InvalidOperation, TypeError):
        raise ValueError("QuickStop product had an invalid price") from None
    if not price.is_finite() or price <= 0:
        raise ValueError("QuickStop product had an invalid price")
    return price


def parse_page(page: str) -> tuple[list[dict], dict]:
    parser = ProductListParser()
    parser.feed(page)
    if len(parser.contexts) != 1:
        raise ValueError("QuickStop Magic category lacked one product list")
    context = json.loads(parser.contexts[0])
    current = (context.get("nestedCategoryContext") or {}).get("current") or {}
    rows = context.get("items")
    if current.get("fullUrl") != "/shop/magic-the-gathering" or not isinstance(rows, list):
        raise ValueError("QuickStop product list was not the Magic category")
    cards = []
    ids = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("QuickStop product row was invalid")
        product_id = str(row.get("id") or "")
        title = str(row.get("title") or "").strip()
        path = str(row.get("fullUrl") or "")
        variants = row.get("variants")
        if not product_id or product_id in ids or not title or not path.startswith("/shop/p/") or \
                not isinstance(variants, list):
            raise ValueError("QuickStop product lacked ID, title, URL, or variants")
        ids.add(product_id)
        if row.get("published") is not True or row.get("soldOut") is not False:
            continue
        try:
            stock = int(row.get("qtyInStock") or 0)
        except (TypeError, ValueError):
            raise ValueError("QuickStop product had invalid stock") from None
        if stock <= 0 or len(variants) != 1:
            continue
        variant = variants[0]
        if not isinstance(variant, dict) or variant.get("soldOut") is not False:
            continue
        try:
            variant_stock = int(variant.get("qtyInStock") or 0)
        except (TypeError, ValueError):
            raise ValueError("QuickStop product variant had invalid stock") from None
        if variant_stock <= 0:
            continue
        price = _usd_price(row.get("salePrice") if row.get("onSale") is True else row.get("price"))
        url = urljoin(SOURCE_URL, path)
        if urlsplit(url).hostname != "www.quickstopcards.com":
            raise ValueError("QuickStop product URL left the store")
        cards.append({"title": title, "url": url, "price": str(price)})
    status = {"site": "QuickStop CardShop", "format": "squarespace/html",
              "pages": 1, "products": len(rows), "available_products": len(cards),
              "complete": True, "source_url": SOURCE_URL}
    return cards, status


def cached_feed() -> tuple[list[dict], dict]:
    cards, status = parse_page(SAMPLE.read_text(encoding="utf-8", errors="replace"))
    status["cached"] = True
    return cards, status


def fetch_feed(idle_check=None) -> tuple[list[dict], dict]:
    status = {"site": "QuickStop CardShop", "format": "squarespace/html",
              "pages": 0, "products": 0, "complete": False, "source_url": SOURCE_URL}
    if idle_check and not idle_check():
        raise InterruptedError("computer became active during QuickStop read")
    try:
        request = urllib.request.Request(SOURCE_URL, headers={"User-Agent": USER_AGENT,
                                                          "Accept": "text/html"})
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(1_500_001)
        if len(raw) > 1_500_000:
            raise ValueError("QuickStop page exceeded 1.5 MB cap")
        return parse_page(raw.decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError, TypeError) as error:
        status["error"] = f"{type(error).__name__}: {error}"
        return [], status
