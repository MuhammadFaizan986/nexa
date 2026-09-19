"""
Structure-aware recursive chunking.

Why chunk at all?
    Embedding a whole 50-page document into ONE vector blurs every topic together,
    and an LLM prompt can't hold thousands of documents. So we split documents
    into passages ("chunks") small enough to be about one thing, but big enough
    to be understandable on their own. The plan's target: ~500–800 tokens.

How ("structure-aware recursive")
    1. Respect structure first: a new section (heading) starts a new chunk, so a
       chunk rarely mixes "Rent" and "Termination".
    2. Within a section, pack whole sentences until the size limit — never cut a
       sentence in half unless one sentence alone is enormous.
    3. When a section is too long for one chunk, the next chunk starts with the
       last ~80 tokens of the previous one (the "overlap"), so a fact that spans
       the boundary is fully contained in at least one chunk.
    4. Tiny sections (a heading + one line) are merged into the following section
       instead of becoming useless 20-token chunks — but only into a sibling or
       sub-section. We never merge across a higher-level heading: in a report
       with one unit per page, "Unit 4B" notes must not bleed into "Unit 5A".

What each chunk remembers (citations depend on it)
    page_start / page_end  — which PDF pages its text came from
    section_title          — the section holding most of its text ("7. Termination")
    heading_path           — the full heading trail (["Lease", "7. Termination"])
    spans                  — WHERE inside the chunk each page/section begins:
                             [[0, 1, "Where to Find Help"], [212, 2, "Annual Leave"]]
                             A chunk can straddle a page break or hold several small
                             sections, so a citation looks up the exact page and
                             section of the sentence it quotes (citations.py).

Tokens are counted with tiktoken's cl100k_base, the tokenizer used by OpenAI's
text-embedding-3 models, so our sizes match what the embedding model sees.
"""

import re
from dataclasses import dataclass, field
from functools import lru_cache

import tiktoken

from app.services.ingestion.parsers.base import Block

# Split after . ! ? when followed by whitespace and something that starts a new
# sentence (capital letter, digit, quote, bracket). Simple and good enough for
# business prose; it will occasionally split after abbreviations like "e.g.".
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")


@lru_cache
def _encoding() -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_encoding().encode(text, disallowed_special=()))


@dataclass
class TextChunk:
    index: int
    content: str
    token_count: int
    page_start: int | None
    page_end: int | None
    section_title: str | None
    heading_path: list[str] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)  # every section touched, in order
    # [char_offset, page, section] at each point where the page or section changes.
    spans: list[list] = field(default_factory=list)


@dataclass
class _Unit:
    """The smallest piece we pack: a heading, a sentence, or a slice of a huge sentence."""

    text: str
    tokens: int
    page: int | None
    heading_path: tuple[str, ...]
    is_heading: bool
    sep: str  # what goes BEFORE this unit when joined: "\n\n" new paragraph, "\n" new line, " "


def chunk_blocks(
    blocks: list[Block],
    *,
    chunk_size: int = 600,
    overlap: int = 80,
    min_chunk: int = 150,
) -> list[TextChunk]:
    if not 0 <= overlap < chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")
    # Any single unit must fit next to a full overlap, or packing could never
    # make progress. Longer sentences get sliced to this size.
    max_unit = chunk_size - overlap
    units = _to_units(blocks, max_unit=max_unit)

    chunks: list[TextChunk] = []
    current: list[_Unit] = []
    new_from = 0  # index in `current` where this chunk's NEW (non-overlap) text starts

    def emit() -> None:
        chunks.append(_make_chunk(len(chunks), current, new_from))

    for unit in units:
        if current:
            size = sum(u.tokens for u in current)
            if unit.is_heading and (size >= min_chunk or _climbs_out(current, new_from, unit)):
                # Rule 1: new section -> new chunk (no overlap across sections).
                emit()
                current, new_from = [], 0
            elif size + unit.tokens > chunk_size:
                # Rule 3: section too long -> split here, carry an overlap forward.
                # Never end a chunk on a heading: move trailing headings forward.
                trailing_headings: list[_Unit] = []
                while current and current[-1].is_heading:
                    trailing_headings.insert(0, current.pop())
                if current:
                    emit()
                    if trailing_headings:
                        # The next unit belongs to a new section: overlap with the
                        # previous section's text would only add noise.
                        current, new_from = trailing_headings, 0
                    else:
                        tail = _overlap_tail(current, overlap)
                        current, new_from = tail, len(tail)
                else:
                    current, new_from = trailing_headings, 0
        current.append(unit)

    if current:
        emit()
    return chunks


