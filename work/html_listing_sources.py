"""Conservative listing parsers for selected non-Shopify Magic categories.

Only cards with a visible purchase action and a listed price are emitted.
These parsers consume category HTML; they never open individual products.
"""

from __future__ import annotations

import html
import re
from urllib.parse import urljoin, urlsplit


SUPPORTED_IDS = {"forgeandfiregaming", "miniaturemarket.com", "shop.justgamesrochester.com",
                 "collectorscache.com"}
STARTS = {
    "forgeandfiregaming": re.compile(r'<li\s+class="product"\s*>', re.I),
    "miniaturemarket.com": re.compile(r'<div\s+class="card product-box\b', re.I),
    "shop.justgamesrochester.com": re.compile(r'<div\s+class="prod-card"\s*>', re.I),
    "collectorscache.com": re.compile(r'<li\s+class="product"', re.I),
}


def _chunks(page: str, site_id: str):
    starts = [m.start() for m in STARTS[site_id].finditer(page)]
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(page)
        card = page[start:end]
        if site_id != "miniaturemarket.com":
            card = card.split("</li>", 1)[0]
        yield card[:25_000]


def _clean(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _card_anchor(card: str, site_id: str):
    if site_id == "forgeandfiregaming":
        pattern = r'<h4\s+class="card-title"[^>]*>\s*<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
    elif site_id == "miniaturemarket.com":
        pattern = r'<a\b[^>]*href="([^"]+)"[^>]*class="product-name stretched-link"[^>]*>(.*?)</a>'
    elif site_id == "collectorscache.com":
        pattern = r'<a\b[^>]*href="([^"]+)"[^>]*>\s*<h4\b[^>]*class="name [^"]+"[^>]*>(.*?)</h4>'
    else:
        pattern = r'<a\b[^>]*href="([^"]+)"[^>]*class="product-card__title"[^>]*>(.*?)</a>'
    match = re.search(pattern, card, re.I | re.S)
    return (html.unescape(match.group(1)), _clean(match.group(2))) if match else None


def _card_price(card: str, site_id: str):
    if site_id == "forgeandfiregaming":
        pattern = r'<span\b[^>]*data-product-price-without-tax[^>]*>\s*\$([\d,]+\.\d{2})'
    elif site_id == "miniaturemarket.com":
        pattern = r'<span\s+class="product-price"[^>]*>\s*\$([\d,]+\.\d{2})'
    elif site_id == "collectorscache.com":
        grid = card.split('class="list-variants grid', 1)[-1].split('class="list-variants list', 1)[0]
        if '<div class="variant-row in-stock">' not in grid:
            return None
        form = re.search(r'<form\b[^>]*class="add-to-cart-form"[^>]*data-price="\$([\d,]+\.\d{2})"'
                         r'[^>]*data-variant="Brand New"', grid, re.I | re.S)
        return form.group(1).replace(",", "") if form else None
    else:
        pattern = r'<ins\s+class="prod-card__price"[^>]*>\s*\$([\d,]+\.\d{2})'
    match = re.search(pattern, card, re.I | re.S)
    return match.group(1).replace(",", "") if match else None


def _purchasable(card: str, site_id: str) -> bool:
    if site_id == "collectorscache.com":
        grid = card.split('class="list-variants grid', 1)[-1].split('class="list-variants list', 1)[0]
        return bool(not re.search(r'<span[^>]*class="preorder|<input[^>]*class="preorder-button',
                                  card, re.I)
                    and '<div class="variant-row in-stock">' in grid
                    and re.search(r'<input\b[^>]*class="utility-button add-to-cart"', grid, re.I))
    if re.search(r"pre[ -]?order|pre[ -]?sale", card, re.I):
        return False
    if site_id == "forgeandfiregaming":
        return bool(re.search(r'card-figcaption-button[^>]*>\s*Add to Cart\s*</a>', card, re.I))
    if site_id == "miniaturemarket.com":
        return 'data-add-to-cart="true"' in card and 'class="buy-widget"' in card
    return bool(re.search(r'href="[^"]*/cart/add/[^" ]+"[^>]*>\s*Add to cart\s*</a>', card, re.I))


def extract_cards(page: str, site_id: str, page_url: str) -> list[dict]:
    if site_id not in SUPPORTED_IDS:
        raise ValueError(f"unsupported HTML site: {site_id}")
    rows = []
    seen = set()
    for card in _chunks(page, site_id):
        anchor = _card_anchor(card, site_id)
        if not anchor:
            continue
        href, title = anchor
        if not title or not _purchasable(card, site_id):
            continue
        price = _card_price(card, site_id)
        if not price:
            continue
        url = urljoin(page_url, href)
        if urlsplit(url).hostname != urlsplit(page_url).hostname or url in seen:
            continue
        seen.add(url)
        rows.append({"title": title, "url": url, "price": price})
    return rows


def next_page(page: str, site_id: str, page_url: str) -> str | None:
    if site_id == "forgeandfiregaming":
        match = re.search(r'<link\b[^>]*rel="next"[^>]*href="([^"]+)"', page, re.I)
    elif site_id == "shop.justgamesrochester.com":
        match = re.search(r'<a\b[^>]*href="([^"]+)"[^>]*aria-label="Go to next page"', page, re.I)
    elif site_id == "collectorscache.com":
        match = re.search(r'<a\b[^>]*href="([^"]+)"[^>]*class="next_page"[^>]*rel="next"', page, re.I)
    else:
        return None  # The selected in-stock category showed all 20 products on one page.
    if not match:
        return None
    url = urljoin(page_url, html.unescape(match.group(1)))
    if urlsplit(url).hostname != urlsplit(page_url).hostname:
        return None
    return url
