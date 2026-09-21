"""
The agent loop: tool choice, the step limit, and passage numbering.

The database-backed tools are covered by the integration tests; what is checked
here is the machinery around them, with a scripted model standing in for Claude:

- a tool call is executed and its result is fed back into the conversation;
- passages keep one number across several tool calls, so citations resolve;
- the loop stops at AGENT_MAX_STEPS and still produces an answer;
- a tool that fails hands the error to the model instead of raising.
"""

import uuid

import pytest

from app.core.config import get_settings
from app.services.agent.loop import AgentDone, AgentStep, run_agent
from app.services.agent.tools import ToolContext, ToolResult, run_tool
from app.services.generation.llm import LLMProvider, StreamEnd, TextDelta, ToolUse
from app.services.retrieval.base import RetrievedChunk


def chunk(content: str, title: str = "Lease 4B") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        document_title=title,
        filename="lease.pdf",
        content=content,
        page_start=3,
        page_end=3,
        section_title="7. Termination",
        score=0.9,
        rank=1,
    )


class ScriptedAgentProvider(LLMProvider):
    """
    Plays a fixed script of turns, so the loop can be tested without a model.

    Each entry is either a list of (tool_name, arguments) to request, or a
    string to say. The provider records what it was asked, which is how the
    tests check that tool results really came back.
    """

    model = "scripted"

    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.turns = 0
        self.seen: list[list[dict]] = []
        self.tools_offered: list[list[dict]] = []

    async def stream(self, *, system, messages, max_tokens, model=None):
        # The loop calls this for the final, tool-free turn.
        self.seen.append(messages)
        self.turns += 1
        text = "Final answer from what I have [1]"
        yield TextDelta(text)
        yield StreamEnd(self.model, 10, 5, "end_turn", content=[{"type": "text", "text": text}])

    async def stream_with_tools(self, *, system, messages, tools, max_tokens, model=None):
        self.seen.append(messages)
        self.tools_offered.append(tools)
        step = self.script[min(self.turns, len(self.script) - 1)]
        self.turns += 1

        if isinstance(step, str):
            yield TextDelta(step)
            yield StreamEnd(self.model, 10, 5, "end_turn", content=[{"type": "text", "text": step}])
            return

        blocks = []
        for index, (name, arguments) in enumerate(step):
            call_id = f"call-{self.turns}-{index}"
            yield ToolUse(id=call_id, name=name, input=arguments)
            blocks.append({"type": "tool_use", "id": call_id, "name": name, "input": arguments})
        yield StreamEnd(self.model, 10, 5, "tool_use", content=blocks)


async def drain(agent):
    """Collect everything the loop yields, split by kind."""
    text, steps, done = [], [], None
    async for event in agent:
        if isinstance(event, TextDelta):
            text.append(event.text)
        elif isinstance(event, AgentStep):
            steps.append(event)
        else:
            done = event
    return "".join(text), steps, done


async def run(provider, session=None, **kwargs):
    return await drain(
        run_agent(
            session,
            tenant_id=uuid.uuid4(),
            collection_ids=[uuid.uuid4()],
            question=kwargs.pop("question", "Compare the two leases"),
            history=kwargs.pop("history", []),
            system="You are a test assistant.",
            provider=provider,
            **kwargs,
        )
    )


# ------------------------------------------------------------- the register


def test_passages_keep_one_number_across_tool_calls():
    ctx = ToolContext(session=None, tenant_id=uuid.uuid4(), collection_ids=[])
    first, second = chunk("Notice is 60 days."), chunk("Rent is $2,450.")

    assert ctx.register([first, second]) == [1, 2]
    # A later search finds the first passage again plus a new one.
    third = chunk("Pets need approval.")
    assert ctx.register([first, third]) == [1, 3]
    assert len(ctx.passages) == 3


def test_formatted_passages_carry_their_global_numbers():
    ctx = ToolContext(session=None, tenant_id=uuid.uuid4(), collection_ids=[])
    ctx.register([chunk("first"), chunk("second")])
    text = ctx.format([chunk("third")])

    assert 'id="3"' in text  # not id="1" — numbering continues


# ------------------------------------------------------------------ the loop