def _climbs_out(current: list[_Unit], new_from: int, heading: _Unit) -> bool:
    """
    True if `heading` sits HIGHER in the outline than where the current chunk
    started (e.g. chunk began under "Unit 4B > Inspector Notes", depth 2, and the
    new heading is "Unit 5A", depth 1). Depth = length of the heading path.
    """
    anchor = current[min(new_from, len(current) - 1)]
    return len(heading.heading_path) < len(anchor.heading_path)


def _to_units(blocks: list[Block], *, max_unit: int) -> list[_Unit]:
    units: list[_Unit] = []
    heading_stack: list[tuple[int, str]] = []  # (level, text)

    for block in blocks:
        if block.kind == "heading":
            # A level-2 heading closes any open level-2+ headings, keeps level 1.
            while heading_stack and heading_stack[-1][0] >= block.level:
                heading_stack.pop()
            heading_stack.append((block.level, block.text))
            path = tuple(text for _, text in heading_stack)
            units.append(
                _Unit(block.text, count_tokens(block.text), block.page, path, True, "\n\n")
            )
            continue

        path = tuple(text for _, text in heading_stack)
        first_in_block = True
        for line in block.text.split("\n"):
            for sentence_no, sentence in enumerate(_split_sentences(line)):
                for piece_no, piece in enumerate(_split_oversized(sentence, max_unit)):
                    if first_in_block:
                        sep = "\n\n"
                    elif sentence_no == 0 and piece_no == 0:
                        sep = "\n"  # keeps list items / table rows on their own lines
                    else:
                        sep = " "
                    first_in_block = False
                    units.append(_Unit(piece, count_tokens(piece), block.page, path, False, sep))
    return units


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text) if s.strip()]


def _split_oversized(sentence: str, max_tokens: int) -> list[str]:
    """Last resort for a 'sentence' longer than a chunk (e.g. a table dumped as one line)."""
    tokens = _encoding().encode(sentence, disallowed_special=())
    if len(tokens) <= max_tokens:
        return [sentence]
    return [
        _encoding().decode(tokens[i : i + max_tokens]).strip()
        for i in range(0, len(tokens), max_tokens)
    ]


def _overlap_tail(units: list[_Unit], overlap: int) -> list[_Unit]:
    """The last few units of a chunk whose total size fits in the overlap budget."""
    tail: list[_Unit] = []
    total = 0
    for unit in reversed(units):
        if total + unit.tokens > overlap:
            break
        tail.insert(0, unit)
        total += unit.tokens
    return tail


def _make_chunk(index: int, units: list[_Unit], new_from: int) -> TextChunk:
    # Join the units, recording where each page/section starts in the joined text.
    parts: list[str] = []
    spans: list[list] = []
    offset = 0
    for i, u in enumerate(units):
        sep = u.sep if i else ""
        section = u.heading_path[-1] if u.heading_path else None
        if not spans or spans[-1][1:] != [u.page, section]:
            spans.append([offset + len(sep), u.page, section])
        parts.append(sep + u.text)
        offset += len(sep) + len(u.text)
    content = "".join(parts)

    sections: list[str] = []
    for u in units:
        if u.heading_path and u.heading_path[-1] not in sections:
            sections.append(u.heading_path[-1])

    # Label the chunk with the section holding most of its NEW text (the overlap
    # at the start belongs to the previous chunk). "First section" would be wrong
    # when a tiny trailing section ("Where to Find Help") was merged with a big one.
    tokens_by_path: dict[tuple[str, ...], int] = {}
    for u in units[min(new_from, len(units) - 1) :]:
        tokens_by_path[u.heading_path] = tokens_by_path.get(u.heading_path, 0) + u.tokens
    main_path = max(tokens_by_path, key=tokens_by_path.__getitem__)  # first wins ties

    pages = [u.page for u in units if u.page is not None]
    return TextChunk(
        index=index,
        content=content,
        token_count=count_tokens(content),
        page_start=min(pages) if pages else None,
        page_end=max(pages) if pages else None,
        section_title=main_path[-1] if main_path else None,
        heading_path=list(main_path),
        sections=sections,
        spans=spans,
    )
