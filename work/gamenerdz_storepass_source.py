"""Read the first 300 Game Nerdz Magic listings from its Storepass search.

The user supplied the browser's request URL and chose this first-page limit.
The response includes accessories and out-of-stock items; only available items
with positive stock become candidates for the existing box/precon classifiers.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlencode, urlsplit


SAMPLE = Path(__file__).resolve().parent / "pending_site_samples" / "gamenerdz_storepass_300.json"
ENDPOINT = "https://store.storepass.com/saas/search"
FIELDS = ("id,productId,availability,stock,selectedFinish,url,imageUrl,price,salePrice,"
          "regularPrice,name,variantInfo,bigCommerceImages,msrp,tags,publisher,"
          "inventoryLevels,customCollectionImages,researchData.order_quantity_maximum")
QUERY = {
    "store_id": "OvpVz0pNlL", "mongo": "true", "override_buylist_gt_price": "true",
    "product_line": "All", "sort": "Sales", "limit": "300", "fields": FIELDS,
    "convert_to_currency": "", "round_price": "", "in_stock": "",
    "big_commerce_category_ids": "30", "brand_name": "", "shopify_collection_id": "",
    "name": "", "product_ids": "", "product_handles": "", "status": "unconnected",
    "sort_order": "Sales",
}
SOURCE_URL = ENDPOINT + "?" + urlencode(QUERY)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/145.0 Safari/537.36"


def parse_feed(payload: dict) -> tuple[list[dict], dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("products"), list):
        raise ValueError("Game Nerdz response has no product list")
    rows = payload["products"]
    if not rows or len(rows) > 300 or int(payload.get("current_page") or 0) != 1:
        raise ValueError("Game Nerdz first page was empty, oversized, or mislabeled")
    count = int(payload.get("count") or 0)
    if count < len(rows):
        raise ValueError("Game Nerdz result count was smaller than the first page")
    cards = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Game Nerdz product row was invalid")
        product_id = str(row.get("id") or "")
        if not product_id or product_id in seen:
            raise ValueError("Game Nerdz product ID was missing or repeated")
        seen.add(product_id)
        if row.get("availability") != "available":
            continue
        try:
            stock = int(row.get("stock") or 0)
        except (TypeError, ValueError):
            raise ValueError("Game Nerdz product had invalid stock") from None
        if stock <= 0:
            continue
        title = str(row.get("name") or "").strip()
        url = str(row.get("url") or "")
        if re.search(r"\bpre[ -]?orders?\b|\bpre[ -]?sales?\b", title + " " + url, re.I):
            continue
        try:
            price = Decimal(str(row.get("price")))
        except (InvalidOperation, TypeError):
            raise ValueError("Game Nerdz product had invalid listed price") from None
        parts = urlsplit(url)
        if not title or parts.scheme != "https" or parts.hostname != "www.gamenerdz.com" or \
                not price.is_finite() or price <= 0:
            raise ValueError("Game Nerdz product had invalid title, URL, or price")
        cards.append({"title": title, "url": url, "price": str(price)})
    status = {"site": "Game Nerdz", "format": "storepass/json", "pages": 1,
              "products": len(rows), "available_products": len(cards), "complete": True,
              "limited_to_first_page": True, "page_limit": 300,
              "source_url": SOURCE_URL}
    return cards, status


def cached_feed() -> tuple[list[dict], dict]:
    cards, status = parse_feed(json.loads(SAMPLE.read_text(encoding="utf-8")))
    status["cached"] = True
    return cards, status


def fetch_feed(idle_check=None) -> tuple[list[dict], dict]:
    status = {"site": "Game Nerdz", "format": "storepass/json", "pages": 0,
              "products": 0, "complete": False, "limited_to_first_page": True,
              "page_limit": 300, "source_url": SOURCE_URL}
    if idle_check and not idle_check():
        raise InterruptedError("computer became active during Game Nerdz read")
    try:
        request = urllib.request.Request(
            SOURCE_URL, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(3_000_001)
        if len(raw) > 3_000_000:
            raise ValueError("Game Nerdz response exceeded 3 MB cap")
        return parse_feed(json.loads(raw))
    except (urllib.error.URLError, OSError, ValueError, TypeError) as error:
        status["error"] = f"{type(error).__name__}: {error}"
        return [], status
