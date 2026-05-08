"""Post success/failure notifications to a Discord webhook."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from .config import DiscordConfig

log = logging.getLogger(__name__)

_TIMEOUT = 10


def _post(webhook_url: str, content: str) -> None:
    body = json.dumps({"content": content[:1900]}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=body,
        headers={
            "Content-Type": "application/json",
            # Discord rejects the default `Python-urllib/X.Y` UA with 403.
            "User-Agent": "kindle-email/1.0 (+https://github.com/rmoff/imap-to-kindle)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        log.warning("Discord webhook returned HTTP %s: %s", e.code, e.reason)
    except (urllib.error.URLError, TimeoutError) as e:
        log.warning("Discord webhook failed: %s", e)


def notify(discord: DiscordConfig | None, *, success: bool, source: str, title: str, detail: str = "") -> None:
    """Send a notification. Silently no-ops if discord is not configured."""
    if discord is None or not discord.webhook_url:
        return
    icon = "✅" if success else "❌"
    verb = "sent to Kindle" if success else "FAILED"
    msg = f"{icon} **{verb}** [{source}] {title}"
    if detail:
        msg += f"\n```{detail}```"
    _post(discord.webhook_url, msg)
