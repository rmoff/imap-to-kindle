"""Orchestrate the full fetch → parse → clean → epub → send pipeline."""

from __future__ import annotations

import logging
import os
import urllib.parse

from . import cleaner, delivery, epub, notifier, parser, pdf_layout, url_fetcher
from .config import Config
from .fetcher import RawEmail
from .raindrop_fetcher import Raindrop

log = logging.getLogger(__name__)


def _is_self_sent(sender_header: str, own_address: str) -> bool:
    """Return True if the sender is the user's own address (e.g. a forwarded copy in a thread)."""
    return own_address.lower() in sender_header.lower()


def _prepare_pdf(data: bytes, config: Config, *, title: str | None, author: str | None) -> bytes:
    """Title the PDF (e-readers list it by that; papers often leave it blank)
    and re-lay it for the e-reader if it's two-column."""
    return pdf_layout.set_metadata(_relayout_pdf(data, config), title, author)


def _relayout_pdf(data: bytes, config: Config) -> bytes:
    """Re-lay two-column PDFs for the e-reader; anything else passes through."""
    if not config.processing.pdf_relayout:
        return data
    try:
        relaid = pdf_layout.relayout_two_column(data)
    except Exception as e:
        log.warning("PDF re-layout failed, delivering original: %s", e)
        return data
    if relaid is None:
        log.info("PDF is not two-column; delivering unchanged")
        return data
    log.info("Re-laid two-column PDF for the e-reader")
    return relaid


def _pdf_title(raindrop: Raindrop, pdf: bytes) -> str | None:
    """The document's title. Raindrop derives a PDF bookmark's title from its
    file name ("P fernandez moctezuma"), so prefer the one in the PDF itself."""
    return pdf_layout.guess_title(pdf) or (raindrop.title if raindrop.title != raindrop.link else None)


def _pdf_filename(raindrop: Raindrop, title: str | None) -> str:
    """Name the PDF after its title, else the URL's file name."""
    if title:
        stem = title
    else:
        stem = os.path.basename(urllib.parse.urlparse(raindrop.link).path)
        if stem.lower().endswith(".pdf"):
            stem = stem[:-4]
    return epub.safe_filename(stem or "document") + ".pdf"


def _deliver_and_notify(filename: str, data: bytes, *, source: str, title: str, config: Config) -> bool:
    """Deliver an EPUB/PDF to the configured target(s) and post a Discord notification."""
    success = delivery.deliver(filename, data, config)
    if success:
        log.info("Successfully delivered '%s'", filename)
        notifier.notify(config.discord, success=True, source=source, title=title)
    else:
        log.error("Failed to deliver '%s'", filename)
        notifier.notify(config.discord, success=False, source=source, title=title, detail="Delivery failed")
    return success


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
            data = _prepare_pdf(
                data, config,
                title=pdf_layout.guess_title(data) or os.path.splitext(filename)[0],
                author=None,
            )
        elif parsed.html_body:
            cleaned = cleaner.clean(parsed, config.processing)
            filename, data = epub.generate(cleaned)
        else:
            log.warning("No content extracted from email, skipping")
            notifier.notify(config.discord, success=False, source="email", title=title, detail="No content extracted")
            return False

        return _deliver_and_notify(filename, data, source="email", title=title, config=config)

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
        if raindrop.type == "document" or url_fetcher.looks_like_pdf(raindrop.link):
            pdf = url_fetcher.fetch_pdf(raindrop.link)
            if pdf is not None:
                title = _pdf_title(raindrop, pdf)
                author = url_fetcher.prettify_domain(raindrop.link)
                return _deliver_and_notify(
                    _pdf_filename(raindrop, title),
                    _prepare_pdf(pdf, config, title=title, author=author),
                    source="raindrop", title=title or raindrop.link, config=config,
                )
            log.info("No PDF at %s; trying it as a web page", raindrop.link)
        cleaned = url_fetcher.build_cleaned_content([raindrop.link], config.processing)
        if cleaned is None:
            log.error("Could not render raindrop %d (%s)", raindrop.id, raindrop.link)
            notifier.notify(
                config.discord, success=False, source="raindrop", title=label,
                detail="Fetch/render failed (see logs)",
            )
            return False
        filename, data = epub.generate(cleaned)
        return _deliver_and_notify(
            filename, data, source="raindrop", title=cleaned.title or label, config=config
        )
    except Exception as e:
        log.exception("Unexpected error processing raindrop %d: %s", raindrop.id, e)
        notifier.notify(config.discord, success=False, source="raindrop", title=label, detail=str(e))
        return False
