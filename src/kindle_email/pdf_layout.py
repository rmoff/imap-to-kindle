"""
Re-lay a two-column PDF (the usual academic-paper layout) for an e-reader.

Each column is cropped out as vector content and restacked onto pages with
the reader's screen aspect ratio, sized to the column width, so text shows at
roughly print size with no panning. Full-width regions (title block, wide
figures/tables) are kept whole, scaled to the page width. Page breaks only
fall between lines, never through one. Text stays real text, so it remains
searchable/selectable. PDFs that aren't two-column come back as None.
"""

from __future__ import annotations

import collections
import logging
import re
import unicodedata
from dataclasses import dataclass, field

import pymupdf

log = logging.getLogger(__name__)

# Screen width / height. Kindles are all close to 3:4 (Paperwhite 1236x1648,
# Basic 1072x1448), so one ratio suits them all.
DEFAULT_ASPECT = 3 / 4
# Share of the screen height taken by the reader's own status bar (KOReader's
# footer, Kindle's progress line). Pages are made this much shorter than the
# screen so a whole page fits: otherwise fit-to-width overflows and the reader
# scrolls, leaving half-lines at the bottom.
_READER_CHROME = 0.05
_MARGIN = 6          # pt of white space above/below each output page's content
_SIDE_MARGIN = 10    # pt of white space left/right
_BLOCK_GAP = 6       # pt between stacked pieces
_MID_SLACK = 8       # a box counts as "crossing the gutter" if it spans mid±this
_COL_PAD = 4         # pt kept either side of a column's text edges when cropping
_BAND_GAP = 8        # pt of full-width white space that separates layout bands
_SHORT_BAND = 0.2    # side-by-side blocks shorter than this (x page height) stay whole


@dataclass
class _Piece:
    page: int
    rect: pymupdf.Rect
    cuts: list[float]   # y values (page coords) where a page break is safe
    side: str = ""      # "L"/"R" for a column piece, "" for full width
    note: bool = False  # a column's footnote area
    # The subset of cuts (plus rect.y1) that end a paragraph: where held-back
    # footnotes can go without interrupting a sentence.
    para_cuts: list[float] = field(default_factory=list)


@dataclass
class _Line:
    """One line of text on a source page."""
    rect: pymupdf.Rect
    text: str
    size: float         # largest font size in the line
    bold: bool
    note_mark: bool     # opens with a superscript footnote marker


# A figure or table caption: "Figure 5: ...", "Fig. 2.", "Table 1 ...".
_CAPTION = re.compile(r"^(Figure|Fig\.|Table)\s*\d+", re.IGNORECASE)


def _content_boxes(page: pymupdf.Page) -> tuple[list[_Line], list[pymupdf.Rect]]:
    """(text lines, non-text boxes: images + vector drawings)."""
    lines: list[_Line] = []
    h = page.rect.height
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            r = pymupdf.Rect(line["bbox"])
            spans = [s for s in line["spans"] if s["text"].strip()]
            text = "".join(s["text"] for s in line["spans"]).strip()
            if r.is_empty or not spans:
                continue
            # Sideways text (arXiv's margin stamp, rotated axis labels) isn't
            # part of the reading flow and would skew the column edges.
            if abs(line["dir"][0] - 1) > 0.01:
                continue
            # Page numbers in the header/footer would land mid-flow; drop them.
            if text.isdigit() and len(text) <= 5 and (r.y0 > 0.9 * h or r.y1 < 0.1 * h):
                continue
            lines.append(_Line(
                rect=r,
                text=text,
                size=max(s["size"] for s in spans),
                bold=all(s["flags"] & 16 for s in spans),
                note_mark=_starts_with_note_mark(spans),
            ))
    other = [pymupdf.Rect(i["bbox"]) for i in page.get_image_info()]
    for d in page.get_drawings():
        r = pymupdf.Rect(d["rect"])
        if r.width > 1 or r.height > 1:
            other.append(r)
    area = page.rect
    clipped = [pymupdf.Rect(r) & area for r in other]
    # Drop degenerate boxes and page-sized backgrounds/frames, which would
    # otherwise make the whole page look full-width.
    other = [
        r for r in clipped
        if (r.width > 0 or r.height > 0) and r.width * r.height < 0.5 * area.width * area.height
    ]
    return lines, other


