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


def _capture_delivery(monkeypatch) -> dict:
    delivered: dict = {}
    monkeypatch.setattr(
        pipeline.delivery, "deliver",
        lambda fn, data, cfg: delivered.update(fn=fn, data=data) or True,
    )
    return delivered


def test_process_raindrop_delivers_pdf_relaid(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline.url_fetcher, "fetch_pdf", lambda url: b"%PDF-original")
    monkeypatch.setattr(pipeline.pdf_layout, "guess_title", lambda data: None)
    monkeypatch.setattr(pipeline.pdf_layout, "relayout_two_column", lambda data: b"%PDF-relaid")
    monkeypatch.setattr(
        pipeline.url_fetcher, "build_cleaned_content",
        lambda *a: (_ for _ in ()).throw(AssertionError("should not render as a web page")),
    )
    delivered = _capture_delivery(monkeypatch)
    config = _config(targets=["cwa"], ingest_dir=str(tmp_path))
    r = Raindrop(id=1, link="https://www.vldb.org/pvldb/vol19/p4953-x.pdf", title="Dataflow Revisited",
                 type="document")
    assert pipeline.process_raindrop(r, config) is True
    assert delivered == {"fn": "Dataflow Revisited.pdf", "data": b"%PDF-relaid"}


def test_process_raindrop_sends_single_column_pdf_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline.url_fetcher, "fetch_pdf", lambda url: b"%PDF-original")
    monkeypatch.setattr(pipeline.pdf_layout, "guess_title", lambda data: None)
    monkeypatch.setattr(pipeline.pdf_layout, "relayout_two_column", lambda data: None)
    delivered = _capture_delivery(monkeypatch)
    config = _config(targets=["cwa"], ingest_dir=str(tmp_path))
    r = Raindrop(id=1, link="https://arxiv.org/pdf/1706.03762", title="https://arxiv.org/pdf/1706.03762")
    assert pipeline.process_raindrop(r, config) is True
    # No title anywhere, so the file is named after the URL's last segment.
    assert delivered == {"fn": "170603762.pdf", "data": b"%PDF-original"}


def test_process_raindrop_pdf_relayout_error_sends_original(monkeypatch, tmp_path):
    def boom(data):
        raise RuntimeError("bad pdf")
    monkeypatch.setattr(pipeline.url_fetcher, "fetch_pdf", lambda url: b"%PDF-original")
    monkeypatch.setattr(pipeline.pdf_layout, "relayout_two_column", boom)
    delivered = _capture_delivery(monkeypatch)
    config = _config(targets=["cwa"], ingest_dir=str(tmp_path))
    r = Raindrop(id=1, link="https://a.com/p.pdf", title="P")
    assert pipeline.process_raindrop(r, config) is True
    assert delivered["data"] == b"%PDF-original"


def test_process_raindrop_pdf_link_without_pdf_falls_back_to_web_page(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline.url_fetcher, "fetch_pdf", lambda url: None)
    cleaned = CleanedContent(title="Landing", html="<p>x</p>")
    monkeypatch.setattr(pipeline.url_fetcher, "build_cleaned_content", lambda links, proc: cleaned)
    monkeypatch.setattr(pipeline.epub, "generate", lambda c: ("Landing.epub", b"PKdata"))
    delivered = _capture_delivery(monkeypatch)
    config = _config(targets=["cwa"], ingest_dir=str(tmp_path))
    r = Raindrop(id=1, link="https://a.com/p.pdf", title="P", type="document")
    assert pipeline.process_raindrop(r, config) is True
    assert delivered["fn"] == "Landing.epub"


def test_process_raindrop_web_page_skips_pdf_fetch(monkeypatch, tmp_path):
    monkeypatch.setattr(
        pipeline.url_fetcher, "fetch_pdf",
        lambda url: (_ for _ in ()).throw(AssertionError("not a PDF link")),
    )
    cleaned = CleanedContent(title="T", html="<p>x</p>")
    monkeypatch.setattr(pipeline.url_fetcher, "build_cleaned_content", lambda links, proc: cleaned)
    monkeypatch.setattr(pipeline.epub, "generate", lambda c: ("T.epub", b"PKdata"))
    _capture_delivery(monkeypatch)
    config = _config(targets=["cwa"], ingest_dir=str(tmp_path))
    r = Raindrop(id=1, link="https://example.com/post", title="T", type="article")
    assert pipeline.process_raindrop(r, config) is True


def test_pdf_relayout_can_be_turned_off(monkeypatch, tmp_path):
    monkeypatch.setattr(
        pipeline.pdf_layout, "relayout_two_column",
        lambda data: (_ for _ in ()).throw(AssertionError("relayout is off")),
    )
    config = _config(targets=["cwa"], ingest_dir=str(tmp_path))
    config.processing.pdf_relayout = False
    assert pipeline._relayout_pdf(b"%PDF-x", config) == b"%PDF-x"


def test_process_raindrop_pdf_prefers_title_from_the_pdf(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline.url_fetcher, "fetch_pdf", lambda url: b"%PDF-original")
    monkeypatch.setattr(pipeline.pdf_layout, "relayout_two_column", lambda data: None)
    monkeypatch.setattr(pipeline.pdf_layout, "guess_title", lambda data: "The Dataflow Model Revisited")
    delivered = _capture_delivery(monkeypatch)
    config = _config(targets=["cwa"], ingest_dir=str(tmp_path))
    r = Raindrop(id=1, link="https://a.com/p4953-x.pdf", title="P4953 x", type="document")
    assert pipeline.process_raindrop(r, config) is True
    assert delivered["fn"] == "The Dataflow Model Revisited.pdf"


def test_process_raindrop_pdf_gets_title_and_author_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline.url_fetcher, "fetch_pdf", lambda url: b"%PDF-original")
    monkeypatch.setattr(pipeline.pdf_layout, "relayout_two_column", lambda data: None)
    monkeypatch.setattr(pipeline.pdf_layout, "guess_title", lambda data: "The Dataflow Model Revisited")
    seen = {}
    monkeypatch.setattr(
        pipeline.pdf_layout, "set_metadata",
        lambda data, title, author: seen.update(title=title, author=author) or b"%PDF-titled",
    )
    delivered = _capture_delivery(monkeypatch)
    config = _config(targets=["cwa"], ingest_dir=str(tmp_path))
    r = Raindrop(id=1, link="https://www.vldb.org/pvldb/p4953-x.pdf", title="P4953 x", type="document")
    assert pipeline.process_raindrop(r, config) is True
    assert seen == {"title": "The Dataflow Model Revisited", "author": "vldb.org"}
    assert delivered["data"] == b"%PDF-titled"
