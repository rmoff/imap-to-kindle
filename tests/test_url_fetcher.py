"""Tests for url_fetcher.py — page fetching and cleaned-content merging."""

from __future__ import annotations

from unittest.mock import patch

from kindle_email import url_fetcher
from kindle_email.config import ProcessingConfig


def _config(**overrides) -> ProcessingConfig:
    defaults = dict(
        max_image_size_kb=500,
        max_images_per_email=20,
        download_external_images=False,  # keep tests offline
        image_timeout_seconds=10,
    )
    defaults.update(overrides)
    return ProcessingConfig(**defaults)


def test_build_cleaned_content_merges_pages():
    page1 = (
        "<html><head><title>Page One</title></head>"
        "<body><article><p>Article one content here.</p></article></body></html>"
    )
    page2 = (
        "<html><head><title>Page Two</title></head>"
        "<body><article><p>Article two content here.</p></article></body></html>"
    )

    with patch.object(url_fetcher, "fetch_page", side_effect=[page1, page2]):
        cleaned = url_fetcher.build_cleaned_content(
            ["https://a.com/1", "https://b.com/2"], _config()
        )

    assert cleaned is not None
    assert "Article one content" in cleaned.html
    assert "Article two content" in cleaned.html
    assert "Page One" in cleaned.html
    assert "Page Two" in cleaned.html
    assert cleaned.title.startswith("Page One")
    assert "+1 more" in cleaned.title


def test_build_cleaned_content_single_url_uses_page_title():
    page = (
        "<html><head><title>Just One</title></head>"
        "<body><article><p>Some text.</p></article></body></html>"
    )
    with patch.object(url_fetcher, "fetch_page", return_value=page):
        cleaned = url_fetcher.build_cleaned_content(["https://a.com/1"], _config())
    assert cleaned is not None
    assert cleaned.title == "Just One"


def test_build_cleaned_content_returns_none_when_all_fail():
    with patch.object(url_fetcher, "fetch_page", return_value=None):
        cleaned = url_fetcher.build_cleaned_content(["https://a.com/1"], _config())
    assert cleaned is None


def test_build_cleaned_content_skips_failed_url():
    """If one URL fails but another succeeds, deliver what we have."""
    page = (
        "<html><head><title>Survivor</title></head>"
        "<body><article><p>Content.</p></article></body></html>"
    )
    with patch.object(url_fetcher, "fetch_page", side_effect=[None, page]):
        cleaned = url_fetcher.build_cleaned_content(
            ["https://a.com/broken", "https://b.com/ok"], _config()
        )
    assert cleaned is not None
    assert "Survivor" in cleaned.html


def test_fetch_page_blocks_ssrf():
    """fetch_page must refuse private/loopback URLs before opening a socket."""
    assert url_fetcher.fetch_page("http://127.0.0.1/") is None
    assert url_fetcher.fetch_page("http://169.254.169.254/latest/meta-data/") is None
