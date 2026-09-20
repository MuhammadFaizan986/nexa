"use client";

/**
 * Documents: upload files, watch them being processed, and manage them.
 *
 * Uploading returns immediately (a worker does the work), so the list polls
 * every few seconds while anything is still queued or processing. That polling
 * stops as soon as everything is ready — no timers running for nothing.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError, documentFileUrl, request } from "@/lib/api";
import type { Collection, Document, DocumentStatus, UploadResult } from "@/lib/types";
import { Button, Card, Empty, ErrorNote, Select, StatusBadge, formatDate } from "@/components/ui";

const ACCEPTED = ".pdf,.docx,.md,.markdown,.txt,.html,.htm,.csv";

export default function DocumentsPage() {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [collections, setCollections] = useState<Collection[]>([]);
  const [collectionId, setCollectionId] = useState("");
  const [statusFilter, setStatusFilter] = useState<"" | DocumentStatus>("");
  const [results, setResults] = useState<UploadResult[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

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

  // Poll only while the worker still has something to do.
  const busy = useMemo(
    () => documents.some((d) => d.status === "pending" || d.status === "processing"),
    [documents],
  );
  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(() => void load(), 3000);
    return () => clearInterval(timer);
  }, [busy, load]);

  async function upload(files: FileList | File[]) {
    if (!collectionId || !files.length) return;
    setUploading(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("collection_id", collectionId);
      for (const file of Array.from(files)) form.append("files", file);
      const uploaded = await request<UploadResult[]>("/documents", { method: "POST", form });
      setResults(uploaded);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Upload failed");
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  async function act(documentId: string, action: "reindex" | "delete" | "download") {
    setError(null);
    try {
      if (action === "reindex") await request(`/documents/${documentId}/reindex`, { method: "POST" });
      if (action === "delete") await request(`/documents/${documentId}`, { method: "DELETE" });
      if (action === "download") window.open(await documentFileUrl(documentId), "_blank");
      if (action !== "download") await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That didn't work");
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-5 p-6">
      <header>
        <h1 className="text-xl font-semibold">Documents</h1>
        <p className="text-sm text-muted">
          PDF, Word, Markdown, text, HTML and CSV. Files are processed in the background; the
          status updates itself.
        </p>
      </header>

      <Card>
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-sm">
            <span className="mb-1 block font-medium">Collection</span>
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
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            void upload(e.dataTransfer.files);
          }}
          onClick={() => fileInput.current?.click()}
          className={`mt-4 cursor-pointer rounded-xl border-2 border-dashed px-6 py-10 text-center transition ${
            dragging ? "border-brand bg-brand-soft" : "border-line hover:border-brand"
          }`}
        >
          <p className="text-sm font-medium">
            {uploading ? "Uploading…" : "Drop files here, or click to choose"}
          </p>
          <p className="mt-1 text-xs text-muted">You can select several files at once</p>
          <input
            ref={fileInput}
            type="file"
            multiple
            accept={ACCEPTED}
            className="hidden"
            onChange={(e) => e.target.files && void upload(e.target.files)}
          />
        </div>

        {results.length > 0 && (
          <ul className="mt-3 space-y-1 text-sm">
            {results.map((result) => (
              <li key={result.filename} className="flex gap-2">
                <span className="font-medium">{result.filename}</span>
                <span className="text-muted">
                  {result.status}
                  {result.detail ? ` — ${result.detail}` : ""}
                </span>
              </li>
            ))}
          </ul>
        )}
        <div className="mt-3">
          <ErrorNote>{error}</ErrorNote>
        </div>
      </Card>

      <Card
        title={`${documents.length} document${documents.length === 1 ? "" : "s"}`}
        action={
          <Select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as DocumentStatus | "")}
          >
            <option value="">All statuses</option>
            <option value="ready">Ready</option>
            <option value="pending">Queued</option>
            <option value="processing">Processing</option>
            <option value="failed">Failed</option>
          </Select>
        }
      >
        {documents.length === 0 ? (
          <Empty>Nothing here yet. Upload a file to make it searchable.</Empty>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase tracking-wide text-muted">
              <tr>
                <th className="pb-2 font-medium">Document</th>
                <th className="pb-2 font-medium">Status</th>
                <th className="pb-2 font-medium">Added</th>
                <th className="pb-2" />
              </tr>
            </thead>
            <tbody>
              {documents.map((document) => (
                <tr key={document.id} className="border-t border-line align-top">
                  <td className="py-2 pr-3">
                    <p className="font-medium">{document.title}</p>
                    <p className="text-xs text-muted">
                      {document.filename}
                      {document.page_count ? ` · ${document.page_count} pages` : ""}
                    </p>
                    {document.error_message && (
                      <p className="mt-1 text-xs text-bad">{document.error_message}</p>
                    )}
                  </td>
                  <td className="py-2 pr-3">
                    <StatusBadge status={document.status} />
                  </td>
                  <td className="py-2 pr-3 text-xs text-muted">{formatDate(document.created_at)}</td>
                  <td className="py-2 text-right">
                    <div className="flex justify-end gap-1">
                      <Button variant="ghost" onClick={() => act(document.id, "download")}>
                        Open
                      </Button>
                      <Button variant="ghost" onClick={() => act(document.id, "reindex")}>
                        Re-index
                      </Button>
                      <Button
                        variant="danger"
                        onClick={() =>
                          confirm(`Delete "${document.title}" and everything indexed from it?`) &&
                          act(document.id, "delete")
                        }
                      >
                        Delete
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
