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

import logging
import unicodedata
from dataclasses import dataclass

import pymupdf

log = logging.getLogger(__name__)

# Screen width / height. Kindles are all close to 3:4 (Paperwhite 1236x1648,
# Basic 1072x1448), so one ratio suits them all.
DEFAULT_ASPECT = 3 / 4
_MARGIN = 6          # pt of white space around each output page's content
_BLOCK_GAP = 6       # pt between stacked pieces
_MID_SLACK = 8       # a box counts as "crossing the gutter" if it spans mid±this
_BAND_GAP = 8        # pt of full-width white space that separates layout bands
_SHORT_BAND = 0.2    # side-by-side blocks shorter than this (x page height) stay whole


@dataclass
class _Piece:
    page: int
    rect: pymupdf.Rect
    cuts: list[float]   # y values (page coords) where a page break is safe


def _content_boxes(page: pymupdf.Page) -> tuple[list[pymupdf.Rect], list[pymupdf.Rect]]:
    """(text line boxes, non-text boxes: images + vector drawings)."""
    lines = []
    h = page.rect.height
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            r = pymupdf.Rect(line["bbox"])
            text = "".join(s["text"] for s in line["spans"]).strip()
            if r.is_empty or not text:
                continue
            # Page numbers in the header/footer would land mid-flow; drop them.
            if text.isdigit() and len(text) <= 5 and (r.y0 > 0.9 * h or r.y1 < 0.1 * h):
                continue
            lines.append(r)
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


def _merge_ranges(ranges: list[tuple[float, float]], gap: float = 2) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _cuts(boxes: list[pymupdf.Rect], y0: float, y1: float) -> list[float]:
    """Safe break points: midpoints of vertical gaps between boxes in [y0, y1]."""
    spans = _merge_ranges([(b.y0, b.y1) for b in boxes if b.y1 > y0 and b.y0 < y1], gap=0)
    return [(a[1] + b[0]) / 2 for a, b in zip(spans, spans[1:])]


def _page_pieces(page: pymupdf.Page, pno: int) -> list[_Piece] | None:
    """Split one page into reading-order pieces, or None if it isn't two-column."""
    lines, other = _content_boxes(page)
    if not lines:
        return []
    boxes = lines + other
    mid = page.rect.width / 2

    def crosses(r: pymupdf.Rect) -> bool:
        return r.x0 < mid - _MID_SLACK and r.x1 > mid + _MID_SLACK

    # Two-column pages have almost no text lines crossing the middle and a
    # healthy share on each side; single-column text mostly spans it.
    left = sum(r.x1 <= mid + _MID_SLACK for r in lines)
    right = sum(r.x0 >= mid - _MID_SLACK for r in lines)
    crossing = len(lines) - left - right
    if len(lines) < 10 or crossing > 0.15 * len(lines) or min(left, right) < 0.25 * len(lines):
        return None

    x0 = min(r.x0 for r in boxes)
    x1 = max(r.x1 for r in boxes)
    # Crop columns to their own text edges (not the page middle) so both
    # columns sit at the same left margin once restacked.
    left_x1 = max((r.x1 for r in boxes if r.x1 <= mid + _MID_SLACK), default=mid)
    right_x0 = min((r.x0 for r in boxes if r.x0 >= mid - _MID_SLACK), default=mid)
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
            cx0, cx1 = (x0, left_x1) if side == "L" else (right_x0, x1)
            pieces.append(_Piece(pno, pymupdf.Rect(cx0, cy0, cx1, cy1), _cuts(col, cy0, cy1)))
    return pieces


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

    narrow = [p.rect.width for p in all_pieces if p.rect.width < src[0].rect.width * 0.6]
    if not narrow:
        return None
    col_w = max(narrow)
    page_w = col_w + 2 * _MARGIN
    page_h = page_w / aspect
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
            left = _MARGIN + (col_w - w) / 2 if w < col_w - 1 else _MARGIN
            cur.show_pdf_page(pymupdf.Rect(left, y, left + w, y + h), src, piece.page, clip=clip)
            y += h + _BLOCK_GAP
            r = pymupdf.Rect(r.x0, take, r.x1, r.y1)
            if r.height > 0.5:
                new_page()
    out.set_metadata(src.metadata)
    return out.tobytes(garbage=3, deflate=True)


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