def _starts_with_note_mark(spans: list[dict]) -> bool:
    """Whether a line opens with a superscript footnote marker: a leading span
    of digits or a note symbol, set smaller than the text that follows."""
    if len(spans) < 2:
        return False
    first = spans[0]["text"].strip()
    marker = first.isdigit() or all(ch in "*†‡§¶" for ch in first)
    return marker and len(first) <= 3 and spans[0]["size"] < 0.85 * max(s["size"] for s in spans[1:])


def _body_size(lines: list[_Line]) -> float:
    """The body text size: the one covering the most line width. A plain
    median gets dragged down by the many tiny labels inside figures."""
    width_by_size: collections.Counter[float] = collections.Counter()
    for line in lines:
        width_by_size[round(line.size, 1)] += line.rect.width
    return width_by_size.most_common(1)[0][0]


def _within(r: pymupdf.Rect, rect: pymupdf.Rect) -> bool:
    return r.x0 >= rect.x0 - 1 and r.x1 <= rect.x1 + 1 and r.y0 >= rect.y0 - 1 and r.y1 <= rect.y1 + 1


def _line_above(y: float, rect: pymupdf.Rect, lines: list[_Line]) -> _Line | None:
    """The last text line inside `rect` above `y`."""
    above = [ln for ln in lines if _within(ln.rect, rect) and ln.rect.y1 <= y + 0.5]
    return max(above, key=lambda ln: ln.rect.y1) if above else None


def _line_below(y: float, rect: pymupdf.Rect, lines: list[_Line]) -> _Line | None:
    """The first text line inside `rect` below `y`."""
    below = [ln for ln in lines if _within(ln.rect, rect) and ln.rect.y0 >= y - 0.5]
    return min(below, key=lambda ln: ln.rect.y0) if below else None


def _ends_paragraph(y: float, rect: pymupdf.Rect, lines: list[_Line]) -> bool:
    """Whether the text in `rect` above `y` ends with a paragraph's last line:
    short of the right edge (justified text fills every other line) and not a
    bold heading, which belongs with the paragraph after it."""
    above = _line_above(y, rect, lines)
    return above is not None and not above.bold and above.rect.x1 < rect.x1 - 12


def _figure_spans(
    rect: pymupdf.Rect, lines: list[_Line], other: list[pymupdf.Rect], body: float,
) -> list[tuple[float, float]]:
    """Vertical extents of the figures/tables/listings in `rect`: drawings and
    images plus the small label text around them. A small-type block with no
    drawing in it (a references list) isn't a figure."""
    graphics = [r for r in other if _within(r, rect)]
    small = [ln.rect for ln in lines if _within(ln.rect, rect) and ln.size < body - 1.5]
    spans = _merge_ranges([(r.y0, r.y1) for r in graphics + small], gap=12)
    return [(a, b) for a, b in spans if any(a - 0.5 <= g.y0 and g.y1 <= b + 0.5 for g in graphics)]


def _break_ok(y: float, rect: pymupdf.Rect, lines: list[_Line], figures: list[tuple[float, float]]) -> bool:
    """Whether a page may break at `y`: not inside a figure, not straight after
    a heading, not just before a caption (which belongs with its figure)."""
    if any(a < y < b for a, b in figures):
        return False
    above = _line_above(y, rect, lines)
    if above is not None and above.bold:
        return False
    below = _line_below(y, rect, lines)
    return below is None or not _CAPTION.match(below.text)


def _footnote_top(
    lines: list[_Line], other: list[pymupdf.Rect], col_x0: float, col_x1: float, page_h: float,
) -> float | None:
    """Where the footnote area of a column starts, or None if it has none.

    Footnotes sit under a short rule that starts at the column's left edge, in
    the lower half of the page. Below it is either only smaller-than-body text
    or (in papers that set notes at body size) a line opening with a
    superscript marker.
    """
    col = [ln for ln in lines if ln.rect.x0 >= col_x0 - 4 and ln.rect.x1 <= col_x1 + 2]
    if not col:
        return None
    body = _body_size(col)
    rules = sorted(
        (
            r for r in other
            if r.height < 2 and 20 < r.width < 0.6 * (col_x1 - col_x0)
            and abs(r.x0 - col_x0) < 3 and r.y0 > 0.5 * page_h
        ),
        key=lambda r: r.y0,
    )
    for rule in rules:
        below = sorted((ln for ln in col if ln.rect.y0 > rule.y0), key=lambda ln: ln.rect.y0)
        if below and (max(ln.size for ln in below) < body - 0.4 or below[0].note_mark):
            return rule.y0
    return None


