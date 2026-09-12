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
