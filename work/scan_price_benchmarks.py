"""Scan selected Magic collection feeds and alert on a new lowest listed price.

The first successful scan seeds each set/type benchmark without an alert.
Later scans report every available offer below the saved benchmark and lower
the benchmark. Requests are sequential and continue until a feed's last page.
No product pages or checkout are visited.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
import gzip
import json
import msvcrt
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from threshold_outbox import queue_threshold_alerts
from deliver_threshold_emails import deliver
from html_listing_sources import SUPPORTED_IDS, extract_cards, next_page
from woot_search_source import cached_woot_search, fetch_woot_search
from target_redsky_source import cached_target_search, fetch_target_search
from amazon_cli_source import fetch_amazon_products
from cardkingdom_source import SOURCE_URL as CARDKINGDOM_FEED_URL, fetch_feed as fetch_cardkingdom_feed, parse_feed as parse_cardkingdom_feed
from starcity_hawksearch_source import SOURCE_URL as STARCITY_SEARCH_URL, fetch_feed as fetch_starcity_feed
from gamenerdz_storepass_source import SOURCE_URL as GAMENERDZ_SEARCH_URL, fetch_feed as fetch_gamenerdz_feed, cached_feed as cached_gamenerdz_feed
from hottopic_source import SOURCE_URL as HOTTOPIC_SOURCE_URL, fetch_feed as fetch_hottopic_feed, cached_feed as cached_hottopic_feed
from laughingdragon_source import SOURCE_URL as LAUGHINGDRAGON_SOURCE_URL, fetch_feed as fetch_laughingdragon_feed, cached_feed as cached_laughingdragon_feed
from quickstop_squarespace_source import SOURCE_URL as QUICKSTOP_SOURCE_URL, fetch_feed as fetch_quickstop_feed, cached_feed as cached_quickstop_feed
from bestbuy_clearance_source import SOURCE_URL as BESTBUY_CLEARANCE_URL, record_scan as record_bestbuy_clearance, deliver_emails as deliver_bestbuy_emails
from commander_precons import best_offers as best_precon_offers, catalog_rows as precon_catalog_rows, identify as identify_precon, listing_offers as listing_precon_offers, shopify_offers as shopify_precon_offers


ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS = ROOT / "price_benchmarks.json"
LATEST = ROOT / "latest_price_scan.json"
ALERTS = ROOT / "new_price_alerts.jsonl"
SCAN_LOG = ROOT / "price_scan_log.jsonl"
ACTIVE_SCAN_LOG: dict | None = None
WINDOWS_OUTBOX = ROOT / "windows_notification_outbox.json"
SCAN_LOCK = ROOT / "price_scan.lock"
CLOUD_COOLDOWNS = ROOT / "cloud_source_cooldowns.json"
PACK_COUNTS = ROOT / "box_pack_counts.json"
RELEASE_DATES = ROOT / "box_release_dates.json"
RETAILER_EXCLUSIONS = ROOT / "RETAILER_EXCLUSIONS.json"
PRECONS = ROOT / "commander_precons.json"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/145.0 Safari/537.36"
pack_counts = (json.loads(PACK_COUNTS.read_text(encoding="utf-8")).get("boxes", {})
               if PACK_COUNTS.exists() else {})
release_dates = (json.loads(RELEASE_DATES.read_text(encoding="utf-8")).get("boxes", {})
                 if RELEASE_DATES.exists() else {})

# Only explicit set names/aliases are grouped. An unrecognized title is held
# for review instead of risking a comparison with the wrong box.
SETS = [
    ("Adventures in the Forgotten Realms", [r"adventures in the forgotten realms"]),
    ("Aetherdrift", [r"aetherdrift"]),
    ("Aether Revolt", [r"aether revolt"]),
    ("Alara Reborn", [r"alara reborn"]),
    ("Amonkhet", [r"\bamonkhet\b", r"\bamonket\b"]),
    ("Avatar: The Last Airbender", [r"avatar.{0,12}last airbender", r"\bthe last airbender\b"]),
    ("Avacyn Restored", [r"avacyn restored"]),
    ("Battlebond", [r"\bbattlebond\b"]),
    ("Betrayers of Kamigawa", [r"betrayers of kamigawa"]),
    ("Bloomburrow", [r"bloomburrow"]),
    ("Born of the Gods", [r"born of the gods"]),
    ("Champions of Kamigawa", [r"champions of kamigawa"]),
    ("Commander Legends", [r"commander legends(?!.*baldur)"]),
    ("Commander Legends: Battle for Baldur's Gate", [r"commander legends.{0,25}baldur"]),
    ("Commander Masters", [r"commander masters"]),
    ("Conflux", [r"\bconflux\b"]),
    ("Conspiracy", [r"\bconspiracy\b"]),
    ("Core Set 2011", [r"core set 2011", r"magic 2011"]),
    ("Core Set 2012", [r"core set 2012", r"magic 2012"]),
    ("Core Set 2013", [r"core set 2013", r"magic 2013"]),
    ("Core Set 2019", [r"core set 2019", r"\bm19\b"]),
    ("Core Set 2020", [r"core set 2020", r"\bm20\b"]),
    ("Core Set 2021", [r"core set 2021", r"\bm21\b"]),
    ("Dark Ascension", [r"dark ascension"]),
    ("Darksteel", [r"\bdarksteel\b"]),
    ("Dominaria Remastered", [r"dominaria remastered"]),
    ("Dominaria United", [r"dominaria united"]),
    ("Dominaria", [r"\bdominaria\b"]),
    ("Double Masters 2022", [r"double masters 2022"]),
    ("Double Masters", [r"double masters"]),
    ("Dragon's Maze", [r"dragon s maze", r"dragons maze"]),
    ("Dragons of Tarkir", [r"dragons of tarkir"]),
    ("Duskmourn: House of Horror", [r"duskmourn"]),
    ("Edge of Eternities", [r"edge of eternities"]),
    ("Eldritch Moon", [r"eldritch moon"]),
    ("Eternal Masters", [r"eternal masters"]),
    ("Fallen Empires", [r"fallen empires"]),
    ("Fate Reforged", [r"fate reforged"]),
    ("Final Fantasy", [r"final fantasy"]),
    ("Foundations", [r"foundations"]),
    ("Gatecrash", [r"\bgatecrash\b"]),
    ("Guilds of Ravnica", [r"guilds of ravnica"]),
    ("Hour of Devastation", [r"hour of devastation"]),
    ("Iconic Masters", [r"iconic masters"]),
    ("Innistrad Remastered", [r"innistrad remastered"]),
    ("Ikoria: Lair of Behemoths", [r"ikoria.{0,5}lair of behemoths"]),
    ("Innistrad: Crimson Vow", [r"innistrad.{0,5}crimson vow", r"crimson vow"]),
    ("Innistrad: Double Feature", [r"innistrad.{0,5}double feature", r"double feature"]),
    ("Innistrad: Midnight Hunt", [r"innistrad.{0,5}midnight hunt", r"midnight hunt"]),
    ("Kamigawa Titanbreach", [r"kamigawa titanbreach"]),
    ("Kamigawa: Neon Dynasty", [r"kamigawa.{0,5}neon dynasty"]),
    ("Kaldheim", [r"kaldheim"]),
    ("Kaladesh", [r"\bkaladesh\b"]),
    ("Khans of Tarkir", [r"khans of tarkir"]),
    ("Lorwyn Eclipsed", [r"lorwyn eclipsed"]),
    ("The Lord of the Rings: Tales of Middle-earth", [r"lord of the rings(?:.{0,20}middle earth)?"]),
    ("March of the Machine", [r"march of the machine"]),
    ("Magic Origins", [r"magic origins"]),
    ("Marvel Darkhold Destiny", [r"marvel darkhold destiny"]),
    ("Marvel Super Heroes", [r"marvel super heroes"]),
    ("Modern Horizons 3", [r"modern horizons 3", r"modern horizons iii"]),
    ("Modern Horizons 2", [r"modern horizons 2", r"modern horizons ii"]),
    ("Modern Horizons", [r"modern horizons"]),
    ("Murders at Karlov Manor", [r"murders at karlov manor"]),
    ("Mirrodin Besieged", [r"mirrodin besieged"]),
    ("Nauctis: The Sunken Realm", [r"nauctis.{0,5}sunken realm"]),
    ("Nemesis", [r"\bnemesis\b"]),
    ("New Phyrexia", [r"new phyrexia"]),
    ("Odyssey", [r"\bodyssey\b"]),
    ("Outlaws of Thunder Junction", [r"outlaws of thunder junction"]),
    ("Phyrexia: All Will Be One", [r"phyrexia.{0,5}all will be one"]),
    ("Ravnica Remastered", [r"ravnica remastered"]),
    ("Ravnica Allegiance", [r"ravnica allegiance"]),
    ("Reality Fracture", [r"reality fracture"]),
    ("Rivals of Ixalan", [r"rivals of ixalan"]),
    ("Scars of Mirrodin", [r"scars of mirrodin"]),
    ("Secrets of Strixhaven", [r"secrets? of strixhaven"]),
    ("Marvel's Spider-Man", [r"(?:marvels? )?spider man", r"spiderman"]),
    ("Star Trek", [r"star trek"]),
    ("Streets of New Capenna", [r"(?:streets of )?new capenna"]),
    ("Strixhaven: School of Mages", [r"strixhaven.{0,5}school of mages", r"\bstrixhaven\b"]),
    ("Shadows over Innistrad", [r"shadows over innistrad"]),
    ("Tarkir: Dragonstorm", [r"tarkir.{0,5}dragonstorm"]),
    ("Teenage Mutant Ninja Turtles", [r"teenage mutant ninja turtles", r"\btmnt\b"]),
    ("The Hobbit", [r"\bthe hobbit\b"]),
    ("The Brothers' War", [r"(?:the )?brothers war"]),
    ("The Lost Caverns of Ixalan", [r"(?:the )?lost caverns of ixalan"]),
    ("Ixalan", [r"\bixalan\b"]),
    ("Theros Beyond Death", [r"theros beyond death"]),
    ("Theros", [r"\btheros\b"]),
    ("Throne of Eldraine", [r"throne of eldraine"]),
    ("Tempest", [r"\btempest\b"]),
    ("Time Spiral Remastered", [r"time spiral remastered"]),
    ("Unfinity", [r"unfinity"]),
    ("Ultimate Masters", [r"ultimate masters"]),
    ("Urza's Legacy", [r"urza s legacy", r"urzas legacy"]),
    ("Urza's Destiny", [r"urza s destiny", r"urzas destiny"]),
    ("Urza's Saga", [r"urza s saga", r"urzas saga"]),
    ("Visions", [r"\bvisions\b"]),
    ("War of the Spark", [r"war of the spark"]),
    ("Wilds of Eldraine", [r"wilds of eldraine"]),
    ("Zendikar Rising", [r"zendikar rising"]),
    ("Zendikar", [r"\bzendikar\b"]),
    ("Zhalfir", [r"\bzhalfir\b"]),
]
EXCLUDED = re.compile(r"\b(?:pre[ -]?order|pre[ -]?sale|pre[ -]?sell|repack|japanese|french|german|spanish|italian|korean|portuguese|chinese|expected release)\b|\[(?:CHN|JPN|RUS|FRA|DEU|KOR|ITA|SPA|POR)\]", re.I)


@contextmanager
def scan_lock():
    """Prevent a manual scan and scheduled scan from changing benchmarks together."""
    with SCAN_LOCK.open("a+b") as handle:
        if handle.seek(0, 2) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise SystemExit("A price scan is already running.")
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def idle_minutes() -> float:
    """Windows keyboard/mouse idle time; fail closed when unavailable."""
    class LastInputInfo(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    if not hasattr(ctypes, "windll"):
        return 0.0
    last = LastInputInfo()
    last.cbSize = ctypes.sizeof(last)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(last)):
        return 0.0
    ctypes.windll.kernel32.GetTickCount.restype = ctypes.c_uint
    elapsed = (ctypes.windll.kernel32.GetTickCount() - last.dwTime) & 0xFFFFFFFF
    return elapsed / 60_000.0


def decimal(value: object) -> Decimal | None:
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result >= 0 else None
    except (ValueError, InvalidOperation, TypeError):
        return None


def pack_price_fields(key: str, amount: Decimal) -> dict:
    """Attach the verified box quantity and rounded listed price per pack."""
    packs = pack_counts.get(key, {}).get("packs")
    if not isinstance(packs, int) or packs <= 0:
        return {}
    price = (amount / Decimal(packs)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {"packs_per_box": packs, "price_per_pack": str(price)}


def unreleased_box(key: str) -> bool:
    release = release_dates.get(key, {}).get("release_date")
    return isinstance(release, str) and release > datetime.now().date().isoformat()


def unreleased_set(name: str) -> bool:
    return any(key.startswith(name + " | ") and unreleased_box(key) for key in release_dates)


def product_key(title: str) -> tuple[str, str] | None:
    text = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
    if ("booster" not in text or not any(x in text.split() for x in ("box", "display"))
            or any(x in text for x in ("collector", "set booster", "jumpstart", "half box"))
            or re.search(r"\bcase\b", text) or EXCLUDED.search(title)):
        return None
    words = text.split()
    if "play" in words:
        box_type = "Play Booster Box"
    elif "draft" in words:
        box_type = "Draft Booster Box"
    elif "darkhold destiny" in text and "booster display" in text:
        box_type = "Play Booster Box"
    elif (any(name in text for name in ("theros beyond death", "throne of eldraine",
                                         "core set 2020", "war of the spark",
                                         "ravnica allegiance", "guilds of ravnica", "core set 2019"))
          or re.search(r"\bmodern horizons\b(?! (?:2|3|ii|iii)\b)", text)):
        # These pre-Set-Booster standard displays were later called Draft boxes.
        box_type = "Draft Booster Box"
    else:
        return None
    for set_name, patterns in SETS:
        if any(re.search(pattern, text) for pattern in patterns):
            return set_name, box_type
    return "", box_type


def source_list() -> list[dict]:
    manifest = json.loads((ROOT / "SELECTED_MAGIC_SOURCES.json").read_text(encoding="utf-8"))
    excluded = set(json.loads(RETAILER_EXCLUSIONS.read_text(encoding="utf-8")).get("site_ids", []))
    selected = [source for source in manifest["sources"] if source["state"] == "selected"
                and source["id"] not in excluded
                and ("/collections/" in urlsplit(source["scrape_start_url"]).path
                     or source["id"] in SUPPORTED_IDS or source["id"] in ("woot", "target", "starcitygames", "gamenerdz.com", "hottopic.com", "laughingdragonmtg.com", "quickstopcards.com"))]
    amazon_watchlist = ROOT / "amazon_watchlist.json"
    amazon_url = (json.loads(amazon_watchlist.read_text(encoding="utf-8")).get("storefront_url")
                  if amazon_watchlist.exists() else None)
    selected.append({"id": "amazon", "site": "Amazon", "scrape_start_url":
                     amazon_url or "https://www.amazon.com/"})
    selected.append({"id": "cardkingdom", "site": "Card Kingdom", "scrape_start_url": CARDKINGDOM_FEED_URL})
    return selected


def source_request_url(source: dict) -> str:
    """Show the public feed or discovery page, without API credentials."""
    if source["id"] == "woot":
        return "https://wootsearch.com/mcp"
    if source["id"] == "target":
        return "https://redsky.target.com/redsky_aggregations/v1/web/plp_search_v2"
    if source["id"] == "amazon":
        return source["scrape_start_url"]
    if source["id"] == "cardkingdom":
        return CARDKINGDOM_FEED_URL
    if source["id"] == "starcitygames":
        return STARCITY_SEARCH_URL
    if source["id"] == "gamenerdz.com":
        return GAMENERDZ_SEARCH_URL
    if source["id"] == "hottopic.com":
        return HOTTOPIC_SOURCE_URL
    if source["id"] == "laughingdragonmtg.com":
        return LAUGHINGDRAGON_SOURCE_URL
    if source["id"] == "quickstopcards.com":
        return QUICKSTOP_SOURCE_URL
    if source["id"] in SUPPORTED_IDS:
        return source["scrape_start_url"]
    parts = urlsplit(source["scrape_start_url"])
    segments = [piece for piece in parts.path.split("/") if piece]
    handle = segments[segments.index("collections") + 1]
    return urlunsplit((parts.scheme, parts.netloc,
                       f"/collections/{handle}/products.json", "limit=250&page=1", ""))


def classify_source_status(status: dict) -> str:
    error = status.get("error", "")
    match = re.search(r"HTTP Error (\d{3})", error)
    if match:
        status["http_status"] = int(match.group(1))
    if status.get("http_status") in (401, 403, 429, 435) or "access stopped" in error.lower():
        return "blocked"
    if error:
        return "error"
    if status.get("skipped_until_utc"):
        return "paused"
    if status.get("limited_to_first_page"):
        return "limited"
    return "complete" if status.get("complete") else "partial"


def append_scan_log(record: dict) -> None:
    with SCAN_LOG.open("a", encoding="utf-8") as output:
        output.write(json.dumps(record, ensure_ascii=False) + "\n")


def log_run(started_at: str, mode: str, outcome: str, statuses: list[dict],
            offers: list[dict] | None = None, precon_offers: list[dict] | None = None,
            new_alerts: list[dict] | None = None, error: str | None = None) -> None:
    append_scan_log({"started_at_utc": started_at,
                     "finished_at_utc": datetime.now(timezone.utc).isoformat(),
                     "mode": mode, "outcome": outcome, "source_count": len(statuses),
                     "issue_count": sum(row.get("result") in ("blocked", "error", "partial")
                                        for row in statuses),
                     "box_offer_count": len(offers or []),
                     "precon_offer_count": len(precon_offers or []),
                     "new_low_count": len(new_alerts or []),
                     "new_lows": new_alerts or [],
                     "error": error, "sources": statuses})
    if ACTIVE_SCAN_LOG and ACTIVE_SCAN_LOG["started_at"] == started_at:
        ACTIVE_SCAN_LOG["logged"] = True


def fetch_html_source(source: dict, idle_only: bool) -> tuple[list[dict], dict]:
    """Read category pages sequentially; follow only their own next-page link."""
    url = source["scrape_start_url"]
    seen = set()
    cards = []
    status = {"site": source["site"], "pages": 0, "complete": False, "format": "html"}
    while url and len(seen) < 20:
        if idle_only and idle_minutes() < 10:
            raise InterruptedError("computer became active; scan stopped without changing benchmarks")
        if url in seen:
            status["error"] = "category repeated a page; stopped to avoid a request loop"
            break
        if seen:
            time.sleep(2.0)
        seen.add(url)
        status["last_request_url"] = url
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                          "Accept": "text/html,application/xhtml+xml"})
            with urllib.request.urlopen(request, timeout=18) as response:
                final_url = response.url
                raw = response.read(3_000_001)
            if len(raw) > 3_000_000:
                raise ValueError("HTML response exceeded 3 MB cap")
            page = raw.decode("utf-8", "replace")
            cards.extend(extract_cards(page, source["id"], final_url))
            status["pages"] += 1
            url = next_page(page, source["id"], final_url)
            if not url:
                status["complete"] = True
        except (urllib.error.URLError, OSError, ValueError) as error:
            status["error"] = f"{type(error).__name__}: {error}"
            break
    if url and len(seen) >= 20:
        status["error"] = "category exceeded 20-page safety limit"
    status["products"] = len(cards)
    return cards, status


def cached_html_source(source: dict) -> tuple[list[dict], dict]:
    sample = ROOT / "work" / "pending_site_samples" / (source["id"].replace(".", "_") + ".html.gz")
    if not sample.exists():
        return [], {"site": source["site"], "pages": 0, "complete": False,
                    "format": "html", "error": "no cached category page"}
    with gzip.open(sample, "rt", encoding="utf-8", errors="replace") as stream:
        cards = extract_cards(stream.read(), source["id"], source["scrape_start_url"])
    return cards, {"site": source["site"], "pages": 1, "complete": False,
                   "format": "html", "products": len(cards), "cached": True}


def html_offers_from(source: dict, cards: list[dict]) -> tuple[list[dict], list[str]]:
    offers = []
    unmatched = []
    seen = set()
    for card in cards:
        title = card["title"]
        match_title = title
        if source["id"] == "woot" and re.search(r"booster boxes\b", title, re.I):
            # Woot has used the plural for a single box. Reject explicitly
            # multiple-box or choose-one listings before normalizing it.
            if re.search(r"\b[2-9]\s*(?:(?:play|draft)\s+booster\s+)?boxes?\b|set of|your choice|bundle", title, re.I):
                continue
            match_title = re.sub(r"booster boxes\b", "booster box", title, flags=re.I)
        match = product_key(match_title)
        if match is None:
            continue
        set_name, box_type = match
        if not set_name:
            unmatched.append(title)
            continue
        url = card["url"]
        if url in seen or EXCLUDED.search(url.replace("-", " ")):
            continue
        amount = decimal(card["price"])
        if amount is None or amount <= 0:
            continue
        seen.add(url)
        key = set_name + " | " + box_type
        if unreleased_box(key):
            continue
        offers.append({"set": set_name, "box_type": box_type,
                       "product_key": key, "site": card.get("site") or source["site"],
                       "title": title, "url": url,
                       "listed_price": str(amount), **pack_price_fields(key, amount)})
    return offers, unmatched


def fetch_source(source: dict, idle_only: bool) -> tuple[list[dict], dict]:
    parts = urlsplit(source["scrape_start_url"])
    segments = [piece for piece in parts.path.split("/") if piece]
    handle = segments[segments.index("collections") + 1]
    path = f"/collections/{handle}/products.json"
    products: list[dict] = []
    status = {"site": source["site"], "pages": 0, "complete": False}
    page_number = 1
    seen_pages = set()
    while True:
        if idle_only and idle_minutes() < 10:
            raise InterruptedError("computer became active; scan stopped without changing benchmarks")
        url = urlunsplit((parts.scheme, parts.netloc, path,
                          f"limit=250&page={page_number}", ""))
        status["last_request_url"] = url
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                          "Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=18) as response:
                raw = response.read(15_000_001)
            if len(raw) > 15_000_000:
                raise ValueError("response exceeded 15 MB cap")
            payload = json.loads(raw)
            chunk = payload.get("products")
            if not isinstance(chunk, list):
                raise ValueError("response has no products array")
            signature = tuple(str(product.get("id") or product.get("handle")) for product in chunk)
            if signature in seen_pages:
                raise ValueError("store repeated a feed page; stopped to avoid a request loop")
            seen_pages.add(signature)
            products.extend(chunk)
            status["pages"] += 1
            if len(chunk) < 250:
                status["complete"] = True
                break
            page_number += 1
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as error:
            status["error"] = f"{type(error).__name__}: {error}"
            break
    status["products"] = len(products)
    return products, status


def cached_source(source: dict, cache: dict[str, dict]) -> tuple[list[dict], dict]:
    row = cache.get(source["site"])
    if row is None:
        return [], {"site": source["site"], "error": "no cached feed"}
    products = []
    for box in row.get("boxes", []):
        products.append({"title": box["title"],
                         "handle": urlsplit(box["url"]).path.rsplit("/", 1)[-1],
                         "variants": box.get("variants", [])})
    return products, {"site": source["site"], "pages": 1,
                      "complete": row.get("page_complete", False),
                      "products": row.get("product_count", 0), "cached": True}


def offers_from(source: dict, products: list[dict]) -> tuple[list[dict], list[str]]:
    parts = urlsplit(source["scrape_start_url"])
    offers = []
    unmatched = []
    seen = set()
    for product in products:
        title = str(product.get("title", ""))
        match = product_key(title)
        if match is None:
            continue
        set_name, box_type = match
        if not set_name:
            unmatched.append(title)
            continue
        key = set_name + " | " + box_type
        if unreleased_box(key):
            continue
        url = urlunsplit((parts.scheme, parts.netloc,
                          "/products/" + str(product.get("handle", "")), "", ""))
        if EXCLUDED.search(url.replace("-", " ")):
            continue
        for variant_index, variant in enumerate(product.get("variants") or []):
            amount = decimal(variant.get("price"))
            if amount is None or amount <= 0 or variant.get("available") is not True:
                continue
            offer_id = source["site"] + "|" + url + "|" + str(variant.get("id", variant_index))
            if offer_id in seen:
                continue
            seen.add(offer_id)
            offers.append({"set": set_name, "box_type": box_type,
                           "product_key": key,
                           "site": source["site"], "title": title, "url": url,
                           "listed_price": str(amount), **pack_price_fields(key, amount)})
    return offers, unmatched


def atomic_json(path: Path, value: object) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def update_benchmarks(saved: dict, offers: list[dict], now: str) -> tuple[dict, list[dict], int]:
    current_best: dict[str, dict] = {}
    for offer in offers:
        key = offer["product_key"]
        if key not in current_best or decimal(offer["listed_price"]) < decimal(current_best[key]["listed_price"]):
            current_best[key] = offer
    new_alerts = []
    new_boxes = 0
    for key, best in current_best.items():
        previous = saved.get(key)
        if previous is None:
            saved[key] = {**best, "first_seen_utc": now, "lowest_seen_utc": now}
            new_boxes += 1
            continue
        previous_low = decimal(previous["listed_price"])
        lower_offers = [offer for offer in offers
                        if offer["product_key"] == key and decimal(offer["listed_price"]) < previous_low]
        if lower_offers:
            for offer in sorted(lower_offers, key=lambda x: decimal(x["listed_price"])):
                new_alerts.append({**offer, "previous_low": str(previous_low),
                                   "observed_at_utc": now})
            saved[key] = {**best, "first_seen_utc": previous["first_seen_utc"],
                          "lowest_seen_utc": now}
    return current_best, new_alerts, new_boxes


def notify_windows_of_new_lows(alerts: list[dict]) -> dict:
    """Persist new lows until Windows accepts one notification for them."""
    state = (json.loads(WINDOWS_OUTBOX.read_text(encoding="utf-8"))
             if WINDOWS_OUTBOX.exists() else {"pending": []})
    pending = state.setdefault("pending", [])
    queued_ids = {row["id"] for row in pending}
    for alert in alerts:
        alert_id = f"{alert['product_key']}|{alert['listed_price']}|{alert['observed_at_utc']}"
        if alert_id not in queued_ids:
            pending.append({"id": alert_id, "alert": alert})
            queued_ids.add(alert_id)
    if not pending:
        return {"pending": 0, "accepted": None}
    atomic_json(WINDOWS_OUTBOX, state)
    best_by_box = {}
    for item in pending:
        alert = item["alert"]
        key = alert["product_key"]
        if key not in best_by_box or decimal(alert["listed_price"]) < decimal(best_by_box[key]["listed_price"]):
            best_by_box[key] = alert
    rows = list(best_by_box.values())
    if len(rows) == 1:
        row = rows[0]
        title = f"New low: {row['set']} {row['box_type']}"
        body = f"${row['listed_price']} at {row['site']} (previous low ${row['previous_low']}). Open the MTG dashboard for the offer."
    else:
        title = f"New lows for {len(rows)} booster boxes"
        body = "; ".join(f"{row['set']}: ${row['listed_price']}" for row in rows[:3])
        if len(rows) > 3:
            body += f"; and {len(rows) - 3} more"
        body += ". Open the MTG dashboard for the offers."
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                 "-File", str(ROOT / "work" / "send_windows_toast.ps1"),
                                 "-Title", title, "-Body", body], cwd=ROOT,
                                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                timeout=15, creationflags=flags)
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout).strip() or
                               f"toast helper exited {result.returncode}")
        state["pending"] = []
        state["last_accepted_utc"] = datetime.now(timezone.utc).isoformat()
        state["last_error"] = None
        print(f"Windows accepted notification for {len(rows)} new-low boxes.")
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as error:
        state["last_error"] = str(error)
        print(f"Windows notification failed; {len(pending)} alerts saved for retry: {error}")
    state["last_attempt_utc"] = datetime.now(timezone.utc).isoformat()
    atomic_json(WINDOWS_OUTBOX, state)
    return {"pending": len(state["pending"]), "accepted": state["last_error"] is None,
            "error": state["last_error"]}


def main() -> None:
    global ACTIVE_SCAN_LOG
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="make sequential retailer feed requests")
    parser.add_argument("--preview", action="store_true", help="show results without writing a benchmark")
    parser.add_argument("--reseed", action="store_true", help="replace initial benchmarks without alerts")
    parser.add_argument("--idle-only", action="store_true", help="scan only while Windows has been idle at least 10 minutes")
    parser.add_argument("--cloud", action="store_true", help="save live results without local Windows alerts or local email")
    args = parser.parse_args()
    if args.cloud and (not args.live or args.idle_only or args.reseed):
        parser.error("--cloud requires --live and cannot be combined with --idle-only or --reseed")
    started_at = datetime.now(timezone.utc).isoformat()
    mode = "live" if args.live else "offline"
    cache = {}
    if not args.live:
        for file in sorted(ROOT.glob("SELECTED_COLLECTION_FEED_BATCH_*.json")):
            for row in json.loads(file.read_text(encoding="utf-8")):
                cache[row["site"]] = row
    statuses = []
    offers = []
    precon_offers = []
    bestbuy_new = []
    bestbuy_products = []
    ACTIVE_SCAN_LOG = {"started_at": started_at, "mode": mode, "statuses": statuses,
                       "offers": offers, "precon_offers": precon_offers, "logged": False}
    unmatched = []
    cloud_cooldowns = (json.loads(CLOUD_COOLDOWNS.read_text(encoding="utf-8"))
                       if args.cloud and CLOUD_COOLDOWNS.exists() else {})
    for source in source_list():
        if args.idle_only and idle_minutes() < 10:
            print("Skipped: computer is active; benchmarks unchanged.")
            if args.live:
                log_run(started_at, mode, "skipped_active", statuses, offers, precon_offers)
            return
        if args.cloud:
            cooldown = cloud_cooldowns.get(source["id"], {})
            until = cooldown.get("until_utc")
            if until:
                try:
                    if datetime.now(timezone.utc) < datetime.fromisoformat(until):
                        statuses.append({"site": source["site"], "source_url": source_request_url(source),
                                         "pages": 0, "complete": False, "result": "paused",
                                         "skipped_until_utc": until})
                        continue
                except ValueError:
                    pass
        is_html = source["id"] in SUPPORTED_IDS
        is_woot = source["id"] == "woot"
        is_target = source["id"] == "target"
        is_amazon = source["id"] == "amazon"
        is_cardkingdom = source["id"] == "cardkingdom"
        is_starcity = source["id"] == "starcitygames"
        is_gamenerdz = source["id"] == "gamenerdz.com"
        is_hottopic = source["id"] == "hottopic.com"
        is_laughingdragon = source["id"] == "laughingdragonmtg.com"
        is_quickstop = source["id"] == "quickstopcards.com"
        try:
            if is_amazon:
                products, status = (fetch_amazon_products((lambda: idle_minutes() >= 10)
                                                          if args.idle_only else None)
                                    if args.live else ([], {"site": "Amazon", "format": "amz-cli/0.3.0",
                                                        "pages": 0, "complete": False,
                                                        "offline_preview_skipped": True}))
            elif is_cardkingdom:
                if args.live:
                    products, status = fetch_cardkingdom_feed()
                else:
                    saved_feed = ROOT / "work" / "pending_site_samples" / "cardkingdom_sealed_pricelist.json"
                    products, status = parse_cardkingdom_feed(
                        json.loads(saved_feed.read_text(encoding="utf-8")))
                    status["cached"] = True
            elif is_starcity:
                products, status = (fetch_starcity_feed((lambda: idle_minutes() >= 10)
                                                        if args.idle_only else None)
                                    if args.live else ([], {"site": "Star City Games", "format": "hawksearch/json",
                                                        "pages": 0, "complete": False,
                                                        "offline_preview_skipped": True}))
            elif is_gamenerdz:
                products, status = (fetch_gamenerdz_feed((lambda: idle_minutes() >= 10)
                                                           if args.idle_only else None)
                                    if args.live else cached_gamenerdz_feed())
            elif is_hottopic:
                wanted = lambda title: product_key(title) is not None or identify_precon(title) is not None
                products, status = (fetch_hottopic_feed(wanted, (lambda: idle_minutes() >= 10)
                                                           if args.idle_only else None)
                                    if args.live else cached_hottopic_feed(wanted))
            elif is_laughingdragon:
                products, status = (fetch_laughingdragon_feed((lambda: idle_minutes() >= 10)
                                                               if args.idle_only else None)
                                    if args.live else cached_laughingdragon_feed())
            elif is_quickstop:
                products, status = (fetch_quickstop_feed((lambda: idle_minutes() >= 10)
                                                         if args.idle_only else None)
                                    if args.live else cached_quickstop_feed())
            elif is_target:
                products, status = (fetch_target_search((lambda: idle_minutes() >= 10)
                                                        if args.idle_only else None)
                                    if args.live else cached_target_search())
            elif is_woot:
                products, status = (fetch_woot_search((lambda: idle_minutes() >= 10)
                                                      if args.idle_only else None)
                                    if args.live else cached_woot_search())
            elif is_html:
                products, status = (fetch_html_source(source, args.idle_only) if args.live
                                    else cached_html_source(source))
            else:
                products, status = (fetch_source(source, args.idle_only) if args.live
                                    else cached_source(source, cache))
        except InterruptedError as error:
            if args.live:
                statuses.append({"site": source["site"], "source_url": source_request_url(source),
                                 "result": "interrupted", "error": str(error)})
                log_run(started_at, mode, "interrupted", statuses, offers, precon_offers,
                        error=str(error))
            print(error)
            return
        except Exception as error:
            if args.live:
                statuses.append({"site": source["site"], "source_url": source_request_url(source),
                                 "result": "error", "error": f"{type(error).__name__}: {error}"})
                log_run(started_at, mode, "failed", statuses, offers, precon_offers,
                        error=f"{type(error).__name__}: {error}")
            raise
        status["source_url"] = source_request_url(source)
        status["result"] = classify_source_status(status)
        if args.cloud:
            if status.get("http_status") == 429:
                attempts = min(4, int(cloud_cooldowns.get(source["id"], {}).get("attempts", 0)) + 1)
                until = datetime.now(timezone.utc) + timedelta(hours=min(24 * 2 ** (attempts - 1), 168))
                cloud_cooldowns[source["id"]] = {"attempts": attempts, "until_utc": until.isoformat()}
            elif status.get("complete"):
                cloud_cooldowns.pop(source["id"], None)
        statuses.append(status)
        if (is_starcity or is_gamenerdz or is_hottopic or is_laughingdragon or is_quickstop) and not status.get("complete"):
            products = []
        found, unknown = (html_offers_from(source, products) if (is_html or is_woot or is_target or is_amazon or is_cardkingdom or is_starcity or is_gamenerdz or is_hottopic or is_laughingdragon or is_quickstop) else
                          offers_from(source, products))
        offers.extend(found)
        found_precons = (listing_precon_offers(source, products) if (is_html or is_woot or is_target or is_amazon or is_cardkingdom or is_starcity or is_gamenerdz or is_hottopic or is_laughingdragon or is_quickstop)
                         else shopify_precon_offers(source, products))
        if is_cardkingdom:
            found_precons = [offer for offer in found_precons if not unreleased_set(offer["set"])]
        precon_offers.extend(found_precons)
        status["box_offers"] = len(found)
        status["precon_offers"] = len(found_precons)
        status["unmatched_titles"] = len(unknown)
        if unknown:
            status["unmatched_sample"] = unknown[:10]
        unmatched.extend({"site": source["site"], "title": title} for title in unknown)
        print(source["site"], "pages", status.get("pages", 0),
              "offers", len(found), status.get("error", ""), flush=True)

    if args.live and not args.preview and not args.reseed:
        if args.idle_only and idle_minutes() < 10:
            log_run(started_at, mode, "interrupted", statuses, offers, precon_offers,
                    error="computer became active before Best Buy clearance read")
            print("Computer became active; scan stopped without changing benchmarks.")
            return
        try:
            command = [sys.executable, "-u", str(ROOT / "work" / "bestbuy_clearance_source.py"), "--live-json"]
            if args.cloud:
                command.append("--headless")
            if args.idle_only:
                command.append("--idle-only")
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=90,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode == 3:
                log_run(started_at, mode, "interrupted", statuses, offers, precon_offers,
                        error="computer became active during Best Buy clearance read")
                print("Computer became active; scan stopped without changing benchmarks.")
                return
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout or "Best Buy reader failed").strip()[-500:])
            response = json.loads(result.stdout)
            bestbuy_products = response["products"]
            status = response["status"]
            status["source_url"] = BESTBUY_CLEARANCE_URL
            status["result"] = classify_source_status(status)
            if status["complete"]:
                bestbuy_new = record_bestbuy_clearance(bestbuy_products, status)
                status["new_available_products"] = len(bestbuy_new)
        except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, RuntimeError) as error:
            status = {"site": "Best Buy clearance", "source_url": BESTBUY_CLEARANCE_URL,
                      "format": "browser-html", "pages": 0, "complete": False,
                      "error": f"{type(error).__name__}: {str(error)[-350:]}", "result": "error"}
        statuses.append(status)
        print("Best Buy clearance", status.get("products", 0), "cards,",
              len(bestbuy_new), "new available sealed products,", status.get("error", ""), flush=True)

    if args.preview:
        current_best, _, _ = update_benchmarks({}, offers, "preview")
        print(f"Preview: {len(offers)} box offers, {len(current_best)} box benchmarks, "
              f"{len(precon_offers)} precon offers, {len(unmatched)} unmatched titles")
        for key, offer in sorted(current_best.items()):
            print(key, offer["listed_price"], offer["site"])
        if unmatched:
            print("Unmatched:", unmatched[:20])
        if args.live:
            log_run(started_at, mode, "preview", statuses, offers, precon_offers)
        return

    if args.cloud:
        atomic_json(CLOUD_COOLDOWNS, cloud_cooldowns)

    now = datetime.now(timezone.utc).isoformat()
    existing = json.loads(BENCHMARKS.read_text(encoding="utf-8")) if BENCHMARKS.exists() else {"version": 1, "boxes": {}}
    saved = existing.setdefault("boxes", {})
    initial = not BENCHMARKS.exists() or args.reseed
    if args.reseed:
        saved.clear()
    current_best, new_alerts, new_boxes = update_benchmarks(saved, offers, now)
    current_precons = best_precon_offers(precon_offers)
    threshold_events = queue_threshold_alerts({**current_best, **current_precons}, now) if args.live and not args.reseed else []

    if args.live:
        precon_data = json.loads(PRECONS.read_text(encoding="utf-8")) if PRECONS.exists() else {"version": 1, "decks": precon_catalog_rows()}
        for key, offer in current_precons.items():
            precon_data["decks"][key] = {**offer, "last_seen_utc": now}
        atomic_json(PRECONS, precon_data)

    snapshot = {"scanned_at_utc": now, "live": args.live, "sources": statuses,
                "available_offer_count": len(offers), "box_count": len(current_best),
                "new_box_count": new_boxes, "unmatched_titles": unmatched,
                "current_best": current_best, "offers": offers, "new_alerts": new_alerts,
                "current_precons": current_precons, "precon_offers": precon_offers,
                "threshold_email_alerts_queued": threshold_events,
                "bestbuy_clearance_products": bestbuy_products,
                "bestbuy_new_clearance_products": bestbuy_new}
    atomic_json(LATEST, snapshot)
    atomic_json(BENCHMARKS, existing)
    if new_alerts:
        with ALERTS.open("a", encoding="utf-8") as output:
            for alert in new_alerts:
                output.write(json.dumps(alert, ensure_ascii=False) + "\n")
    if args.live and not args.reseed and not args.cloud:
        snapshot["windows_notification"] = notify_windows_of_new_lows(new_alerts)
        atomic_json(LATEST, snapshot)
        sent, failed = deliver()
        if sent or failed:
            print(f"Threshold emails: {sent} sent, {failed} failed.")
        bestbuy_sent, bestbuy_failed = deliver_bestbuy_emails()
        if bestbuy_sent or bestbuy_failed:
            print(f"Best Buy clearance emails: {bestbuy_sent} sent, {bestbuy_failed} failed.")
    print(f"{'Seeded' if initial else 'Compared'} {len(current_best)} boxes from {len(offers)} available offers; "
          f"{len(new_alerts)} new lows, {new_boxes} newly tracked boxes.")
    print(f"Found {len(current_precons)} named Commander precons from {len(precon_offers)} available offers; "
          "precons do not create record-low notifications.")
    print("Saved", BENCHMARKS.name, "and", LATEST.name)
    if args.live:
        log_run(started_at, mode, "complete", statuses, offers, precon_offers, new_alerts)


if __name__ == "__main__":
    with scan_lock():
        try:
            main()
        except Exception as error:
            context = ACTIVE_SCAN_LOG
            if context and context["mode"] == "live" and not context["logged"]:
                log_run(context["started_at"], context["mode"], "failed",
                        context["statuses"], context["offers"], context["precon_offers"],
                        error=f"{type(error).__name__}: {error}")
            raise
