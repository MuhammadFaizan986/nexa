/**
 * The only place that talks to the NEXA API.
 *
 * Everything runs in the browser and calls FastAPI directly (CORS is enabled
 * there). Two tokens are involved: a short-lived access token sent with every
 * request, and a refresh token used once the access token expires — `request()`
 * retries transparently when that happens.
 *
 * Tokens live in localStorage, which is simple and standard for a dashboard but
 * readable by any script on the page: if this ever serves untrusted content,
 * move them to httpOnly cookies (a Week 7 hardening task).
 */

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8010/api/v1";

const ACCESS_KEY = "nexa.access_token";
const REFRESH_KEY = "nexa.refresh_token";

export const tokens = {
  access: () => (typeof window === "undefined" ? null : localStorage.getItem(ACCESS_KEY)),
  refresh: () => (typeof window === "undefined" ? null : localStorage.getItem(REFRESH_KEY)),
  save(access: string, refresh: string) {
    localStorage.setItem(ACCESS_KEY, access);
    localStorage.setItem(REFRESH_KEY, refresh);
  },
  clear() {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
  },
};

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function readError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    const detail = body?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map((d) => d.msg ?? String(d)).join(", ");
    return response.statusText;
  } catch {
    return response.statusText;
  }
}

async function refreshTokens(): Promise<boolean> {
  const refresh_token = tokens.refresh();
  if (!refresh_token) return false;
  const response = await fetch(`${API_URL}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token }),
  });
  if (!response.ok) {
    tokens.clear();
    return false;
  }
  const data = await response.json();
  tokens.save(data.access_token, data.refresh_token);
  return true;
}

type RequestOptions = { method?: string; body?: unknown; form?: FormData; retry?: boolean };

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, form, retry = true } = options;
  const headers: Record<string, string> = {};
  const access = tokens.access();
  if (access) headers.Authorization = `Bearer ${access}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";

  const response = await fetch(`${API_URL}${path}`, {
    method,
    headers,
    body: form ?? (body !== undefined ? JSON.stringify(body) : undefined),
  });

  // The access token expires every 15 minutes: refresh once, then try again.
  if (response.status === 401 && retry && (await refreshTokens())) {
    return request<T>(path, { ...options, retry: false });
  }
  if (!response.ok) throw new ApiError(response.status, await readError(response));
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** Streamed chat: Server-Sent Events read straight from the response body. */
export async function streamChat(
  payload: { question: string; conversation_id?: string; collection_ids?: string[] },
  handlers: {
    onMeta?: (data: any) => void;
    onToken?: (text: string) => void;
    onDone?: (data: any) => void;
    onError?: (detail: string) => void;
  },
): Promise<void> {
  const send = async (): Promise<Response> =>
    fetch(`${API_URL}/chat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${tokens.access()}`,
      },
      body: JSON.stringify(payload),
    });

  let response = await send();
  if (response.status === 401 && (await refreshTokens())) response = await send();
  if (!response.ok || !response.body) {
    handlers.onError?.(await readError(response));
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // Events are separated by a blank line: "event: token\ndata: {...}\n\n".
    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";
    for (const block of events) {
      const event = block.match(/^event: (.+)$/m)?.[1];
      const raw = block.match(/^data: (.+)$/m)?.[1];
      if (!event || !raw) continue;
      const data = JSON.parse(raw);
      if (event === "meta") handlers.onMeta?.(data);
      else if (event === "token") handlers.onToken?.(data.text);
      else if (event === "done") handlers.onDone?.(data);
      else if (event === "error") handlers.onError?.(data.detail);
    }
  }
}

/** Download a document's original file (needs the Authorization header). */
export async function documentFileUrl(documentId: string): Promise<string> {
  const response = await fetch(`${API_URL}/documents/${documentId}/file`, {
    headers: { Authorization: `Bearer ${tokens.access()}` },
  });
  if (!response.ok) throw new ApiError(response.status, await readError(response));
  return URL.createObjectURL(await response.blob());
}
