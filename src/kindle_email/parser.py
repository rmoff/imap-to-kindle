"""Parse raw RFC822 email bytes into structured content."""

from __future__ import annotations

import email
import email.header
import email.message
import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class ParsedEmail:
    subject: str
    sender: str
    date: str
    html_body: str
    # Mapping from Content-ID (without angle brackets) to raw image bytes
    inline_images: dict[str, bytes] = field(default_factory=dict)
    # MIME type of each inline image, keyed by the same Content-ID
    inline_image_types: dict[str, str] = field(default_factory=dict)


def _decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    parts = email.header.decode_header(value)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return "".join(decoded)


def _decode_payload(part: email.message.Message) -> str:
    payload = part.get_payload(decode=True)
    if not isinstance(payload, bytes):
        return ""
    charset = part.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def _strip_cid_angle_brackets(cid: str) -> str:
    """Content-ID headers look like <foo@bar>; strip the angle brackets."""
    return cid.strip("<>")


def parse(raw: bytes) -> ParsedEmail:
    """
    Parse a raw RFC822 email and extract:
    - Subject, From, Date metadata
    - HTML body (preferred over text/plain)
    - Inline images from multipart/related parts
    """
    msg = email.message_from_bytes(raw)

    subject = _decode_header_value(msg.get("Subject"))
    sender = _decode_header_value(msg.get("From"))
    date = msg.get("Date", "")

    html_body = ""
    inline_images: dict[str, bytes] = {}
    inline_image_types: dict[str, str] = {}

    if msg.is_multipart():
        html_body, inline_images, inline_image_types = _extract_multipart(msg)
    else:
        content_type = msg.get_content_type()
        if content_type == "text/html":
            html_body = _decode_payload(msg)
        elif content_type == "text/plain":
            html_body = f"<pre>{_decode_payload(msg)}</pre>"

    if not html_body:
        log.warning("No usable content found in email: %s", subject)

    return ParsedEmail(
        subject=subject,
        sender=sender,
        date=date,
        html_body=html_body,
        inline_images=inline_images,
        inline_image_types=inline_image_types,
    )


def _extract_multipart(
    msg: email.message.Message,
) -> tuple[str, dict[str, bytes], dict[str, str]]:
    """
    Walk a multipart message to find:
    - The best HTML body (from text/html or multipart/alternative)
    - Inline images from multipart/related

    Returns (html_body, inline_images, inline_image_types).
    """
    html_body = ""
    plain_body = ""
    inline_images: dict[str, bytes] = {}
    inline_image_types: dict[str, str] = {}

    for part in msg.walk():
        content_type = part.get_content_type()
        disposition = part.get_content_disposition() or ""

        if content_type == "text/html" and "attachment" not in disposition:
            candidate = _decode_payload(part)
            if candidate:
                html_body = candidate

        elif content_type == "text/plain" and "attachment" not in disposition and not html_body:
            candidate = _decode_payload(part)
            if candidate:
                plain_body = candidate

        elif content_type.startswith("image/") and "attachment" not in disposition:
            cid_raw = part.get("Content-ID")
            if cid_raw:
                cid = _strip_cid_angle_brackets(cid_raw)
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    inline_images[cid] = payload
                    inline_image_types[cid] = content_type

    if not html_body and plain_body:
        html_body = f"<pre>{plain_body}</pre>"

    return html_body, inline_images, inline_image_types
