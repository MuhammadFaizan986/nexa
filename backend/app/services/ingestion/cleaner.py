"""
Text cleaning: make parsed text consistent before chunking.

Garbage in = garbage out. Two kinds of noise hurt retrieval the most:

1. Invisible character noise — ligatures ("ﬁ" instead of "fi"), non-breaking
   spaces, zero-width characters, stray control codes. They make identical words
   look different to keyword search and to the embedding model.

2. Repeated page furniture — running headers ("Harbourview Property Group —
   Confidential") and footers ("Page 3 of 12") printed on every page of a PDF.
   Left in, they get embedded into every chunk, making all chunks look alike and
   wasting tokens. We detect text that repeats at the top/bottom of many pages
   and drop it.
"""

import math
import re
import unicodedata
from collections import defaultdict
from dataclasses import replace

from app.services.ingestion.parsers.base import Block, ParsedDocument

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f​-‍⁠﻿]")
_HORIZONTAL_WS = re.compile(r"[ \t\f\v]+")
_MANY_NEWLINES = re.compile(r"\n{3,}")

# "Page 3 of 12", "page 3/12", "Page 3"  ->  "page #"
_PAGE_OF = re.compile(r"\bpage\s*\d+\s*(?:of|/)\s*\d+\b")
_PAGE_N = re.compile(r"\bpage\s*\d+\b")
# A line that is only a page number: "3", "- 3 -", "3 / 12", "3 | 12"
_BARE_PAGE_NUMBER = re.compile(r"^[\s\-–—|/.]*\d+(?:\s*[/|]\s*\d+)?[\s\-–—|/.]*$")

EDGE_BLOCKS = 2  # look at the first/last 2 blocks of each page
REPEAT_MIN_RATIO = 0.5  # ...and drop text found there on >= 50% of pages
MAX_FURNITURE_CHARS = 150


def normalize_text(text: str) -> str:
    # NFKC folds "compatibility" characters into their plain form:
    # ligature "ﬁ" -> "fi", non-breaking space -> space, full-width "Ａ" -> "A".
    text = unicodedata.normalize("NFKC", text)
    text = _CONTROL_CHARS.sub("", text)
    text = _HORIZONTAL_WS.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _MANY_NEWLINES.sub("\n\n", text)
    return text.strip()


def clean_document(parsed: ParsedDocument) -> ParsedDocument:
    blocks = [replace(b, text=normalize_text(b.text)) for b in parsed.blocks]
    blocks = [b for b in blocks if b.text]
    blocks = remove_repeated_page_furniture(blocks)
    title = normalize_text(parsed.title) if parsed.title else None
    return ParsedDocument(blocks=blocks, page_count=parsed.page_count, title=title or None)


def _fingerprint(text: str) -> str:
    """
    Make running headers/footers on different pages compare equal.

    Only page numbers are normalised. We deliberately do NOT replace every digit:
    in a 50-page inspection report every page starts with "Inspection date:
    2026-03-14"-style lines that differ only in digits — those are real content.
    """
    t = " ".join(text.lower().split())
    if _BARE_PAGE_NUMBER.match(t):
        return "<page-number>"
    t = _PAGE_OF.sub("page #", t)
    return _PAGE_N.sub("page #", t)


def remove_repeated_page_furniture(blocks: list[Block]) -> list[Block]:
    page_numbers = sorted({b.page for b in blocks if b.page is not None})
    if len(page_numbers) < 3:
        return blocks  # too few pages to tell furniture from content

    # Block indices at the top/bottom edge of each page.
    by_page: dict[int, list[int]] = defaultdict(list)
    for i, block in enumerate(blocks):
        if block.page is not None:
            by_page[block.page].append(i)
    edge_indices: set[int] = set()
    for indices in by_page.values():
        edge_indices.update(indices[:EDGE_BLOCKS])
        edge_indices.update(indices[-EDGE_BLOCKS:])

    # On how many different pages does each short edge text appear?
    pages_seen: dict[str, set[int]] = defaultdict(set)
    for i in edge_indices:
        if len(blocks[i].text) <= MAX_FURNITURE_CHARS:
            pages_seen[_fingerprint(blocks[i].text)].add(blocks[i].page)  # type: ignore[arg-type]

    threshold = max(3, math.ceil(len(page_numbers) * REPEAT_MIN_RATIO))
    furniture = {fp for fp, pages in pages_seen.items() if len(pages) >= threshold}
    if not furniture:
        return blocks

    return [
        b
        for i, b in enumerate(blocks)
        if not (i in edge_indices and _fingerprint(b.text) in furniture)
    ]
