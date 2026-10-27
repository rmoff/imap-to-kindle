"""Tests for cleaner.py — content extraction, cleanup, and SSRF protection."""

from __future__ import annotations

from unittest.mock import patch

from .conftest import load_fixture
from kindle_email import cleaner, parser
from kindle_email.config import ProcessingConfig


def _default_config(**overrides) -> ProcessingConfig:
    defaults = dict(
        max_image_size_kb=500,
        max_images_per_email=20,
        download_external_images=True,
        image_timeout_seconds=10,
    )
    defaults.update(overrides)
    return ProcessingConfig(**defaults)


def test_clean_removes_tracking_pixel():
    raw = load_fixture("simple_newsletter.eml")
    parsed = parser.parse(raw)
    config = _default_config()
    result = cleaner.clean(parsed, config)

    # Tracking pixel (1x1) should be removed
    assert "pixel.gif" not in result.html


def test_clean_removes_unsubscribe_block():
    raw = load_fixture("simple_newsletter.eml")
    parsed = parser.parse(raw)
    config = _default_config()
    result = cleaner.clean(parsed, config)

    # Unsubscribe link/text should be gone
    assert "Unsubscribe" not in result.html


def test_clean_preserves_article_content():
    raw = load_fixture("simple_newsletter.eml")
    parsed = parser.parse(raw)
    config = _default_config()
    result = cleaner.clean(parsed, config)

    assert result.title  # Should have a title
    assert result.html   # Should have some content


def test_clean_handles_cid_images():
    raw = load_fixture("multipart_with_cid_image.eml")
    parsed = parser.parse(raw)
    config = _default_config()
    result = cleaner.clean(parsed, config)

    # The CID image should be extracted into the images dict
    assert len(result.images) == 1
    filename = list(result.images.keys())[0]
    assert filename.endswith(".png")
    # The src in HTML should be rewritten to the local filename
    assert f'src="{filename}"' in result.html


def test_ssrf_protection_blocks_private_ips():
    """External image downloads to private/link-local addresses must be blocked."""
    raw = load_fixture("ssrf_test.eml")
    parsed = parser.parse(raw)
    config = _default_config(download_external_images=True)

    # _download_image should not be called for any of the malicious URLs,
    # or if called, _is_safe_url should return False and no download happens.
    # Either way, no images should end up in result.images.
    result = cleaner.clean(parsed, config)
    assert result.images == {}


def test_ssrf_is_safe_url_blocks_known_bad():
    assert cleaner.is_safe_url("http://169.254.169.254/latest/meta-data/") is False
    assert cleaner.is_safe_url("http://localhost/admin") is False
    assert cleaner.is_safe_url("http://192.168.1.1/config") is False
    assert cleaner.is_safe_url("http://10.0.0.1/internal") is False
    assert cleaner.is_safe_url("http://127.0.0.1/") is False


def test_disable_external_images():
    """When download_external_images=False, no external images downloaded."""
    raw = load_fixture("simple_newsletter.eml")
    parsed = parser.parse(raw)
    # Add a fake external image to the HTML
    parsed.html_body += '<img src="https://example.com/real-image.jpg" alt="photo" />'
    config = _default_config(download_external_images=False)
    result = cleaner.clean(parsed, config)
    assert result.images == {}


def test_max_images_per_email_enforced():
    """Images beyond the limit are dropped."""
    from kindle_email.parser import ParsedEmail

    # Build a parsed email with many CID images
    n = 5
    images = {f"img{i}@x": b"\x89PNG\r\n" for i in range(n)}
    image_types = {f"img{i}@x": "image/png" for i in range(n)}
    html = "".join(f'<img src="cid:img{i}@x" />' for i in range(n))
    parsed = ParsedEmail(
        subject="test",
        sender="x@x.com",
        date="",
        html_body=f"<p>article</p>{html}",
        inline_images=images,
        inline_image_types=image_types,
    )
    config = _default_config(max_images_per_email=3)
    result = cleaner.clean(parsed, config)
    assert len(result.images) <= 3


_UNTITLED_EMAIL = (
    b"From: The Consensus Weekly <weeklynewsletter@theconsensus.dev>\r\n"
    b"Subject: The Consensus Weekly, October 25, 2026\r\n"
    b"Date: Sun, 25 Oct 2026 18:30:00 +0000\r\n"
    b"Content-Type: text/html; charset=utf-8\r\n\r\n"
    b"<html><head></head><body><div><h3>The Consensus</h3>"
    b"<p>In a closed loop benchmark, mean latency is just the number of clients "
    b"divided by throughput. Latency is better measured with an open loop "
    b"benchmark, but there you have to consider request distribution.</p>"
    b"</div></body></html>"
)


def test_clean_falls_back_to_subject_when_html_has_no_title():
    # readability returns "[no-title]" (with brackets) when there's no <title>
    result = cleaner.clean(parser.parse(_UNTITLED_EMAIL), _default_config())

    assert result.title == "The Consensus Weekly, October 25, 2026"
    assert result.author == "The Consensus Weekly"


def test_clean_carries_the_email_date():
    result = cleaner.clean(parser.parse(_UNTITLED_EMAIL), _default_config())

    assert result.date == "2026-10-25"


def test_clean_ignores_a_malformed_date():
    raw = _UNTITLED_EMAIL.replace(b"25 Oct 2026 18:30:00", b"25 Oct 9999999999 18:30:00")
    result = cleaner.clean(parser.parse(raw), _default_config())

    assert result.date == ""
