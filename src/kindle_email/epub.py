"""Generate a Kindle-compatible EPUB from cleaned content."""

from __future__ import annotations

import io
import re
import uuid
from dataclasses import dataclass

from bs4 import BeautifulSoup
from ebooklib import epub

from .cleaner import CleanedContent

# Minimal CSS that renders well on Kindle
_KINDLE_CSS = """
body {
    font-family: serif;
    font-size: 1em;
    line-height: 1.6;
    margin: 1em;
}
h1, h2, h3, h4, h5, h6 {
    font-family: sans-serif;
    line-height: 1.3;
    margin-top: 1.2em;
    margin-bottom: 0.4em;
}
p {
    margin: 0.6em 0;
    text-align: left;
}
img {
    max-width: 100%;
    height: auto;
    display: block;
    margin: 0.5em auto;
}
a {
    color: inherit;
    text-decoration: underline;
}
pre, code {
    font-family: monospace;
    font-size: 0.9em;
}
blockquote {
    margin: 0.5em 1.5em;
    border-left: 3px solid #999;
    padding-left: 0.8em;
    font-style: italic;
}
"""


def safe_filename(s: str) -> str:
    """Sanitize a string for use as a filename."""
    # Allow word chars, spaces, and hyphens; strip everything else including dots
    s = re.sub(r"[^\w\s\-]", "", s)
    s = s.strip()[:100]
    return s or "newsletter"


def generate(content: CleanedContent) -> tuple[str, bytes]:
    """
    Build an EPUB from CleanedContent.
    Returns (filename, epub_bytes).
    """
    book = epub.EpubBook()
    book.set_identifier(str(uuid.uuid4()))
    book.set_title(content.title)
    book.set_language("en")
    if content.author:
        book.add_author(content.author)
    if content.date:
        book.add_metadata("DC", "date", content.date)

    # Add CSS
    css = epub.EpubItem(
        uid="style",
        file_name="style.css",
        media_type="text/css",
        content=_KINDLE_CSS.encode(),
    )
    book.add_item(css)

    # Add images
    for filename, img_bytes in content.images.items():
        mime = content.image_types.get(filename, "image/jpeg")
        img_item = epub.EpubItem(
            uid=f"img_{filename}",
            file_name=f"images/{filename}",
            media_type=mime,
            content=img_bytes,
        )
        book.add_item(img_item)

    # Rewrite image src paths to match their location in the EPUB
    html = _rewrite_image_paths(content.html, set(content.images.keys()))
    html, headings = _anchor_headings(html)

    # Wrap in a proper XHTML document
    chapter_html = f"""<?xml version='1.0' encoding='utf-8'?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en">
<head>
    <title>{_escape_xml(content.title)}</title>
    <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
<h1>{_escape_xml(content.title)}</h1>
{html}
</body>
</html>"""

    document = epub.EpubHtml(
        title=content.title,
        file_name="document.xhtml",
        lang="en",
        content=chapter_html.encode(),
    )
    document.add_item(css)
    book.add_item(document)

    # Open directly to the document; the nav is included for navigation but
    # is not the landing page. Headings get a flat entry each after the title.
    book.toc = [epub.Link("document.xhtml", content.title, "document")] + [
        epub.Link(f"document.xhtml#{anchor}", text, anchor) for anchor, text in headings
    ]
    book.spine = [document]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    buf = io.BytesIO()
    epub.write_epub(buf, book)
    epub_bytes = buf.getvalue()

    filename = safe_filename(content.title) + ".epub"
    return filename, epub_bytes


def _anchor_headings(html: str) -> tuple[str, list[tuple[str, str]]]:
    """Give each heading an id and return (html, [(id, text)]) for the TOC.

    A multi-article book (an <h1> per article) lists the articles only;
    otherwise every h2-h4 is listed, flat, since newsletters use heading
    levels for styling rather than nesting (Benedict's column is an h3, the
    news items after it h4s). Fewer than two headings: no entries."""
    soup = BeautifulSoup(html, "html.parser")
    articles = [h for h in soup.find_all("h1") if h.get_text(strip=True)]
    found = articles if len(articles) >= 2 else [
        h for h in soup.find_all(["h2", "h3", "h4"]) if h.get_text(strip=True)
    ]
    if len(found) < 2:
        return html, []
    taken = {tag["id"] for tag in soup.find_all(id=True)}
    headings = []
    for n, h in enumerate(found, 1):
        if not h.get("id"):
            anchor = f"toc-{n}"
            while anchor in taken:
                anchor += "x"
            taken.add(anchor)
            h["id"] = anchor
        headings.append((h["id"], " ".join(h.get_text(" ", strip=True).split())))
    return str(soup), headings


def _rewrite_image_paths(html: str, image_filenames: set[str]) -> str:
    """Rewrite bare image filenames to their EPUB path (images/<filename>)."""
    for filename in image_filenames:
        # Replace src="filename" with src="images/filename"
        html = html.replace(f'src="{filename}"', f'src="images/{filename}"')
    return html


def _escape_xml(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
