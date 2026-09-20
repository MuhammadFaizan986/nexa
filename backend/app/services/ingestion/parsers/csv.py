"""
CSV parser.

A spreadsheet row means nothing to an embedding model on its own: "4B, 2450,
2026-03-01" has no words to match a question against. So each row is turned
into a readable line with its column names attached:

    Unit: 4B; Monthly rent: 2450; Lease start: 2026-03-01

Now "what is the rent for unit 4B" can match it, and the answer is quotable.
This is the same trick used for tables in Word and HTML documents.

Large files are capped (see MAX_ROWS) and the cap is stated in the text, so a
truncated spreadsheet is never silently half-indexed.
"""

import csv
import io
from pathlib import Path

from app.services.ingestion.parsers.base import Block, ParsedDocument, ParseError
from app.services.ingestion.parsers.text import read_text_file

MAX_ROWS = 20_000
_SAMPLE_BYTES = 4096


def parse_csv(path: Path) -> ParsedDocument:
    text = read_text_file(path)
    if not text.strip():
        raise ParseError("The file is empty")

    # Comma, semicolon or tab? Let Python work it out from the first few lines.
    try:
        dialect = csv.Sniffer().sniff(text[:_SAMPLE_BYTES], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)

    try:
        header = next(reader)
    except StopIteration:
        raise ParseError("The file has no rows") from None
    header = [(name or f"column {i + 1}").strip() for i, name in enumerate(header)]

    title = path.stem.replace("_", " ").replace("-", " ").strip().title()
    blocks: list[Block] = [Block(title or "Table", kind="heading", level=1)]
    blocks.append(Block("Columns: " + ", ".join(header)))

    rows = 0
    for row in reader:
        if not any(cell.strip() for cell in row):
            continue
        if rows >= MAX_ROWS:
            blocks.append(
                Block(f"Note: only the first {MAX_ROWS:,} rows of this file were indexed.")
            )
            break
        line = "; ".join(
            f"{name}: {value.strip()}"
            for name, value in zip(header, row, strict=False)
            if value.strip()
        )
        if line:
            blocks.append(Block(line))
            rows += 1

    if rows == 0:
        raise ParseError("The file has a header but no data rows")

    parsed = ParsedDocument(blocks=blocks)
    parsed.title = title or None
    return parsed
