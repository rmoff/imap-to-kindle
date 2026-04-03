"""Generate a Kindle-compatible EPUB from cleaned content."""

from __future__ import annotations

import io
import re
import uuid
from dataclasses import dataclass

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

    chapter = epub.EpubHtml(
        title=content.title,
        file_name="chapter.xhtml",
        lang="en",
        content=chapter_html.encode(),
    )
    chapter.add_item(css)
    book.add_item(chapter)

    book.toc = [epub.Link("chapter.xhtml", content.title, "chapter")]
    book.spine = ["nav", chapter]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    buf = io.BytesIO()
    epub.write_epub(buf, book)
    epub_bytes = buf.getvalue()

    filename = safe_filename(content.title) + ".epub"
    return filename, epub_bytes


def _rewrite_image_paths(html: str, image_filenames: set[str]) -> str:
    """Rewrite bare image filenames to their EPUB path (images/<filename>)."""
    for filename in image_filenames:
        # Replace src="filename" with src="images/filename"
        html = html.replace(f'src="{filename}"', f'src="images/{filename}"')
    return html


def _escape_xml(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
