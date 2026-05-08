"""
Fetch a web page and render it as a CleanedContent ready for EPUB generation.

Used by Raindrop mode: each bookmark's URL is fetched, run through the same
cleaner used for newsletter emails, and merged into one EPUB.
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.parse
import urllib.request

from . import cleaner
from .cleaner import CleanedContent, is_safe_url
from .config import ProcessingConfig
from .parser import ParsedEmail

log = logging.getLogger(__name__)

_MAX_PAGE_BYTES = 5 * 1024 * 1024
_FETCH_TIMEOUT_SEC = 20
# Some publishers (e.g. dl.acm.org) 403 anything that looks like a bot, so we
# present as a recent desktop Chrome.
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def build_cleaned_content(urls: list[str], config: ProcessingConfig) -> CleanedContent | None:
    """
    Fetch each URL, run it through the article cleaner, and merge the results
    into a single CleanedContent. Returns None if every fetch failed.
    """
    sections: list[str] = []
    titles: list[str] = []
    merged_images: dict[str, bytes] = {}
    merged_types: dict[str, str] = {}

    for i, url in enumerate(urls):
        html = fetch_page(url)
        if not html:
            log.warning("Skipping URL (fetch failed): %s", url)
            continue

        domain = urllib.parse.urlparse(url).netloc or "web"
        fake = ParsedEmail(subject=url, sender=domain, date="", html_body=html)
        cleaned = cleaner.clean(fake, config)

        # cleaner.clean restarts its image counter at 0 for each call, so
        # filenames (img_ext_0 etc.) collide across URLs. Prefix by page index.
        prefix = f"p{i}_"
        block_html = cleaned.html
        for fname, data in cleaned.images.items():
            new_fname = prefix + fname
            block_html = block_html.replace(f'src="{fname}"', f'src="{new_fname}"')
            merged_images[new_fname] = data
            merged_types[new_fname] = cleaned.image_types.get(fname, "image/jpeg")

        title = cleaned.title or url
        titles.append(title)
        sections.append(
            f'<h1>{_escape(title)}</h1>\n'
            f'<p><a href="{_escape(url)}">{_escape(url)}</a></p>\n'
            f'{block_html}\n<hr/>\n'
        )

    if not sections:
        return None

    combined_title = titles[0] if len(titles) == 1 else f"{titles[0]} (+{len(titles) - 1} more)"
    author = urllib.parse.urlparse(urls[0]).netloc or "Web"
    return CleanedContent(
        title=combined_title,
        html="".join(sections),
        author=author,
        images=merged_images,
        image_types=merged_types,
    )


def fetch_page(url: str) -> str | None:
    """Fetch a URL and return its HTML body. SSRF-protected and size-capped.

    On 403/406/429 from the origin (typical bot-block responses), falls back to
    https://r.jina.ai/<url>, which renders many gated pages server-side. The
    fallback itself can be blocked when the target sits behind a Cloudflare
    CAPTCHA (e.g. dl.acm.org) — those pages are not retrievable without a real
    browser session.
    """
    if not is_safe_url(url):
        log.warning("Blocked URL fetch (SSRF protection): %s", url)
        return None

    html = _direct_fetch(url)
    if html is not None:
        return html

    log.info("Direct fetch blocked, trying r.jina.ai fallback: %s", url)
    return _jina_fetch(url)


def _direct_fetch(url: str) -> str | None:
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
                "Accept-Encoding": "identity",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
            },
        )
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT_SEC) as resp:
            content_type = resp.headers.get_content_type() or ""
            if not (content_type.startswith("text/html") or content_type.startswith("application/xhtml")):
                log.warning("Refusing non-HTML response (%s) from %s", content_type, url)
                return None
            data = resp.read(_MAX_PAGE_BYTES + 1)
            if len(data) > _MAX_PAGE_BYTES:
                log.warning("Page exceeds %d bytes, truncating: %s", _MAX_PAGE_BYTES, url)
                data = data[:_MAX_PAGE_BYTES]
            charset = resp.headers.get_content_charset() or "utf-8"
            return data.decode(charset, errors="replace")
    except urllib.error.HTTPError as e:
        if e.code in (403, 406, 429):
            log.warning("Fetch blocked (HTTP %s): %s", e.code, url)
            return None
        log.warning("Failed to fetch %s: HTTP %s %s", url, e.code, e.reason)
        return None
    except (urllib.error.URLError, OSError) as e:
        log.warning("Failed to fetch %s: %s", url, e)
        return None


def _jina_fetch(url: str) -> str | None:
    """Fetch via r.jina.ai, which returns a markdown rendering wrapped as text.

    We wrap the response in a minimal HTML envelope so the existing cleaner
    pipeline can ingest it unchanged.
    """
    proxied = "https://r.jina.ai/" + url
    try:
        req = urllib.request.Request(
            proxied,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "text/plain, text/html;q=0.9",
                "Accept-Encoding": "identity",
            },
        )
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT_SEC) as resp:
            data = resp.read(_MAX_PAGE_BYTES + 1)
            if len(data) > _MAX_PAGE_BYTES:
                data = data[:_MAX_PAGE_BYTES]
            text = data.decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as e:
        log.warning("Jina fallback failed for %s: %s", url, e)
        return None

    if "Performing security verification" in text or "requiring CAPTCHA" in text:
        log.warning("Jina fallback hit CAPTCHA wall for %s", url)
        return None

    paragraphs = "".join(f"<p>{_escape(line)}</p>\n" for line in text.splitlines() if line.strip())
    return f"<html><body>{paragraphs}</body></html>"


def _escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
    )
