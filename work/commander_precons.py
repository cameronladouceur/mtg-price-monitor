"""Conservative recognition of individual, named MTG Commander precons."""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit, urlunsplit


# Named products are explicit so a generic/random deck or a multi-deck set
# cannot be compared with an individual precon. Extend after reviewing titles.
DECKS = {
    "Aetherdrift": ("Living Energy", "Eternal Might"),
    "Doctor Who": ("Masters of Evil", "Timey-Wimey"),
    "Edge of Eternities": ("Counter Intelligence", "World Shaper"),
    "Final Fantasy": ("Revival Trance", "Limit Break", "Counter Blitz", "Scions and Spellcraft"),
    "Foundations": ("Calling All Angels", "Tramplesaurus Rex", "Reign of Dragons", "Wretched Ranks", "Keen Engineering"),
    "Lorwyn Eclipsed": ("Dance of the Elements", "Blight Curse"),
    "Marvel Super Heroes": ("The Fantastic Four", "Wakanda Forever", "Avengers Assemble", "Doom Prevails"),
    "Reality Fracture": ("Multiverse Reforged",),
    "Secrets of Strixhaven": ("Quandrix Unlimited", "Witherbloom Pestilence", "Prismari Artistry", "Lorehold Spirit"),
    "Star Trek": ("We Are the Borg", "Landing Party", "Federation Fleet", "Klingon Fury"),
    "Starter Commander": ("First Flight", "Grave Danger", "Draconic Destruction", "Chaos Incarnate", "Token Triumph"),
}


def catalog_rows() -> dict[str, dict]:
    return {f"Commander Precon | {set_name} | {name}": {
        "set": set_name, "deck_name": name, "box_type": "Commander Precon",
        "product_key": f"Commander Precon | {set_name} | {name}",
        "site": None, "url": None, "listed_price": None, "last_seen_utc": None,
    } for set_name, names in DECKS.items() for name in names}
EXCLUDED = re.compile(
    r"\b(?:pre[ -]?order|pre[ -]?sale|collector(?:'s)? edition|set of|all [2-9] decks?|"
    r"bundle|case|lot|assort(?:ed|ment)|random|repack|used|damaged|"
    r"japanese|french|german|spanish|italian|korean|portuguese|chinese)\b|\bx[2-9]\b",
    re.I,
)


def normalize(value: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", ascii_text).strip()


def identify(title: str) -> tuple[str, str, str] | None:
    clean = normalize(title)
    if not re.search(r"\bcommander decks?\b", clean) or EXCLUDED.search(title):
        return None
    for set_name, names in DECKS.items():
        for name in names:
            if re.search(r"\b" + re.escape(normalize(name)) + r"\b", clean):
                return set_name, name, f"Commander Precon | {set_name} | {name}"
    return None


def _amount(value: object) -> Decimal | None:
    try:
        amount = Decimal(str(value))
        return amount if amount.is_finite() and amount > 0 else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _image(product: dict) -> str | None:
    images = product.get("images") or []
    candidate = (images[0].get("src") if isinstance(images[0], dict) else images[0]) if images else None
    if not candidate:
        candidate = (product.get("image") or {}).get("src") if isinstance(product.get("image"), dict) else None
    return candidate if isinstance(candidate, str) and candidate.startswith("https://cdn.shopify.com/") else None


def shopify_offers(source: dict, products: list[dict]) -> list[dict]:
    parts = urlsplit(source["scrape_start_url"])
    offers = []
    seen = set()
    for product in products:
        title = str(product.get("title") or "")
        handle = str(product.get("handle") or "")
        if not handle or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", handle):
            continue
        url = urlunsplit((parts.scheme, parts.netloc, "/products/" + handle, "", ""))
        if EXCLUDED.search(url.replace("-", " ")):
            continue
        for variant in product.get("variants") or []:
            if variant.get("available") is not True:
                continue
            variant_title = str(variant.get("title") or "")
            match = identify(title + (" " + variant_title if variant_title != "Default Title" else ""))
            if not match:
                continue
            amount = _amount(variant.get("price"))
            if amount is None:
                continue
            set_name, deck_name, key = match
            offer_id = (key, url, str(variant.get("id") or ""))
            if offer_id in seen:
                continue
            seen.add(offer_id)
            offer = {"set": set_name, "deck_name": deck_name, "box_type": "Commander Precon",
                     "product_key": key, "site": source["site"], "title": title,
                     "url": url, "listed_price": str(amount)}
            image = _image(product)
            if image:
                offer["image_url"] = image
            offers.append(offer)
    return offers


def listing_offers(source: dict, cards: list[dict]) -> list[dict]:
    """HTML/Woot cards already carry their adapter's purchase/stock signal."""
    if source["id"] == "target":
        return []  # Target's search result has no verified availability field.
    offers = []
    seen = set()
    for card in cards:
        title = str(card.get("title") or "")
        match = identify(title)
        if not match:
            continue
        amount = _amount(card.get("price"))
        url = str(card.get("url") or "")
        if amount is None or not url.startswith("https://") or url in seen:
            continue
        seen.add(url)
        set_name, deck_name, key = match
        offers.append({"set": set_name, "deck_name": deck_name, "box_type": "Commander Precon",
                       "product_key": key, "site": card.get("site") or source["site"], "title": title,
                       "url": url, "listed_price": str(amount)})
    return offers


def best_offers(offers: list[dict]) -> dict[str, dict]:
    best = {}
    for offer in offers:
        key = offer["product_key"]
        if key not in best or _amount(offer["listed_price"]) < _amount(best[key]["listed_price"]):
            best[key] = offer
    return best
