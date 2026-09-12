"""Tests for delivery.py — the CWA ingest sink and the multi-target dispatcher."""

from __future__ import annotations

from kindle_email import delivery
from kindle_email.config import (
    Config,
    CwaConfig,
    DeliveryConfig,
    ImapConfig,
    KindleConfig,
    LabelConfig,
    ProcessingConfig,
    ScheduleConfig,
    SmtpConfig,
)


def _config(*, targets, ingest_dir=None, staging_dir=None) -> Config:
    return Config(
        imap=ImapConfig(host="h", port=993, username="u", password="p"),
        labels=LabelConfig(watch="w", processed="p", failed="f"),
        schedule=ScheduleConfig(poll_interval_seconds=300),
        processing=ProcessingConfig(500, 20, False, 10),
        delivery=DeliveryConfig(targets=targets),
        smtp=SmtpConfig(host="h", port=587, username="u", password="p") if "smtp" in targets else None,
        kindle=KindleConfig(address="k@kindle.com", from_address="u@x.com") if "smtp" in targets else None,
        cwa=CwaConfig(ingest_dir=ingest_dir, staging_dir=staging_dir) if ingest_dir else None,
    )


# --- CWA sink ---------------------------------------------------------------

def test_cwa_sink_writes_epub_bytes(tmp_path):
    config = _config(targets=["cwa"], ingest_dir=str(tmp_path))
    ok = delivery.deliver_to_cwa("My Article.epub", b"PK\x03\x04payload", config)
    assert ok is True
    assert (tmp_path / "My Article.epub").read_bytes() == b"PK\x03\x04payload"


def test_cwa_sink_leaves_no_partial_temp_files(tmp_path):
    config = _config(targets=["cwa"], ingest_dir=str(tmp_path))
    delivery.deliver_to_cwa("a.epub", b"data", config)
    # The final file is the only thing left; no temp/partial artefacts remain.
    assert [p.name for p in tmp_path.iterdir()] == ["a.epub"]


def test_cwa_sink_does_not_clobber_existing_file(tmp_path):
    config = _config(targets=["cwa"], ingest_dir=str(tmp_path))
    delivery.deliver_to_cwa("dup.epub", b"first", config)
    delivery.deliver_to_cwa("dup.epub", b"second", config)
    files = sorted(tmp_path.iterdir())
    assert len(files) == 2
    assert sorted(p.read_bytes() for p in files) == [b"first", b"second"]


def test_cwa_sink_strips_directory_components_from_filename(tmp_path):
    config = _config(targets=["cwa"], ingest_dir=str(tmp_path))
    ok = delivery.deliver_to_cwa("../../etc/evil.epub", b"data", config)
    assert ok is True
    assert (tmp_path / "evil.epub").read_bytes() == b"data"
    # Nothing escaped the ingest dir.
    assert [p.name for p in tmp_path.iterdir()] == ["evil.epub"]


def test_cwa_sink_returns_false_when_dir_cannot_be_created(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a dir")
    config = _config(targets=["cwa"], ingest_dir=str(blocker / "sub"))
    assert delivery.deliver_to_cwa("a.epub", b"data", config) is False


def test_cwa_sink_uses_separate_staging_dir(tmp_path):
    ingest = tmp_path / "ingest"
    ingest.mkdir()
    staging = tmp_path / "staging"
    staging.mkdir()
    config = _config(targets=["cwa"], ingest_dir=str(ingest), staging_dir=str(staging))
    ok = delivery.deliver_to_cwa("x.epub", b"data", config)
    assert ok is True
    assert (ingest / "x.epub").read_bytes() == b"data"
    assert list(staging.iterdir()) == []  # staging left clean after the rename


# --- Dispatcher -------------------------------------------------------------

def test_deliver_cwa_only_writes_to_library(tmp_path):
    config = _config(targets=["cwa"], ingest_dir=str(tmp_path))
    assert delivery.deliver("a.epub", b"data", config) is True
    assert (tmp_path / "a.epub").read_bytes() == b"data"


def test_deliver_to_both_attempts_both_and_succeeds(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(delivery.sender, "send", lambda fn, data, cfg: calls.append(fn) or True)
    config = _config(targets=["cwa", "smtp"], ingest_dir=str(tmp_path))
    assert delivery.deliver("a.epub", b"data", config) is True
    assert calls == ["a.epub"]                 # SMTP attempted
    assert (tmp_path / "a.epub").exists()      # CWA attempted


def test_deliver_to_both_fails_overall_when_one_target_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(delivery.sender, "send", lambda fn, data, cfg: False)
    config = _config(targets=["cwa", "smtp"], ingest_dir=str(tmp_path))
    # Overall failure because not every target succeeded...
    assert delivery.deliver("a.epub", b"data", config) is False
    # ...but the CWA target was still attempted (no silent skip on partial failure).
    assert (tmp_path / "a.epub").exists()
