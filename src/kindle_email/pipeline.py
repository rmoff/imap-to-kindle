"""Orchestrate the full fetch → parse → clean → epub → send pipeline."""

from __future__ import annotations

import logging

from . import cleaner, epub, parser, sender
from .config import Config
from .fetcher import RawEmail

log = logging.getLogger(__name__)


def _is_self_sent(sender_header: str, own_address: str) -> bool:
    """Return True if the sender is the user's own address (e.g. a forwarded copy in a thread)."""
    return own_address.lower() in sender_header.lower()


def process_email(raw: RawEmail, config: Config) -> bool:
    """
    Process a single raw email through the full pipeline.
    Returns True if the email was successfully sent to Kindle.
    """
    try:
        parsed = parser.parse(raw.raw)

        # Gmail labels entire threads, so when you forward a newsletter to yourself
        # and label it, both the forwarded copy (From: you) and the original newsletter
        # end up in the label. Skip messages sent from your own address.
        if _is_self_sent(parsed.sender, config.imap.username):
            log.info("Skipping self-sent message (thread label): %s", parsed.subject)
            return True  # Return True so it gets moved to Processed, not Failed

        log.info("Processing: %s (from %s)", parsed.subject, parsed.sender)

        if not parsed.html_body:
            log.warning("No content extracted from email, skipping")
            return False

        cleaned = cleaner.clean(parsed, config.processing)
        epub_filename, epub_bytes = epub.generate(cleaned)
        success = sender.send(epub_filename, epub_bytes, config)

        if success:
            log.info("Successfully delivered '%s' to Kindle", epub_filename)
        else:
            log.error("Failed to deliver '%s' to Kindle", epub_filename)

        return success

    except Exception as e:
        log.exception("Unexpected error processing email: %s", e)
        return False
