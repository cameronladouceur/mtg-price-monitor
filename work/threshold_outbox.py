"""Queue per-box email alerts without storing email credentials or sending mail."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
THRESHOLDS = ROOT / "price_thresholds.json"
OUTBOX = ROOT / "threshold_email_outbox.json"


def configured_thresholds(path: Path = THRESHOLDS) -> dict:
    settings = read_json(path, {"boxes": {}, "precons": {}})
    return {**settings.get("boxes", {}), **settings.get("precons", {})}


def read_json(path: Path, fallback: dict) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else fallback


def save_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def amount(value: object) -> Decimal | None:
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result > 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def queue_threshold_alerts(current_best: dict, observed_at: str,
                           thresholds_path: Path = THRESHOLDS,
                           outbox_path: Path = OUTBOX) -> list[dict]:
    """Queue once on qualification, then only for a lower price or a fresh crossing."""
    thresholds = configured_thresholds(thresholds_path)
    state = read_json(outbox_path, {"version": 1, "qualifications": {}, "pending": []})
    qualifications = state.setdefault("qualifications", {})
    pending = state.setdefault("pending", [])
    queued = []
    for key in list(qualifications):
        if key not in thresholds:
            del qualifications[key]
    for key, raw_threshold in thresholds.items():
        limit = amount(raw_threshold)
        offer = current_best.get(key)
        price = amount(offer.get("listed_price")) if isinstance(offer, dict) else None
        if limit is None or price is None or price > limit:
            qualifications.pop(key, None)
            pending[:] = [event for event in pending if event["product_key"] != key]
            continue
        previous = qualifications.get(key)
        previous_price = amount(previous.get("lowest_queued_price")) if isinstance(previous, dict) else None
        if (previous is None or previous.get("threshold") != str(limit)
                or previous_price is None or price < previous_price):
            event = {"id": uuid4().hex, "product_key": key,
                     "set": offer["set"], "box_type": offer["box_type"],
                     "deck_name": offer.get("deck_name"),
                     "listed_price": str(price), "threshold": str(limit),
                     "site": offer["site"], "url": offer["url"],
                     "observed_at_utc": observed_at,
                     "packs_per_box": offer.get("packs_per_box"),
                     "price_per_pack": offer.get("price_per_pack")}
            pending[:] = [item for item in pending if item["product_key"] != key]
            pending.append(event)
            queued.append(event)
            qualifications[key] = {"threshold": str(limit), "lowest_queued_price": str(price)}
    pending[:] = [event for event in pending if event["product_key"] in thresholds]
    save_json(outbox_path, state)
    return queued


def pending_alerts(thresholds_path: Path = THRESHOLDS,
                   outbox_path: Path = OUTBOX) -> list[dict]:
    thresholds = configured_thresholds(thresholds_path)
    state = read_json(outbox_path, {"pending": []})
    return [event for event in state.get("pending", [])
            if amount(thresholds.get(event["product_key"])) == amount(event["threshold"])]


def mark_sent(event_id: str, outbox_path: Path = OUTBOX) -> bool:
    state = read_json(outbox_path, {"version": 1, "qualifications": {}, "pending": []})
    before = len(state.get("pending", []))
    state["pending"] = [event for event in state.get("pending", []) if event["id"] != event_id]
    if len(state["pending"]) == before:
        return False
    state["last_sent_utc"] = datetime.now(timezone.utc).isoformat()
    save_json(outbox_path, state)
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--pending", action="store_true", help="print unsent qualifying alerts")
    group.add_argument("--mark-sent", metavar="EVENT_ID", help="mark one successfully sent email")
    args = parser.parse_args()
    if args.pending:
        print(json.dumps(pending_alerts(), ensure_ascii=False, indent=2))
    elif not mark_sent(args.mark_sent):
        raise SystemExit("Alert was not pending; nothing marked sent")


if __name__ == "__main__":
    main()
