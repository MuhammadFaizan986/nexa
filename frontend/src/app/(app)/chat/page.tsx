"use client";

/**
 * Ask questions and watch the answer arrive.
 *
 * Three columns: past conversations, the thread, and (when you click a source)
 * the citation panel. The answer streams in token by token, exactly as the API
 * sends it, so the user sees progress within a second instead of staring at a
 * spinner for five.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { ApiError, request, streamChat } from "@/lib/api";
import type { ChatMessage, Citation, Conversation, ConversationDetail } from "@/lib/types";
import { CitationPanel } from "@/components/CitationPanel";
import { Button, Empty, ErrorNote } from "@/components/ui";

type Draft = { question: string; answer: string; done: boolean };

export default function ChatPage() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [citation, setCitation] = useState<Citation | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

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

  async function ask(event: React.FormEvent) {
    event.preventDefault();
    const asked = question.trim();
    if (!asked || busy) return;

    setQuestion("");
    setError(null);
    setBusy(true);
    setDraft({ question: asked, answer: "", done: false });

    let newConversationId = conversationId;
    await streamChat(
      { question: asked, conversation_id: conversationId ?? undefined },
      {
        onMeta: (data) => {
          newConversationId = data.conversation_id;
        },
        onToken: (text) =>
          setDraft((current) => (current ? { ...current, answer: current.answer + text } : current)),
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
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save your feedback");
    }
  }

  return (
    <div className="flex h-screen">
      {/* Past conversations */}
      <div className="flex w-60 shrink-0 flex-col border-r border-line bg-surface">
        <div className="p-3">
          <Button
            className="w-full"
            onClick={() => {
              setConversationId(null);
              setMessages([]);
              setDraft(null);
              setCitation(null);
            }}
          >
            New question
          </Button>
        </div>
        <ul className="flex-1 overflow-y-auto px-2 pb-3">
          {conversations.map((conversation) => (
            <li key={conversation.id}>
              <button
                onClick={() => void openConversation(conversation.id)}
                className={`mb-1 w-full truncate rounded-lg px-3 py-2 text-left text-sm ${
                  conversation.id === conversationId ? "bg-brand-soft text-brand" : "hover:bg-canvas"
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
        <div className="flex-1 space-y-5 overflow-y-auto px-6 py-6">
          {messages.length === 0 && !draft && (
            <Empty>
              Ask anything about your documents. Every answer shows the sources it used.
            </Empty>
          )}

          {messages.map((message) =>
            message.role === "user" ? (
              <Question key={message.id} text={message.content} />
            ) : (
              <Answer
                key={message.id}
                message={message}
                onCitation={setCitation}
                onRate={(value) => void rate(message, value)}
              />
            ),
          )}

          {draft && (
            <>
              <Question text={draft.question} />
              <div className="max-w-3xl">
                <div className="prose-answer text-sm leading-relaxed">
                  {draft.answer || <span className="text-muted">Searching your documents…</span>}
                </div>
              </div>
            </>
          )}
          <div ref={bottom} />
        </div>

        <form onSubmit={ask} className="border-t border-line bg-surface p-4">
          <ErrorNote>{error}</ErrorNote>
          <div className="mt-2 flex gap-2">
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="What is the notice period for terminating the lease for Unit 4B?"
              className="flex-1 rounded-lg border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-brand"
            />
            <Button type="submit" disabled={busy || !question.trim()}>
              {busy ? "Answering…" : "Ask"}
            </Button>
          </div>
        </form>
      </div>

      {citation && <CitationPanel citation={citation} onClose={() => setCitation(null)} />}
    </div>
  );
}

function Question({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <p className="max-w-2xl rounded-2xl bg-brand px-4 py-2 text-sm text-white">{text}</p>
    </div>
  );
}

function Answer({
  message,
  onCitation,
  onRate,
}: {
  message: ChatMessage;
  onCitation: (citation: Citation) => void;
  onRate: (value: 1 | -1) => void;
}) {
  const usage = message.latency_ms?.total;
  return (
    <div className="max-w-3xl space-y-2">
      <div className="prose-answer text-sm leading-relaxed">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
      </div>

      {message.citations.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted">Sources:</span>
          {message.citations.map((citation) => (
            <button
              key={citation.number}
              onClick={() => onCitation(citation)}
              className="rounded-full border border-line px-2.5 py-1 text-xs hover:border-brand hover:text-brand"
              title={citation.snippet ?? undefined}
            >
              [{citation.number}] {citation.document_title}
              {citation.page_start ? ` · p${citation.page_start}` : ""}
            </button>
          ))}
        </div>
      )}

      <div className="flex items-center gap-3 text-xs text-muted">
        <button
          onClick={() => onRate(1)}
          className={message.feedback === 1 ? "text-good" : "hover:text-ink"}
          aria-label="Helpful"
        >
          ▲ helpful
        </button>
        <button
          onClick={() => onRate(-1)}
          className={message.feedback === -1 ? "text-bad" : "hover:text-ink"}
          aria-label="Not helpful"
        >
          ▼ not helpful
        </button>
        {message.model && <span>{message.model}</span>}
        {usage && <span>{(usage / 1000).toFixed(1)}s</span>}
        {message.input_tokens != null && (
          <span>
            {message.input_tokens}+{message.output_tokens} tokens
          </span>
        )}
      </div>
    </div>
  );
}