def _merge_ranges(ranges: list[tuple[float, float]], gap: float = 2) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _cuts(boxes: list[pymupdf.Rect], y0: float, y1: float) -> list[float]:
    """Candidate break points: midpoints of vertical gaps between boxes in [y0, y1]."""
    spans = _merge_ranges([(b.y0, b.y1) for b in boxes if b.y1 > y0 and b.y0 < y1], gap=0)
    return [(a[1] + b[0]) / 2 for a, b in zip(spans, spans[1:])]


def _page_pieces(page: pymupdf.Page, pno: int) -> list[_Piece] | None:
    """Split one page into reading-order pieces, or None if it isn't two-column."""
    lines, other = _content_boxes(page)
    if not lines:
        return []
    rects = [ln.rect for ln in lines]
    boxes = rects + other
    mid = page.rect.width / 2

    def crosses(r: pymupdf.Rect) -> bool:
        return r.x0 < mid - _MID_SLACK and r.x1 > mid + _MID_SLACK

    # Two-column pages have almost no text lines crossing the middle and a
    # healthy share on each side; single-column text mostly spans it.
    left_lines = [r for r in rects if r.x1 <= mid + _MID_SLACK]
    right_lines = [r for r in rects if r.x0 >= mid - _MID_SLACK]
    crossing = len(rects) - len(left_lines) - len(right_lines)
    if (
        len(rects) < 10 or crossing > 0.15 * len(rects)
        or min(len(left_lines), len(right_lines)) < 0.25 * len(rects)
    ):
        return None

    x0 = min(r.x0 for r in boxes)
    x1 = max(r.x1 for r in boxes)
    # Crop each column to the extent of its text, so every column lands at the
    # same margin once restacked. Text only: drawings can poke out (a code
    # listing's drop shadow, a stray box in the gutter).
    left_x0 = min(r.x0 for r in left_lines) - _COL_PAD
    left_x1 = max(r.x1 for r in left_lines) + _COL_PAD
    right_x0 = min(r.x0 for r in right_lines) - _COL_PAD
    right_x1 = max(r.x1 for r in right_lines) + _COL_PAD
    # Full-width bands: y-ranges occupied by anything straddling the gutter.
    full = _merge_ranges([(r.y0, r.y1) for r in boxes if crosses(r)])
    # Everything else is two-column; walk the page top to bottom.
    ys = sorted({min(r.y0 for r in boxes), max(r.y1 for r in boxes), *[y for b in full for y in b]})
    pieces: list[_Piece] = []
    for top, bot in zip(ys, ys[1:]):
        if bot - top < 1:
            continue
        is_full = any(a <= top + 0.5 and bot - 0.5 <= b for a, b in full)
        in_band = [r for r in boxes if r.y1 > top and r.y0 < bot]
        if not in_band:
            continue
        if is_full:
            rect = pymupdf.Rect(x0, top, x1, bot)
            pieces.append(_Piece(pno, rect, _cuts(in_band, top, bot)))
            continue
        # The author grid under a paper's title reads across, not down: keep
        # its short side-by-side rows whole. Only on page 1 right below the
        # title, so body text is never glued across the gutter.
        after_title = pno == 0 and any(abs(b - top) < 0.5 for _, b in full)
        spans = _merge_ranges([(r.y0, r.y1) for r in in_band], gap=0)
        gaps = [(a[1] + b[0]) / 2 for a, b in zip(spans, spans[1:]) if b[0] - a[1] >= _BAND_GAP]
        if not after_title:
            gaps = []
        for g in gaps:
            sub = [r for r in in_band if r.y0 >= top and r.y1 <= g]
            lft = [r for r in sub if r.x1 <= mid + _MID_SLACK]
            rgt = [r for r in sub if r.x0 >= mid - _MID_SLACK]
            short = page.rect.height * _SHORT_BAND
            if not (lft and rgt) or g - top > short:
                break
            pieces.append(_Piece(pno, pymupdf.Rect(x0, top, x1, g), _cuts(sub, top, g)))
            top = g
            in_band = [r for r in in_band if r.y1 > top]
        for side in ("L", "R"):
            col = [r for r in in_band if (r.x1 <= mid + _MID_SLACK) == (side == "L") and not crosses(r)]
            if not col:
                continue
            cy0 = max(top, min(r.y0 for r in col))
            cy1 = min(bot, max(r.y1 for r in col))
            cx0, cx1 = (left_x0, left_x1) if side == "L" else (right_x0, right_x1)
            pieces.append(_Piece(pno, pymupdf.Rect(cx0, cy0, cx1, cy1), _cuts(col, cy0, cy1), side))
    pieces = _move_footnotes(pieces, lines, other, page.rect.height)
    body = _body_size(lines)
    for piece in pieces:
        figures = _figure_spans(piece.rect, lines, other, body)
        piece.cuts = [c for c in piece.cuts if _break_ok(c, piece.rect, lines, figures)]
        if not piece.note:
            piece.para_cuts = [
                c for c in [*piece.cuts, piece.rect.y1] if _ends_paragraph(c, piece.rect, lines)
            ]
    return pieces


