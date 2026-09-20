"""
HTML parser (BeautifulSoup).

Clients arrive with exported wiki pages, Confluence/Notion exports, saved web
pages and email templates. HTML carries real structure (`<h1>`…`<h6>`), so
headings are read directly rather than guessed.

Everything that isn't content is dropped first: scripts, styles, navigation
menus, headers and footers. Left in, a site's navigation would appear in every
single chunk and make them all look alike — the same problem repeated page
headers cause in PDFs.
"""

from pathlib import Path

from bs4 import BeautifulSoup

from app.services.ingestion.parsers.base import Block, ParsedDocument, ParseError
from app.services.ingestion.parsers.text import read_text_file

# Tags whose text is never document content.
_NOISE_TAGS = ("script", "style", "noscript", "nav", "header", "footer", "aside", "form")
_HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")
_BLOCK_TAGS = (*_HEADINGS, "p", "li", "blockquote", "pre", "table")


def parse_html(path: Path) -> ParsedDocument:
    soup = BeautifulSoup(read_text_file(path), "html.parser")
    for tag in soup(list(_NOISE_TAGS)):
        tag.decompose()

    blocks: list[Block] = []
    # `find_all` walks the document in reading order. Tables are handled as a
    # unit, so their rows aren't also emitted as stray list items.
    for element in soup.find_all(_BLOCK_TAGS):
        if element.find_parent("table") is not None:
            continue
        if element.name == "table":
            rows = _table_rows(element)
            if rows:
                blocks.append(Block("\n".join(rows)))
            continue
        text = element.get_text(" ", strip=True)
        if not text:
            continue
        if element.name in _HEADINGS:
            blocks.append(Block(text, kind="heading", level=int(element.name[1])))
        else:
            blocks.append(Block(text))

    if not blocks:  # e.g. a page that is entirely JavaScript
        raise ParseError("No readable text found in this HTML file")

    parsed = ParsedDocument(blocks=blocks)
    title = soup.title.get_text(strip=True) if soup.title else None
    parsed.title = title or parsed.first_heading()
    return parsed


def _table_rows(table) -> list[str]:
    """One line per row, with column names attached (see docx.py for why)."""
    rows = [
        [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
        for row in table.find_all("tr")
    ]
    rows = [row for row in rows if any(row)]
    if not rows:
        return []
    header, body = rows[0], rows[1:]
    if not body:
        return [" | ".join(header)]
    return ["; ".join(f"{h}: {v}" for h, v in zip(header, row, strict=False) if v) for row in body]
