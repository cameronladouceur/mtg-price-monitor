"""Find official booster display images on Wizards product articles.

One request per article, spaced apart. Review candidates before --apply.
"""

from __future__ import annotations

import argparse
from html import unescape
from html.parser import HTMLParser
import json
import re
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "box_images.json"
BENCHMARKS = ROOT / "price_benchmarks.json"
PAGES = {
    "Dominaria United": "https://magic.wizards.com/en/news/feature/dominaria-united-product-overview-2022-08-18",
    "Theros Beyond Death": "https://wpn.wizards.com/en/products/theros-beyond-death",
    "Core Set 2021": "https://wpn.wizards.com/en/products/core-set-2021",
    "Core Set 2020": "https://magic.wizards.com/en/news/card-preview/core-set-2020-promos-and-packaging-2019-06-17",
    "Throne of Eldraine": "https://wpn.wizards.com/en/products/throne-of-eldraine",
    "War of the Spark": "https://wpn.wizards.com/en/products/war-of-the-spark",
    "Modern Horizons": "https://wpn.wizards.com/en/products/modern-horizons",
    "Ikoria: Lair of Behemoths": "https://wpn.wizards.com/en/products/ikoria-lair-of-behemoths",
    "March of the Machine": "https://magic.wizards.com/en/news/feature/collecting-march-of-the-machine",
    "Innistrad: Double Feature": "https://magic.wizards.com/en/news/feature/innistrad-double-feature-product-overview-2021-11-15",
    "Ravnica Remastered": "https://magic.wizards.com/en/news/feature/collecting-ravnica-remastered",
    "Innistrad: Midnight Hunt": "https://magic.wizards.com/en/news/announcements/first-look-innistrad-midnight-hunt-and-innistrad-crimson-vow-2021-08-05",
    "The Lord of the Rings: Tales of Middle-earth": "https://magic.wizards.com/en/news/feature/collecting-the-lord-of-the-rings-tales-of-middle-earth",
    "Zendikar Rising": "https://magic.wizards.com/en/news/feature/zendikar-rising-product-overview-2020-09-02",
    "Innistrad: Crimson Vow": "https://magic.wizards.com/en/news/announcements/first-look-innistrad-midnight-hunt-and-innistrad-crimson-vow-2021-08-05",
    "Dominaria Remastered": "https://magic.wizards.com/en/news/feature/collecting-dominaria-remastered-and-product-overview",
    "Unfinity": "https://magic.wizards.com/en/news/feature/almost-everything-know-about-unfinity-boosters-2022-09-20",
    "Phyrexia: All Will Be One": "https://magic.wizards.com/en/news/feature/collecting-phyrexia-all-will-be-one",
    "Commander Masters": "https://magic.wizards.com/en/news/feature/collecting-commander-masters",
    "The Lost Caverns of Ixalan": "https://magic.wizards.com/en/news/feature/collecting-the-lost-caverns-of-ixalan",
    "Innistrad Remastered": "https://magic.wizards.com/en/news/feature/collecting-innistrad-remastered",
    "Streets of New Capenna": "https://magic.wizards.com/en/news/feature/streets-of-new-capenna-product-overview-2022-04-07",
    "The Brothers' War": "https://magic.wizards.com/en/news/feature/whats-inside-the-brothers-war-boosters",
    "Commander Legends: Battle for Baldur's Gate": "https://magic.wizards.com/en/news/feature/commander-legends-battle-for-baldurs-gate-product-overview",
    "Kaldheim": "https://magic.wizards.com/en/news/feature/kaldheim-product-overview-2021-01-25",
    "Strixhaven: School of Mages": "https://magic.wizards.com/en/news/feature/strixhaven-school-mages-product-overview-2021-03-25",
    "Adventures in the Forgotten Realms": "https://magic.wizards.com/en/news/feature/adventures-forgotten-realms-product-overview-2021-06-29",
    "Double Masters 2022": "https://magic.wizards.com/en/news/feature/collecting-double-masters-2022-and-product-overview",
    "Modern Horizons 2": "https://magic.wizards.com/en/news/feature/collecting-modern-horizons-2-2021-05-21",
}

