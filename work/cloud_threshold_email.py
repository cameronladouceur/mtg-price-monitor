"""Send queued threshold emails from a GitHub runner through Brevo SMTP."""

from __future__ import annotations

from email.message import EmailMessage
import os
import smtplib
import ssl

from threshold_outbox import mark_sent, pending_alerts


def send_pending() -> tuple[int, int]:
    events = pending_alerts()
    if not events:
        return 0, 0
    names = ("BREVO_SMTP_LOGIN", "BREVO_SMTP_KEY", "BREVO_SENDER", "ALERT_RECIPIENT")
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        raise RuntimeError("Cloud email secrets are missing: " + ", ".join(missing))
    login, key, sender, recipient = (os.environ[name] for name in names)
    sent = 0
    for event in events:
        product = (f"{event['deck_name']} Commander deck" if event.get("deck_name")
                   else f"{event['set']} {event['box_type']}")
        lines = [
            f"{event['set']} — {product}",
            f"Listed price: ${event['listed_price']} (your threshold: ${event['threshold']})",
            f"Store: {event['site']}",
            f"Offer: {event['url']}",
            f"Observed: {event['observed_at_utc']}",
            "Tax and shipping are not included. Check the final price at the store.",
        ]
        if event.get("packs_per_box") and event.get("price_per_pack"):
            lines.append(f"Packs: {event['packs_per_box']}; listed price per pack: ${event['price_per_pack']}")
        message = EmailMessage()
        message["From"] = f"MTG Price Watch <{sender}>"
        message["To"] = recipient
        message["Subject"] = f"MTG price alert: {product} at ${event['listed_price']}"
        message.set_content("\n".join(lines))
        with smtplib.SMTP("smtp-relay.brevo.com", 587, timeout=30) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(login, key)
            smtp.send_message(message)
        if not mark_sent(event["id"]):
            raise RuntimeError("Email sent but its pending event could not be marked delivered")
        sent += 1
    return sent, len(events) - sent
