from app.services.ingestion.cleaner import (
    clean_document,
    normalize_text,
    remove_repeated_page_furniture,
)
from app.services.ingestion.parsers.base import Block, ParsedDocument


def test_normalize_fixes_ligatures_spaces_and_control_chars():
    raw = "The ﬁnal notice​  is   due.\x07\n\n\n\nNext   line "
    assert normalize_text(raw) == "The final notice is due.\n\nNext line"


def _pages(n: int, body: str = "Section {p} explains the rules in detail.") -> list[Block]:
    blocks = []
    for p in range(1, n + 1):
        blocks += [
            Block("Harbourview Property Group - Confidential", page=p),
            Block(body.format(p=p), page=p),
            Block(f"Page {p} of {n}", page=p),
        ]
    return blocks


def test_repeated_headers_and_footers_are_removed():
    cleaned = remove_repeated_page_furniture(_pages(5))
    texts = [b.text for b in cleaned]
    assert len(cleaned) == 5
    assert not any("Confidential" in t or t.startswith("Page ") for t in texts)


def test_lines_differing_only_in_digits_are_kept():
    # Every page of an inspection report starts with a date line. Different dates
    # are real content, not a running header.
    blocks = []
    for p in range(1, 6):
        blocks += [Block(f"Date of inspection: {p} June 2026", page=p), Block("Details.", page=p)]
    cleaned = remove_repeated_page_furniture(blocks)
    assert sum("Date of inspection" in b.text for b in cleaned) == 5


def test_short_documents_are_left_alone():
    assert len(remove_repeated_page_furniture(_pages(2))) == 6


def test_clean_document_drops_empty_blocks():
    doc = ParsedDocument(blocks=[Block("  ​ "), Block("Real text")], page_count=None)
    assert [b.text for b in clean_document(doc).blocks] == ["Real text"]
