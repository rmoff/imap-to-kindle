"""Tests for pipeline.py — delivery is routed through the delivery dispatcher."""

from __future__ import annotations

from kindle_email import pipeline
from kindle_email.cleaner import CleanedContent
from kindle_email.config import (
    Config,
    CwaConfig,
    DeliveryConfig,
    ImapConfig,
    LabelConfig,
    ProcessingConfig,
    ScheduleConfig,
)
from kindle_email.raindrop_fetcher import Raindrop


def _config(*, targets, ingest_dir) -> Config:
    return Config(
        imap=ImapConfig(host="h", port=993, username="u", password="p"),
        labels=LabelConfig(watch="w", processed="p", failed="f"),
        schedule=ScheduleConfig(poll_interval_seconds=300),
        processing=ProcessingConfig(500, 20, False, 10),
        delivery=DeliveryConfig(targets=targets),
        cwa=CwaConfig(ingest_dir=ingest_dir),
        # discord left None → notifier no-ops
    )


def test_deliver_and_notify_returns_delivery_result(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(
        pipeline.delivery, "deliver",
        lambda fn, data, cfg: seen.update(fn=fn, data=data) or True,
    )
    config = _config(targets=["cwa"], ingest_dir=str(tmp_path))
    ok = pipeline._deliver_and_notify("t.epub", b"bytes", source="raindrop", title="T", config=config)
    assert ok is True
    assert seen == {"fn": "t.epub", "data": b"bytes"}


def test_process_raindrop_delivers_via_delivery(monkeypatch, tmp_path):
    cleaned = CleanedContent(title="T", html="<p>x</p>")
    monkeypatch.setattr(pipeline.url_fetcher, "build_cleaned_content", lambda links, proc: cleaned)
    monkeypatch.setattr(pipeline.epub, "generate", lambda c: ("T.epub", b"PKdata"))
    delivered = {}
    monkeypatch.setattr(
        pipeline.delivery, "deliver",
        lambda fn, data, cfg: delivered.update(fn=fn) or True,
    )
    config = _config(targets=["cwa"], ingest_dir=str(tmp_path))
    r = Raindrop(id=1, link="http://example.com/a", title="A")
    assert pipeline.process_raindrop(r, config) is True
    assert delivered["fn"] == "T.epub"
