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


def test_two_column_pdf_pages_fit_screen_and_column_width():
    out = pdf_layout.relayout_two_column(_two_column_pdf())
    assert out is not None
    doc = pymupdf.open(stream=out, filetype="pdf")
    assert doc.page_count > 3
    for page in doc:
        # Slightly shorter than the screen, leaving room for the reader's status bar.
        expected = pdf_layout.DEFAULT_ASPECT / (1 - pdf_layout._READER_CHROME)
        assert page.rect.width / page.rect.height == pytest.approx(expected, rel=0.01)
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


def test_footnotes_move_after_the_page_text():
    doc = pymupdf.open()
    page = doc.new_page(width=_W, height=_H)
    for i, y in enumerate(range(60, 640, 11)):
        page.insert_text((54, y), _line("P0L", i, 6), fontsize=9)
    for i, y in enumerate(range(60, 720, 11)):
        page.insert_text((318, y), _line("P0R", i, 6), fontsize=9)
    page.draw_line((54, 660), (102, 660), width=0.4)   # footnote rule
    page.insert_text((54, 675), "NOTE01 a footnote in smaller type", fontsize=7)
    page.insert_text((54, 685), "NOTE02 carrying on", fontsize=7)
    out = pdf_layout.relayout_two_column(doc.tobytes())
    assert out is not None
    words = [
        w[4] for pg in pymupdf.open(stream=out, filetype="pdf")
        for w in pg.get_text("words", clip=pg.rect)
    ]
    last_right = max(i for i, w in enumerate(words) if w.startswith("P0R"))
    assert words.index("NOTE01") > last_right
    assert words.index("NOTE02") > words.index("NOTE01")


def test_rule_above_body_size_text_is_not_a_footnote():
    """A short rule followed by normal-size text (e.g. a section divider) stays put."""
    doc = pymupdf.open()
    page = doc.new_page(width=_W, height=_H)
    for i, y in enumerate(range(60, 640, 11)):
        page.insert_text((54, y), _line("P0L", i, 6), fontsize=9)
    for i, y in enumerate(range(60, 720, 11)):
        page.insert_text((318, y), _line("P0R", i, 6), fontsize=9)
    page.draw_line((54, 660), (102, 660), width=0.4)
    page.insert_text((54, 675), "BODY01 more body text", fontsize=9)
    out = pdf_layout.relayout_two_column(doc.tobytes())
    words = [
        w[4] for pg in pymupdf.open(stream=out, filetype="pdf")
        for w in pg.get_text("words", clip=pg.rect)
    ]
    first_right = min(i for i, w in enumerate(words) if w.startswith("P0R"))
    assert words.index("BODY01") < first_right


def test_set_metadata_fills_title_and_author():
    out = pdf_layout.set_metadata(_two_column_pdf(pages=1), "A Paper", "vldb.org")
    meta = pymupdf.open(stream=out, filetype="pdf").metadata
    assert (meta["title"], meta["author"]) == ("A Paper", "vldb.org")


def test_set_metadata_keeps_existing_author():
    doc = pymupdf.open(stream=_two_column_pdf(pages=1), filetype="pdf")
    doc.set_metadata({"author": "Real Author"})
    out = pdf_layout.set_metadata(doc.tobytes(), "A Paper", "vldb.org")
    assert pymupdf.open(stream=out, filetype="pdf").metadata["author"] == "Real Author"


def test_set_metadata_returns_garbage_unchanged():
    assert pdf_layout.set_metadata(b"not a pdf", "T", "A") == b"not a pdf"


# --- page-break and footnote placement rules ----------------------------------

def _piece(y0, y1, *, cuts=(), para=(), note=False):
    return pdf_layout._Piece(0, pymupdf.Rect(0, y0, 100, y1), list(cuts), "L", note=note, para_cuts=list(para))


def test_notes_wait_for_the_next_paragraph_end():
    body_mid_sentence = _piece(0, 100)                    # ends mid-paragraph
    note = _piece(100, 120, note=True)
    next_col = _piece(0, 100, cuts=[20, 40], para=[20])   # paragraph ends at y=20
    out = pdf_layout._place_notes([body_mid_sentence, note, next_col])
    assert [(p.rect.y0, p.rect.y1, p.note) for p in out] == [
        (0, 100, False),
        (0, 20, False),     # rest of the interrupted paragraph
        (100, 120, True),   # then the note
        (20, 100, False),
    ]


def test_notes_stay_put_when_the_text_before_already_ends_a_paragraph():
    ends_para = _piece(0, 100, para=[100])
    note = _piece(100, 120, note=True)
    out = pdf_layout._place_notes([ends_para, note, _piece(0, 100)])
    assert [p.note for p in out] == [False, True, False]


def test_notes_with_no_later_paragraph_end_go_last():
    out = pdf_layout._place_notes([_piece(0, 100), _piece(100, 120, note=True), _piece(0, 50)])
    assert [p.note for p in out] == [False, False, True]


def _ln(y0, y1, text="body text", *, bold=False, size=9.0):
    return pdf_layout._Line(pymupdf.Rect(0, y0, 90, y1), text, size, bold, False)


def test_no_break_after_a_heading_or_before_a_caption_or_inside_a_figure():
    rect = pymupdf.Rect(0, 0, 100, 300)
    lines = [
        _ln(0, 10), _ln(12, 22, "2.4 Examples", bold=True), _ln(24, 34),
        _ln(100, 110, "Figure 5: Example Inputs"), _ln(112, 122),
    ]
    figures = [(40, 90)]
    ok = lambda y: pdf_layout._break_ok(y, rect, lines, figures)
    assert ok(11)           # between body lines
    assert not ok(23)       # straight after a heading
    assert not ok(60)       # inside a figure
    assert not ok(95)       # just before a caption
    assert ok(111)          # after the caption
