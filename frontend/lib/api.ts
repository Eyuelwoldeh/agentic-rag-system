export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Me = { id: number; email: string; role: "admin" | "member" };

/** fetch() against the backend: sends the session cookie + CSRF header, bounces to /login on 401. */
export async function api(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("x-requested-with", "agentic-rag");
  if (init.body && typeof init.body === "string" && !headers.has("content-type")) headers.set("content-type", "application/json");
  const res = await fetch(`${API}${path}`, { ...init, headers, credentials: "include" });
  if (res.status === 401 && typeof window !== "undefined" && !location.pathname.startsWith("/login")) {
    location.href = "/login";
  }
  return res;
}

export async function apiJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await api(path, init);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail ?? `Request failed (${res.status})`);
  return body as T;
}

/** Chart as emitted by the backend's create_chart tool (flat: type/x/y at top level). */
export type RawChart = { id: string; type: "line" | "bar" | "number" | "table"; title: string; x: string | null; y: string[];
  source: string; sql: string; columns: string[]; rows: unknown[][]; truncated?: boolean };

export type Source = { label: string; kind: "doc" | "web"; source?: string; updated_at?: string; document?: string; page?: number; content?: string; url?: string; title?: string };

export type AgentEvent =
  | { type: "text"; text: string }
  | { type: "thinking"; text: string }
  | { type: "tool"; name: string; input: unknown }
  | { type: "tool_result"; name: string; summary: string; error?: boolean }
  | { type: "sources"; items: Source[] }
  | { type: "critic"; attempt: number; passed: boolean; grounded: boolean; complete: boolean; relevant: boolean; hallucination_risk: string; feedback: string }
  | { type: "revising"; attempt: number }
  | { type: "chart"; chart: RawChart }
  | { type: "error"; message: string }
  | { type: "done"; run_id: number; answer: string; latency_ms: number; attempts: number; verified: boolean; input_tokens: number; output_tokens: number };

/** POST a question and yield server-sent events as they arrive. */
export async function* streamChat(question: string, history: { role: string; content: string }[]): AsyncGenerator<AgentEvent> {
  const res = await api("/api/chat", { method: "POST", body: JSON.stringify({ question, history }) });
  if (!res.ok || !res.body) throw new Error(`Request failed: ${res.status} ${await res.text()}`);
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += value;
    const frames = buf.split("\n\n");
    buf = frames.pop()!;
    for (const f of frames) if (f.startsWith("data: ")) yield JSON.parse(f.slice(6));
  }
}
