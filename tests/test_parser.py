"""Tests for parser.py — MIME parsing."""

from __future__ import annotations

from .conftest import load_fixture
from kindle_email import parser


def test_parse_simple_newsletter():
    raw = load_fixture("simple_newsletter.eml")
    result = parser.parse(raw)

    assert result.subject == "My Weekly Newsletter #42"
    assert "author@example.com" in result.sender
    assert result.html_body
    assert "This Week in Tech" in result.html_body
    assert result.inline_images == {}


def test_parse_multipart_with_cid_image():
    raw = load_fixture("multipart_with_cid_image.eml")
    result = parser.parse(raw)

    assert result.subject == "Newsletter with Inline Image"
    assert result.html_body
    assert "cid:logo@example.com" in result.html_body
    assert "logo@example.com" in result.inline_images
    assert result.inline_image_types["logo@example.com"] == "image/png"


def test_parse_extracts_html_over_plain():
    """When both text/html and text/plain are present, HTML is preferred."""
    raw = load_fixture("multipart_with_cid_image.eml")
    result = parser.parse(raw)
    # Should be HTML, not the plain text fallback wrapped in <pre>
    assert "<h1>" in result.html_body


def test_parse_handles_empty_subject():
    """Gracefully handle emails with no Subject header."""
    raw = b"From: x@x.com\r\nContent-Type: text/html\r\n\r\n<p>hi</p>"
    result = parser.parse(raw)
    assert result.subject == ""
    assert "hi" in result.html_body
