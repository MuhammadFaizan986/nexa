"use client";

/**
 * The landing page: what NEXA does, shown rather than described.
 *
 * The centrepiece is a self-running demo — a question types itself, the answer
 * streams in, and the sources appear underneath — because that sequence IS the
 * product. Everything animates once on entry and then holds still; motion is
 * disabled entirely for anyone who asked for reduced motion (globals.css).
 */

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  FileText,
  Gauge,
  Lock,
  Quote,
  Search,
  ShieldCheck,
  Sparkles,
} from "lucide-react";

import { Pipeline } from "@/components/Pipeline";
import { Logo } from "@/components/brand";
import { ThemeToggle } from "@/components/theme";
import { useAuth } from "@/lib/auth";

const DEMOS = [
  {
    question: "What is the notice period for terminating the lease for Unit 4B?",
    answer:
      "The tenant must give **60 days written notice** to the landlord's agent, and the notice period starts on the day it is received.",
    sources: ["Lease Agreement – Unit 4B · p3", "7. Term and Termination"],
  },
  {
    question: "What does error code E-204 mean?",
    answer:
      "**E-204 — Beneficiary account closed.** The receiving bank reports the account is closed, so the payment can't be credited. The original payment returns in 3–5 business days.",
    sources: ["Payment Error Codes · E-204", "Customer FAQ"],
  },
  {
    question: "Is there a swimming pool in Building A?",
    answer: "I don't have enough information in the available documents to answer that.",
    sources: [],
  },
];

const FEATURES = [
  {
    Icon: Quote,
    title: "Answers you can check",
    body: "Every claim carries a citation: the document, the page, and the exact sentence it came from.",
  },
  {
    Icon: Search,
    title: "Finds meaning and exact terms",
    body: "Semantic search understands paraphrases; keyword search nails codes like E-204. Both, fused, then reranked.",
  },
  {
    Icon: ShieldCheck,
    title: "Says “I don't know”",
    body: "When the documents don't cover it, it says so instead of guessing — and doesn't even call the model.",
  },
  {
    Icon: Lock,
    title: "Permissions that hold",
    body: "Per-collection access by group, enforced inside the search itself and again by the database.",
  },
  {
    Icon: FileText,
    title: "Every common format",
    body: "PDF with page numbers, Word, Markdown, text, HTML and spreadsheets — processed in the background.",
  },
  {
    Icon: Gauge,
    title: "Measured, not claimed",
    body: "A 50-question evaluation set runs on demand: Hit@1 92%, Hit@5 98%, with cost and latency per answer.",
  },
];

