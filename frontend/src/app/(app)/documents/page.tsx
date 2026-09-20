"use client";

/**
 * Documents: upload files, watch them being processed, and manage them.
 *
 * Uploading returns immediately (a worker does the work), so the list refreshes
 * itself every few seconds while anything is still queued or processing — and
 * stops as soon as everything is ready, so no timer runs for nothing.
 */

import {
  Download,
  FileSpreadsheet,
  FileText,
  FileType,
  RefreshCw,
  Trash2,
  UploadCloud,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  Button,
  Card,
  Empty,
  ErrorNote,
  Select,
  Skeleton,
  StatusBadge,
  formatDate,
  useToast,
} from "@/components/ui";
import { ApiError, documentFileUrl, request } from "@/lib/api";
import type { Collection, Document, DocumentStatus, UploadResult } from "@/lib/types";

const ACCEPTED = ".pdf,.docx,.md,.markdown,.txt,.html,.htm,.csv";

function FileIcon({ filename }: { filename: string }) {
  const extension = filename.split(".").pop()?.toLowerCase();
  const Icon = extension === "csv" ? FileSpreadsheet : extension === "pdf" ? FileType : FileText;
  return (
    <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-canvas text-muted">
      <Icon size={16} />
    </span>
  );
}

export default function DocumentsPage() {
  const [documents, setDocuments] = useState<Document[] | null>(null);
  const [collections, setCollections] = useState<Collection[]>([]);
  const [collectionId, setCollectionId] = useState("");
  const [statusFilter, setStatusFilter] = useState<"" | DocumentStatus>("");
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const notify = useToast();

  const load = useCallback(async () => {
    try {
      const query = new URLSearchParams();
      if (statusFilter) query.set("status", statusFilter);
      const [docs, cols] = await Promise.all([
        request<Document[]>(`/documents?${query}`),
        request<Collection[]>("/collections"),
      ]);
      setDocuments(docs);
      setCollections(cols);
      setCollectionId((current) => current || cols[0]?.id || "");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load documents");
    }
  }, [statusFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  // Refresh only while the worker still has something to do.
  const busy = useMemo(
    () => (documents ?? []).some((d) => d.status === "pending" || d.status === "processing"),
    [documents],
  );
  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(() => void load(), 3000);
    return () => clearInterval(timer);
  }, [busy, load]);

  async function upload(files: FileList | File[]) {
    const list = Array.from(files);
    if (!collectionId || list.length === 0) return;
    setUploading(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("collection_id", collectionId);
      for (const file of list) form.append("files", file);
      const results = await request<UploadResult[]>("/documents", { method: "POST", form });
      const created = results.filter((r) => r.status === "created" || r.status === "retried");
      const rejected = results.filter((r) => r.status === "rejected");
      if (created.length) notify(`${created.length} file(s) queued for processing`);
      for (const result of rejected) notify(`${result.filename}: ${result.detail}`, "bad");
      if (!created.length && !rejected.length) notify("Already uploaded — nothing to do");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Upload failed");
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  async function act(document: Document, action: "reindex" | "delete" | "download") {
    setError(null);
    try {
      if (action === "reindex") {
        await request(`/documents/${document.id}/reindex`, { method: "POST" });
        notify(`Re-processing “${document.title}”`);
      }
      if (action === "delete") {
        await request(`/documents/${document.id}`, { method: "DELETE" });
        notify(`Deleted “${document.title}”`);
      }
      if (action === "download") window.open(await documentFileUrl(document.id), "_blank");
      if (action !== "download") await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That didn't work");
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-5 p-8">
      <header className="animate-fade-up">
        <h1 className="text-2xl font-semibold tracking-tight">Documents</h1>
        <p className="mt-1 text-sm text-muted">
          PDF, Word, Markdown, text, HTML and spreadsheets. Files are processed in the background;
          the status updates itself.
        </p>
      </header>

      <Card className="animate-fade-up">
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-sm">
            <span className="mb-1.5 block font-medium">Upload into</span>
            <Select value={collectionId} onChange={(e) => setCollectionId(e.target.value)}>
              {collections.map((collection) => (
                <option key={collection.id} value={collection.id}>
                  {collection.name}
                  {collection.visibility === "restricted" ? " (restricted)" : ""}
                </option>
              ))}
            </Select>
          </label>
        </div>

        <div
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            void upload(event.dataTransfer.files);
          }}
          onClick={() => fileInput.current?.click()}
          className={`mt-4 cursor-pointer rounded-xl border-2 border-dashed px-6 py-12 text-center transition ${
            dragging
              ? "scale-[1.01] border-brand bg-brand-soft"
              : "border-line hover:border-brand hover:bg-canvas"
          }`}
        >
          <UploadCloud
            size={26}
            className={`mx-auto transition ${dragging ? "text-brand" : "text-muted"} ${
              uploading ? "animate-pulse-soft" : ""
            }`}
          />
          <p className="mt-3 text-sm font-medium">
            {uploading ? "Uploading…" : "Drop files here, or click to choose"}
          </p>
          <p className="mt-1 text-xs text-muted">Several at once is fine</p>
          <input
            ref={fileInput}
            type="file"
            multiple
            accept={ACCEPTED}
            className="hidden"
            onChange={(event) => event.target.files && void upload(event.target.files)}
          />
        </div>

        <div className="mt-3">
          <ErrorNote>{error}</ErrorNote>
        </div>
      </Card>

      <Card
        className="animate-fade-up"
        title={documents ? `${documents.length} document${documents.length === 1 ? "" : "s"}` : "Documents"}
        description={busy ? "Some files are still being processed…" : undefined}
        action={
          <Select
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value as DocumentStatus | "")}
          >
            <option value="">All statuses</option>
            <option value="ready">Ready</option>
            <option value="pending">Queued</option>
            <option value="processing">Processing</option>
            <option value="failed">Failed</option>
          </Select>
        }
      >
        {documents === null ? (
          <div className="space-y-2">
            <Skeleton className="h-14 w-full" />
            <Skeleton className="h-14 w-full" />
            <Skeleton className="h-14 w-full" />
          </div>
        ) : documents.length === 0 ? (
          <Empty icon={UploadCloud} title="No documents yet">
            Upload a file above and it becomes searchable within seconds.
          </Empty>
        ) : (
          <ul className="divide-y divide-line">
            {documents.map((document) => (
              <li
                key={document.id}
                className="group flex items-start gap-3 py-3 transition first:pt-0 last:pb-0"
              >
                <FileIcon filename={document.filename} />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{document.title}</p>
                  <p className="truncate text-xs text-muted">
                    {document.filename}
                    {document.page_count ? ` · ${document.page_count} pages` : ""} ·{" "}
                    {formatDate(document.created_at)}
                  </p>
                  {document.error_message && (
                    <p className="mt-1 text-xs text-bad">{document.error_message}</p>
                  )}
                </div>
                <StatusBadge status={document.status} />
                <div className="flex gap-1 opacity-0 transition group-hover:opacity-100 focus-within:opacity-100">
                  <Button
                    size="sm"
                    variant="ghost"
                    icon={Download}
                    onClick={() => act(document, "download")}
                    aria-label="Open the original"
                  />
                  <Button
                    size="sm"
                    variant="ghost"
                    icon={RefreshCw}
                    onClick={() => act(document, "reindex")}
                    aria-label="Re-index"
                  />
                  <Button
                    size="sm"
                    variant="ghost"
                    icon={Trash2}
                    className="text-bad"
                    onClick={() =>
                      confirm(`Delete “${document.title}” and everything indexed from it?`) &&
                      act(document, "delete")
                    }
                    aria-label="Delete"
                  />
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
