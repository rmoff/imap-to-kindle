"""
Content cleaning pipeline:
  1. readability-lxml extracts the main article body
  2. BeautifulSoup removes newsletter cruft (tracking pixels, unsubscribe links, etc.)
  3. Inline CID images are preserved; external images are downloaded (with SSRF protection)
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from bs4 import BeautifulSoup
from readability import Document

from .config import ProcessingConfig
from .parser import ParsedEmail

log = logging.getLogger(__name__)

# Regex patterns for newsletter-specific cruft removal
_UNSUBSCRIBE_PATTERNS = re.compile(
    r"unsubscribe|opt.?out|manage.{0,20}preference|email.{0,20}preference|"
    r"no longer.{0,20}receive|update.{0,20}subscription",
    re.IGNORECASE,
)

_TRACKING_PIXEL_MAX_DIMENSION = 3  # px; images <= this in both dimensions are tracking pixels


@dataclass
class CleanedContent:
    title: str
    html: str
    # Mapping from a local filename (e.g. "img_abc123.jpg") to raw image bytes
    images: dict[str, bytes] = field(default_factory=dict)
    # MIME type for each image file
    image_types: dict[str, str] = field(default_factory=dict)


def clean(parsed: ParsedEmail, config: ProcessingConfig) -> CleanedContent:
    """
    Full cleaning pipeline. Returns CleanedContent with sanitized HTML
    and a dict of images to embed in the EPUB.
    """
    html = parsed.html_body
    if not html:
        return CleanedContent(title=parsed.subject, html="<p>No content.</p>")

    # Step 1: readability-lxml extraction
    try:
        doc = Document(html)
        raw_title = doc.title() or ""
        title = raw_title if raw_title and raw_title.lower() != "no-title" else parsed.subject
        html = doc.summary(html_partial=True)
    except Exception as e:
        log.warning("readability extraction failed (%s), using raw HTML", e)
        title = parsed.subject

    # Step 2: BeautifulSoup cleanup
    soup = BeautifulSoup(html, "lxml")
    _remove_tracking_pixels(soup)
    _remove_unsubscribe_blocks(soup)
    _remove_email_layout_tables(soup)
    _remove_scripts_and_styles(soup)

    # Step 3: Image handling
    images: dict[str, bytes] = {}
    image_types: dict[str, str] = {}
    image_count = 0

    for img in soup.find_all("img"):
        if image_count >= config.max_images_per_email:
            img.decompose()
            continue

        src = img.get("src", "")

        if src.startswith("cid:"):
            # Inline image referenced by Content-ID
            cid = src[4:]  # strip "cid:"
            if cid in parsed.inline_images:
                ext = _mime_to_ext(parsed.inline_image_types.get(cid, "image/jpeg"))
                filename = f"img_{_safe_cid(cid)}{ext}"
                images[filename] = parsed.inline_images[cid]
                image_types[filename] = parsed.inline_image_types.get(cid, "image/jpeg")
                img["src"] = filename
                image_count += 1
            else:
                img.decompose()

        elif src.startswith("data:"):
            # Base64-encoded data URI
            data = _decode_data_uri(src)
            if data:
                mime, img_bytes = data
                if len(img_bytes) > config.max_image_size_kb * 1024:
                    log.debug("Data URI image too large, skipping")
                    img.decompose()
                    continue
                ext = _mime_to_ext(mime)
                filename = f"img_data_{image_count}{ext}"
                images[filename] = img_bytes
                image_types[filename] = mime
                img["src"] = filename
                image_count += 1
            else:
                img.decompose()

        elif src.startswith("http://") or src.startswith("https://"):
            if not config.download_external_images:
                img.decompose()
                continue

            img_bytes, mime = _download_image(src, config)
            if img_bytes:
                ext = _mime_to_ext(mime)
                filename = f"img_ext_{image_count}{ext}"
                images[filename] = img_bytes
                image_types[filename] = mime
                img["src"] = filename
                image_count += 1
            else:
                # Replace with alt text if available, else remove
                alt = img.get("alt", "")
                if alt:
                    img.replace_with(f"[{alt}]")
                else:
                    img.decompose()
        else:
            img.decompose()

    # Strip all remaining attributes except a safe subset on allowed tags
    _sanitize_attributes(soup)

    return CleanedContent(
        title=title,
        html=str(soup),
        images=images,
        image_types=image_types,
    )


# ---------------------------------------------------------------------------
# BeautifulSoup cleanup helpers
# ---------------------------------------------------------------------------

def _remove_tracking_pixels(soup: BeautifulSoup) -> None:
    for img in soup.find_all("img"):
        try:
            w = int(img.get("width", 999))
            h = int(img.get("height", 999))
            if w <= _TRACKING_PIXEL_MAX_DIMENSION and h <= _TRACKING_PIXEL_MAX_DIMENSION:
                img.decompose()
                continue
        except (ValueError, TypeError):
            pass
        # Also remove by known tracker domains
        src = img.get("src", "")
        if _is_tracker_url(src):
            img.decompose()


_TRACKER_DOMAINS = re.compile(
    r"pixel\.|tracking\.|open\.list-manage\.com|trk\.|beacon\.|click\.",
    re.IGNORECASE,
)


def _is_tracker_url(url: str) -> bool:
    try:
        host = urllib.parse.urlparse(url).netloc
        return bool(_TRACKER_DOMAINS.search(host))
    except Exception:
        return False


def _remove_unsubscribe_blocks(soup: BeautifulSoup) -> None:
    """Remove paragraphs/divs that look like unsubscribe footers."""
    for tag in soup.find_all(["p", "div", "td", "span"]):
        text = tag.get_text(" ", strip=True)
        if _UNSUBSCRIBE_PATTERNS.search(text) and len(text) < 500:
            tag.decompose()


def _remove_email_layout_tables(soup: BeautifulSoup) -> None:
    """
    Newsletter HTML uses nested tables for layout. Replace with divs
    so the content reflows properly on Kindle. We keep any tables
    that appear to contain actual tabular data (have <th> elements).
    """
    for table in soup.find_all("table"):
        if table.find("th"):
            continue  # Looks like a real data table, keep it
        # Unwrap the table, tbody, tr, td structure to plain divs
        for td in table.find_all(["td", "th"]):
            td.name = "div"
        for tr in table.find_all("tr"):
            tr.name = "div"
        for tbody in table.find_all(["tbody", "thead", "tfoot"]):
            tbody.unwrap()
        table.name = "div"


def _remove_scripts_and_styles(soup: BeautifulSoup) -> None:
    for tag in soup.find_all(["script", "style", "link", "meta", "noscript"]):
        tag.decompose()


_ALLOWED_ATTRS: dict[str, set[str]] = {
    "a": {"href"},
    "img": {"src", "alt"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
    "*": set(),  # all other tags: strip all attributes
}


def _sanitize_attributes(soup: BeautifulSoup) -> None:
    """Strip all HTML attributes except a safe allowlist."""
    for tag in soup.find_all(True):
        allowed = _ALLOWED_ATTRS.get(tag.name, set())
        attrs_to_remove = [a for a in list(tag.attrs) if a not in allowed]
        for attr in attrs_to_remove:
            del tag[attr]


# ---------------------------------------------------------------------------
# Image download with SSRF protection
# ---------------------------------------------------------------------------

def _is_safe_url(url: str) -> bool:
    """Return True only if the URL resolves to a globally-routable IP."""
    try:
        host = urllib.parse.urlparse(url).hostname
        if not host:
            return False
        # Resolve hostname — use first result
        infos = socket.getaddrinfo(host, None)
        if not infos:
            return False
        ip_str = infos[0][4][0]
        ip = ipaddress.ip_address(ip_str)
        return ip.is_global and not ip.is_loopback and not ip.is_private and not ip.is_link_local
    except Exception:
        return False


def _download_image(url: str, config: ProcessingConfig) -> tuple[bytes | None, str]:
    """
    Download an external image. Returns (bytes, mime_type) or (None, "").
    Enforces SSRF protection and size limits via streaming.
    """
    if not _is_safe_url(url):
        log.debug("Blocked image download (SSRF protection): %s", url)
        return None, ""

    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "kindle-email/1.0"},
        )
        with urllib.request.urlopen(req, timeout=config.image_timeout_seconds) as resp:
            mime = resp.headers.get_content_type() or "image/jpeg"
            if not mime.startswith("image/"):
                return None, ""
            max_bytes = config.max_image_size_kb * 1024
            data = resp.read(max_bytes + 1)
            if len(data) > max_bytes:
                log.debug("Image too large, skipping: %s", url)
                return None, ""
            return data, mime
    except (urllib.error.URLError, OSError) as e:
        log.debug("Failed to download image %s: %s", url, e)
        return None, ""


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _mime_to_ext(mime: str) -> str:
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
    }.get(mime, ".jpg")


def _safe_cid(cid: str) -> str:
    """Strip non-alphanumeric chars from a Content-ID for use as a filename."""
    return re.sub(r"[^\w]", "_", cid)[:40]


def _decode_data_uri(src: str) -> tuple[str, bytes] | None:
    """Decode a base64 data URI into (mime_type, bytes)."""
    import base64
    try:
        # data:<mime>;base64,<data>
        header, _, data = src.partition(",")
        if "base64" not in header:
            return None
        mime = header.split(":")[1].split(";")[0]
        return mime, base64.b64decode(data)
    except Exception:
        return None
