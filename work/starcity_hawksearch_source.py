"""One filtered Star City Games HawkSearch listing read per scan.

The request body is the site's own in-stock Booster Boxes filter, supplied by
the user from DevTools. No product pages or checkout requests are made.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urljoin, urlsplit


ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://ajax.starcitygames.com/hawksearch/searchapi/api/v2/search"
REQUEST_BODY = ROOT / "work" / "starcity_search_request.json"
STORE_URL = "https://starcitygames.com/"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/145.0 Safari/537.36"


def one(document: dict, name: str):
    value = document.get(name)
    if isinstance(value, list):
        return value[0] if len(value) == 1 else None
    return value


def parse_page(payload: dict, page_number: int) -> tuple[list[dict], int, int, int]:
    if payload.get("Success") is not True or payload.get("QueryValidationErrors"):
        raise ValueError("Star City Games search failed or rejected the filters")
    page = payload.get("Pagination") or {}
    rows = payload.get("Results")
    if not isinstance(rows, list):
        raise ValueError("Star City Games response has no result list")
    current = int(page.get("CurrentPage") or 0)
    total = int(page.get("NofResults") or 0)
    pages = int(page.get("NofPages") or 0)
    size = int(page.get("MaxPerPage") or 0)
    if current != page_number or pages < 1 or size != 96 or not 0 <= total <= pages * size:
        raise ValueError("Star City Games pagination did not match the request")
    if len(rows) > size or (page_number < pages and len(rows) != size):
        raise ValueError("Star City Games returned an incomplete result page")
    cards = []
    ids = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("Document"), dict):
            raise ValueError("Star City Games result lacked product details")
        doc_id = str(row.get("DocId") or "")
        doc = row["Document"]
        required = ("product_name", "price_retail", "availability", "metric_inventory",
                    "url_detail", "language", "sealed_type", "is_visible")
        if not doc_id or doc_id in ids or any(one(doc, field) is None for field in required):
            raise ValueError("Star City Games result lacked unique ID or required fields")
        ids.add(doc_id)
        if (one(doc, "availability") != "Available" or int(one(doc, "metric_inventory")) <= 0
                or one(doc, "language") != "English" or one(doc, "sealed_type") != "Booster Boxes"
                or one(doc, "is_visible") is not True or one(doc, "preorder_release_date")
                or one(doc, "preorder_message")):
            continue
        name = str(one(doc, "product_name") or "").strip()
        path = str(one(doc, "url_detail") or "")
        try:
            price = Decimal(str(one(doc, "price_retail")))
        except (InvalidOperation, TypeError):
            raise ValueError("Star City Games result had invalid retail price") from None
        url = urljoin(STORE_URL, path)
        if not name or not path.startswith("/") or path.startswith("//") or \
                urlsplit(url).hostname != "starcitygames.com" or not price.is_finite() or price <= 0:
            raise ValueError("Star City Games result had invalid title, price, or URL")
        cards.append({"title": name, "url": url, "price": str(price)})
    return cards, len(rows), total, pages


def fetch_feed(idle_check=None) -> tuple[list[dict], dict]:
    status = {"site": "Star City Games", "format": "hawksearch/json", "pages": 0,
              "complete": False, "products": 0, "source_url": SOURCE_URL}
    cards = []
    page_number = 1
    try:
        base_body = json.loads(REQUEST_BODY.read_text(encoding="utf-8"))
        while True:
            if idle_check and not idle_check():
                raise InterruptedError("computer became active during Star City Games read")
            if page_number > 1:
                time.sleep(2)
            body = dict(base_body)
            if page_number > 1:
                body["PageNo"] = page_number
            request = urllib.request.Request(
                SOURCE_URL, data=json.dumps(body).encode("utf-8"), method="POST",
                headers={"User-Agent": USER_AGENT, "Content-Type": "application/json",
                         "Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ValueError("Star City Games response exceeded 2 MB cap")
            page_cards, count, total, pages = parse_page(json.loads(raw), page_number)
            if pages > 10:
                raise ValueError("Star City Games search exceeded 10-page safety limit")
            cards.extend(page_cards)
            status["pages"] += 1
            status["products"] += count
            if page_number >= pages:
                if status["products"] != total:
                    raise ValueError("Star City Games search result count was incomplete")
                status["complete"] = True
                status["available_products"] = len(cards)
                return cards, status
            page_number += 1
    except InterruptedError:
        raise
    except (urllib.error.URLError, OSError, ValueError, TypeError) as error:
        status["error"] = f"{type(error).__name__}: {error}"
        return [], status
