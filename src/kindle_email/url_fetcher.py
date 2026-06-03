"""
Fetch a web page and render it as a CleanedContent ready for EPUB generation.

Used by Raindrop mode: each bookmark's URL is fetched, run through the same
cleaner used for newsletter emails, and merged into one EPUB.
"""

from __future__ import annotations

import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from html import unescape

from bs4 import BeautifulSoup

from . import cleaner
from .cleaner import CleanedContent, is_safe_url
from .config import ProcessingConfig
from .parser import ParsedEmail

log = logging.getLogger(__name__)

_MAX_PAGE_BYTES = 5 * 1024 * 1024
_FETCH_TIMEOUT_SEC = 20
# Minimum amount of visible article text we expect a real page to yield after
# cleaning. Below this we assume extraction failed — e.g. a paywall stub or a
# Substack redirect interstitial, whose only "content" is the destination URL.
# Delivering those produces a Kindle "article" that is just a bare link.
_MIN_ARTICLE_TEXT_CHARS = 200
# How many client-side (meta-refresh / JS) redirects fetch_page will chase.
_MAX_CLIENT_REDIRECTS = 3

# Substack share links (open.substack.com) often answer with a tiny HTML page
# that redirects to the post via <meta refresh> / location.replace() rather than
# an HTTP 3xx. urllib won't follow those, so we extract and follow them here.
_META_REFRESH_RE = re.compile(
    r"""http-equiv=["']?refresh["']?[^>]*content=["'][^"']*?url=([^"']+)["']""",
    re.IGNORECASE,
)
_JS_REDIRECT_RE = re.compile(
    r"""(?:location\.replace|location\.assign)\(\s*["']([^"']+)["']\s*\)"""
    r"""|(?:window\.)?location(?:\.href)?\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE,
)
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
        cleaned = _fetch_and_clean(url, config)

        # Substack (and others) intermittently serve a redirect interstitial
        # instead of the post: HTTP 200, but the only content is the destination
        # URL. That extracts to near-zero article text. A single refetch usually
        # gets the real article, so retry once before giving up on this URL.
        if cleaned is not None and _article_text_len(cleaned.html) < _MIN_ARTICLE_TEXT_CHARS:
            log.info("Thin extraction for %s (%d chars), refetching once", url, _article_text_len(cleaned.html))
            retry = _fetch_and_clean(url, config)
            if retry is not None and _article_text_len(retry.html) >= _MIN_ARTICLE_TEXT_CHARS:
                cleaned = retry

        if cleaned is None:
            log.warning("Skipping URL (fetch failed): %s", url)
            continue
        if _article_text_len(cleaned.html) < _MIN_ARTICLE_TEXT_CHARS:
            log.warning("Skipping URL (no substantive article extracted): %s", url)
            continue

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


def _fetch_and_clean(url: str, config: ProcessingConfig) -> CleanedContent | None:
    """Fetch a single URL and run it through the article cleaner.

    Returns None if the fetch itself failed (network error / bot block).
    """
    html = fetch_page(url)
    if not html:
        return None
    domain = urllib.parse.urlparse(url).netloc or "web"
    fake = ParsedEmail(subject=url, sender=domain, date="", html_body=html)
    return cleaner.clean(fake, config)


def _article_text_len(html: str) -> int:
    """Length of the visible text in a cleaned HTML body, used as a proxy for
    whether a real article was extracted (vs. a redirect/paywall stub)."""
    return len(BeautifulSoup(html, "lxml").get_text(" ", strip=True))


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
        return _follow_client_redirects(html, url)

    log.info("Direct fetch blocked, trying r.jina.ai fallback: %s", url)
    return _jina_fetch(url)


def _follow_client_redirects(html: str, url: str) -> str:
    """Chase <meta refresh>/JS redirects that urllib's HTTP layer can't see.

    Returns the HTML of the final page (or the original if there's no redirect).
    """
    current = url
    for _ in range(_MAX_CLIENT_REDIRECTS):
        target = _resolve_client_redirect(html, current)
        if not target or target == current:
            break
        if not is_safe_url(target):
            log.warning("Refusing unsafe client-redirect target: %s", target)
            break
        log.info("Following client-side redirect: %s -> %s", current, target)
        nxt = _direct_fetch(target)
        if nxt is None:
            break
        html, current = nxt, target
    return html


def _resolve_client_redirect(html: str, base_url: str) -> str | None:
    """Return the absolute URL a page redirects to via <meta refresh> or a JS
    location.replace/assign/href, or None if the page isn't a client redirect."""
    m = _META_REFRESH_RE.search(html)
    if not m:
        m = _JS_REDIRECT_RE.search(html)
    if not m:
        return None
    raw = next((g for g in m.groups() if g), None)
    if not raw:
        return None
    return urllib.parse.urljoin(base_url, unescape(raw.strip()))


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