VERIFIED = {
    "Dominaria United | Draft Booster Box": "https://media.wizards.com/2022/images/daily/en_vJ5TMWneKk.png",
    "Theros Beyond Death | Draft Booster Box": "https://images.ctfassets.net/0piqveu8x9oj/10P11fZD3b9Lc2jej3jvsj/2624332c6065fa67df6d79b8166810d4/MTGTHB_EN_BstrDsp_03_1577110873.jpg",
    "Core Set 2021 | Draft Booster Box": "https://images.ctfassets.net/0piqveu8x9oj/QBUG2xewouHskoZnIHbqb/53e286e32b46eb0c1128afb1f24d946b/mtgm21_en_bstrdspbx_01_03_1592417380.jpg",
    "Core Set 2020 | Draft Booster Box": "https://media.wizards.com/2019/images/daily/iouBbUvcyn.png",
    "Throne of Eldraine | Draft Booster Box": "https://images.ctfassets.net/0piqveu8x9oj/2AwrcvmqmweTfXUDMfmeVe/b34a40c2d9edaf2745da24ea2ee59273/MTGELD_EN_BstrDsp_02_1569864833.jpg",
    "War of the Spark | Draft Booster Box": "https://images.ctfassets.net/0piqveu8x9oj/2zhXTxpkyafg6kCMnccgIr/6bc9e6c7b865093255d2f3790f09ba36/mtgwar_en_bstrdsp_03.jpg",
    "Modern Horizons | Draft Booster Box": "https://images.ctfassets.net/0piqveu8x9oj/3zomIpRrUWK9gPGu0ssZsA/9ec587e3143cef21554a94290784cb98/mtgmh1_en_bstrdsp_0001.jpg",
    "Ikoria: Lair of Behemoths | Draft Booster Box": "https://images.ctfassets.net/0piqveu8x9oj/bSaN6CaSo1bKya1hzb8ms/281ba7fb88771fdbf6b445fdbec06151/MTGIKO_EN_BstrDspBX_03_1586881389.jpg",
    "March of the Machine | Draft Booster Box": "https://media.wizards.com/2022/images/daily/en_kcwuw79dkj6m.png",
    "Innistrad: Double Feature | Draft Booster Box": "https://media.wizards.com/2021/images/daily/ncQdwhWMa0.png",
    "Ravnica Remastered | Draft Booster Box": "https://media.wizards.com/2023/images/daily/en_8roblcr9blsi.png",
    "Innistrad: Midnight Hunt | Draft Booster Box": "https://media.wizards.com/2021/images/daily/en_DMs4Mv8Lr61.png",
    "The Lord of the Rings: Tales of Middle-earth | Draft Booster Box": "https://media.wizards.com/2023/images/daily/en_Zwfg7jACuSjG.png",
    "Zendikar Rising | Draft Booster Box": "https://media.wizards.com/2020/znrproduct/en_znr_draftbooster.png",
    "Innistrad: Crimson Vow | Draft Booster Box": "https://media.wizards.com/2021/images/daily/en_oIOA4wZXgK1.png",
    "Dominaria Remastered | Draft Booster Box": "https://media.wizards.com/2022/images/daily/en_Cs3FsIuegs.png",
    "Unfinity | Draft Booster Box": "https://media.wizards.com/2022/images/daily/aIUP2g0UgEm_500x_4.png",
    "Phyrexia: All Will Be One | Draft Booster Box": "https://media.wizards.com/2022/images/daily/en_2sjDm3uasd.png",
    "Commander Masters | Draft Booster Box": "https://media.wizards.com/2023/images/daily/en_ktVTnfSSKrIM.png",
    "The Lost Caverns of Ixalan | Draft Booster Box": "https://media.wizards.com/2023/images/daily/en_BPwC9oRSH3n.png",
}

PRODUCT_PAGES = {
    "Kamigawa: Neon Dynasty": "https://magic.wizards.com/en/products/kamigawa-neon-dynasty",
    "Theros Beyond Death": "https://magic.wizards.com/en/products/theros-beyond-death",
    "Core Set 2021": "https://magic.wizards.com/en/products/core-set-2021",
    "Guilds of Ravnica": "https://magic.wizards.com/en/products/guilds-of-ravnica",
    "Throne of Eldraine": "https://magic.wizards.com/en/products/throne-of-eldraine",
    "Core Set 2020": "https://magic.wizards.com/en/products/core-set-2020",
    "Ravnica Allegiance": "https://magic.wizards.com/en/products/ravnica-allegiance",
    "Core Set 2019": "https://magic.wizards.com/en/products/core-set-2019",
    "War of the Spark": "https://magic.wizards.com/en/products/war-of-the-spark",
    "Modern Horizons": "https://magic.wizards.com/en/products/modern-horizons",
    "Ikoria: Lair of Behemoths": "https://magic.wizards.com/en/products/ikoria",
    "Double Masters": "https://magic.wizards.com/en/products/double-masters",
    "Commander Legends": "https://magic.wizards.com/en/products/commander-legends",
    "Time Spiral Remastered": "https://magic.wizards.com/en/products/timespiral-remastered",
    "Modern Horizons 2": "https://magic.wizards.com/en/products/modern-horizons-2",
}