def _move_footnotes(
    pieces: list[_Piece], lines: list[_Line], other: list[pymupdf.Rect], page_h: float,
) -> list[_Piece]:
    """Split each column's footnotes off and move them after the page's text
    (from where _place_notes later moves them on to the end of a paragraph)."""
    notes: list[_Piece] = []
    out: list[_Piece] = []
    for piece in pieces:
        top = None
        if piece.side:
            top = _footnote_top(lines, other, piece.rect.x0 + _COL_PAD, piece.rect.x1 - _COL_PAD, page_h)
        if top is None or not piece.rect.y0 < top < piece.rect.y1:
            out.append(piece)
            continue
        # Strictly above the rule: the rule itself (zero height, y1 == top)
        # belongs with the notes as their separator.
        above = [
            r for r in [ln.rect for ln in lines] + other
            if r.y1 < top - 0.5 and piece.rect.contains(r)
        ]
        if above:
            body_bottom = max(r.y1 for r in above)
            rect = pymupdf.Rect(piece.rect.x0, piece.rect.y0, piece.rect.x1, body_bottom)
            out.append(_Piece(piece.page, rect, [c for c in piece.cuts if c < body_bottom], piece.side))
        # Start a hair above the rule so its stroke isn't clipped.
        rect = pymupdf.Rect(piece.rect.x0, top - 1, piece.rect.x1, piece.rect.y1)
        notes.append(_Piece(piece.page, rect, [c for c in piece.cuts if c > top], piece.side, note=True))
    return out + notes


def _split(piece: _Piece, y: float) -> tuple[_Piece, _Piece | None]:
    """Cut a piece at `y` into (top, bottom); bottom is None if nothing is left."""
    head = _Piece(
        piece.page, pymupdf.Rect(piece.rect.x0, piece.rect.y0, piece.rect.x1, y),
        [c for c in piece.cuts if c < y], piece.side,
        para_cuts=[c for c in piece.para_cuts if c < y] + [y],
    )
    if piece.rect.y1 - y < 1:
        return head, None
    tail = _Piece(
        piece.page, pymupdf.Rect(piece.rect.x0, y, piece.rect.x1, piece.rect.y1),
        [c for c in piece.cuts if c > y], piece.side,
        para_cuts=[c for c in piece.para_cuts if c > y],
    )
    return head, tail


def _place_notes(pieces: list[_Piece]) -> list[_Piece]:
    """Hold footnotes back until the paragraph they interrupt has finished.

    A page's footnotes arrive after its last column, which often ends
    mid-sentence ("... turned out to matter? And"), with the sentence carrying
    on at the top of the next page. So queue them and emit them at the next
    paragraph end instead.
    """
    out: list[_Piece] = []
    pending: list[_Piece] = []
    for piece in pieces:
        if piece.note:
            last = out[-1] if out else None
            if last is not None and not last.note and last.para_cuts and last.para_cuts[-1] >= last.rect.y1 - 0.5:
                out.append(piece)   # the text before it already ends a paragraph
            else:
                pending.append(piece)
            continue
        if pending:
            ends = [c for c in piece.para_cuts if c > piece.rect.y0 + 0.5]
            if ends:
                head, tail = _split(piece, ends[0])
                out.append(head)
                out.extend(pending)
                pending = []
                if tail is not None:
                    out.append(tail)
                continue
        out.append(piece)
    return out + pending


