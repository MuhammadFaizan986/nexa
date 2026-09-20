"use client";

/**
 * The "show me where that came from" panel.
 *
 * This is the feature that turns a chatbot into something a business can trust:
 * every claim points at a document, a page and the exact sentence, and the
 * original file is one click away.
 */

import { documentFileUrl } from "@/lib/api";
import type { Citation } from "@/lib/types";
import { Button } from "@/components/ui";

export function CitationPanel({
  citation,
  onClose,
}: {
  citation: Citation;
  onClose: () => void;
}) {
  const where = [
    citation.page_start ? `page ${citation.page_start}` : null,
    citation.section_title ?? null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <aside className="flex w-80 shrink-0 flex-col border-l border-line bg-surface">
      <header className="flex items-start justify-between gap-2 border-b border-line px-4 py-3">
        <div>
          <p className="text-xs uppercase tracking-wide text-muted">Source [{citation.number}]</p>
          <h2 className="text-sm font-semibold">{citation.document_title}</h2>
          {where && <p className="text-xs text-muted">{where}</p>}
        </div>
        <button onClick={onClose} className="text-muted hover:text-ink" aria-label="Close">
          ✕
        </button>
      </header>

      <div className="flex-1 overflow-y-auto px-4 py-3">
        <p className="text-xs uppercase tracking-wide text-muted">Quoted text</p>
        {/* The snippet is the sentence of the passage that supports the claim. */}
        <blockquote className="mt-2 border-l-2 border-brand bg-brand-soft/40 p-3 text-sm leading-relaxed">
          {citation.snippet}
        </blockquote>
        <p className="mt-3 text-xs text-muted">{citation.filename}</p>
      </div>

      {citation.document_id && (
        <div className="border-t border-line p-3">
          <Button
            variant="ghost"
            className="w-full"
            onClick={async () =>
              window.open(await documentFileUrl(citation.document_id as string), "_blank")
            }
          >
            Open the original document
          </Button>
        </div>
      )}
    </aside>
  );
}
