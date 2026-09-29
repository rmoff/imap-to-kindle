"""Tests for pdf_layout.py — synthetic papers built with PyMuPDF, no network."""

from __future__ import annotations

import pymupdf
import pytest

from kindle_email import pdf_layout

_W, _H = 612, 792  # US Letter, points
_WORDS = "lorem ipsum dolor sit amet consectetur adipiscing elit sed do".split()


def _line(tag: str, n: int, width_words: int) -> str:
    words = [_WORDS[(n + i) % len(_WORDS)] for i in range(width_words)]
    return f"{tag}{n:03d} " + " ".join(words)


def _two_column_pdf(pages: int = 3) -> bytes:
    doc = pymupdf.open()
    for p in range(pages):
        page = doc.new_page(width=_W, height=_H)
        if p == 0:
            page.insert_text((200, 70), "A Paper Title Spanning Both Columns", fontsize=14)
        y0 = 100 if p == 0 else 60
        for i, y in enumerate(range(y0, 720, 11)):
            page.insert_text((54, y), _line(f"P{p}L", i, 6), fontsize=9)
            page.insert_text((318, y), _line(f"P{p}R", i, 6), fontsize=9)
        page.insert_text((300, 760), str(100 + p), fontsize=9)  # page number
    return doc.tobytes()


def _one_column_pdf(pages: int = 3) -> bytes:
    doc = pymupdf.open()
    for p in range(pages):
        page = doc.new_page(width=_W, height=_H)
        for i, y in enumerate(range(60, 720, 11)):
            page.insert_text((72, y), _line(f"P{p}L", i, 14), fontsize=9)
    return doc.tobytes()


def _text_order(data: bytes) -> list[str]:
    """Line tags (P0L000, P0R000, ...) in reading order of the output."""
    doc = pymupdf.open(stream=data, filetype="pdf")
    tags = []
    for page in doc:
        # Only text actually visible on the page: clipped-away content from
        # show_pdf_page is still in the stream, so filter by the page rect.
        for *_, text, _block, _line, _word in page.get_text("words", clip=page.rect):
            if len(text) == 6 and text[0] == "P" and text[2] in "LR" and text[3:].isdigit():
                if text not in tags:
                    tags.append(text)
    return tags


def test_one_column_pdf_is_left_alone():
    assert pdf_layout.relayout_two_column(_one_column_pdf()) is None


def test_two_column_pdf_pages_have_screen_aspect_and_column_width():
    out = pdf_layout.relayout_two_column(_two_column_pdf())
    assert out is not None
    doc = pymupdf.open(stream=out, filetype="pdf")
    assert doc.page_count > 3
    for page in doc:
        assert page.rect.width / page.rect.height == pytest.approx(pdf_layout.DEFAULT_ASPECT, rel=0.01)
        assert page.rect.width < _W / 2  # sized to a column, not the full page


def test_two_column_pdf_reads_left_column_then_right():
    out = pdf_layout.relayout_two_column(_two_column_pdf(pages=2))
    order = _text_order(out)
    lines_per_col = len(range(60, 720, 11))
    expected = (
        [f"P0L{i:03d}" for i in range(len(range(100, 720, 11)))]
        + [f"P0R{i:03d}" for i in range(len(range(100, 720, 11)))]
        + [f"P1L{i:03d}" for i in range(lines_per_col)]
        + [f"P1R{i:03d}" for i in range(lines_per_col)]
    )
    assert order == expected


def test_page_breaks_never_split_a_line():
    out = pdf_layout.relayout_two_column(_two_column_pdf())
    doc = pymupdf.open(stream=out, filetype="pdf")
    for page in doc:
        for block in page.get_text("dict", clip=page.rect)["blocks"]:
            for line in block.get("lines", []):
                r = pymupdf.Rect(line["bbox"])
                assert r.y0 >= -0.5 and r.y1 <= page.rect.height + 0.5


def test_page_numbers_are_dropped():
    out = pdf_layout.relayout_two_column(_two_column_pdf())
    text = "".join(p.get_text(clip=p.rect) for p in pymupdf.open(stream=out, filetype="pdf"))
    assert "100" not in text.split() and "101" not in text.split()


def test_encrypted_pdf_is_left_alone():
    doc = pymupdf.open(stream=_two_column_pdf(), filetype="pdf")
    locked = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="x", owner_pw="y")
    assert pdf_layout.relayout_two_column(locked) is None


def test_guess_title_uses_largest_text_on_page_one():
    assert pdf_layout.guess_title(_two_column_pdf()) == "A Paper Title Spanning Both Columns"


def test_guess_title_prefers_metadata():
    doc = pymupdf.open(stream=_two_column_pdf(), filetype="pdf")
    doc.set_metadata({"title": "Embedded Metadata Title"})
    assert pdf_layout.guess_title(doc.tobytes()) == "Embedded Metadata Title"


def test_guess_title_none_for_garbage():
    assert pdf_layout.guess_title(b"not a pdf") is None