def relayout_two_column(data: bytes, aspect: float = DEFAULT_ASPECT) -> bytes | None:
    """Return the re-laid-out PDF, or None if `data` doesn't look two-column."""
    src = pymupdf.open(stream=data, filetype="pdf")
    if src.needs_pass:
        return None
    all_pieces: list[_Piece] = []
    two_col_pages = 0
    for pno, page in enumerate(src):
        pieces = _page_pieces(page, pno)
        if pieces is None:
            # Single-column page inside a two-column paper (e.g. a full-page
            # figure): keep it whole.
            pieces = [_Piece(pno, page.rect, [])]
        else:
            two_col_pages += 1
        all_pieces.extend(pieces)
    if two_col_pages < max(1, src.page_count // 2):
        return None
    all_pieces = _place_notes(all_pieces)

    narrow = [p.rect.width for p in all_pieces if p.rect.width < src[0].rect.width * 0.6]
    if not narrow:
        return None
    col_w = max(narrow)
    page_w = col_w + 2 * _SIDE_MARGIN
    page_h = page_w / aspect * (1 - _READER_CHROME)
    usable_h = page_h - 2 * _MARGIN

    out = pymupdf.open()
    cur = None
    y = 0.0

    def new_page():
        nonlocal cur, y
        cur = out.new_page(width=page_w, height=page_h)
        y = _MARGIN

    new_page()
    for piece in all_pieces:
        scale = min(1.0, col_w / piece.rect.width)
        r = pymupdf.Rect(piece.rect)
        while r.height > 0.5:
            room = (page_h - _MARGIN - y) / scale
            if r.height <= room:
                take = r.y1
            else:
                fits = [c for c in piece.cuts if r.y0 < c <= r.y0 + room]
                if fits:
                    take = fits[-1]
                elif y > _MARGIN + 1:
                    new_page()          # try again on an empty page
                    continue
                else:
                    # Unsplittable and taller than a page (big figure): shrink to fit.
                    take = r.y1
                    scale = usable_h / r.height
            clip = pymupdf.Rect(r.x0, r.y0, r.x1, take)
            h = clip.height * scale
            w = clip.width * scale
            left = _SIDE_MARGIN + (col_w - w) / 2 if w < col_w - 1 else _SIDE_MARGIN
            cur.show_pdf_page(pymupdf.Rect(left, y, left + w, y + h), src, piece.page, clip=clip)
            y += h + _BLOCK_GAP
            r = pymupdf.Rect(r.x0, take, r.x1, r.y1)
            if r.height > 0.5:
                new_page()
    out.set_metadata(src.metadata)
    return out.tobytes(garbage=3, deflate=True)


def set_metadata(data: bytes, title: str | None, author: str | None) -> bytes:
    """Fill in the PDF's title/author, which is what e-readers list it by.
    Existing values are only replaced by non-empty new ones; on any error the
    PDF is returned unchanged."""
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
        if doc.needs_pass:
            return data
        meta = dict(doc.metadata or {})
        if title:
            meta["title"] = title
        if author and not (meta.get("author") or "").strip():
            meta["author"] = author
        doc.set_metadata({k: v for k, v in meta.items() if k not in ("format", "encryption")})
        return doc.tobytes(garbage=3, deflate=True)
    except Exception as e:
        log.warning("Could not set PDF metadata: %s", e)
        return data


def guess_title(data: bytes) -> str | None:
    """The paper's title: its embedded metadata title, else the largest text
    near the top of page 1. None if neither gives something plausible."""
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception:
        return None
    meta = (doc.metadata or {}).get("title", "").strip()
    if 10 <= len(meta) <= 200:
        return meta
    if doc.needs_pass or doc.page_count == 0:
        return None
    page = doc[0]
    spans = [
        s
        for block in page.get_text("dict")["blocks"]
        for line in block.get("lines", [])
        # Horizontal text only: skips sideways margin stamps like arXiv's.
        if abs(line["dir"][0] - 1) < 0.01
        for s in line["spans"]
        if s["text"].strip() and s["bbox"][1] < page.rect.height * 0.4
    ]
    if not spans:
        return None
    biggest = max(s["size"] for s in spans)
    title = " ".join(s["text"].strip() for s in spans if s["size"] >= biggest - 0.5)
    # NFKC folds typographic ligatures ("ﬂ" -> "fl") that papers are full of.
    title = " ".join(unicodedata.normalize("NFKC", title).split())
    return title if 10 <= len(title) <= 200 else None
