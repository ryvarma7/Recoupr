"use client";

import { useState } from "react";
import { ago, rupees } from "@/lib/format";
import type { CaseSummary } from "@/lib/types";
import { Chip, StateTag } from "./ui";

const FILTERS = [
  { key: "ALL", label: "all" },
  { key: "AWAITING_OUTCOME", label: "open" },
  { key: "RECOVERED", label: "recovered" },
  { key: "LOST", label: "lost" },
  { key: "ESCALATED_TO_HUMAN", label: "escalated" },
  { key: "STOPPED_UNRECOVERABLE", label: "stopped" },
] as const;

/** 02 — Case feed. Filter row above the table; rows open the detail drawer. */
export function CaseFeed({
  cases,
  onOpen,
  selectedId,
}: {
  cases: CaseSummary[];
  onOpen: (c: CaseSummary) => void;
  selectedId: number | null;
}) {
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["key"]>("ALL");
  const [flow, setFlow] = useState("all");

  const shown = cases.filter(
    (c) =>
      (filter === "ALL" || c.state === filter) &&
      (flow === "all" || c.flow_type === flow),
  );

  return (
    <div className="flex flex-col gap-4">
      {/* Filters in one row above the chart/table. */}
      <div className="flex flex-wrap items-center gap-2">
        {FILTERS.map((f) => (
          <Chip key={f.key} active={filter === f.key} onClick={() => setFilter(f.key)}>
            {f.label}
          </Chip>
        ))}
        <span className="mx-1 h-4 w-px bg-hairline-strong" aria-hidden />
        {["all", "A", "B", "C"].map((f) => (
          <Chip key={f} active={flow === f} onClick={() => setFlow(f)}>
            {f === "all" ? "flows" : `flow ${f}`}
          </Chip>
        ))}
        <span className="ml-auto font-mono text-[11.5px] text-faint">
          {shown.length} of {cases.length}
        </span>
      </div>

      <div className="max-h-[520px] overflow-y-auto border border-hairline slim-scroll">
        <table className="w-full border-collapse text-[13px]">
          <thead className="sticky top-0 z-10 bg-paper-sink/95 backdrop-blur-sm">
            <tr className="text-left text-[11px] uppercase tracking-wide text-muted">
              <th className="px-3 py-2 font-medium">ref</th>
              <th className="px-3 py-2 font-medium">flow</th>
              <th className="px-3 py-2 font-medium">state</th>
              <th className="px-3 py-2 text-right font-medium">amount</th>
              <th className="hidden px-3 py-2 text-right font-medium md:table-cell">
                attempts
              </th>
              <th className="hidden px-3 py-2 text-right font-medium lg:table-cell">
                msgs
              </th>
              <th className="px-3 py-2 text-right font-medium">updated</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((c) => (
              <tr
                key={c.id}
                tabIndex={0}
                onClick={() => onOpen(c)}
                onKeyDown={(e) => e.key === "Enter" && onOpen(c)}
                className={`rule-t cursor-pointer transition-colors hover:bg-paper-sink/60 ${
                  selectedId === c.id ? "bg-paper-sink" : ""
                }`}
              >
                <td className="px-3 py-2 font-mono text-[12.5px]">{c.display_ref}</td>
                <td className="px-3 py-2 font-mono text-[12px] text-muted">{c.flow_type}</td>
                <td className="px-3 py-1.5">
                  <StateTag state={c.state} />
                </td>
                <td className="px-3 py-2 text-right font-mono tabular-nums">
                  {rupees(c.amount_paise)}
                </td>
                <td className="hidden px-3 py-2 text-right font-mono tabular-nums md:table-cell">
                  {c.attempts_count}
                </td>
                <td className="hidden px-3 py-2 text-right font-mono tabular-nums lg:table-cell">
                  {c.messages_sent_count}
                </td>
                <td className="px-3 py-2 text-right font-mono text-[12px] text-muted">
                  {ago(c.updated_at) === "now" ? "just now" : `${ago(c.updated_at)} ago`}
                </td>
              </tr>
            ))}
            {shown.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-3 py-10 text-center text-muted">
                  No cases match this filter.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}
