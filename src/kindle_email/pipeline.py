"""Orchestrate the full fetch → parse → clean → epub → send pipeline."""

from __future__ import annotations

import logging

from . import cleaner, epub, notifier, parser, sender, url_fetcher
from .config import Config
from .fetcher import RawEmail
from .raindrop_fetcher import Raindrop

log = logging.getLogger(__name__)


def _is_self_sent(sender_header: str, own_address: str) -> bool:
    """Return True if the sender is the user's own address (e.g. a forwarded copy in a thread)."""
    return own_address.lower() in sender_header.lower()


def process_email(raw: RawEmail, config: Config) -> bool:
    """
    Process a single raw email through the full pipeline.
    Returns True if the email was successfully sent to Kindle.
    """
    title = "(unknown)"
    try:
        parsed = parser.parse(raw.raw)
        title = parsed.subject or "(no subject)"

        # Gmail labels entire threads, so when you forward a newsletter to yourself
        # and label it, both the forwarded copy (From: you) and the original newsletter
        # end up in the label. Skip messages sent from your own address.
        if _is_self_sent(parsed.sender, config.imap.username):
            log.info("Skipping self-sent message (thread label): %s", parsed.subject)
            return True  # Return True so it gets moved to Processed, not Failed

        log.info("Processing: %s (from %s)", parsed.subject, parsed.sender)

        if parsed.pdf_attachment is not None:
            filename, data = parsed.pdf_attachment
            log.info("Forwarding PDF attachment '%s' to Kindle", filename)
        elif parsed.html_body:
            cleaned = cleaner.clean(parsed, config.processing)
            filename, data = epub.generate(cleaned)
        else:
            log.warning("No content extracted from email, skipping")
            notifier.notify(config.discord, success=False, source="email", title=title, detail="No content extracted")
            return False

        success = sender.send(filename, data, config)

        if success:
            log.info("Successfully delivered '%s' to Kindle", filename)
            notifier.notify(config.discord, success=True, source="email", title=title)
        else:
            log.error("Failed to deliver '%s' to Kindle", filename)
            notifier.notify(config.discord, success=False, source="email", title=title, detail="SMTP send failed")

        return success

    except Exception as e:
        log.exception("Unexpected error processing email: %s", e)
        notifier.notify(config.discord, success=False, source="email", title=title, detail=str(e))
        return False


def process_raindrop(raindrop: Raindrop, config: Config) -> bool:
    """
    Fetch a Raindrop bookmark's URL, render it, and send it to Kindle.
    Returns True on successful delivery.
    """
    label = raindrop.link
    try:
        log.info("Processing raindrop %d: %s", raindrop.id, raindrop.link)
        cleaned = url_fetcher.build_cleaned_content([raindrop.link], config.processing)
        if cleaned is None:
            log.error("Could not render raindrop %d (%s)", raindrop.id, raindrop.link)
            notifier.notify(
                config.discord, success=False, source="raindrop", title=label,
                detail="Fetch/render failed (see logs)",
            )
            return False
        filename, data = epub.generate(cleaned)
        success = sender.send(filename, data, config)
        if success:
            log.info("Successfully delivered raindrop %d to Kindle", raindrop.id)
            notifier.notify(config.discord, success=True, source="raindrop", title=cleaned.title or label)
        else:
            log.error("Failed to deliver raindrop %d to Kindle", raindrop.id)
            notifier.notify(
                config.discord, success=False, source="raindrop",
                title=cleaned.title or label, detail="SMTP send failed",
            )
        return success
    except Exception as e:
        log.exception("Unexpected error processing raindrop %d: %s", raindrop.id, e)
        notifier.notify(config.discord, success=False, source="raindrop", title=label, detail=str(e))
        return False
