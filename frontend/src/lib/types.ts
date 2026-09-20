/** Shapes returned by the NEXA API (mirrors backend/app/schemas). */

export type Role = "owner" | "admin" | "member" | "viewer";

export type User = {
  id: string;
  email: string;
  role: Role;
  is_active: boolean;
  created_at: string;
};

export type Tenant = { id: string; name: string; slug: string };

export type Collection = {
  id: string;
  name: string;
  description: string | null;
  visibility: "tenant_wide" | "restricted";
  created_at: string;
};

export type DocumentStatus = "pending" | "processing" | "ready" | "failed";

export type Document = {
  id: string;
  collection_id: string;
  title: string;
  filename: string;
  mime_type: string;
  status: DocumentStatus;
  error_message: string | null;
  page_count: number | null;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type IngestionJob = {
  id: string;
  status: "queued" | "running" | "done" | "failed";
  attempts: number;
  chunks_created: number | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
};

export type DocumentDetail = Document & {
  chunk_count: number;
  ingestion: IngestionJob | null;
};

export type UploadResult = {
  filename: string;
  status: "created" | "duplicate" | "retried" | "rejected";
  document: Document | null;
  detail: string | null;
};

export type Citation = {
  number: number;
  chunk_id: string | null;
  document_id: string | null;
  document_title: string | null;
  filename: string | null;
  page_start: number | null;
  section_title?: string | null;
  snippet: string | null;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  model: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  latency_ms: Record<string, number> | null;
  feedback?: number | null;
  created_at: string;
  citations: Citation[];
};

export type Conversation = { id: string; title: string | null; created_at: string };
export type ConversationDetail = Conversation & { messages: ChatMessage[] };

export type SearchHit = {
  rank: number;
  score: number;
  similarity: number | null;
  keyword_score: number | null;
  rerank_score: number | null;
  chunk_id: string;
  document_id: string;
  document_title: string;
  filename: string;
  page_start: number | null;
  page_end: number | null;
  section_title: string | null;
  content: string;
};

export type Group = { id: string; name: string; description: string | null; created_at: string };
export type GroupDetail = Group & { member_ids: string[] };
export type AccessGrant = { group_id: string; permission: "read" | "write" };

export type UsageDay = {
  date: string;
  questions: number;
  tokens: number;
  cost_usd: number;
  by_type: Record<string, number>;
};

export type UsageReport = {
  days: UsageDay[];
  totals: {
    questions: number;
    tokens: number;
    cost_usd: number;
    documents: number;
    chunks: number;
    users: number;
  };
};

export type TenantSettings = {
  name: string;
  assistant_name: string | null;
  tone: string | null;
  model: string | null;
};
