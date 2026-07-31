"""Turn a 10-K into searchable chunks.

Filings are HTML built for a browser, not a reader: nested tables, inline
styling, and page furniture. This strips to plain text while keeping cell
boundaries as separators, because a debt maturity schedule read without them
collapses into an unreadable run of numbers.

Chunks overlap. A disclosure that straddles a boundary would otherwise be
retrievable only in halves, and half a sentence cannot be quoted verbatim.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

CHUNK_CHARS = 1800
OVERLAP_CHARS = 300

# Blocks whose contents are markup, not prose.
SKIP_TAGS = {"script", "style", "head"}
# Tags that imply a visual break, so the text either side must not run together.
BREAK_TAGS = {"p", "div", "br", "tr", "table", "li", "h1", "h2", "h3", "h4"}
CELL_TAGS = {"td", "th"}


class _Extract(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag in BREAK_TAGS:
            self.parts.append("\n")
        elif tag in CELL_TAGS:
            self.parts.append("  ")

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag in BREAK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def to_text(html):
    p = _Extract()
    p.feed(html)
    text = "".join(p.parts)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def normalise(s):
    """Whitespace-insensitive form, used for verbatim quote checking.

    A model that reproduces a quote correctly but re-wraps it should still pass;
    one that paraphrases should not. Collapsing whitespace draws that line.
    """
    return re.sub(r"\s+", " ", s or "").strip().lower()


def chunk(text, size=CHUNK_CHARS, overlap=OVERLAP_CHARS):
    """Overlapping windows, preferring to break at a paragraph boundary."""
    chunks = []
    i = 0
    n = len(text)
    while i < n:
        end = min(i + size, n)
        if end < n:
            brk = text.rfind("\n\n", i + size // 2, end)
            if brk != -1:
                end = brk
        piece = text[i:end].strip()
        if len(piece) > 120:  # skip navigational scraps
            chunks.append(piece)
        if end >= n:
            break
        i = max(end - overlap, i + 1)
    return chunks
