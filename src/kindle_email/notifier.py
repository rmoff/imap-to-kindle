"""Post success/failure notifications to Telegram (the "Homelab alerts" bot).

Successful deliveries are sent silently (``disable_notification``): information, not alarms. Failures make a sound
(RM, 9 Nov 2026). Replaced the Discord webhook on 8 Nov 2026.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from .config import TelegramConfig

log = logging.getLogger(__name__)

_TIMEOUT = 10


def _post(telegram: TelegramConfig, text: str, *, silent: bool) -> None:
    body = json.dumps({
        "chat_id": telegram.chat_id,
        "text": text[:4000],
        "disable_notification": silent,
        "disable_web_page_preview": True,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{telegram.token}/sendMessage",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            resp.read()
    # never log the exception itself: urllib errors can carry the URL, which contains the bot token
    except urllib.error.HTTPError as e:
        log.warning("Telegram returned HTTP %s", e.code)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        log.warning("Telegram send failed (%s)", type(e).__name__)


def _build_message(*, success: bool, source: str, title: str, detail: str = "") -> str:
    icon = "✅" if success else "❌"
    verb = "Delivered" if success else "FAILED"
    msg = f"{icon} {verb} [{source}] {title}"
    if detail:
        msg += f"\n\n{detail}"
    return msg


def notify(telegram: TelegramConfig | None, *, success: bool, source: str, title: str, detail: str = "") -> None:
    """Send a notification. Silently no-ops if Telegram is not configured."""
    if telegram is None or not telegram.token or not telegram.chat_id:
        return
    _post(telegram, _build_message(success=success, source=source, title=title, detail=detail), silent=success)
