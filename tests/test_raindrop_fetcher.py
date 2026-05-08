"""Tests for raindrop_fetcher.py — API calls are stubbed, no network."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from kindle_email import raindrop_fetcher
from kindle_email.config import (
    Config,
    ImapConfig,
    KindleConfig,
    LabelConfig,
    ProcessingConfig,
    RaindropConfig,
    ScheduleConfig,
    SmtpConfig,
)
from kindle_email.raindrop_fetcher import Raindrop, RaindropAPIError


def _make_config(**raindrop_overrides) -> Config:
    raindrop_kwargs = dict(
        token="test-token",
        source_tag="sendtokindle",
        processed_tag="sendtokindle_processed",
        failed_tag="sendtokindle_failed",
    )
    raindrop_kwargs.update(raindrop_overrides)
    return Config(
        imap=ImapConfig(host="x", port=993, username="u", password="p"),
        smtp=SmtpConfig(host="x", port=587, username="u", password="p"),
        kindle=KindleConfig(address="k@kindle.com", from_address="u@x.com"),
        labels=LabelConfig(watch="w", processed="p", failed="f"),
        schedule=ScheduleConfig(poll_interval_seconds=300),
        processing=ProcessingConfig(500, 20, False, 10),
        raindrop=RaindropConfig(**raindrop_kwargs),
    )


def _stub_response(payload: dict) -> MagicMock:
    ctx = MagicMock()
    ctx.read.return_value = json.dumps(payload).encode()
    cm = MagicMock()
    cm.__enter__.return_value = ctx
    cm.__exit__.return_value = False
    return cm


def test_fetch_raindrops_queries_source_tag():
    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        return _stub_response({"items": []})

    with patch.object(raindrop_fetcher.urllib.request, "urlopen", side_effect=fake_urlopen):
        list(raindrop_fetcher.fetch_raindrops(_make_config()))

    # Collection 0 = all; query escaped as %23sendtokindle.
    assert "/raindrops/0?" in captured["url"]
    assert "%23sendtokindle" in captured["url"]


def test_fetch_raindrops_yields_items_with_tags():
    payload = {
        "items": [
            {"_id": 1, "link": "https://a.com/x", "title": "A", "tags": ["sendtokindle", "tech"]},
            {"_id": 2, "link": "https://b.com/y", "title": "B", "tags": ["sendtokindle"]},
        ]
    }
    with patch.object(raindrop_fetcher.urllib.request, "urlopen", return_value=_stub_response(payload)):
        items = list(raindrop_fetcher.fetch_raindrops(_make_config()))
    assert items == [
        Raindrop(id=1, link="https://a.com/x", title="A", tags=["sendtokindle", "tech"]),
        Raindrop(id=2, link="https://b.com/y", title="B", tags=["sendtokindle"]),
    ]


def test_fetch_raindrops_skips_malformed_items():
    payload = {
        "items": [
            {"_id": 1, "link": "https://a.com/x", "title": "A", "tags": ["sendtokindle"]},
            {"_id": None, "link": "nope"},
            {"_id": 3, "link": "", "title": "C"},
        ]
    }
    with patch.object(raindrop_fetcher.urllib.request, "urlopen", return_value=_stub_response(payload)):
        items = list(raindrop_fetcher.fetch_raindrops(_make_config()))
    assert [r.id for r in items] == [1]


def test_mark_processed_swaps_source_for_processed_tag_preserving_others():
    config = _make_config()
    captured = {}

    def fake_urlopen(req, timeout):
        captured["method"] = req.method
        captured["url"] = req.full_url
        captured["body"] = req.data
        return _stub_response({})

    rd = Raindrop(id=42, link="https://a.com", title="A", tags=["sendtokindle", "tech", "longreads"])
    with patch.object(raindrop_fetcher.urllib.request, "urlopen", side_effect=fake_urlopen):
        raindrop_fetcher.mark_processed(config, rd)

    assert captured["method"] == "PUT"
    assert captured["url"].endswith("/raindrop/42")
    sent_tags = json.loads(captured["body"])["tags"]
    assert "sendtokindle" not in sent_tags
    assert "sendtokindle_processed" in sent_tags
    assert "tech" in sent_tags
    assert "longreads" in sent_tags


def test_mark_failed_uses_failed_tag():
    config = _make_config()
    captured = {}

    def fake_urlopen(req, timeout):
        captured["body"] = req.data
        return _stub_response({})

    rd = Raindrop(id=7, link="https://b.com", title="B", tags=["sendtokindle"])
    with patch.object(raindrop_fetcher.urllib.request, "urlopen", side_effect=fake_urlopen):
        raindrop_fetcher.mark_failed(config, rd)

    sent_tags = json.loads(captured["body"])["tags"]
    assert sent_tags == ["sendtokindle_failed"]


def test_swap_does_not_duplicate_when_target_already_present():
    """If a raindrop somehow already has the target tag, don't create a duplicate."""
    config = _make_config()
    captured = {}

    def fake_urlopen(req, timeout):
        captured["body"] = req.data
        return _stub_response({})

    rd = Raindrop(
        id=9, link="https://c.com", title="C",
        tags=["sendtokindle", "sendtokindle_processed"],
    )
    with patch.object(raindrop_fetcher.urllib.request, "urlopen", side_effect=fake_urlopen):
        raindrop_fetcher.mark_processed(config, rd)

    sent_tags = json.loads(captured["body"])["tags"]
    assert sent_tags.count("sendtokindle_processed") == 1
    assert "sendtokindle" not in sent_tags


def test_fetch_and_process_routes_success_and_failure():
    config = _make_config()
    rds = [
        Raindrop(id=1, link="https://a.com/good", title="ok", tags=["sendtokindle"]),
        Raindrop(id=2, link="https://b.com/bad", title="fail", tags=["sendtokindle"]),
    ]
    moves: list[tuple[str, int]] = []

    def fake_proc(cfg, r):
        moves.append(("processed", r.id))

    def fake_fail(cfg, r):
        moves.append(("failed", r.id))

    def process_fn(r: Raindrop) -> bool:
        return r.id == 1

    with patch.object(raindrop_fetcher, "fetch_raindrops", return_value=iter(rds)), \
         patch.object(raindrop_fetcher, "mark_processed", side_effect=fake_proc), \
         patch.object(raindrop_fetcher, "mark_failed", side_effect=fake_fail):
        count = raindrop_fetcher.fetch_and_process(config, process_fn)

    assert count == 1
    assert moves == [("processed", 1), ("failed", 2)]


def test_fetch_and_process_retries_then_raises():
    config = _make_config()

    def always_fail(cfg):
        raise RaindropAPIError("boom")

    with patch.object(raindrop_fetcher, "fetch_raindrops", side_effect=always_fail), \
         patch.object(raindrop_fetcher.time, "sleep"):
        with pytest.raises(RaindropAPIError):
            raindrop_fetcher.fetch_and_process(config, lambda r: True)
