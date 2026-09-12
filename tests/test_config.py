"""Tests for config.py — delivery-target selection and optional SMTP."""

from __future__ import annotations

import pytest

from kindle_email import config as cfg

_ENV_VARS = [
    "KINDLE_EMAIL_IMAP_PASSWORD",
    "KINDLE_EMAIL_SMTP_PASSWORD",
    "KINDLE_EMAIL_RAINDROP_TOKEN",
    "KINDLE_EMAIL_DISCORD_WEBHOOK",
]


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    # The config file is the only source of truth in these tests.
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _write(tmp_path, body: str) -> str:
    path = tmp_path / "config.toml"
    path.write_text(body)
    return str(path)


# IMAP is always required; passwords live in the file so no env var is needed.
_IMAP = """
[imap]
host = "imap.gmail.com"
username = "me@example.com"
password = "imap-pw"
"""

_SMTP = """
[smtp]
host = "smtp.gmail.com"
username = "me@example.com"
password = "smtp-pw"

[kindle]
address = "me@kindle.com"
from_address = "me@example.com"
"""

_CWA = """
[cwa]
ingest_dir = "/cwa-book-ingest"
"""


def test_default_target_is_smtp(tmp_path):
    config = cfg.load(_write(tmp_path, _IMAP + _SMTP))
    assert config.delivery.targets == ["smtp"]
    assert config.smtp is not None
    assert config.cwa is None


def test_cwa_only_does_not_require_smtp(tmp_path):
    body = _IMAP + '\n[delivery]\ntargets = ["cwa"]\n' + _CWA
    config = cfg.load(_write(tmp_path, body))
    assert config.delivery.targets == ["cwa"]
    assert config.cwa.ingest_dir == "/cwa-book-ingest"
    assert config.smtp is None
    assert config.kindle is None


def test_target_string_normalizes_to_list(tmp_path):
    body = _IMAP + '\n[delivery]\ntarget = "cwa"\n' + _CWA
    config = cfg.load(_write(tmp_path, body))
    assert config.delivery.targets == ["cwa"]


def test_both_targets_keep_order_and_require_both_sections(tmp_path):
    body = _IMAP + _SMTP + '\n[delivery]\ntargets = ["cwa", "smtp"]\n' + _CWA
    config = cfg.load(_write(tmp_path, body))
    assert config.delivery.targets == ["cwa", "smtp"]
    assert config.smtp is not None
    assert config.cwa is not None


def test_cwa_target_without_cwa_section_raises(tmp_path):
    body = _IMAP + '\n[delivery]\ntargets = ["cwa"]\n'
    with pytest.raises(ValueError):
        cfg.load(_write(tmp_path, body))


def test_smtp_target_requires_password(tmp_path):
    body = _IMAP + '\n[delivery]\ntargets = ["smtp"]\n' + """
[smtp]
host = "smtp.gmail.com"
username = "me@example.com"

[kindle]
address = "me@kindle.com"
from_address = "me@example.com"
"""
    with pytest.raises(ValueError):
        cfg.load(_write(tmp_path, body))


def test_unknown_target_raises(tmp_path):
    body = _IMAP + '\n[delivery]\ntargets = ["carrier-pigeon"]\n'
    with pytest.raises(ValueError):
        cfg.load(_write(tmp_path, body))


def test_optional_staging_dir_is_parsed(tmp_path):
    body = _IMAP + '\n[delivery]\ntargets = ["cwa"]\n' + """
[cwa]
ingest_dir = "/cwa-book-ingest"
staging_dir = "/cwa-ingest-tmp"
"""
    config = cfg.load(_write(tmp_path, body))
    assert config.cwa.staging_dir == "/cwa-ingest-tmp"
