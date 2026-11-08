"""Tests for notifier.py — message wording is delivery-target-neutral."""

from __future__ import annotations

from kindle_email import notifier


def test_build_message_is_delivery_neutral():
    msg = notifier._build_message(success=True, source="raindrop", title="My Post", detail="")
    assert "Kindle" not in msg           # no longer Kindle-specific
    assert "raindrop" in msg
    assert "My Post" in msg


def test_build_message_failure_includes_detail():
    msg = notifier._build_message(success=False, source="email", title="X", detail="boom")
    assert "boom" in msg
    assert "X" in msg


def test_notify_posts_silently_to_telegram(monkeypatch):
    sent = {}

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"ok":true}'

    def fake_urlopen(req, timeout):
        sent["url"] = req.full_url
        sent["body"] = __import__("json").loads(req.data)
        return Resp()

    monkeypatch.setattr(notifier.urllib.request, "urlopen", fake_urlopen)
    notifier.notify(notifier.TelegramConfig(token="T0K", chat_id="42"), success=True, source="email", title="Book")
    assert sent["url"].endswith("/botT0K/sendMessage")
    assert sent["body"]["chat_id"] == "42"
    assert sent["body"]["disable_notification"] is True
    assert "Book" in sent["body"]["text"]


def test_notify_noops_without_config(monkeypatch):
    monkeypatch.setattr(notifier.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("called")))
    notifier.notify(None, success=True, source="email", title="Book")


def test_send_failure_does_not_log_the_token(monkeypatch, caplog):
    def boom(req, timeout):
        raise notifier.urllib.error.URLError(f"bad {req.full_url}")
    monkeypatch.setattr(notifier.urllib.request, "urlopen", boom)
    notifier.notify(notifier.TelegramConfig(token="SECRET123", chat_id="42"), success=False, source="email", title="Book")
    assert "SECRET123" not in caplog.text
