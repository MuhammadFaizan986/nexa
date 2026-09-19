"""
Markdown (.md) and plain text (.txt) parsers.

Markdown has explicit headings ("# Title", "## Section"), so we read those. We
must skip fenced code blocks, because a line like "# install deps" inside
```bash ... ``` is a shell comment, not a heading.

Plain text has no markup. Paragraphs are separated by blank lines, and we use a
cautious heading heuristic: a short, single-line paragraph that doesn't end
like a sentence and looks like a title ("KEYS AND ACCESS", "3. Utilities",
"Before You Arrive").
"""

import re
from pathlib import Path

from app.services.ingestion.parsers.base import Block, ParsedDocument, ParseError

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_NUMBERED_TITLE = re.compile(r"^(\d+(\.\d+)*[.)]?|[A-Z][.)])\s+\S")


def read_text_file(path: Path) -> str:
    raw = path.read_bytes()
    if b"\x00" in raw[:4096]:
        raise ParseError("File looks binary, not text")
    # utf-8-sig also strips the invisible BOM that Windows editors add.
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ParseError("Could not decode text file (expected UTF-8)")


def parse_markdown(path: Path) -> ParsedDocument:
    blocks: list[Block] = []
    paragraph: list[str] = []
    in_fence = False

    def flush() -> None:
        if paragraph:
            blocks.append(Block("\n".join(paragraph).strip()))
            paragraph.clear()

    for line in read_text_file(path).splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            paragraph.append(line)
            continue
        if in_fence:
            paragraph.append(line)
            continue
        heading = _MD_HEADING.match(line)
        if heading:
            flush()
            blocks.append(Block(heading.group(2), kind="heading", level=len(heading.group(1))))
        elif not line.strip():
            flush()
        else:
            # Keep line breaks: in Markdown they matter for lists and tables.
            paragraph.append(line.rstrip())
    flush()

    blocks = [b for b in blocks if b.text]
    if not blocks:
        raise ParseError("Document contains no text")
    parsed = ParsedDocument(blocks=blocks)
    parsed.title = parsed.first_heading()
    return parsed


def parse_text(path: Path) -> ParsedDocument:
    content = read_text_file(path)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", content) if p.strip()]
    if not paragraphs:
        raise ParseError("Document contains no text")

    blocks = []
    for i, para in enumerate(paragraphs):
        has_following_text = i + 1 < len(paragraphs)
        if has_following_text and _looks_like_title(para):
            blocks.append(Block(para, kind="heading", level=1))
        else:
            blocks.append(Block(para))
    parsed = ParsedDocument(blocks=blocks)
    parsed.title = parsed.first_heading()
    return parsed


def _looks_like_title(para: str) -> bool:
    if "\n" in para or len(para) > 80 or para[-1] in ".,;:!?":
        return False
    letters = [c for c in para if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        return True  # "KEYS AND ACCESS"
    if _NUMBERED_TITLE.match(para):
        return True  # "3. Utilities"
    words = para.split()
    capitalised = sum(1 for w in words if w[:1].isupper())
    return len(words) <= 8 and capitalised / len(words) >= 0.6  # "Before You Arrive"
