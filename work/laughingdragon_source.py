"""One Laughing Dragon English booster-box listing read per scan.

The site's own JavaScript posts this category to Conduct Commerce. A fresh
request ID works without a browser session; only positive-stock New variants
are emitted for the existing Play/Draft booster-box classifier.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://api.conductcommerce.com/v1/getProductListings"
CATEGORY = "Booster Boxes - English"
HOST = "laughingdragonmtg.com"
SAMPLE = ROOT / "work" / "pending_site_samples" / "laughingdragon_listings.json"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/145.0 Safari/537.36"


def parse_feed(payload: dict) -> tuple[list[dict], dict]:
    if not isinstance(payload, dict) or payload.get("success") is not True or payload.get("errors"):
        raise ValueError("Laughing Dragon listing request failed")
    result = payload.get("result") or {}
    listings = result.get("listings")
    if not isinstance(listings, list) or not listings or result.get("isBuyCategory") is True:
        raise ValueError("Laughing Dragon returned no retail product listings")
    cards = []
    ids = set()
    for listing in listings:
        if not isinstance(listing, dict) or listing.get("categoryName") != CATEGORY:
            raise ValueError("Laughing Dragon returned an unexpected category")
        inventory_id = listing.get("inventoryID")
        title = str(listing.get("inventoryName") or "").strip()
        variants = listing.get("variants")
        if not isinstance(inventory_id, int) or inventory_id <= 0 or inventory_id in ids or \
                not title or not isinstance(variants, list):
            raise ValueError("Laughing Dragon product lacked ID, title, or variants")
        ids.add(inventory_id)
        if listing.get("hide") or re.search(r"\bpre[ -]?orders?\b|\bpre[ -]?sales?\b|not before", title, re.I):
            continue
        new_prices = []
        for variant in variants:
            if not isinstance(variant, dict):
                raise ValueError("Laughing Dragon variant was invalid")
            if variant.get("name") != "New":
                continue
            try:
                quantity = int(variant.get("quantity") or 0)
            except (TypeError, ValueError):
                raise ValueError("Laughing Dragon New variant had invalid stock") from None
            if quantity <= 0:
                continue
            if not isinstance(variant.get("id"), int):
                raise ValueError("Laughing Dragon stocked New variant lacked an ID")
            try:
                price = Decimal(str(variant.get("price")))
            except (InvalidOperation, TypeError):
                raise ValueError("Laughing Dragon New variant had invalid price") from None
            if not price.is_finite() or price <= 0:
                raise ValueError("Laughing Dragon New variant had invalid price")
            new_prices.append(price)
        if not new_prices:
            continue
        slug = quote(title.replace(" ", "_"), safe="")
        url = f"https://{HOST}/store/category/{quote(CATEGORY, safe='')}/item/{inventory_id}/{slug}"
        cards.append({"title": title, "url": url, "price": str(min(new_prices))})
    status = {"site": "Laughing Dragon MTG", "format": "conduct-commerce/json",
              "pages": 1, "products": len(listings), "available_products": len(cards),
              "complete": True, "source_url": SOURCE_URL}
    return cards, status


def cached_feed() -> tuple[list[dict], dict]:
    cards, status = parse_feed(json.loads(SAMPLE.read_text(encoding="utf-8")))
    status["cached"] = True
    return cards, status


def fetch_feed(idle_check=None) -> tuple[list[dict], dict]:
    status = {"site": "Laughing Dragon MTG", "format": "conduct-commerce/json",
              "pages": 0, "products": 0, "complete": False, "source_url": SOURCE_URL}
    if idle_check and not idle_check():
        raise InterruptedError("computer became active during Laughing Dragon read")
    try:
        body = json.dumps({"category": CATEGORY, "host": HOST,
                           "reqID": str(uuid.uuid4())}).encode("utf-8")
        request = urllib.request.Request(
            SOURCE_URL, data=body, method="POST",
            headers={"User-Agent": USER_AGENT, "Content-Type": "text/plain",
                     "Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(1_500_001)
        if len(raw) > 1_500_000:
            raise ValueError("Laughing Dragon response exceeded 1.5 MB cap")
        return parse_feed(json.loads(raw))
    except (urllib.error.URLError, OSError, ValueError, TypeError) as error:
        status["error"] = f"{type(error).__name__}: {error}"
        return [], status
