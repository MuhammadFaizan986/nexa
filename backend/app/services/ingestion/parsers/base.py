"""
The common output format of every parser.

PDF, DOCX, Markdown and plain text are very different formats, but the rest of
the pipeline (cleaner -> chunker) should not care. So every parser converts its
file into the same simple structure: an ordered list of `Block`s.

    Block(kind="heading",   text="7. Termination",               page=3, level=2)
    Block(kind="paragraph", text="Either party may end this...", page=3)

Two pieces of information are preserved because citations depend on them:
  - `page`   (PDFs only; None for formats without pages)
  - headings (so every chunk knows which section it came from)
"""

from dataclasses import dataclass, field
from typing import Literal


class ParseError(Exception):
    """The file can't be turned into text (corrupt, encrypted, scanned image...)."""


@dataclass
class Block:
    text: str
    kind: Literal["heading", "paragraph"] = "paragraph"
    page: int | None = None  # 1-based page number
    level: int = 0  # heading depth: 1 = top-level; 0 for paragraphs


@dataclass
class ParsedDocument:
    blocks: list[Block] = field(default_factory=list)
    page_count: int | None = None
    title: str | None = None  # best guess at a human title (first top-level heading)

    def first_heading(self) -> str | None:
        top = [b for b in self.blocks if b.kind == "heading"]
        if not top:
            return None
        best_level = min(b.level for b in top)
        return next(b.text for b in top if b.level == best_level)
