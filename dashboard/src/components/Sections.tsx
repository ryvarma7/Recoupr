"use client";

import { ACTION_LABELS, istTime } from "@/lib/format";
import type { AuditEntry, CaseSummary, GuardrailLogEntry, MetricsSummary } from "@/lib/types";
import { StateTag } from "./ui";

/** 03 — Exceptions queue: everything that needs a human or explains itself. */
export function Exceptions({
  items,
  onOpen,
}: {
  items: Array<CaseSummary & { state_tag?: string; terminal_reason?: string | null }>;
  onOpen: (c: CaseSummary) => void;
}) {
  if (items.length === 0) {
    return <p className="font-mono text-[12.5px] text-faint">Queue empty — nothing waiting on a human.</p>;
  }
  return (
    <ul className="grid grid-cols-1 gap-[2px] md:grid-cols-2">
      {items.slice(0, 24).map((c) => (
        <li key={c.id}>
          <button
            type="button"
            onClick={() => onOpen(c)}
            className="flex w-full flex-col items-start gap-1.5 border border-hairline bg-paper-raise p-3.5 text-left transition-colors hover:border-hairline-strong"
          >
            <span className="flex w-full items-center gap-2.5">
              <span className="font-mono text-[13px] font-medium">{c.display_ref}</span>
              <StateTag state={c.state} />
              <span className="ml-auto font-mono text-[11.5px] tabular-nums text-muted">
                ₹{(c.amount_paise / 100).toLocaleString("en-IN")}
              </span>
            </span>
            <span className="line-clamp-2 text-[12.5px] leading-snug text-muted">
              {c.terminal_reason ?? "awaiting human review"}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}

/** 04 — Guardrail activity: the gate doing its job. Violations must read zero. */
export function GuardrailSection({
  checks,
  metrics,
}: {
  checks: GuardrailLogEntry[];
  metrics: MetricsSummary;
}) {
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap gap-[2px]">
        <GateStat label="checks logged" value={String(checks.length)} />
        <GateStat label="blocked" value={String(metrics.guardrail_blocks)} tone="rust" />
        <GateStat
          label="violations"
          value={String(metrics.guardrail_violations)}
          tone={metrics.guardrail_violations === 0 ? "green" : "brick"}
        />
        <p className="w-full pt-1 font-mono text-[11.5px] leading-snug text-muted">
          A violation is an executed action without a passing check — the invariant that must
          stay zero. Blocks are the gate working as designed.
        </p>
      </div>

      <div className="max-h-[420px] overflow-y-auto border border-hairline slim-scroll">
        <table className="w-full border-collapse text-[13px]">
          <thead className="sticky top-0 bg-paper-sink/95 backdrop-blur-sm">
            <tr className="text-left text-[11px] uppercase tracking-wide text-muted">
              <th className="px-3 py-2 font-medium">time</th>
              <th className="px-3 py-2 font-medium">case</th>
              <th className="px-3 py-2 font-medium">proposal</th>
              <th className="px-3 py-2 font-medium">verdict</th>
            </tr>
          </thead>
          <tbody>
            {checks.slice(0, 120).map((c) => (
              <tr key={c.id} className="rule-t align-top">
                <td className="whitespace-nowrap px-3 py-2 font-mono text-[11.5px] text-muted">
                  {istTime(c.created_at)}
                </td>
                <td className="px-3 py-2 font-mono text-[12px]">{c.case_ref}</td>
                <td className="px-3 py-2 font-mono text-[12px] text-muted">
                  {(ACTION_LABELS[c.proposed_action] ?? c.proposed_action).split(" ")[0]}
                </td>
                <td className="px-3 py-2">
                  {c.passed ? (
                    <span className="font-mono text-[11px] uppercase text-text-green">pass</span>
                  ) : (
                    <span className="flex flex-wrap gap-1">
                      {c.violated_rules.map((r) => (
                        <span
                          key={r}
                          className="border border-hairline-strong px-1 py-px font-mono text-[10.5px] text-text-rust"
                        >
                          {r}
                        </span>
                      ))}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function GateStat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  const color =
    tone === "rust" ? "text-text-rust" : tone === "green" ? "text-text-green" : tone === "brick" ? "text-text-brick" : "";
  return (
    <div className="flex min-w-[140px] flex-col gap-0.5 border border-hairline bg-paper-raise px-4 py-3">
      <span className="text-[11.5px] text-muted">{label}</span>
      <span className={`font-mono text-[22px] font-medium leading-tight ${color}`}>{value}</span>
    </div>
  );
}

/** 05 — Append-only audit stream, teletype style. */
export function AuditStream({ entries }: { entries: AuditEntry[] }) {
  return (
    <div className="max-h-[460px] overflow-y-auto border border-hairline bg-paper-sink/40 px-4 py-3 slim-scroll">
      <ol className="flex flex-col divide-y divide-hairline/70">
        {entries.slice(0, 150).map((e, i) => (
          <li key={i} className="py-1.5 font-mono text-[12px] leading-snug">
            <span className="text-faint">{istTime(e.timestamp)}</span>{" "}
            <span className={`uppercase ${e.actor === "human" ? "text-ink" : "text-muted"}`}>
              {e.actor.padEnd(6, " ").slice(0, 6)}
            </span>{" "}
            <span>{e.summary}</span>{" "}
            <span className="text-faint">[{e.case_ref}]</span>
          </li>
        ))}
      </ol>
    </div>
  );
}
