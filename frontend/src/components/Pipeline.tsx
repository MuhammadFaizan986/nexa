"use client";

/**
 * The animated "how an answer gets made" diagram.
 *
 * Why this exists: RAG is six steps, and a visitor who doesn't know the words
 * "chunking" or "reranking" will not read a paragraph that explains them. So
 * the diagram walks the six stages one at a time — the working stage lights up,
 * the dashes between stages flow in the direction the data moves, and a caption
 * underneath says in plain language what just happened, with a real example
 * from the demo documents.
 *
 * Three behaviours worth knowing:
 *  - It advances itself, so a first-time visitor sees the whole story without
 *    touching anything.
 *  - Hovering (or tabbing to) a stage pauses the walk and holds that stage, so
 *    someone who wants to read stage 4 isn't fighting a timer. Leaving resumes.
 *  - If the visitor asked their system for reduced motion, nothing moves: every
 *    stage is drawn in its finished state and the caption follows the keyboard
 *    or mouse instead of a clock.
 */

import {
  ArrowDownWideNarrow,
  Binary,
  FileStack,
  MessageSquareQuote,
  Scissors,
  Search,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useState } from "react";

type Stage = {
  Icon: LucideIcon;
  /** The word the industry uses — visitors will meet it again elsewhere. */
  title: string;
  /** What it does, in four or five words, printed on the card. */
  subtitle: string;
  /** Who does the work: our own code, or a named third-party model. */
  by: string;
  /** The caption: the same idea explained without jargon. */
  caption: string;
  /** A concrete example from the demo documents, so it stops being abstract. */
  example: string;
};

const STAGES: Stage[] = [
  {
    Icon: FileStack,
    title: "Documents",
    subtitle: "PDF, Word, sheets",
    by: "Your files",
    caption: "You upload the documents you already have — no reformatting, no data entry.",
    example: "A 14-page lease, a payments error-code manual, an employee handbook.",
  },
  {
    Icon: Scissors,
    title: "Chunking",
    subtitle: "Split into passages",
    by: "NEXA",
    caption:
      "Each file is cut into passages small enough to read closely, overlapping slightly so a sentence is never split down the middle. Every passage remembers its page and section.",
    example: "The lease becomes 62 passages; one of them is “7. Term and Termination”, page 3.",
  },
  {
    Icon: Binary,
    title: "Embedding",
    subtitle: "Text → numbers",
    by: "Gemini",
    caption:
      "Every passage is turned into a list of numbers that captures its meaning. Passages about the same idea end up close together, even when they share no words.",
    example: "“ending the tenancy” lands next to “termination of the lease”.",
  },
  {
    Icon: Search,
    title: "Retrieval",
    subtitle: "Meaning + exact terms",
    by: "Postgres + pgvector",
    caption:
      "Your question is searched two ways at once: by meaning, and by exact wording for things like codes and IDs. The two rankings are then fused into one shortlist.",
    example: "Meaning finds the termination clause; exact wording is what nails “E-204”.",
  },
  {
    Icon: ArrowDownWideNarrow,
    title: "Reranking",
    subtitle: "Re-score the shortlist",
    by: "Cohere Rerank",
    caption:
      "A second, slower model reads your question against each shortlisted passage and re-scores them. It is the step that moves the right passage from fifth place to first.",
    example: "Hit@1 rose from 70% to 92% when this stage was added — measured, not assumed.",
  },
  {
    Icon: MessageSquareQuote,
    title: "Answer",
    subtitle: "Written with citations",
    by: "Claude Opus 5",
    caption:
      "Claude writes the answer using only those passages, citing each one. If they don't cover your question, it says so instead of guessing — and the model isn't even called.",
    example: "“The tenant must give 60 days written notice” [1] — click [1], read the sentence.",
  },
];

const STEP_MS = 2600;

