"""Cache reviewed box images locally for the personal dashboard.

Requests are sequential, spaced 1.5 seconds apart, and never retried. A failed
download leaves the existing remote image URL in place.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time
import urllib.request
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "box_images.json"
ART = ROOT / "box_art"
ALLOWED_HOSTS = {"images.ctfassets.net", "media.wizards.com", "cdn.shopify.com", "m.media-amazon.com"}
MAX_BYTES = 10_000_000


def extension(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    raise ValueError("Response is not a JPEG, PNG, or WebP image")


def save_images(data: dict) -> None:
    temporary = IMAGES.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(IMAGES)


def cache_one(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
        raise ValueError("Image URL is outside reviewed hosts")
    request = urllib.request.Request(url.replace(" ", "%20"), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=25) as response:
        final = urlsplit(response.url)
        if final.scheme != "https" or final.hostname not in ALLOWED_HOSTS:
            raise ValueError("Image redirected outside reviewed hosts")
        content = response.read(MAX_BYTES + 1)
    if not content or len(content) > MAX_BYTES:
        raise ValueError("Image is empty or exceeds 10 MB")
    suffix = extension(content)
    filename = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16] + suffix
    ART.mkdir(exist_ok=True)
    destination = ART / filename
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(destination)
    return "/box-art/" + filename


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="maximum new downloads for a pilot run")
    args = parser.parse_args()
    images = json.loads(IMAGES.read_text(encoding="utf-8"))
    saved = failed = attempts = 0
    for key, row in images.items():
        if row.get("pending"):
            continue
        current = row.get("url", "")
        if current.startswith("/box-art/") and (ART / current.rsplit("/", 1)[-1]).is_file():
            continue
        remote = row.get("remote_url") or current
        if args.limit and attempts >= args.limit:
            break
        if attempts:
            time.sleep(1.5)
        attempts += 1
        try:
            local = cache_one(remote)
            row["remote_url"] = remote
            row["url"] = local
            save_images(images)
            saved += 1
            print(f"SAVED {key}: {local}", flush=True)
        except (OSError, ValueError) as error:
            failed += 1
            print(f"FAILED {key}: {type(error).__name__}: {error}", flush=True)
    print(f"Cached {saved} images; {failed} failed; {attempts} requests.")


if __name__ == "__main__":
    main()
