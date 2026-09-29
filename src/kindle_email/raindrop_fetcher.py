"""
Raindrop.io API client: find raindrops tagged with the source tag, deliver
them to Kindle, then add a processed (or failed) tag alongside it.

The source tag is deliberately left in place: if it were removed from every
bookmark, Raindrop would drop it from the tag list and it would stop showing
up as a suggestion when tagging new saves.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field

from .config import Config

log = logging.getLogger(__name__)

_API_BASE = "https://api.raindrop.io/rest/v1"
_HTTP_TIMEOUT = 30
_USER_AGENT = "kindle-email/1.0"
_PAGE_SIZE = 50


@dataclass
class Raindrop:
    id: int
    link: str
    title: str
    tags: list[str] = field(default_factory=list)
    # Raindrop's own classification from the page's content type:
    # "link", "article", "document" (PDFs), "image", "video", "audio".
    type: str = "link"


class RaindropAPIError(Exception):
    """Raised on non-2xx responses from the Raindrop API."""


def fetch_raindrops(config: Config) -> Iterator[Raindrop]:
    """Yield raindrops (across all collections) carrying the source tag but
    not yet marked processed or failed."""
    assert config.raindrop is not None
    rc = config.raindrop
    # Raindrop's search DSL: #"tag" matches a specific tag, -#"tag" excludes
    # it. Collection 0 means "search across everything the user owns".
    search = f'#"{rc.source_tag}" -#"{rc.processed_tag}" -#"{rc.failed_tag}"'
    query = urllib.parse.urlencode(
        {"search": search, "perpage": _PAGE_SIZE, "sort": "-created"}
    )
    url = f"{_API_BASE}/raindrops/0?{query}"
    payload = _request("GET", url, rc.token)
    items = payload.get("items", [])
    if items:
        log.info("Found %d raindrop(s) tagged '%s'", len(items), rc.source_tag)
    for item in items:
        rid = item.get("_id")
        link = item.get("link")
        if not isinstance(rid, int) or not link:
            log.warning("Skipping malformed raindrop: %s", item)
            continue
        tags = item.get("tags") or []
        if not isinstance(tags, list):
            tags = []
        # Belt and braces: if the search ever stopped honouring the exclusions,
        # every past bookmark would be re-sent on every poll.
        if rc.processed_tag in tags or rc.failed_tag in tags:
            log.warning("Search returned already-handled raindrop %d; skipping", rid)
            continue
        yield Raindrop(
            id=rid, link=link, title=item.get("title") or link, tags=list(tags),
            type=item.get("type") or "link",
        )


def mark_processed(config: Config, raindrop: Raindrop) -> None:
    assert config.raindrop is not None
    _add_tag(config, raindrop, config.raindrop.processed_tag)


def mark_failed(config: Config, raindrop: Raindrop) -> None:
    assert config.raindrop is not None
    _add_tag(config, raindrop, config.raindrop.failed_tag)


def _add_tag(config: Config, raindrop: Raindrop, tag: str) -> None:
    """Add `tag` to the raindrop's tag list, keeping the source tag and the rest."""
    assert config.raindrop is not None
    new_tags = list(raindrop.tags)
    if tag and tag not in new_tags:
        new_tags.append(tag)
    url = f"{_API_BASE}/raindrop/{raindrop.id}"
    body = json.dumps({"tags": new_tags}).encode()
    _request("PUT", url, config.raindrop.token, body=body)
    log.debug("Tagged raindrop %d with %s", raindrop.id, tag)


def _request(method: str, url: str, token: str, body: bytes | None = None) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": _USER_AGENT,
        "Accept": "application/json",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            raw = resp.read()
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RaindropAPIError(f"Raindrop API {method} {url} → HTTP {e.code}") from e
    except (urllib.error.URLError, OSError) as e:
        raise RaindropAPIError(f"Raindrop API {method} {url} → {e}") from e


def fetch_and_process(config: Config, process_fn) -> int:
    """
    Drain raindrops carrying the source tag, calling process_fn(raindrop) for each.
    Returns count of successful deliveries. Retries transient API errors
    with exponential backoff.

    process_fn returns True on success → raindrop tagged as processed;
    False → tagged as failed.
    """
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            processed = 0
            for raindrop in fetch_raindrops(config):
                success = process_fn(raindrop)
                try:
                    if success:
                        mark_processed(config, raindrop)
                        processed += 1
                    else:
                        mark_failed(config, raindrop)
                except RaindropAPIError as e:
                    log.error("Could not retag raindrop %d after processing: %s", raindrop.id, e)
            return processed
        except RaindropAPIError as e:
            if attempt == max_retries:
                raise
            wait = 2 ** attempt
            log.warning(
                "Raindrop API error (attempt %d/%d): %s — retrying in %ds",
                attempt, max_retries, e, wait,
            )
            time.sleep(wait)
    return 0
