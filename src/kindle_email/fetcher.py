"""IMAP connection, Gmail label scanning, email retrieval, and label management."""

from __future__ import annotations

import imaplib
import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass

from .config import Config

log = logging.getLogger(__name__)

# Gmail exposes labels as IMAP folders, using "/" as a separator.
# The IMAP folder name for a Gmail label "SendToKindle" is just "SendToKindle".
# For nested labels like "SendToKindle/Processed", Gmail uses the same path.


@dataclass
class RawEmail:
    uid: bytes
    raw: bytes


_IMAP_TIMEOUT = 30  # seconds


def _connect(config: Config) -> imaplib.IMAP4_SSL:
    conn = imaplib.IMAP4_SSL(config.imap.host, config.imap.port, timeout=_IMAP_TIMEOUT)
    conn.login(config.imap.username, config.imap.password)
    log.debug("IMAP login successful for %s", config.imap.username)
    return conn


def _ensure_label_exists(conn: imaplib.IMAP4_SSL, label: str) -> None:
    """Create a Gmail label (IMAP folder) if it doesn't exist."""
    status, _ = conn.select(f'"{label}"')
    if status != "OK":
        conn.create(f'"{label}"')
        log.debug("Created IMAP folder: %s", label)


def fetch_emails(config: Config) -> Iterator[RawEmail]:
    """
    Connect to IMAP, select the watch label, and yield all messages found.
    Messages are yielded in arrival order. The caller is responsible for
    calling mark_processed() or mark_failed() after each message.
    """
    conn = _connect(config)
    try:
        # Ensure processed/failed labels exist before we need them
        _ensure_label_exists(conn, config.labels.processed)
        _ensure_label_exists(conn, config.labels.failed)

        status, _ = conn.select(f'"{config.labels.watch}"')
        if status != "OK":
            log.warning("Label '%s' not found or empty — nothing to process", config.labels.watch)
            return

        status, data = conn.uid("search", None, "ALL")
        if status != "OK" or not data or not data[0]:
            log.debug("No messages in label '%s'", config.labels.watch)
            return

        uids = data[0].split()
        log.info("Found %d message(s) in label '%s'", len(uids), config.labels.watch)

        for uid in uids:
            status, msg_data = conn.uid("fetch", uid, "(RFC822)")
            if status != "OK" or not msg_data or msg_data[0] is None:
                log.warning("Failed to fetch UID %s, skipping", uid)
                continue
            raw = msg_data[0][1]
            yield RawEmail(uid=uid, raw=raw)
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def mark_processed(config: Config, uid: bytes) -> None:
    """Move a message from the watch label to the processed label."""
    _move_message(config, uid, config.labels.watch, config.labels.processed)


def mark_failed(config: Config, uid: bytes) -> None:
    """Move a message from the watch label to the failed label."""
    _move_message(config, uid, config.labels.watch, config.labels.failed)


def _move_message(config: Config, uid: bytes, src_label: str, dst_label: str) -> None:
    """
    In Gmail IMAP, 'moving' a label means copying to the destination folder,
    then removing the source label by deleting from the source folder.
    Gmail doesn't actually delete the message — it just removes the label.
    """
    conn = _connect(config)
    try:
        _ensure_label_exists(conn, dst_label)

        status, _ = conn.select(f'"{src_label}"')
        if status != "OK":
            log.error("Cannot select source label '%s' for move", src_label)
            return

        # Copy to destination
        status, _ = conn.uid("copy", uid, f'"{dst_label}"')
        if status != "OK":
            log.error("Failed to copy UID %s to '%s'", uid, dst_label)
            return

        # Mark for deletion in source (removes the label in Gmail)
        conn.uid("store", uid, "+FLAGS", "\\Deleted")
        conn.expunge()
        log.debug("Moved UID %s from '%s' to '%s'", uid, src_label, dst_label)
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def fetch_and_process(config: Config, process_fn) -> int:
    """
    Fetch all emails from the watch label and call process_fn(raw_email) for each.
    Returns the count of successfully processed emails.

    process_fn should return True on success, False on failure.
    Uses exponential backoff for transient connection errors.
    """
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            processed = 0
            for raw_email in fetch_emails(config):
                success = process_fn(raw_email)
                if success:
                    mark_processed(config, raw_email.uid)
                    processed += 1
                else:
                    mark_failed(config, raw_email.uid)
            return processed
        except imaplib.IMAP4.error as e:
            if attempt == max_retries:
                raise
            wait = 2 ** attempt
            log.warning("IMAP error (attempt %d/%d): %s — retrying in %ds", attempt, max_retries, e, wait)
            time.sleep(wait)
    return 0
