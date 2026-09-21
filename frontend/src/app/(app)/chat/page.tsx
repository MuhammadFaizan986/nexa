"use client";

/**
 * Ask questions and watch the answer arrive.
 *
 * Three columns: past conversations, the thread, and (when you click a source)
 * the citation panel. The answer streams in token by token, exactly as the API
 * sends it, so there's something to read within a second instead of a spinner.
 */

import {
  ArrowDownWideNarrow,
  Check,
  FileStack,
  FileText,
  MessageSquarePlus,
  Scissors,
  Search,
  SendHorizonal,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  Wrench,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { CitationPanel } from "@/components/CitationPanel";
import { LogoMark } from "@/components/brand";
import { Button, Empty, ErrorNote, useToast } from "@/components/ui";
import { ApiError, request, streamChat } from "@/lib/api";
import type {
  ChatMessage,
  Citation,
  Conversation,
  ConversationDetail,
  ToolStep,
} from "@/lib/types";

const SUGGESTIONS = [
  "What is the notice period for terminating the lease for Unit 4B?",
  "What does error code E-204 mean?",
  "How many days of annual leave do I get after 3 years?",
];

type Draft = { question: string; answer: string; steps: ToolStep[] };

/**
 * Agent mode. Off by default because it costs more: the model may call several
 * tools, and each round trip is another paid request. It earns its keep on
 * questions that span documents ("compare the two leases"), where one search
 * can't reach both answers.
 */
const AGENT_HINT = "Let the assistant search, read and compare by itself";

export default function ChatPage() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [citation, setCitation] = useState<Citation | null>(null);
  const [agent, setAgent] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const notify = useToast();

  const loadConversations = useCallback(async () => {
    setConversations(await request<Conversation[]>("/conversations"));
  }, []);

  const openConversation = useCallback(async (id: string) => {
    const detail = await request<ConversationDetail>(`/conversations/${id}`);
    setConversationId(id);
    setMessages(detail.messages);
    setDraft(null);
    setCitation(null);
  }, []);

  useEffect(() => {
    void loadConversations();
  }, [loadConversations]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, draft]);

  async function ask(asked: string) {
    if (!asked.trim() || busy) return;
    setQuestion("");
    setError(null);
    setBusy(true);
    setDraft({ question: asked, answer: "", steps: [] });

    let newConversationId = conversationId;
    await streamChat(
      { question: asked, conversation_id: conversationId ?? undefined, agent },
      {
        onMeta: (data) => {
          newConversationId = data.conversation_id;
        },
        onToken: (text) =>
          setDraft((current) => (current ? { ...current, answer: current.answer + text } : current)),
        // Each tool call appears the moment it finishes, so the wait is
        // narrated instead of silent.
        onTool: (step) =>
          setDraft((current) =>
            current ? { ...current, steps: [...current.steps, step] } : current,
          ),
        onDone: async () => {
          if (newConversationId) await openConversation(newConversationId);
          await loadConversations();
          setBusy(false);
        },
        onError: (detail) => {
          setError(detail);
          setDraft(null);
          setBusy(false);
        },
      },
    );
  }

  async function rate(message: ChatMessage, value: 1 | -1) {
    const next = message.feedback === value ? 0 : value;
    setMessages((current) =>
      current.map((m) => (m.id === message.id ? { ...m, feedback: next } : m)),
    );
    try {
      await request(`/messages/${message.id}/feedback`, { method: "POST", body: { value: next } });
      if (next !== 0) notify(next === 1 ? "Thanks — marked as helpful" : "Thanks — we'll look at it");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save your feedback");
    }
  }

  function startNew() {
    setConversationId(null);
    setMessages([]);
    setDraft(null);
    setCitation(null);
    input.current?.focus();
  }

  return (
    <div className="flex h-screen">
      {/* Past conversations */}
      <div className="flex w-60 shrink-0 flex-col border-r border-line bg-surface">
        <div className="p-3">
          <Button icon={MessageSquarePlus} className="w-full" onClick={startNew}>
            New question
          </Button>
        </div>
        <ul className="flex-1 space-y-0.5 overflow-y-auto px-2 pb-3">
          {conversations.map((conversation) => (
            <li key={conversation.id}>
              <button
                onClick={() => void openConversation(conversation.id)}
                className={`w-full truncate rounded-lg px-3 py-2 text-left text-sm transition ${
                  conversation.id === conversationId
                    ? "bg-brand-soft font-medium text-brand"
                    : "text-muted hover:bg-canvas hover:text-ink"
                }`}
                title={conversation.title ?? "Conversation"}
              >
                {conversation.title ?? "Conversation"}
              </button>
            </li>
          ))}
        </ul>
      </div>

      {/* The thread */}
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex-1 space-y-6 overflow-y-auto px-6 py-8">
          {messages.length === 0 && !draft && (
            <div className="mx-auto max-w-2xl">
              <Empty icon={Sparkles} title="Ask anything about your documents">
                Every answer shows the passages it used — document, page and the exact sentence.
              </Empty>
              <div className="stagger mt-2 grid gap-2">
                {SUGGESTIONS.map((suggestion) => (
                  <button
                    key={suggestion}
                    onClick={() => void ask(suggestion)}
                    className="rounded-lg border border-line bg-surface px-4 py-2.5 text-left text-sm transition hover:-translate-y-0.5 hover:border-brand hover:shadow-soft"
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((message) =>
            message.role === "user" ? (
              <Question key={message.id} text={message.content} />
            ) : (
              <Answer
                key={message.id}
                message={message}
                activeCitation={citation}
                onCitation={setCitation}
                onRate={(value) => void rate(message, value)}
              />
            ),
          )}

          {draft && (
            <>
              <Question text={draft.question} />
              <div className="mx-auto flex max-w-3xl gap-3">
                <LogoMark size={26} className="mt-0.5 shrink-0" />
                <div className="min-w-0 flex-1 space-y-3">
                  {draft.steps.length > 0 && <ToolTrace steps={draft.steps} live={busy} />}
                  <div className="prose-answer text-sm leading-relaxed">
                    {draft.answer ? (
                      <p>
                        {draft.answer}
                        <span className="caret" />
                      </p>
                    ) : (
                      <span className="flex items-center gap-2 text-muted">
                        <Search size={14} className="animate-pulse-soft" />
                        {agent ? "Working on it…" : "Searching your documents…"}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            </>
          )}
          <div ref={bottom} />
        </div>

        <form
          onSubmit={(event) => {
            event.preventDefault();
            void ask(question);
          }}
          className="border-t border-line bg-surface/80 p-4 backdrop-blur"
        >
          <div className="mx-auto max-w-3xl">
            <ErrorNote>{error}</ErrorNote>
            <div className="mt-2 flex items-end gap-2 rounded-xl border border-line bg-surface p-2 shadow-soft transition focus-within:border-brand focus-within:ring-2 focus-within:ring-brand/20">
              <button
                type="button"
                onClick={() => setAgent((on) => !on)}
                title={AGENT_HINT}
                aria-pressed={agent}
                className={`mb-0.5 inline-flex shrink-0 items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition ${
                  agent
                    ? "border-brand bg-brand-soft text-brand"
                    : "border-line text-muted hover:border-brand hover:text-brand"
                }`}
              >
                <Wrench size={13} />
                Agent
                {agent && <Check size={12} />}
              </button>
              <textarea
                ref={input}
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(event) => {
                  // Enter sends, Shift+Enter makes a new line.
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void ask(question);
                  }
                }}
                rows={1}
                placeholder="Ask a question about your documents…"
                className="max-h-40 flex-1 resize-none bg-transparent px-2 py-1.5 text-sm outline-none placeholder:text-muted"
              />
              <Button type="submit" loading={busy} disabled={!question.trim()} icon={SendHorizonal}>
                Ask
              </Button>
            </div>
            <p className="mt-2 text-center text-xs text-muted">
              {agent
                ? "Agent mode: the assistant searches, reads and compares by itself — slower, and it costs more per question."
                : "Answers come only from your documents, with citations. Enter sends · Shift+Enter for a new line."}
            </p>
          </div>
        </form>
      </div>

      {citation && <CitationPanel citation={citation} onClose={() => setCitation(null)} />}
    </div>
  );
}

function Question({ text }: { text: string }) {
  return (
    <div className="mx-auto flex max-w-3xl justify-end animate-fade-up">
      <p className="max-w-[80%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-brand px-4 py-2.5 text-sm text-brand-ink shadow-soft">
        {text}
      </p>
    </div>
  );
}

function Answer({
  message,
  activeCitation,
  onCitation,
  onRate,
}: {
  message: ChatMessage;
  activeCitation: Citation | null;
  onCitation: (citation: Citation) => void;
  onRate: (value: 1 | -1) => void;
}) {
  const seconds = message.latency_ms?.total ? (message.latency_ms.total / 1000).toFixed(1) : null;

  return (
    <div className="mx-auto flex max-w-3xl gap-3 animate-fade-up">
      <LogoMark size={26} className="mt-0.5 shrink-0" />
      <div className="min-w-0 flex-1 space-y-3">
        {/* A follow-up like "what about the pet bond?" can't be searched for as
            typed, so it was rewritten first. Showing the rewrite is how someone
            checks the assistant understood which lease they meant. */}
        {message.tool_steps && message.tool_steps.length > 0 && (
          <ToolTrace steps={message.tool_steps} />
        )}

        {message.search_query && (
          <p
            className="inline-flex max-w-full items-center gap-1.5 rounded-full bg-canvas px-2.5 py-1 text-xs text-muted"
            title="Your follow-up was rewritten into a standalone question before searching"
          >
            <Search size={12} className="shrink-0" />
            <span className="truncate">Searched for: {message.search_query}</span>
          </p>
        )}

        <div className="prose-answer text-sm leading-relaxed">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
        </div>

        {message.citations.length > 0 && (
          <div className="flex flex-wrap items-center gap-2">
            {message.citations.map((citation) => {
              const active = activeCitation?.number === citation.number;
              return (
                <button
                  key={citation.number}
                  onClick={() => onCitation(citation)}
                  title={citation.snippet ?? undefined}
                  className={`group inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition hover:-translate-y-0.5 ${
                    active
                      ? "border-brand bg-brand-soft text-brand"
                      : "border-line hover:border-brand hover:text-brand"
                  }`}
                >
                  <span className="grid size-4 place-items-center rounded-full bg-brand/10 text-[10px] font-semibold text-brand">
                    {citation.number}
                  </span>
                  <span className="max-w-52 truncate">{citation.document_title}</span>
                  {citation.page_start && <span className="text-muted">p{citation.page_start}</span>}
                </button>
              );
            })}
          </div>
        )}

        <div className="flex flex-wrap items-center gap-3 text-xs text-muted">
          <button
            onClick={() => onRate(1)}
            className={`rounded p-1 transition hover:bg-canvas ${
              message.feedback === 1 ? "text-good" : "hover:text-ink"
            }`}
            aria-label="Helpful"
          >
            <ThumbsUp size={13} />
          </button>
          <button
            onClick={() => onRate(-1)}
            className={`rounded p-1 transition hover:bg-canvas ${
              message.feedback === -1 ? "text-bad" : "hover:text-ink"
            }`}
            aria-label="Not helpful"
          >
            <ThumbsDown size={13} />
          </button>
          {message.model && <span>{message.model}</span>}
          {seconds && <span>{seconds}s</span>}
          {message.input_tokens != null && (
            <span>
              {message.input_tokens}+{message.output_tokens} tokens
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * What the assistant did before answering.
 *
 * In agent mode the wait is longer than plain RAG — several model calls and
 * several searches — so showing each step as it completes turns a silent pause
 * into a narration. It is also the honest answer to "why did this question
 * cost more than that one".
 */
const TOOL_ICONS: Record<string, typeof Search> = {
  search_documents: Search,
  list_documents: FileStack,
  summarize_document: FileText,
  extract_fields: Scissors,
  compare_documents: ArrowDownWideNarrow,
};

function ToolTrace({ steps, live = false }: { steps: ToolStep[]; live?: boolean }) {
  return (
    <ol className="space-y-1.5 rounded-xl border border-line bg-canvas/60 p-3">
      {steps.map((step) => {
        const Icon = TOOL_ICONS[step.tool] ?? Wrench;
        return (
          <li key={step.number} className="flex items-start gap-2 text-xs animate-fade-up">
            <span
              className={`mt-px grid size-5 shrink-0 place-items-center rounded-md ${
                step.error ? "bg-bad/10 text-bad" : "bg-brand-soft text-brand"
              }`}
            >
              <Icon size={12} />
            </span>
            <span className="min-w-0 flex-1">
              <span className={step.error ? "text-bad" : "text-ink"}>{step.summary}</span>
              <span className="ml-1.5 text-muted">{(step.latency_ms / 1000).toFixed(1)}s</span>
            </span>
          </li>
        );
      })}
      {live && (
        <li className="flex items-center gap-2 text-xs text-muted">
          <span className="grid size-5 shrink-0 place-items-center">
            <Sparkles size={12} className="animate-pulse-soft" />
          </span>
          Thinking…
        </li>
      )}
    </ol>
  );
}
