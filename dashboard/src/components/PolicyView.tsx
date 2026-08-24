"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { PolicyResponse } from "@/lib/types";

const KEY_ORDER = [
  "name",
  "id",
  "max_retries_per_case",
  "message_cap_per_case",
  "cooldown_hours",
  "quiet_hours_start",
  "quiet_hours_end",
  "allowed_channels",
  "link_expiry_hours",
  "case_ttl_hours",
];

/** 07 — Guardrail policy: current values + which snapshot versions open cases still carry. */
export function PolicyView() {
  const [policy, setPolicy] = useState<PolicyResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .policy()
      .then(setPolicy)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  if (error) return <p className="font-mono text-[12px] text-text-brick">{error}</p>;
  if (!policy) return <p className="font-mono text-[12.5px] text-faint">loading policy…</p>;

  const entries = Object.entries(policy.current).sort(
    ([a], [b]) => rank(a) - rank(b),
  );

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      <dl className="grid grid-cols-[minmax(160px,max-content)_1fr] border border-hairline bg-paper-raise">
        {entries.map(([k, v], i) => (
          <Row key={k} k={k} v={v} last={i === entries.length - 1} />
        ))}
      </dl>

      <div className="flex flex-col gap-2">
        <span className="text-[11px] uppercase tracking-widest text-faint">
          Snapshot versions carried by open cases
        </span>
        {Object.keys(policy.open_case_snapshots).length === 0 ? (
          <p className="font-mono text-[11.5px] text-faint">no open cases</p>
        ) : (
          <ul className="divide-y divide-hairline border border-hairline bg-paper-raise">
            {Object.entries(policy.open_case_snapshots).map(([key, n]) => (
              <li key={key} className="flex items-center justify-between px-3 py-2 font-mono text-[12.5px]">
                <span>{key}</span>
                <span className="text-muted">{n} case{n === 1 ? "" : "s"}</span>
              </li>
            ))}
          </ul>
        )}
        <p className="text-[12px] leading-snug text-muted">
          Each case freezes its policy at creation — a live policy edit never rewrites rules
          under an in-flight case.
        </p>
      </div>
    </div>
  );
}

function Row({ k, v, last }: { k: string; v: unknown; last: boolean }) {
  return (
    <>
      <dt
        className={`border-b border-hairline px-3 py-2 text-[12px] text-muted ${last ? "border-b-0" : ""}`}
      >
        {k.replace(/_/g, " ")}
      </dt>
      <dd
        className={`border-b border-hairline px-3 py-2 font-mono text-[13px] ${last ? "border-b-0" : ""}`}
      >
        {Array.isArray(v)
          ? v.join(", ")
          : typeof v === "boolean"
            ? v
              ? "yes"
              : "no"
            : String(v)}
      </dd>
    </>
  );
}

function rank(key: string): number {
  const i = KEY_ORDER.indexOf(key);
  return i === -1 ? KEY_ORDER.length : i;
}