async def test_tool_result_is_fed_back_and_the_answer_follows(monkeypatch):
    async def fake_search(ctx, query, top_k=None):
        ctx.register([chunk(f"Passage about {query}")])
        return ToolResult(
            content='<passage id="1">found</passage>', summary="searched", passages_found=1
        )

    monkeypatch.setitem(
        __import__("app.services.agent.tools", fromlist=["HANDLERS"]).HANDLERS,
        "search_documents",
        fake_search,
    )
    provider = ScriptedAgentProvider(
        [[("search_documents", {"query": "termination"})], "The notice is 60 days [1]"]
    )
    text, steps, done = await run(provider)

    assert isinstance(done, AgentDone)
    assert text == "The notice is 60 days [1]"
    assert [s.tool for s in steps] == ["search_documents"]
    assert steps[0].passages_found == 1
    assert len(done.passages) == 1
    # Turn two saw the assistant's tool call and the tool's result.
    replayed = provider.seen[1]
    assert replayed[-2]["role"] == "assistant"
    assert replayed[-1]["content"][0]["type"] == "tool_result"
    # Tokens from both turns are added up, so the cost is the whole answer's.
    assert done.input_tokens == 20 and done.output_tokens == 10


async def test_two_tools_in_one_turn_both_run(monkeypatch):
    calls = []

    async def fake_search(ctx, query, top_k=None):
        calls.append(query)
        return ToolResult(content="ok", summary=f"searched {query}", passages_found=0)

    monkeypatch.setitem(
        __import__("app.services.agent.tools", fromlist=["HANDLERS"]).HANDLERS,
        "search_documents",
        fake_search,
    )
    provider = ScriptedAgentProvider(
        [
            [
                ("search_documents", {"query": "Unit 4B"}),
                ("search_documents", {"query": "Unit 7A"}),
            ],
            "Both leases say 60 days [1][2]",
        ]
    )
    _, steps, done = await run(provider)

    assert calls == ["Unit 4B", "Unit 7A"]
    assert [s.number for s in steps] == [1, 2]
    assert not done.hit_step_limit


async def test_the_step_limit_stops_the_loop_and_still_answers(monkeypatch):
    async def fake_search(ctx, query, top_k=None):
        return ToolResult(content="nothing useful", summary="searched", passages_found=0)

    monkeypatch.setitem(
        __import__("app.services.agent.tools", fromlist=["HANDLERS"]).HANDLERS,
        "search_documents",
        fake_search,
    )
    monkeypatch.setattr(get_settings(), "agent_max_steps", 3)
    # A model that would search forever if we let it.
    provider = ScriptedAgentProvider([[("search_documents", {"query": "again"})]])
    text, steps, done = await run(provider)

    assert len(steps) == 3  # the cap, not one more
    assert done.hit_step_limit
    assert text == "Final answer from what I have [1]"
    # The last turn was made WITHOUT tools, so the model had to answer.
    assert len(provider.tools_offered) == 3


async def test_an_unknown_tool_is_reported_to_the_model_not_raised():
    ctx = ToolContext(session=None, tenant_id=uuid.uuid4(), collection_ids=[])
    result = await run_tool(ctx, "delete_everything", {})

    assert result.error
    assert "No tool named delete_everything" in result.content


async def test_bad_arguments_are_reported_to_the_model(monkeypatch):
    async def needs_query(ctx, query, top_k=None):  # pragma: no cover - never reached
        return ToolResult("", "")

    monkeypatch.setitem(
        __import__("app.services.agent.tools", fromlist=["HANDLERS"]).HANDLERS,
        "search_documents",
        needs_query,
    )
    ctx = ToolContext(session=None, tenant_id=uuid.uuid4(), collection_ids=[])
    result = await run_tool(ctx, "search_documents", {"wrong_name": "x"})

    assert result.error
    assert "Invalid arguments" in result.content


async def test_a_failing_tool_does_not_end_the_answer(monkeypatch):
    async def explodes(ctx, query, top_k=None):
        raise RuntimeError("database on fire")

    monkeypatch.setitem(
        __import__("app.services.agent.tools", fromlist=["HANDLERS"]).HANDLERS,
        "search_documents",
        explodes,
    )
    provider = ScriptedAgentProvider(
        [[("search_documents", {"query": "x"})], "I couldn't look that up."]
    )
    text, steps, done = await run(provider)

    assert steps[0].error
    assert text == "I couldn't look that up."
    assert isinstance(done, AgentDone)


async def test_a_provider_without_tools_is_rejected_clearly():
    class NoTools(LLMProvider):
        model = "no-tools"

        async def stream(self, **kwargs):  # pragma: no cover - not used
            yield TextDelta("")

    with pytest.raises(NotImplementedError, match="does not support tools"):
        await run(NoTools())
