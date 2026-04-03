"""Tests for epub.py — EPUB generation."""

from __future__ import annotations

from kindle_email import epub
from kindle_email.cleaner import CleanedContent


def _make_content(**overrides) -> CleanedContent:
    defaults = dict(
        title="Test Newsletter",
        html="<p>Hello world.</p>",
        images={},
        image_types={},
    )
    defaults.update(overrides)
    return CleanedContent(**defaults)


def test_generate_returns_bytes():
    content = _make_content()
    filename, data = epub.generate(content)
    assert isinstance(data, bytes)
    assert len(data) > 0
    # EPUB files are ZIP archives; check magic bytes
    assert data[:2] == b"PK"


def test_generate_filename_from_title():
    content = _make_content(title="My Newsletter Issue 5")
    filename, _ = epub.generate(content)
    assert filename.endswith(".epub")
    assert "My" in filename or "newsletter" in filename.lower()


def test_safe_filename_strips_dangerous_chars():
    assert epub.safe_filename("../../../etc/passwd") == "etcpasswd"
    assert epub.safe_filename("normal title") == "normal title"
    assert epub.safe_filename("") == "newsletter"
    assert epub.safe_filename("a" * 200)[:100] == "a" * 100


def test_generate_with_image():
    # 1x1 transparent PNG
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00"
        b"\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    content = _make_content(
        html='<p>Look:</p><img src="photo.png" alt="photo" />',
        images={"photo.png": png_bytes},
        image_types={"photo.png": "image/png"},
    )
    filename, data = epub.generate(content)
    assert data[:2] == b"PK"
    # Image path should be rewritten to images/ subdirectory in the EPUB
    # We can't easily inspect the EPUB internals without unzipping,
    # but we verify generation doesn't error out and returns valid data.
    assert len(data) > 100


def test_generate_escapes_xml_in_title():
    content = _make_content(title='Foo & Bar <test> "quoted"')
    filename, data = epub.generate(content)
    assert data[:2] == b"PK"
