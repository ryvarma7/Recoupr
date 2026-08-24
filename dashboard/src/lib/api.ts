import type {
  AuditLogResponse,
  CaseDetail,
  CaseSummary,
  ExceptionsResponse,
  GuardrailLogEntry,
  MetricsSummary,
  PolicyResponse,
} from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`GET ${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: body !== undefined ? { "content-type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`POST ${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

export const api = {
  health: () => get<{ status: string }>("/health"),
  metrics: () => get<MetricsSummary>("/metrics/summary"),
  cases: (params = "") => get<{ total: number; cases: CaseSummary[] }>(`/cases${params}`),
  caseDetail: (id: number) => get<CaseDetail>(`/cases/${id}`),
  exceptions: () => get<ExceptionsResponse>("/exceptions"),
  guardrailLog: () => get<{ total: number; checks: GuardrailLogEntry[] }>("/guardrail-log?limit=400"),
  auditLog: () => get<AuditLogResponse>("/audit-log?limit=400"),
  policy: () => get<PolicyResponse>("/policy"),
  approve: (caseId: number) =>
    post<{ status: string; case_ref: string; state: string }>(`/cases/${caseId}/approve`, {}),
  runBatch: (count: number, seed: number | null) =>
    post<Record<string, unknown>>("/simulate/batch", { count, seed }),
};

export { API_BASE };
