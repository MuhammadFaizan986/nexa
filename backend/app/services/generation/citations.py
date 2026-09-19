"""
Citation parsing and validation.

The model writes markers like [1] or [2][3] (sometimes [2, 3]). After the answer
is complete we:

1. Extract every cited number, in order of first appearance.
2. Validate: a number must refer to a passage we actually sent. A model can
   occasionally cite [7] when only 5 passages existed — such citations are
   dropped from the citation list and recorded as `invalid` so we can measure
   how often it happens (citation accuracy is an eval metric in Week 6).
3. Map valid numbers to chunk/document/page and pick a *snippet*: the sentence
   of the passage that best matches the claim the citation supports. The UI
   shows it as "highlighted source text" (Week 5).

What text does a citation support? Models often put one marker at the END of a
bullet or paragraph that makes several statements ("- 60 days written notice...
Notice may be given by email... (Clause 7.2) [1]"). So a citation covers all
the text since the previous citation marker in the same line/bullet, and for
the snippet we use the FIRST claim that cites the number (its main use).
"""

import re
import uuid
from dataclasses import dataclass

from app.services.retrieval.semantic import RetrievedChunk

_MARKER = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
# Bare numbering left over from splitting "7. Termination" at the "7." — re-attached.
_NUMBERING = re.compile(r"^(\d+(\.\d+)*\.?|[A-Za-z]\.)$")
# A piece that is ONLY citation markers, e.g. the "[1]" in "...60 days. [1]".
_ONLY_MARKERS = re.compile(r"^(\s*\[\d+(?:\s*,\s*\d+)*\])+\s*$")
_WORD = re.compile(r"[a-z0-9]+")
SNIPPET_MAX_CHARS = 300


@dataclass
class Citation:
    number: int
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    filename: str
    page_start: int | None
    section_title: str | None
    snippet: str


@dataclass
class CitationResult:
    citations: list[Citation]
    invalid_numbers: list[int]


def extract_citation_numbers(answer: str) -> list[int]:
    numbers: list[int] = []
    for match in _MARKER.finditer(answer):
        for part in match.group(1).split(","):
            n = int(part.strip())
            if n not in numbers:
                numbers.append(n)
    return numbers


def resolve_citations(answer: str, passages: list[RetrievedChunk]) -> CitationResult:
    citations: list[Citation] = []
    invalid: list[int] = []
    for number in extract_citation_numbers(answer):
        if not 1 <= number <= len(passages):
            invalid.append(number)
            continue
        chunk = passages[number - 1]
        snippet = best_snippet(answer, number, chunk.content)
        page, section = locate_in_chunk(chunk, snippet)
        citations.append(
            Citation(
                number=number,
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                document_title=chunk.document_title,
                filename=chunk.filename,
                page_start=page,
                section_title=section,
                snippet=snippet,
            )
        )
    return CitationResult(citations=citations, invalid_numbers=invalid)


def locate_in_chunk(chunk: RetrievedChunk, snippet: str) -> tuple[int | None, str | None]:
    """
    Page and section of the snippet itself, not just of the whole chunk.

    A chunk can start on page 1 and end on page 2, or hold several small
    sections. Its `spans` say where each page/section begins inside the text,
    so we find the snippet's position and take the span it falls in.
    """
    fallback = (chunk.page_start, chunk.section_title)
    position = chunk.content.find(snippet[:80]) if snippet else -1
    if not chunk.spans or position < 0:
        return fallback
    located = fallback
    for offset, page, section in chunk.spans:
        if offset > position:
            break
        located = (page, section)
    return located


def split_sentences(text: str) -> list[str]:
    """
    Lines first (headings, list items and table rows are separate lines), then
    sentences within each line. Two repairs after splitting at ". ":
      "7." + "Term and Termination"  -> "7. Term and Termination"
      "The fee is $25." + "[2]"      -> "The fee is $25. [2]"  (LLMs often cite
                                         after the full stop)
    """
    sentences: list[str] = []
    for line in text.split("\n"):
        merged: list[str] = []
        for piece in _SENTENCE.split(line.strip()):
            if merged and (_NUMBERING.match(merged[-1]) or _ONLY_MARKERS.match(piece)):
                merged[-1] = f"{merged[-1]} {piece}"
            else:
                merged.append(piece)
        sentences.extend(m.strip() for m in merged if m.strip())
    return sentences


def _content_words(text: str) -> set[str]:
    # Words of 3+ letters, and any token with a digit ("60", "204", "4b").
    return {w for w in _WORD.findall(text.lower()) if len(w) > 2 or any(c.isdigit() for c in w)}


def best_matching_sentence(text: str, passage: str) -> str:
    """The passage sentence sharing the most content words with `text`."""
    words = _content_words(text)
    candidates = split_sentences(passage)
    if not candidates:
        return passage[:SNIPPET_MAX_CHARS]
    best = max(candidates, key=lambda sentence: len(words & _content_words(sentence)))
    return best[:SNIPPET_MAX_CHARS]


def claim_text(answer: str, number: int) -> str:
    """
    The text that the first [number] marker vouches for: everything since the
    previous marker on the same line (a paragraph or bullet), up to [number].

        "Rent is $2,450 [1]. Notice is 60 days [2]."  -> claim for 2: ". Notice is 60 days"
        "Pets need approval [2][3]."                   -> claim for 3: "Pets need approval"
    """
    for line in answer.split("\n"):
        claim_start = prev_end = 0
        for marker in _MARKER.finditer(line):
            if line[prev_end : marker.start()].strip():
                claim_start = prev_end  # new text since the last marker = a new claim
            numbers = {int(n) for n in marker.group(1).split(",")}
            if number in numbers:
                # Drop other [n] markers, or "[2]" would match any line containing a "2".
                claim = _MARKER.sub(" ", line[claim_start : marker.start()])
                if claim.strip():
                    return claim
            prev_end = marker.end()
    return ""


def best_snippet(answer: str, number: int, passage: str) -> str:
    """
    Word-overlap matching: the passage sentence sharing the most words with the
    claim that cites [number]. Cheap, no extra API call, and good enough to
    highlight the right line most of the time.
    """
    claim = claim_text(answer, number) or _MARKER.sub(" ", answer)
    return best_matching_sentence(claim, passage)
