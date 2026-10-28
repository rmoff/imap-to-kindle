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


def test_generate_sets_date_metadata():
    import io
    import zipfile

    _, data = epub.generate(_make_content(date="2026-10-25"))
    opf = zipfile.ZipFile(io.BytesIO(data)).read("EPUB/content.opf").decode()
    assert "<dc:date>2026-10-25</dc:date>" in opf


def _toc_and_doc(data: bytes) -> tuple[str, str]:
    import io
    import zipfile

    z = zipfile.ZipFile(io.BytesIO(data))
    return z.read("EPUB/toc.ncx").decode(), z.read("EPUB/document.xhtml").decode()


def test_toc_lists_each_heading():
    # Benedict's newsletter: one h3 column, then h4 news items
    html = (
        "<h3>Black October</h3><p>a</p>"
        "<h4>Agent economics</h4><p>b</p>"
        "<h4>Apple’s shadow &amp; regulation</h4><p>c</p>"
    )
    toc, doc = _toc_and_doc(epub.generate(_make_content(html=html))[1])
    for text in ("Test Newsletter", "Black October", "Agent economics", "Apple’s shadow &amp; regulation"):
        assert f"<text>{text}</text>" in toc
    # Flat: the h4 news items are not children of the h3 column
    assert toc.count("<navPoint") == 4 and "</navPoint></navPoint>" not in toc.replace("\n", "").replace(" ", "")
    # Each entry points at an anchor that exists in the document
    import re
    for anchor in re.findall(r'src="document\.xhtml#([^"]+)"', toc):
        assert f'id="{anchor}"' in doc


def test_toc_keeps_existing_heading_id():
    html = '<h2 id="intro">Intro</h2><p>a</p><h2>Next</h2>'
    toc, doc = _toc_and_doc(epub.generate(_make_content(html=html))[1])
    assert 'src="document.xhtml#intro"' in toc
    assert doc.count('id="intro"') == 1


def test_toc_single_entry_without_headings():
    toc, _ = _toc_and_doc(epub.generate(_make_content(html="<h2>Only one</h2><p>x</p>"))[1])
    assert toc.count("<navPoint") == 1


def test_toc_multi_article_book_lists_articles_only():
    html = "<h1>First</h1><h2>Sub a</h2><p>x</p><h1>Second</h1><h2>Sub b</h2>"
    toc, _ = _toc_and_doc(epub.generate(_make_content(html=html))[1])
    assert "<text>First</text>" in toc and "<text>Second</text>" in toc
    assert "Sub a" not in toc


def test_toc_single_h1_still_lists_sections():
    html = "<h1>Article</h1><h2>First</h2><p>x</p><h2>Second</h2>"
    toc, _ = _toc_and_doc(epub.generate(_make_content(html=html))[1])
    assert "<text>First</text>" in toc and "<text>Second</text>" in toc
