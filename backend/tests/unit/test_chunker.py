"""
Chunker tests. These pin down the behaviour citations depend on: page tracking,
section titles, size limits, overlap, and never mixing unrelated sections.
"""

from app.services.ingestion.chunker import chunk_blocks, count_tokens
from app.services.ingestion.parsers.base import Block


def sentence(i: int) -> str:
    return f"Sentence number {i} explains an important rule about the tenancy agreement."


def long_section(n_sentences: int, page: int = 1) -> list[Block]:
    return [Block(" ".join(sentence(i) for i in range(n_sentences)), page=page)]


def test_short_document_is_one_chunk_with_section_and_page():
    blocks = [
        Block("Lease Agreement", kind="heading", level=1, page=1),
        Block("7. Termination", kind="heading", level=2, page=3),
        Block("Either party may end the lease with 60 days written notice.", page=3),
    ]
    chunks = chunk_blocks(blocks)
    assert len(chunks) == 1
    chunk = chunks[0]
    assert "60 days written notice" in chunk.content
    assert chunk.page_start == 1 and chunk.page_end == 3
    # Labelled by the section holding most of the text, not by the first heading.
    assert chunk.section_title == "7. Termination"
    assert chunk.heading_path == ["Lease Agreement", "7. Termination"]
    assert chunk.sections == ["Lease Agreement", "7. Termination"]


def test_chunks_respect_size_limit():
    chunks = chunk_blocks(long_section(200), chunk_size=200, overlap=30)
    assert len(chunks) > 1
    # Small tolerance: separators between sentences add a few tokens.
    assert all(c.token_count <= 210 for c in chunks)


def test_consecutive_chunks_overlap():
    chunks = chunk_blocks(long_section(100), chunk_size=200, overlap=40)
    for previous, following in zip(chunks, chunks[1:], strict=False):
        last_sentence_of_previous = previous.content.split(". ")[-1]
        assert last_sentence_of_previous.rstrip(".") in following.content


def test_zero_overlap_means_no_repeated_text():
    chunks = chunk_blocks(long_section(100), chunk_size=200, overlap=0)
    joined = " ".join(c.content for c in chunks)
    assert joined.count(sentence(50)) == 1


def test_new_section_starts_new_chunk_when_current_is_big_enough():
    blocks = [
        Block("Rent", kind="heading", level=1, page=1),
        *long_section(12, page=1),  # ~200 tokens > min_chunk
        Block("Termination", kind="heading", level=1, page=2),
        Block("Sixty days notice is required.", page=2),
    ]
    chunks = chunk_blocks(blocks, chunk_size=600, min_chunk=150)
    assert [c.section_title for c in chunks] == ["Rent", "Termination"]
    assert chunks[1].page_start == 2
    assert "Rent" not in chunks[1].content


def test_tiny_sibling_sections_are_merged():
    blocks = [
        Block("Report", kind="heading", level=1, page=1),
        Block("Details", kind="heading", level=2, page=1),
        Block("Inspector: Sam.", page=1),
        Block("Rooms", kind="heading", level=2, page=1),
        Block("Kitchen: Good.", page=1),
    ]
    chunks = chunk_blocks(blocks, min_chunk=150)
    assert len(chunks) == 1
    assert chunks[0].sections == ["Report", "Details", "Rooms"]


def test_small_sections_never_merge_across_a_higher_level_heading():
    # One unit per page: the tiny "Notes" of Unit 4B must not bleed into Unit 5A.
    blocks = [
        Block("Unit 4B", kind="heading", level=1, page=3),
        *long_section(12, page=3),
        Block("Notes", kind="heading", level=2, page=3),
        Block("A cat lives here.", page=3),
        Block("Unit 5A", kind="heading", level=1, page=4),
        Block("No issues found.", page=4),
    ]
    chunks = chunk_blocks(blocks, min_chunk=150)
    assert all(c.page_start == c.page_end for c in chunks)
    unit_5a = [c for c in chunks if "No issues found" in c.content]
    assert len(unit_5a) == 1 and "cat" not in unit_5a[0].content


def test_chunk_never_ends_with_a_dangling_heading():
    blocks = [
        *long_section(11, page=1),
        Block("Next Section", kind="heading", level=1, page=2),
        *long_section(11, page=2),
    ]
    chunks = chunk_blocks(blocks, chunk_size=200, overlap=20, min_chunk=10_000)
    for chunk in chunks:
        assert not chunk.content.rstrip().endswith("Next Section")


def test_enormous_sentence_is_split_by_tokens():
    huge = "word " * 2000
    chunks = chunk_blocks([Block(huge, page=1)], chunk_size=300, overlap=50)
    assert len(chunks) > 5
    assert all(c.token_count <= 310 for c in chunks)


def test_list_items_keep_their_line_breaks():
    block = Block("- Kitchen: Good\n- Bathroom: Fair\n- Balcony: Good", page=1)
    chunks = chunk_blocks([block])
    assert chunks[0].content == block.text


def test_count_tokens_matches_embedding_tokenizer():
    assert count_tokens("hello world") == 2


def test_spans_record_where_each_page_and_section_starts():
    blocks = [
        Block("Where to Find Help", kind="heading", level=2, page=1),
        Block("Ask HR.", page=1),
        Block("Annual Leave", kind="heading", level=2, page=2),
        Block("After 3 completed years of service: 21 days per year. " * 3, page=2),
    ]
    [chunk] = chunk_blocks(blocks, min_chunk=150)  # tiny first section gets merged
    assert [(page, section) for _, page, section in chunk.spans] == [
        (1, "Where to Find Help"),
        (2, "Annual Leave"),
    ]
    # Each offset points at the exact start of that part of the text.
    assert chunk.content[chunk.spans[1][0] :].startswith("Annual Leave")
    assert chunk.section_title == "Annual Leave"  # the bigger section wins the label
