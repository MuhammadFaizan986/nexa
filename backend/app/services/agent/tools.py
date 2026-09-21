"""
The tools the assistant may call (plan section 10, Week 6).

Ordinary RAG does one search and writes one answer. That fails for questions
that need more than one look at the shelf:

    "Compare the termination clauses in the leases for Unit 4B and Unit 7A,
     and say which is more tenant-friendly."

One search can't answer that: it needs the clause from EACH lease, which means
two searches and then a comparison. So instead of searching once for the user,
we hand the model a set of tools and let it decide what to do — search again,
read a whole document, compare two of them — until it can answer.

Three rules hold this together:

1. **A tool is the only way to reach data.** The model never touches the
   database; it asks for a tool by name with JSON arguments, and this module
   runs the same tenant-scoped, permission-filtered queries the normal search
   uses. A model that hallucinates a document id gets "not found", not a leak.
2. **Every passage a tool returns is registered and numbered.** Numbering is
   global across the whole conversation with the model, so passage [7] found by
   the fourth tool call still resolves to a real chunk when the answer is
   checked (services/generation/citations.py). This is why the agent can cite
   as reliably as plain RAG.
3. **Tools return text, never objects.** Whatever comes back is formatted
   exactly like the passages in a normal prompt, so the model reads one
   consistent format however the text was found.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import Chunk, Document, DocumentStatus
from app.services.generation.prompts import format_passages
from app.services.retrieval.base import RetrievedChunk, SearchFilters
from app.services.retrieval.retriever import retrieve

log = get_logger(__name__)

# A whole document can be far longer than a context window, and reading one is
# rarely about the last page. Summarising and extraction get the first N chunks,
# which for the documents this is built for (leases, policies, manuals) is the
# part that carries the facts.
MAX_DOCUMENT_CHUNKS = 25
MAX_SEARCH_RESULTS = 8


@dataclass
class ToolContext:
    """
    Everything a tool is allowed to touch, and the passages found so far.

    `collection_ids` is resolved ONCE per question, from the user's groups and
    grants, and every tool filters on it. A tool therefore cannot reach a
    collection the user can't read, whatever arguments the model invents.
    """

    session: AsyncSession
    tenant_id: uuid.UUID
    collection_ids: list[uuid.UUID]
    filters: SearchFilters | None = None
    # The running register: index 0 is passage [1], and so on.
    passages: list[RetrievedChunk] = field(default_factory=list)
    # Every search a tool ran. Each one embedded a query and possibly called the
    # reranker, and both are billed — the caller records them as usage so an
    # agent answer reports its true cost, not just the model tokens.
    retrievals: list = field(default_factory=list)

    def register(self, chunks: list[RetrievedChunk]) -> list[int]:
        """
        Add passages to the register and return their numbers.

        A chunk found twice (two searches, overlapping results) keeps its first
        number instead of appearing twice under different ones — otherwise the
        model would cite the same sentence as [2] and [9] in one answer.
        """
        numbers = []
        for chunk in chunks:
            existing = next(
                (i for i, p in enumerate(self.passages) if p.chunk_id == chunk.chunk_id), None
            )
            if existing is None:
                self.passages.append(chunk)
                numbers.append(len(self.passages))
            else:
                numbers.append(existing + 1)
        return numbers

    def format(self, chunks: list[RetrievedChunk]) -> str:
        """Register passages and render them with their global numbers."""
        if not chunks:
            return "No passages found."
        numbers = self.register(chunks)
        return format_passages(chunks, numbers=numbers)


@dataclass
class ToolResult:
    """What goes back to the model, plus what we log in the trace."""

    content: str
    summary: str  # one line for the tool trace shown in the UI
    passages_found: int = 0
    error: bool = False


# --------------------------------------------------------------------- tools


async def search_documents(ctx: ToolContext, query: str, top_k: int | None = None) -> ToolResult:
    """The normal hybrid search, as a tool the model can aim itself."""
    top_k = min(top_k or 6, MAX_SEARCH_RESULTS)
    result = await retrieve(
        ctx.session,
        tenant_id=ctx.tenant_id,
        collection_ids=ctx.collection_ids,
        query=query,
        top_k=top_k,
        filters=ctx.filters,
    )
    ctx.retrievals.append(result)
    if not result.chunks:
        return ToolResult(
            content=f"No passages found for: {query}",
            summary=f"searched “{query}” — nothing found",
        )
    return ToolResult(
        content=ctx.format(result.chunks),
        summary=f"searched “{query}” — {len(result.chunks)} passages",
        passages_found=len(result.chunks),
    )


async def list_documents(ctx: ToolContext, name_contains: str | None = None) -> ToolResult:
    """
    What is in the library, so the model can turn a name into an id.

    Without this, "compare the two leases" forces the model to guess document
    ids; with it, it looks them up first.
    """
    query = (
        select(Document.id, Document.title, Document.filename, Document.page_count)
        .where(
            Document.tenant_id == ctx.tenant_id,
            Document.collection_id.in_(ctx.collection_ids),
            Document.status == DocumentStatus.READY,
        )
        .order_by(Document.title)
        .limit(50)
    )
    if name_contains:
        query = query.where(Document.title.ilike(f"%{name_contains}%"))

    rows = (await ctx.session.execute(query)).all()
    if not rows:
        return ToolResult(content="No documents match.", summary="listed documents — none matched")
    lines = [
        f'- id={row.id} title="{row.title}" file={row.filename}'
        + (f" pages={row.page_count}" if row.page_count else "")
        for row in rows
    ]
    return ToolResult(
        content="Documents you can read:\n" + "\n".join(lines),
        summary=f"listed {len(rows)} document(s)",
    )


async def summarize_document(ctx: ToolContext, document_id: str) -> ToolResult:
    """
    The text of one document, for summarising.

    The tool does not write the summary — the model does. Its job is to fetch
    the right text, with the passage numbers that let the summary be cited.
    """
    chunks, error = await _document_chunks(ctx, document_id)
    if error:
        return error
    return ToolResult(
        content=(
            f"Passages from the document, in order. Summarise them and cite each point.\n\n"
            f"{ctx.format(chunks)}"
        ),
        summary=f"read “{chunks[0].document_title}” ({len(chunks)} passages)",
        passages_found=len(chunks),
    )


async def extract_fields(ctx: ToolContext, document_id: str, fields: list[str]) -> ToolResult:
    """
    The same text, asked for as structured values.

    "Give me the tenant, the rent and the end date of this lease" is how a
    client turns a pile of PDFs into a spreadsheet, and it is the single most
    requested feature in document-AI freelance work. The model reads the
    passages and returns JSON; anything it can't find must come back null
    rather than invented.
    """
    chunks, error = await _document_chunks(ctx, document_id)
    if error:
        return error
    wanted = ", ".join(fields)
    return ToolResult(
        content=(
            f"Extract these fields from the passages below: {wanted}.\n"
            f"Return one JSON object with exactly those keys. Use null for any field that "
            f"the passages do not state — never guess. Cite the passage for each value.\n\n"
            f"{ctx.format(chunks)}"
        ),
        summary=f"extracted {len(fields)} field(s) from “{chunks[0].document_title}”",
        passages_found=len(chunks),
    )


async def compare_documents(
    ctx: ToolContext, document_id_a: str, document_id_b: str, aspect: str
) -> ToolResult:
    """
    The passages about one aspect from each of two documents, side by side.

    Done as two targeted searches rather than two whole documents: comparing
    termination clauses does not need the rest of either lease, and a focused
    pair of extracts is both cheaper and easier for the model to line up.
    """
    parts, found = [], 0
    for document_id in (document_id_a, document_id_b):
        document, error = await _load_document(ctx, document_id)
        if error:
            return error
        result = await retrieve(
            ctx.session,
            tenant_id=ctx.tenant_id,
            collection_ids=ctx.collection_ids,
            query=aspect,
            top_k=4,
            filters=SearchFilters(document_ids=[document.id]),
        )
        ctx.retrievals.append(result)
        found += len(result.chunks)
        body = ctx.format(result.chunks) if result.chunks else f"Nothing about “{aspect}” here."
        parts.append(f'From "{document.title}":\n{body}')

    return ToolResult(
        content=f"Comparing “{aspect}”.\n\n" + "\n\n".join(parts),
        summary=f"compared “{aspect}” across 2 documents",
        passages_found=found,
    )


# ------------------------------------------------------------------ plumbing


async def _load_document(
    ctx: ToolContext, document_id: str
) -> tuple[Document | None, ToolResult | None]:
    """
    Fetch a document the user is allowed to read, or explain why not.

    Both checks matter: `tenant_id` keeps another client's document out, and
    `collection_id in readable` keeps a restricted collection out even inside
    the right tenant. The model gets the same message either way — "no document
    with that id" — so a wrong guess reveals nothing about what exists.
    """
    try:
        parsed = uuid.UUID(str(document_id))
    except (ValueError, AttributeError):
        return None, ToolResult(
            content=f"'{document_id}' is not a document id. Use list_documents first.",
            summary="invalid document id",
            error=True,
        )

    document = await ctx.session.get(Document, parsed)
    if (
        document is None
        or document.tenant_id != ctx.tenant_id
        or document.collection_id not in ctx.collection_ids
    ):
        return None, ToolResult(
            content=f"No document with id {document_id} is available.",
            summary="document not found",
            error=True,
        )
    return document, None


async def _document_chunks(
    ctx: ToolContext, document_id: str
) -> tuple[list[RetrievedChunk], ToolResult | None]:
    document, error = await _load_document(ctx, document_id)
    if error:
        return [], error

    rows = (
        await ctx.session.scalars(
            select(Chunk)
            .where(Chunk.document_id == document.id)
            .order_by(Chunk.chunk_index)
            .limit(MAX_DOCUMENT_CHUNKS)
        )
    ).all()
    if not rows:
        return [], ToolResult(
            content=f'"{document.title}" has no indexed text yet.',
            summary="document not indexed",
            error=True,
        )

    chunks = [
        RetrievedChunk(
            chunk_id=row.id,
            document_id=document.id,
            document_title=document.title,
            filename=document.filename,
            content=row.content,
            page_start=row.page_start,
            page_end=row.page_end,
            section_title=row.section_title,
            spans=(row.meta or {}).get("spans"),
            score=1.0,  # not ranked: this is the document, in its own order
            rank=index + 1,
        )
        for index, row in enumerate(rows)
    ]
    return chunks, None


# The JSON schemas the model sees. Descriptions are written for the MODEL, not
# for us: they are the only instructions it gets about when to reach for each
# tool, so they say when to use it, not merely what it does.
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "search_documents",
        "description": (
            "Search the user's documents for passages relevant to a query. Use this first for "
            "most questions, and again with a different query if the first passages were not "
            "enough. Returns numbered passages you must cite as [n]."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search for. Use the user's own words plus any "
                    "subject carried over from earlier in the conversation.",
                },
                "top_k": {
                    "type": "integer",
                    "description": f"How many passages to return (1-{MAX_SEARCH_RESULTS}).",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_documents",
        "description": (
            "List the documents available, with their ids and titles. Use this when the user "
            "names a document, or when you need a document id for another tool."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name_contains": {
                    "type": "string",
                    "description": "Only list documents whose title contains this text.",
                }
            },
        },
    },
    {
        "name": "summarize_document",
        "description": (
            "Read one document in order, to summarise it or answer a question about the "
            "document as a whole. Prefer search_documents for a single fact."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string", "description": "From list_documents."}
            },
            "required": ["document_id"],
        },
    },
    {
        "name": "extract_fields",
        "description": (
            "Pull specific named values out of one document — for example tenant, monthly rent "
            "and end date from a lease. Returns the passages; you return the JSON."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string", "description": "From list_documents."},
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "The field names to extract, e.g. ['tenant', 'monthly_rent'].",
                },
            },
            "required": ["document_id", "fields"],
        },
    },
    {
        "name": "compare_documents",
        "description": (
            "Get the passages about one aspect from two documents at once, to compare them — "
            "for example the termination clause in two different leases."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "document_id_a": {"type": "string"},
                "document_id_b": {"type": "string"},
                "aspect": {
                    "type": "string",
                    "description": "What to compare, e.g. 'termination clause', 'late fees'.",
                },
            },
            "required": ["document_id_a", "document_id_b", "aspect"],
        },
    },
]

HANDLERS: dict[str, Callable[..., Awaitable[ToolResult]]] = {
    "search_documents": search_documents,
    "list_documents": list_documents,
    "summarize_document": summarize_document,
    "extract_fields": extract_fields,
    "compare_documents": compare_documents,
}


async def run_tool(ctx: ToolContext, name: str, arguments: dict) -> ToolResult:
    """
    Execute one tool call from the model. Never raises.

    A tool that fails returns its error TO THE MODEL as text, because the model
    can recover from "no document with that id" by calling list_documents —
    whereas an exception would end the answer. Unknown names and bad arguments
    are treated the same way: they are things a model does, not bugs.
    """
    handler = HANDLERS.get(name)
    if handler is None:
        return ToolResult(f"No tool named {name}.", summary=f"unknown tool {name}", error=True)

    settings = get_settings()
    if not settings.agent_tools_enabled:  # pragma: no cover - defensive
        return ToolResult("Tools are disabled.", summary="tools disabled", error=True)

    try:
        return await handler(ctx, **arguments)
    except TypeError as exc:  # wrong or missing arguments
        log.warning("agent.tool_arguments_invalid", tool=name, error=str(exc))
        return ToolResult(
            content=f"Invalid arguments for {name}: {exc}. Check the tool's schema and retry.",
            summary=f"{name}: invalid arguments",
            error=True,
        )
    except Exception as exc:
        log.exception("agent.tool_failed", tool=name, error_type=type(exc).__name__)
        return ToolResult(
            content=f"The tool {name} failed. Try a different approach.",
            summary=f"{name}: failed",
            error=True,
        )