export default function LandingPage() {
  const { user, loading } = useAuth();
  const appHref = user ? "/chat" : "/login";

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-30 border-b border-line/70 bg-canvas/80 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-5 py-3">
          <Logo size={30} />
          <div className="flex items-center gap-3">
            <ThemeToggle compact />
            <Link
              href={appHref}
              className="rounded-lg bg-brand px-3.5 py-2 text-sm font-medium text-brand-ink shadow-soft transition hover:brightness-110"
            >
              {loading ? "…" : user ? "Open NEXA" : "Sign in"}
            </Link>
          </div>
        </div>
      </header>

      {/* ------------------------------------------------------------- hero */}
      <section className="aurora relative overflow-hidden px-5 pb-20 pt-16">
        <div className="relative mx-auto grid max-w-6xl items-center gap-12 lg:grid-cols-2">
          <div className="stagger">
            <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-3 py-1 text-xs text-muted">
              <Sparkles size={13} className="text-brand" />
              Retrieval-augmented answers, with receipts
            </span>
            <h1 className="mt-5 text-4xl font-semibold leading-[1.1] tracking-tight sm:text-5xl">
              Ask your documents.
              <br />
              <span className="bg-gradient-to-r from-brand to-accent bg-clip-text text-transparent">
                Get answers you can trust.
              </span>
            </h1>
            <p className="mt-5 max-w-lg text-base leading-relaxed text-muted">
              NEXA reads your contracts, policies and manuals once, then answers questions about
              them in seconds — quoting the document, the page and the sentence behind every fact.
            </p>
            <div className="mt-7 flex flex-wrap gap-3">
              <Link
                href={appHref}
                className="group inline-flex items-center gap-2 rounded-lg bg-brand px-4 py-2.5 text-sm font-medium text-brand-ink shadow-soft transition hover:brightness-110"
              >
                {user ? "Open NEXA" : "Try the demo"}
                <ArrowRight size={16} className="transition group-hover:translate-x-0.5" />
              </Link>
              <a
                href="https://github.com/MuhammadFaizan986/nexa"
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-2 rounded-lg border border-line bg-surface px-4 py-2.5 text-sm font-medium transition hover:bg-canvas"
              >
                See the code
              </a>
            </div>
            <dl className="mt-10 flex gap-8">
              {[
                ["92%", "answer found first"],
                ["98%", "found in the top 5"],
                ["~2s", "to the first word"],
              ].map(([value, label]) => (
                <div key={label}>
                  <dt className="text-2xl font-semibold tabular-nums">{value}</dt>
                  <dd className="text-xs text-muted">{label}</dd>
                </div>
              ))}
            </dl>
          </div>

          <LiveDemo />
        </div>
      </section>

      {/* --------------------------------------------------------- features */}
      <section className="mx-auto max-w-6xl px-5 py-16">
        <h2 className="text-center text-2xl font-semibold tracking-tight">
          Built like a product, not a prototype
        </h2>
        <p className="mx-auto mt-2 max-w-xl text-center text-sm text-muted">
          The parts that decide whether a knowledge assistant is usable at work.
        </p>
        <div className="stagger mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map(({ Icon, title, body }) => (
            <article
              key={title}
              className="group rounded-xl border border-line bg-surface p-5 shadow-soft transition hover:-translate-y-0.5 hover:shadow-lift"
            >
              <span className="inline-grid size-9 place-items-center rounded-lg bg-brand-soft text-brand transition group-hover:scale-105">
                <Icon size={18} />
              </span>
              <h3 className="mt-3.5 text-sm font-semibold">{title}</h3>
              <p className="mt-1.5 text-sm leading-relaxed text-muted">{body}</p>
            </article>
          ))}
        </div>
      </section>

      {/* -------------------------------------------------------- how it works */}
      {/* The pipeline diagram does the explaining here: six stages that light up
          one after another. See components/Pipeline.tsx. */}
      <section className="border-y border-line bg-surface px-5 py-16">
        <div className="mx-auto max-w-6xl">
          <h2 className="text-center text-2xl font-semibold tracking-tight">
            How an answer gets made
          </h2>
          <p className="mx-auto mb-10 mt-2 max-w-xl text-center text-sm text-muted">
            Six steps, from your file to a cited sentence. Hover any step to hold it.
          </p>
          <Pipeline />
        </div>
      </section>

      <section className="mx-auto max-w-3xl px-5 py-20 text-center">
        <h2 className="text-2xl font-semibold tracking-tight">
          Put your documents to work in an afternoon
        </h2>
        <p className="mx-auto mt-3 max-w-lg text-sm text-muted">
          Sign in to the demo workspaces — property, fintech and internal company knowledge — or
          create your own organisation and upload a file.
        </p>
        <Link
          href={appHref}
          className="group mt-7 inline-flex items-center gap-2 rounded-lg bg-brand px-5 py-3 text-sm font-medium text-brand-ink shadow-soft transition hover:brightness-110"
        >
          {user ? "Open NEXA" : "Get started"}
          <ArrowRight size={16} className="transition group-hover:translate-x-0.5" />
        </Link>
      </section>

      <footer className="border-t border-line px-5 py-8">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 text-xs text-muted">
          <Logo size={24} subtitle="AI knowledge base" />
          <p>Answers are generated from your own documents and always cite their source.</p>
        </div>
      </footer>
    </div>
  );
}

