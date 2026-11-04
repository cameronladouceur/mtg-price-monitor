"""Discover current Woot deals from Woot Search's public MCP search feed.

Woot event URLs expire. This uses three low-frequency search terms rather than
visiting Woot categories or product pages. Woot Search is an independent site
that obtains deal data from Woot's API; result links redirect to Woot.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path


MCP_URL = "https://wootsearch.com/mcp"
QUERIES = ("magic the gathering", "mtg", "booster box", "booster display")
UUID = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$", re.I)
SAMPLE = Path(__file__).resolve().parent / "wootsearch_probe.json"


def _post(method: str, params: dict, call_id: int, session: str | None) -> tuple[dict, str | None]:
    payload = json.dumps({"jsonrpc": "2.0", "id": call_id, "method": method,
                          "params": params}).encode("utf-8")
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream",
               "MCP-Protocol-Version": "2025-06-18",
               "User-Agent": "MTG-Box-Monitor/1.0"}
    if session:
        headers["Mcp-Session-Id"] = session
    request = urllib.request.Request(MCP_URL, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=18) as response:
        body = response.read(1_500_001)
        if len(body) > 1_500_000:
            raise ValueError("Woot Search response exceeded 1.5 MB")
        content_type = response.headers.get("Content-Type", "")
        session = response.headers.get("Mcp-Session-Id") or session
    body_text = body.decode("utf-8", "replace")
    if "text/event-stream" in content_type:
        events = [line[6:] for line in body_text.splitlines() if line.startswith("data: ")]
        if not events:
            raise ValueError("Woot Search returned no MCP event")
        result = json.loads(events[-1])
    else:
        result = json.loads(body_text)
    if result.get("error") or result.get("result", {}).get("isError"):
        raise ValueError("Woot Search MCP request failed")
    return result, session


def cards_from_results(results: list[dict]) -> list[dict]:
    """Accept only available, new, single-price offers with a safe Woot redirect."""
    cards = []
    seen = set()
    for row in results:
        offer_id = str(row.get("id", ""))
        if (not UUID.fullmatch(offer_id) or offer_id in seen
                or row.get("isSoldOut") is not False
                or str(row.get("vendor", "")).lower() != "woot"):
            continue
        condition = str(row.get("condition") or "").strip().lower()
        if condition not in ("", "new"):
            continue
        title = str(row.get("title") or "").strip()
        if not title or re.search(r"open[ -]?box|repack|damaged|used|renewed", title, re.I):
            continue
        try:
            low = Decimal(str(row.get("priceMin")))
            high = Decimal(str(row.get("priceMax")))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if not low.is_finite() or low <= 0 or low != high:
            continue
        seen.add(offer_id)
        cards.append({"title": title,
                      "url": f"https://api.wootsearch.com/offers/{offer_id}/click?source=mcp",
                      "price": str(low), "offer_id": offer_id})
    return cards


def fetch_woot_search(idle_guard=None) -> tuple[list[dict], dict]:
    """One initialize request, then a few spaced, read-only searches."""
    status = {"site": "Woot", "format": "woot-search", "pages": 0,
              "complete": False, "search_terms": list(QUERIES)}
    rows = []
    seen = set()
    call_id = 1
    last_request = None
    session = None

    def paced_post(method, params):
        nonlocal call_id, last_request, session
        if idle_guard and not idle_guard():
            raise InterruptedError("computer became active; scan stopped without changing benchmarks")
        if last_request is not None:
            time.sleep(max(0, 1.0 - (time.monotonic() - last_request)))
        last_request = time.monotonic()
        result, session = _post(method, params, call_id, session)
        call_id += 1
        return result

    try:
        paced_post("initialize", {"protocolVersion": "2025-06-18",
                                  "capabilities": {},
                                  "clientInfo": {"name": "mtg-box-monitor", "version": "1.0"}})
        for query in QUERIES:
            for page in range(1, 11):
                response = paced_post("tools/call", {"name": "search_offers",
                                                      "arguments": {"query": query,
                                                                    "includeSoldOut": False,
                                                                    "page": page}})
                content = response.get("result", {}).get("structuredContent", {})
                found = content.get("results")
                if not isinstance(found, list):
                    raise ValueError("Woot Search response has no results array")
                status["pages"] += 1
                for row in found:
                    if isinstance(row, dict) and row.get("id") not in seen:
                        seen.add(row.get("id"))
                        rows.append(row)
                if len(found) < 20:
                    break
            else:
                raise ValueError("Woot Search exceeded 10-page safety limit")
        status["complete"] = True
    except InterruptedError:
        raise
    except (OSError, ValueError, json.JSONDecodeError) as error:
        status["error"] = f"{type(error).__name__}: {error}"
        rows = []  # Fail closed on partial or malformed search results.
    cards = cards_from_results(rows)
    status["products"] = len(cards)
    return cards, status


def cached_woot_search() -> tuple[list[dict], dict]:
    if not SAMPLE.exists():
        return [], {"site": "Woot", "format": "woot-search", "pages": 0,
                    "complete": False, "error": "no cached Woot Search sample"}
    searches = json.loads(SAMPLE.read_text(encoding="utf-8"))
    rows = [row for search in searches for row in search.get("data", {}).get("results", [])]
    cards = cards_from_results(rows)
    return cards, {"site": "Woot", "format": "woot-search", "pages": len(searches),
                   "complete": False, "cached": True, "products": len(cards)}
