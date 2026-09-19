"""
PDF parser (PyMuPDF).

A PDF has no real notion of "paragraph" or "heading" — it's a set of positioned
text fragments ("spans") with fonts and sizes. We reconstruct structure with two
simple, robust heuristics:

1. Paragraphs: PyMuPDF already groups nearby lines into "blocks"; one block is
   usually one paragraph.
2. Headings: text noticeably LARGER than the document's normal body text.
   We find the body size as "the font size used by the most characters", then
   treat short lines at >= 1.15x that size as headings. Bigger size = higher level.
   Many real-world PDFs also use bold, body-sized lines on their own as
   sub-headings ("7.2 Notice Period"); those become the lowest heading level.

This works well for typical business documents (contracts, policies, manuals).
Scanned PDFs have no text layer at all — we detect that and fail clearly
(OCR is a paid add-on later, per the plan).
"""

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from app.services.ingestion.parsers.base import Block, ParsedDocument, ParseError

HEADING_SIZE_RATIO = 1.15  # heading font must be >= 115% of body font size
MAX_HEADING_CHARS = 120  # longer "big text" is probably a pull-quote, not a heading
MAX_BOLD_HEADING_CHARS = 80
BOLD_FLAG = 16  # PyMuPDF span flag bit for bold text


@dataclass
class _Line:
    text: str
    size: float  # dominant font size of the line (rounded to 0.5pt)
    page: int
    bold: bool  # every span on the line is bold
    alone: bool  # the line is the only line in its PyMuPDF block


def parse_pdf(path: Path) -> ParsedDocument:
    try:
        doc = pymupdf.open(path)
    except Exception as exc:  # PyMuPDF raises several different types for bad files
        raise ParseError(f"Could not open PDF: {exc}") from exc

    with doc:
        if doc.needs_pass:
            raise ParseError("PDF is password-protected")

        # Pass 1: collect lines, grouped per PyMuPDF block, with their font sizes.
        page_blocks: list[list[_Line]] = []
        size_by_chars: Counter[float] = Counter()
        for page_number, page in enumerate(doc, start=1):
            # sort=True orders blocks top-to-bottom, left-to-right (reading order).
            data = page.get_text("dict", sort=True)
            for block in data["blocks"]:
                if block.get("type") != 0:  # 0 = text; 1 = image
                    continue
                lines: list[_Line] = []
                for line in block["lines"]:
                    spans = [s for s in line["spans"] if s["text"].strip()]
                    if not spans:
                        continue
                    text = "".join(s["text"] for s in line["spans"]).strip()
                    # A line's size = the size covering most of its characters.
                    sizes: Counter[float] = Counter()
                    for s in spans:
                        sizes[round(s["size"] * 2) / 2] += len(s["text"].strip())
                    size = sizes.most_common(1)[0][0]
                    size_by_chars.update(sizes)
                    bold = all(s["flags"] & BOLD_FLAG for s in spans)
                    lines.append(
                        _Line(text=text, size=size, page=page_number, bold=bold, alone=False)
                    )
                if len(lines) == 1:
                    lines[0].alone = True
                if lines:
                    page_blocks.append(lines)

        if not size_by_chars:
            raise ParseError(
                "No extractable text found — this looks like a scanned PDF. "
                "OCR is not supported yet."
            )

        body_size = size_by_chars.most_common(1)[0][0]
        heading_sizes = sorted(
            {ln.size for blk in page_blocks for ln in blk if _is_large_heading(ln, body_size)},
            reverse=True,
        )
        # Biggest heading size -> level 1, next -> level 2, ... ; bold body-size
        # headings sit one level below the smallest large heading.
        level_for_size = {size: i + 1 for i, size in enumerate(heading_sizes)}
        bold_level = len(heading_sizes) + 1

        def heading_level(ln: _Line) -> int | None:
            if _is_large_heading(ln, body_size):
                return level_for_size[ln.size]
            if _is_bold_heading(ln, body_size):
                return bold_level
            return None

        # Pass 2: turn lines into heading / paragraph blocks.
        blocks: list[Block] = []
        for lines in page_blocks:
            paragraph: list[str] = []
            for ln in lines:
                level = heading_level(ln)
                if level is not None:
                    if paragraph:
                        _add_paragraph(blocks, _join_lines(paragraph), ln.page)
                        paragraph = []
                    blocks.append(Block(ln.text, kind="heading", page=ln.page, level=level))
                else:
                    paragraph.append(ln.text)
            if paragraph:
                _add_paragraph(blocks, _join_lines(paragraph), lines[-1].page)

        parsed = ParsedDocument(blocks=blocks, page_count=doc.page_count)
        parsed.title = _metadata_title(doc) or parsed.first_heading()
        return parsed


def _add_paragraph(blocks: list[Block], text: str, page: int) -> None:
    """
    Append a paragraph — or glue it onto the previous one when PyMuPDF split a
    single paragraph into two blocks (the previous text doesn't end a sentence
    and this text starts in lowercase: "...and check the" + "exhaust fan, due...").
    """
    prev = blocks[-1] if blocks else None
    if (
        prev is not None
        and prev.kind == "paragraph"
        and prev.page == page
        and text[:1].islower()
        and not prev.text.rstrip().endswith((".", "!", "?", ":"))
    ):
        prev.text = _join_lines([prev.text, text])
    else:
        blocks.append(Block(text, page=page))


def _metadata_title(doc: pymupdf.Document) -> str | None:
    """
    The PDF's own Title property, if it looks like a real title. Many tools write
    junk here ("Microsoft Word - draft3.docx", "untitled"), so we filter that out
    and fall back to the first top-level heading.
    """
    title = ((doc.metadata or {}).get("title") or "").strip()
    if not 3 <= len(title) <= 200:
        return None
    lowered = title.lower()
    if lowered.startswith(("microsoft word", "untitled")) or lowered.endswith(
        (".pdf", ".doc", ".docx", ".txt")
    ):
        return None
    return title


def _is_large_heading(line: _Line, body_size: float) -> bool:
    return line.size >= body_size * HEADING_SIZE_RATIO and len(line.text) <= MAX_HEADING_CHARS


def _is_bold_heading(line: _Line, body_size: float) -> bool:
    return (
        line.bold
        and line.alone
        and line.size >= body_size  # small bold print (headers, footnotes) isn't a heading
        and len(line.text) <= MAX_BOLD_HEADING_CHARS
        and not line.text.endswith((".", ",", ";", ":"))
    )


def _join_lines(lines: list[str]) -> str:
    """
    Join the visual lines of a paragraph back into running text.

    PDFs break lines at the page width, sometimes hyphenating a word across two
    lines ("manage-" / "ment"). If a line ends with "-" and the next starts with
    a lowercase letter, we glue the word back together. Trade-off: a genuinely
    hyphenated word split at exactly that spot ("well-" / "known") loses its
    hyphen — rare, and harmless for search.
    """
    out = ""
    for line in lines:
        if not out:
            out = line
        elif out.endswith("-") and line[:1].islower():
            out = out[:-1] + line
        else:
            out = f"{out} {line}"
    return out
