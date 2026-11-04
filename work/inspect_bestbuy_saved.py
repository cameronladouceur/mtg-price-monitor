"""Summarize product cards in a user-saved Best Buy clearance page offline."""

import json
import re
from html.parser import HTMLParser
from pathlib import Path


SOURCE = Path.home() / "Downloads" / "Clearance Magic_ The Gathering Trading Cards - Best Buy.htm"
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class CardParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cards = []
        self.page_text_parts = []
        self.card = None
        self.depth = 0
        self.title_depth = None
        self.price_depth = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "li" and "product-list-item" in a.get("class", "").split():
            self.card = {"sku": a.get("data-product-id"), "url": None,
                         "title_parts": [], "price_parts": [], "text_parts": []}
            self.depth = 1
            return
        if self.card is None:
            return
        if tag not in VOID_TAGS:
            self.depth += 1
        if tag == "a" and "/product/" in a.get("href", "") and not self.card["url"]:
            self.card["url"] = a["href"]
            self.title_depth = self.depth
        if a.get("data-testid") == "price-block-customer-price":
            self.price_depth = self.depth

    def handle_endtag(self, tag):
        if self.card is None or tag in VOID_TAGS:
            return
        if self.title_depth == self.depth:
            self.title_depth = None
        if self.price_depth == self.depth:
            self.price_depth = None
        self.depth -= 1
        if self.depth == 0:
            card = self.card
            card["title"] = " ".join(" ".join(card.pop("title_parts")).split())
            card["price_text"] = " ".join(" ".join(card.pop("price_parts")).split())
            full_text = " ".join(" ".join(card.pop("text_parts")).split())
            if not card["title"]:
                card["title"] = re.split(r"\bRating\b", full_text, maxsplit=1)[0].strip()
            match = re.search(r"\$\s*(\d+(?:\.\d{2})?)", card["price_text"])
            card["price"] = float(match.group(1)) if match else None
            card["sold_out"] = bool(re.search(r"\bSold Out\b|\bNotify Me\b", full_text))
            card["available"] = bool(not card["sold_out"] and re.search(
                r"\bAdd to Cart\b|\bGet it by\b|\bPick up\b", full_text, re.I))
            card["text"] = full_text[:260]
            self.cards.append(card)
            self.card = None

    def handle_data(self, data):
        if not data.strip():
            return
        self.page_text_parts.append(data)
        if self.card is None:
            return
        self.card["text_parts"].append(data)
        if self.title_depth is not None:
            self.card["title_parts"].append(data)
        if self.price_depth is not None:
            self.card["price_parts"].append(data)


def main():
    html = SOURCE.read_text(encoding="utf-8", errors="replace")
    parser = CardParser()
    parser.feed(html)
    cards = parser.cards
    page_text = " ".join(" ".join(parser.page_text_parts).split())
    ids = [card["sku"] for card in cards]
    sealed = [card for card in cards if "Wizards of The Coast" in card["title"]
              and not re.search(r"\bBooster Sleeve\b", card["title"])]
    print(json.dumps({
        "saved_file_size": len(html),
        "card_count": len(cards),
        "unique_sku_count": len(set(ids)),
        "cards_with_url": sum(bool(card["url"]) for card in cards),
        "cards_with_price": sum(bool(card["price_text"]) for card in cards),
        "sealed_cards": len(sealed),
        "sealed_sold_out": sum(card["sold_out"] for card in sealed),
        "results_mentions": re.findall(r"\b(?:\d+\s+of\s+\d+|\d+\s+results)\b", page_text, re.I)[:8],
        "sealed": [{key: card[key] for key in ("sku", "title", "price", "sold_out", "url")}
                   for card in sealed],
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