class Images(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "img":
            return
        row = dict(attrs)
        alt = row.get("alt") or ""
        url = row.get("src") or row.get("data-src") or ""
        if url.startswith("https://"):
            self.rows.append((alt, url))


def find_image(page_url: str, box_type: str, set_name: str) -> tuple[str, str] | None:
    request = urllib.request.Request(page_url, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"})
    with urllib.request.urlopen(request, timeout=18) as response:
        if not response.url.startswith("https://magic.wizards.com/"):
            raise ValueError("Article redirected outside official Magic website")
        html = response.read(4_000_001)
    if len(html) > 4_000_000:
        raise ValueError("Article exceeds 4 MB")
    parser = Images()
    parser.feed(html.decode("utf-8", "replace"))
    kind = "play" if box_type.startswith("Play") else "draft"
    matches = [(alt, url) for alt, url in parser.rows
               if re.search(rf"\b{kind}\b.*\b(?:display|box)\b|\b(?:display|box)\b.*\b{kind}\b", alt, re.I)
               and re.match(r"https://(?:media\.wizards\.com|images\.ctfassets\.net)/", url)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        # Articles may show several sets. Require the exact set's packaging.
        words = [word for word in re.findall(r"[a-z0-9]+", set_name.lower()) if len(word) > 3]
        narrowed = [item for item in matches if sum(word in item[0].lower() for word in words) >= min(2, len(words))]
        if len(narrowed) == 1:
            return narrowed[0]
    print(f"REVIEW {set_name}: {matches[:5]}")
    return None


def find_product_page_image(page_url: str, box_type: str, set_name: str) -> tuple[str, str] | None:
    request = urllib.request.Request(page_url, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"})
    with urllib.request.urlopen(request, timeout=18) as response:
        if not response.url.startswith("https://magic.wizards.com/en/products/"):
            raise ValueError("Product page redirected outside official Magic products")
        html = response.read(4_000_001)
    if len(html) > 4_000_000:
        raise ValueError("Product page exceeds 4 MB")
    text = html.decode("utf-8", "replace")
    kind = "play" if box_type.startswith("Play") else "draft"
    candidates = []
    for slide in re.split(r'<div class="swiper-slide"', text):
        heading = re.search(r"<h3[^>]*>(.*?)</h3>", slide, re.S | re.I)
        image = re.search(r'<img[^>]+src="(//images\.ctfassets\.net/[^\"]+)"', slide, re.I)
        if not heading or not image:
            continue
        title = unescape(re.sub(r"<[^>]+>", "", heading.group(1))).strip()
        if (re.search(rf"\b{kind}\b.*\bbooster", title, re.I)
                and re.search(r"\b(?:display|box)\b", title, re.I)):
            candidates.append((title, "https:" + unescape(image.group(1))))
    display = [row for row in candidates if "display" in row[0].lower()]
    if len(display) == 1:
        return display[0]
    if len(candidates) == 1:
        return candidates[0]
    print(f"REVIEW {set_name}: {candidates[:5]}")
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--verified-only", action="store_true", help="apply reviewed image URLs without page requests")
    parser.add_argument("--inspect-url")
    args = parser.parse_args()
    if args.inspect_url:
        if not args.inspect_url.startswith("https://magic.wizards.com/en/"):
            raise SystemExit("Only official English Magic pages may be inspected")
        request = urllib.request.Request(args.inspect_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=18) as response:
            html = response.read(4_000_001).decode("utf-8", "replace")
        for match in list(re.finditer(r"Draft Boosters?(?:\s*/\s*Booster Display)?", html, re.I))[:4]:
            print("CONTEXT", re.sub(r"\s+", " ", html[max(0, match.start()-800):match.end()+700])[:1500])
        return
    images = json.loads(IMAGES.read_text(encoding="utf-8"))
    boxes = json.loads(BENCHMARKS.read_text(encoding="utf-8"))["boxes"]
    candidates = {}
    for key, url in VERIFIED.items():
        if key in images and images[key].get("source") != "Official Magic product image":
            set_name = key.split(" | ", 1)[0]
            candidates[key] = {"url": url, "source": "Official Magic product image", "source_url": PAGES[set_name]}
            print(f"VERIFIED {key}: {url}")
    last_request = 0.0
    for key, box in boxes.items():
        if images.get(key, {}).get("source") == "Official Magic product image":
            continue
        page = PAGES.get(box["set"])
        product_page = PRODUCT_PAGES.get(box["set"])
        if not page and not product_page:
            continue
        if key in VERIFIED:
            candidates[key] = {"url": VERIFIED[key], "source": "Official Magic product image", "source_url": page}
            print(f"VERIFIED {key}: {VERIFIED[key]}")
            continue
        if args.verified_only:
            continue
        delay = 1.5 - (time.monotonic() - last_request)
        if delay > 0:
            time.sleep(delay)
        try:
            found = find_image(page, box["box_type"], box["set"]) if page else None
            last_request = time.monotonic()
            if not found and product_page:
                delay = 1.5 - (time.monotonic() - last_request)
                if delay > 0:
                    time.sleep(delay)
                found = find_product_page_image(product_page, box["box_type"], box["set"])
                last_request = time.monotonic()
                page = product_page
            if found:
                alt, url = found
                print(f"FOUND {key}: {alt} | {url}")
                candidates[key] = {"url": url, "source": "Official Magic product image", "source_url": page}
        except (OSError, ValueError) as error:
            last_request = time.monotonic()
            print(f"ERROR {key}: {type(error).__name__}: {error}")
    if args.apply and candidates:
        images.update(candidates)
        temporary = IMAGES.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(images, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(IMAGES)
    print(f"{len(candidates)} official images {'saved' if args.apply else 'found for review'}")


if __name__ == "__main__":
    main()