/** A question types itself, the answer streams in, the sources appear. */
function LiveDemo() {
  const [index, setIndex] = useState(0);
  const [typed, setTyped] = useState("");
  const [answer, setAnswer] = useState("");
  const [phase, setPhase] = useState<"typing" | "searching" | "answering" | "done">("typing");
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);

  useEffect(() => {
    const demo = DEMOS[index];
    const after = (ms: number, fn: () => void) => timers.current.push(setTimeout(fn, ms));
    setTyped("");
    setAnswer("");
    setPhase("typing");

    // 1. type the question
    demo.question.split("").forEach((_, i) => {
      after(28 * i, () => setTyped(demo.question.slice(0, i + 1)));
    });
    const typingDone = 28 * demo.question.length;

    // 2. a beat of "searching", then 3. stream the answer word by word
    after(typingDone + 250, () => setPhase("searching"));
    after(typingDone + 1100, () => setPhase("answering"));
    const words = demo.answer.split(" ");
    words.forEach((_, i) => {
      after(typingDone + 1100 + 45 * i, () => setAnswer(words.slice(0, i + 1).join(" ")));
    });
    const answerDone = typingDone + 1100 + 45 * words.length;
    after(answerDone + 200, () => setPhase("done"));

    // 4. hold, then move to the next example
    after(answerDone + 3800, () => setIndex((current) => (current + 1) % DEMOS.length));

    return () => {
      timers.current.forEach(clearTimeout);
      timers.current = [];
    };
  }, [index]);

  const demo = DEMOS[index];

  return (
    <div className="relative animate-fade-up">
      <div className="rounded-2xl border border-line bg-surface shadow-lift">
        <div className="flex items-center gap-2 border-b border-line px-4 py-3">
          <span className="size-2.5 rounded-full bg-bad/70" />
          <span className="size-2.5 rounded-full bg-warn/70" />
          <span className="size-2.5 rounded-full bg-good/70" />
          <span className="ml-2 text-xs text-muted">Ask · Harbourview Property Group</span>
        </div>

        <div className="space-y-4 p-5">
          <div className="flex justify-end">
            <p className="max-w-[85%] rounded-2xl rounded-br-sm bg-brand px-3.5 py-2 text-sm text-brand-ink">
              {typed || " "}
              {phase === "typing" && <span className="caret" />}
            </p>
          </div>

          {phase === "searching" && (
            <div className="flex items-center gap-2 text-sm text-muted">
              <Search size={14} className="animate-pulse-soft" />
              Searching 179 passages…
            </div>
          )}

          {(phase === "answering" || phase === "done") && (
            <div className="space-y-3">
              <p className="max-w-[92%] text-sm leading-relaxed">
                {answer.split("**").map((part, i) =>
                  i % 2 ? (
                    <strong key={i} className="font-semibold">
                      {part}
                    </strong>
                  ) : (
                    <span key={i}>{part}</span>
                  ),
                )}
                {phase === "answering" && <span className="caret" />}
              </p>

              {phase === "done" && demo.sources.length > 0 && (
                <div className="flex flex-wrap items-center gap-2 animate-fade-up">
                  <span className="text-xs text-muted">Sources:</span>
                  {demo.sources.map((source, i) => (
                    <span
                      key={source}
                      className="rounded-full border border-line px-2.5 py-1 text-xs text-muted"
                    >
                      [{i + 1}] {source}
                    </span>
                  ))}
                </div>
              )}
              {phase === "done" && demo.sources.length === 0 && (
                <p className="text-xs text-muted animate-fade-in">
                  No source was relevant enough, so no model was called — and nothing was invented.
                </p>
              )}
            </div>
          )}
        </div>
      </div>

      <div className="mt-3 flex justify-center gap-1.5">
        {DEMOS.map((demoItem, i) => (
          <button
            key={demoItem.question}
            onClick={() => setIndex(i)}
            aria-label={`Show example ${i + 1}`}
            className={`h-1.5 rounded-full transition-all ${
              i === index ? "w-6 bg-brand" : "w-1.5 bg-line hover:bg-muted"
            }`}
          />
        ))}
      </div>
    </div>
  );
}
