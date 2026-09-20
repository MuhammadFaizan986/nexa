"use client";

/**
 * The "show me where that came from" panel.
 *
 * This is the feature that turns a chatbot into something a business can trust:
 * every claim points at a document, a page and the exact sentence, and the
 * original file is one click away.
 */

import { ExternalLink, FileText, X } from "lucide-react";
import { useState } from "react";

import { Button, useToast } from "@/components/ui";
import { documentFileUrl } from "@/lib/api";
import type { Citation } from "@/lib/types";

export function CitationPanel({
  citation,
  onClose,
}: {
  citation: Citation;
  onClose: () => void;
}) {
  const [opening, setOpening] = useState(false);
  const notify = useToast();

  const where = [
    citation.page_start ? `page ${citation.page_start}` : null,
    citation.section_title ?? null,
  ]
    .filter(Boolean)
    .join(" · ");

  async function openOriginal() {
    if (!citation.document_id) return;
    setOpening(true);
    try {
      window.open(await documentFileUrl(citation.document_id), "_blank");
    } catch {
      notify("Could not open that file", "bad");
    } finally {
      setOpening(false);
    }
  }

  return (
    <aside className="flex w-80 shrink-0 flex-col border-l border-line bg-surface animate-slide-in">
      <header className="flex items-start justify-between gap-2 border-b border-line px-4 py-3.5">
        <div className="min-w-0">
          <span className="inline-flex items-center gap-1.5 rounded-full bg-brand-soft px-2 py-0.5 text-[11px] font-medium text-brand">
            Source {citation.number}
          </span>
          <h2 className="mt-2 truncate text-sm font-semibold" title={citation.document_title ?? ""}>
            {citation.document_title}
          </h2>
          {where && <p className="text-xs text-muted">{where}</p>}
        </div>
        <button
          onClick={onClose}
          className="rounded-md p-1 text-muted transition hover:bg-canvas hover:text-ink"
          aria-label="Close"
        >
          <X size={16} />
        </button>
      </header>

      <div className="flex-1 overflow-y-auto px-4 py-4">
        <p className="text-xs uppercase tracking-wide text-muted">Quoted text</p>
        {/* The snippet is the sentence of the passage that supports the claim. */}
        <blockquote className="mt-2 rounded-lg border-l-2 border-brand bg-brand-soft/50 p-3 text-sm leading-relaxed">
          {citation.snippet}
        </blockquote>
        <p className="mt-3 flex items-center gap-1.5 text-xs text-muted">
          <FileText size={13} />
          {citation.filename}
        </p>
      </div>

      {citation.document_id && (
        <div className="border-t border-line p-3">
          <Button
            variant="secondary"
            className="w-full"
            icon={ExternalLink}
            loading={opening}
            onClick={openOriginal}
          >
            Open the original
          </Button>
        </div>
      )}
    </aside>
  );
}
