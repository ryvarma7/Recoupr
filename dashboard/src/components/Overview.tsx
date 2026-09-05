"use client";

import { useMemo, useState } from "react";
import type { MetricsSummary } from "@/lib/types";
import { hours, pct, rupees } from "@/lib/format";
import { OutcomeStrip, Sparkline, type StripSegment } from "./charts";
import { StatCell, StatStrip } from "./ui";

/**
 * 01 — Overview.
 * Hero figure (recovery rate) exactly one per view; supporting stat tiles;
 * 12-day intake sparkline; outcome distribution strip.
 */
export function Overview({
  metrics,
  cases,
}: {
  metrics: MetricsSummary;
  cases: Array<Pick<import("@/lib/types").CaseSummary, "created_at" | "amount_paise" | "state">>;
}) {
  // Last 12 UTC days of case creation, derived client-side from the feed.
  const days = useMemoDays(cases);
  const cleanRate = metrics.recovery_rate;
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const filteredAtRisk = cases.filter((c) => (!from || c.created_at >= from) && (!to || c.created_at < `${to}T23:59:59`))
    .reduce((sum, c) => sum + (c.state === "RECOVERED" ? 0 : c.amount_paise), 0);

  const segments: StripSegment[] = [
    { key: "recovered", label: "recovered", color: "#177a53", textColor: "green", value: metrics.recovered },
    { key: "pending", label: "open", color: "#c9cbc0", value: metrics.pending },
    { key: "escalated", label: "escalated", color: "#b0752f", textColor: "rust", value: metrics.escalated },
    { key: "stopped", label: "stopped", color: "#55645c", value: metrics.stopped_unrecoverable },
    { key: "lost", label: "lost", color: "#8c2e26", textColor: "brick", value: metrics.lost },
  ];

  return (
    <div className="flex flex-col gap-6">
      {/* Hero cell carries the larger figure; exactly one hero per view. */}
      <StatStrip>
        <StatCell
          hero
          label="Recovery rate"
          value={pct(cleanRate)}
          sub={`${metrics.recovered} recovered ÷ ${metrics.recovered + metrics.lost} resolved`}
        />
        <StatCell label="₹ at risk detected" value={rupees(from || to ? filteredAtRisk : metrics.at_risk_paise, { compact: true })} sub="unrecovered case value" />
        <StatCell
          label="Money recovered"
          value={rupees(metrics.total_recovered_paise, { compact: true })}
          sub={`mean ${hours(metrics.mean_time_to_recovery_hours)} to recovery`}
        />
        <StatCell
          label="Escalation load"
          value={`${metrics.escalated_pct}%`}
          sub={`${metrics.escalated} cases sent to a human`}
        />
        <StatCell
          label="False-positive rate"
          value={pct(metrics.false_positive_rate)}
          sub="acted-on cases that were unrecoverable"
        />
      </StatStrip>

      <div className="flex flex-wrap items-center gap-2 font-mono text-[11.5px] text-muted">
        <span>date range</span>
        <input aria-label="from date" type="date" value={from} onChange={(e) => setFrom(e.target.value)} className="border border-hairline bg-paper px-2 py-1" />
        <span>to</span>
        <input aria-label="to date" type="date" value={to} onChange={(e) => setTo(e.target.value)} className="border border-hairline bg-paper px-2 py-1" />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
        <div className="border border-hairline bg-paper-raise p-4 lg:col-span-3">
          <div className="mb-3 flex items-baseline justify-between">
            <span className="text-[12px] text-muted">
              Cases opened per day <span className="text-faint">— last 12 days</span>
            </span>
            <span className="font-mono text-[11.5px] text-faint">
              peak {Math.max(...days.values, 0)}
            </span>
          </div>
          <Sparkline points={days.values} labels={days.labels} />
        </div>

        <div className="flex flex-col gap-4 border border-hairline bg-paper-raise p-4 lg:col-span-2">
          <span className="text-[12px] text-muted">Outcome distribution</span>
          <OutcomeStrip segments={segments} />
          <div className="rule-t mt-auto pt-3 text-[11.5px] leading-relaxed text-muted">
            Recovery rate counts only settled cases — open cases stay out of both sides.
            Stopped means the agent declined to act (customer cancelled, unrecoverable).
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-[2px] sm:grid-cols-3">
        <MiniTile
          label="Diagnosis engine"
          lines={[
            `rules  ${metrics.diagnosis_method_split.rule}`,
            `llm  ${metrics.diagnosis_method_split.llm}`,
            `fallback  ${metrics.diagnosis_method_split.fallback}`,
          ]}
        />
        <MiniTile
          label="Resolution channel"
          lines={[
            `payment link  ${metrics.resolved_via_payment_link_pct}%`,
            `mandate retry  ${metrics.resolved_via_mandate_retry_pct}%`,
          ]}
        />
        <MiniTile
          label="Gate activity"
          lines={[
            `violations  ${metrics.guardrail_violations}`,
            `blocks  ${metrics.guardrail_blocks}`,
          ]}
        />
      </div>
      {metrics.late_recovery_after_ttl > 0 ? (
        <p className="font-mono text-[11.5px] text-text-rust">{metrics.late_recovery_note}</p>
      ) : null}
    </div>
  );
}

function MiniTile({ label, lines }: { label: string; lines: string[] }) {
  return (
    <div className="border border-hairline bg-paper-raise p-4">
      <span className="text-[12px] text-muted">{label}</span>
      <div className="mt-2 flex flex-col gap-1 font-mono text-[13px]">
        {lines.map((l) => (
          <span key={l} className="flex justify-between gap-4">
            <span className="text-muted">{l.split(/\s{2}/)[0]}</span>
            <span>{l.split(/\s{2}/).slice(1).join(" ")}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

function useMemoDays(
  cases: Array<Pick<import("@/lib/types").CaseSummary, "created_at">>,
): { values: number[]; labels: string[] } {
  return useMemo(() => {
    const days = Array.from({ length: 12 }, (_, i) => {
      const d = new Date(Date.now() - (11 - i) * 86_400_000);
      return { key: d.toISOString().slice(0, 10), label: d.toLocaleDateString("en-IN", { day: "numeric", month: "short" }) };
    });
    const counts = new Map<string, number>(days.map((d) => [d.key, 0]));
    for (const c of cases) {
      const key = c.created_at.slice(0, 10); // naive-UTC convention → UTC day bucket
      if (counts.has(key)) counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return { values: days.map((d) => counts.get(d.key) ?? 0), labels: days.map((d) => d.label) };
  }, [cases]);
}
