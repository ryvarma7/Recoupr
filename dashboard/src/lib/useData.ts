"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import type {
  AuditLogResponse,
  CaseSummary,
  ExceptionsResponse,
  GuardrailLogEntry,
  MetricsSummary,
} from "./types";

export interface DashboardData {
  metrics: MetricsSummary | null;
  cases: CaseSummary[];
  exceptions: ExceptionsResponse | null;
  guardrail: GuardrailLogEntry[];
  audit: AuditLogResponse["entries"];
  healthy: boolean;
  updatedAt: Date | null;
}

const POLL_MS = 10_000;

/** One shared fetch cycle for every section; polls only while visible. */
export function useDashboard(): DashboardData & { refresh: () => void } {
  const [data, setData] = useState<DashboardData>({
    metrics: null,
    cases: [],
    exceptions: null,
    guardrail: [],
    audit: [],
    healthy: false,
    updatedAt: null,
  });
  const busy = useRef(false);

  const load = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    try {
      await api.health();
      // Parallel after health passes; individual failures degrade gracefully.
      const [metrics, casesRes, exceptions, guardrailRes, auditRes] = await Promise.all([
        api.metrics(),
        api.cases("?limit=500"),
        api.exceptions(),
        api.guardrailLog(),
        api.auditLog(),
      ]);
      setData({
        metrics,
        cases: casesRes.cases,
        exceptions,
        guardrail: guardrailRes.checks,
        audit: auditRes.entries,
        healthy: true,
        updatedAt: new Date(),
      });
    } catch {
      setData((prev) => ({ ...prev, healthy: false }));
    } finally {
      busy.current = false;
    }
  }, []);

  useEffect(() => {
    void load();
    const id = setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, POLL_MS);
    return () => clearInterval(id);
  }, [load]);

  return { ...data, refresh: load };
}