export function Pipeline() {
  const [active, setActive] = useState(0);
  const [paused, setPaused] = useState(false);
  const [still, setStill] = useState(false); // the visitor asked for less motion

  // Ask the operating system once, and keep listening: someone can change the
  // setting while the page is open.
  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const sync = () => setStill(query.matches);
    sync();
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  }, []);

  // Walk the stages. Pausing or reduced motion simply means no timer runs —
  // there is nothing to unwind, which is why this stays a two-line effect.
  useEffect(() => {
    if (paused || still) return;
    const timer = setTimeout(() => setActive((current) => (current + 1) % STAGES.length), STEP_MS);
    return () => clearTimeout(timer);
  }, [active, paused, still]);

  const stage = STAGES[active];

  return (
    <div className="animate-fade-up">
      {/* The rail. It scrolls sideways on a narrow screen rather than wrapping,
          because a pipeline that wraps stops reading as a sequence. */}
      <div className="-mx-5 overflow-x-auto px-5 pb-2">
        <ol className="mx-auto flex min-w-[52rem] max-w-5xl items-stretch">
          {STAGES.map((item, index) => (
            <li key={item.title} className="flex flex-1 items-stretch">
              <StageCard
                stage={item}
                index={index}
                state={index === active ? "live" : index < active ? "done" : "next"}
                onHold={() => {
                  setActive(index);
                  setPaused(true);
                }}
                onRelease={() => setPaused(false)}
              />
              {index < STAGES.length - 1 && (
                <span
                  aria-hidden
                  className={`conn mx-2 h-0.5 min-w-8 flex-1 self-center ${
                    still ? "conn-done" : index === active ? "conn-live" : index < active ? "conn-done" : ""
                  }`}
                />
              )}
            </li>
          ))}
        </ol>
      </div>

      {/* The caption. A fixed minimum height keeps the section from jumping as
          longer and shorter explanations replace each other. aria-live means a
          screen reader hears each stage as it becomes active. */}
      <div
        aria-live="polite"
        className="mx-auto mt-7 min-h-28 max-w-2xl rounded-xl border border-line bg-surface p-5 text-center shadow-soft"
      >
        <p key={`caption-${active}`} className="animate-fade-in text-sm leading-relaxed">
          <span className="font-semibold">{stage.title}: </span>
          {stage.caption}
        </p>
        <p key={`example-${active}`} className="animate-fade-in mt-2 text-xs italic text-muted">
          {stage.example}
        </p>
      </div>

      {/* Progress pips: where we are in the six steps, and a way to jump. */}
      <div className="mt-4 flex justify-center gap-1.5">
        {STAGES.map((item, index) => (
          <button
            key={item.title}
            onClick={() => {
              setActive(index);
              setPaused(true);
            }}
            aria-label={`Show step ${index + 1}: ${item.title}`}
            className={`h-1.5 rounded-full transition-all ${
              index === active ? "w-6 bg-brand" : "w-1.5 bg-line hover:bg-muted"
            }`}
          />
        ))}
      </div>
    </div>
  );
}

function StageCard({
  stage,
  index,
  state,
  onHold,
  onRelease,
}: {
  stage: Stage;
  index: number;
  state: "live" | "done" | "next";
  onHold: () => void;
  onRelease: () => void;
}) {
  const { Icon } = stage;

  return (
    <button
      type="button"
      onMouseEnter={onHold}
      onMouseLeave={onRelease}
      onFocus={onHold}
      onBlur={onRelease}
      onClick={onHold}
      aria-current={state === "live" ? "step" : undefined}
      className={`flex h-full w-32 shrink-0 flex-col rounded-xl border bg-surface p-3 text-left transition-all duration-300 ${
        state === "live"
          ? "stage-live -translate-y-1"
          : "border-line shadow-soft hover:-translate-y-0.5 hover:border-brand"
      } ${state === "next" ? "opacity-70" : ""}`}
    >
      <div className="flex items-center justify-between">
        <span
          className={`grid size-8 place-items-center rounded-lg transition-colors ${
            state === "live" ? "bg-brand text-brand-ink" : "bg-brand-soft text-brand"
          }`}
        >
          <Icon size={16} />
        </span>
        <span className="text-[10px] font-semibold tabular-nums text-muted">
          {String(index + 1).padStart(2, "0")}
        </span>
      </div>
      <p className="mt-2.5 text-sm font-semibold leading-tight">{stage.title}</p>
      <p className="mt-0.5 text-[11px] leading-snug text-muted">{stage.subtitle}</p>
      <p
        className={`mt-auto truncate pt-2 text-[10px] transition-colors ${
          state === "live" ? "text-brand" : "text-muted"
        }`}
        title={stage.by}
      >
        {stage.by}
      </p>
    </button>
  );
}
