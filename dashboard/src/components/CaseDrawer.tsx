"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { ACTION_LABELS, istTime, rupees } from "@/lib/format";
import type { CaseDetail } from "@/lib/types";
import { StateTag } from "./ui";

/**
 * Case detail drawer — the full agent reasoning chain for one case:
 * diagnosis → decision → gate verdict → action → outcome, plus the
 * append-only audit timeline and the Approve & Send stamp.
 */
export function CaseDrawer({
  caseId,
  onClose,
  onChanged,
}: {
  caseId: number;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [arming, setArming] = useState(false); // two-step confirm before the stamp

  useEffect(() => {
    setArming(false);
    let cancelled = false;
    api
      .caseDetail(caseId)
      .then((d) => !cancelled && setDetail(d))
      .catch((e) => !cancelled && setError(String(e)));
    return () => {
      cancelled = true;
    };
  }, [caseId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const approve = useCallback(async () => {
    if (!detail) return;
    setError(null);
    try {
      await api.approve(detail.id);
      setArming(false);
      setDetail(await api.caseDetail(caseId));
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [detail, caseId, onChanged]);

  return (
    <aside
      aria-label={`Case ${detail?.display_ref ?? caseId}`}
      className="fixed inset-y-0 right-0 z-30 flex w-full max-w-[540px] flex-col border-l border-hairline-strong bg-paper-raise shadow-[-8px_0_24px_rgba(16,36,28,0.10)] drawer-in"
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-4 border-b border-hairline px-5 py-4">
        <div className="min-w-0">
          {detail ? (
            <>
              <div className="flex items-center gap-3">
                <h3 className="font-mono text-[17px] font-medium">{detail.display_ref}</h3>
                <StateTag state={detail.state} />
              </div>
              <p className="mt-1 font-mono text-[12px] text-muted">
                flow {detail.flow_type} · {rupees(detail.amount_paise)} ·{" "}
                {detail.order_id ?? detail.subscription_id ?? "—"}
              </p>
            </>
          ) : (
            <p className="font-mono text-[13px] text-muted">loading…</p>
          )}
          {error ? (
            <p className="mt-1 font-mono text-[11.5px] text-text-brick">{error}</p>
          ) : null}
        </div>
        <button
          type="button"
          onClick={() => {
            if (arming) setArming(false);
            else onClose();
          }}
          aria-label="Close panel"
          className="border border-hairline-strong px-2 py-0.5 font-mono text-[13px] leading-none text-muted transition-colors hover:border-ink hover:text-ink"
        >
          ✕
        </button>
      </div>

      {!detail ? (
        <div className="flex flex-1 items-center justify-center text-muted">…</div>
      ) : (
        <div className="flex-1 overflow-y-auto slim-scroll">
          {/* Approve & Send stamp */}
          {detail.can_approve ? (
            <div className="border-b border-hairline bg-paper px-5 py-4">
              {arming ? (
                <div className="flex items-center justify-between gap-3 fade-up">
                  <span className="text-[13px]">
                    Execute the proposed action as a{" "}
                    <strong className="font-medium">human-approved</strong> recovery?
                  </span>
                  <span className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => setArming(false)}
                      className="border border-hairline-strong px-2.5 py-1 font-mono text-[11.5px] text-muted hover:border-ink hover:text-ink"
                    >
                      cancel
                    </button>
                    <button
                      type="button"
                      onClick={approve}
                      className="border border-text-green bg-mark-green px-2.5 py-1 font-mono text-[11.5px] uppercase tracking-wide text-paper hover:brightness-110"
                    >
                      confirm send
                    </button>
                  </span>
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => setArming(true)}
                  className="w-full border border-dashed border-text-green px-3 py-2.5 text-center font-mono text-[12.5px] uppercase tracking-[0.2em] text-text-green transition-colors hover:bg-mark-green/5"
                >
                  approve &amp; send
                </button>
              )}
            </div>
          ) : null}

          <Chain detail={detail} />
          <AuditTimeline detail={detail} />
        </div>
      )}
    </aside>
  );
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="px-5 py-4 rule-b">
      <h4 className="mb-2.5 text-[11px] uppercase tracking-widest text-faint">{title}</h4>
      {children}
    </section>
  );
}

function Chain({ detail }: { detail: CaseDetail }) {
  return (
    <>
      <Block title="Diagnosis">
        {detail.diagnoses.length === 0 ? (
          <Empty text="not diagnosed yet" />
        ) : (
          detail.diagnoses.map((d, i) => (
            <div key={i} className="mb-2 last:mb-0">
              <div className="flex items-baseline gap-2">
                <span className="font-mono text-[13.5px] font-medium">{d.root_cause_category}</span>
                <span className="font-mono text-[11.5px] text-muted">
                  {(d.confidence * 100).toFixed(0)}% · {d.method}
                </span>
              </div>
              {d.reasoning ? (
                <p className="mt-0.5 text-[12.5px] leading-snug text-muted">{d.reasoning}</p>
              ) : null}
            </div>
          ))
        )}
      </Block>

      <Block title="Decision">
        {detail.decisions.map((d, i) => (
          <div key={i} className="mb-2 last:mb-0">
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <span className="font-mono text-[13.5px] font-medium">
                {ACTION_LABELS[d.proposed_action] ?? d.proposed_action}
              </span>
              <StatusPill status={d.status} />
              {d.message_language ? (
                <span className="font-mono text-[11px] text-muted">
                  {d.message_language}/{d.message_tone}
                </span>
              ) : null}
            </div>
            {d.reasoning ? (
              <p className="mt-0.5 text-[12.5px] leading-snug text-muted">{d.reasoning}</p>
            ) : null}
            {Object.keys(d.action_params).length > 0 ? (
              <ParamsTable params={d.action_params} />
            ) : null}
          </div>
        ))}
        {detail.decisions.length === 0 ? <Empty text="no decision recorded" /> : null}
      </Block>

      <Block title="Guardrail gate">
        {detail.guardrail_checks.map((c, i) => (
          <div key={i} className="mb-1.5 flex flex-wrap items-center gap-2 last:mb-0">
            <span
              className={`font-mono text-[11px] uppercase tracking-wide ${
                c.passed ? "text-text-green" : "text-text-rust"
              }`}
            >
              {c.passed ? "✓ passed" : "✕ blocked"}
            </span>
            {c.violated_rules.map((r) => (
              <span
                key={r}
                className="border border-hairline-strong px-1.5 py-px font-mono text-[10.5px] text-text-rust"
              >
                {r}
              </span>
            ))}
            {c.passed ? null : detail.decisions[i]?.status === "DEFERRED" ? (
              <span className="font-mono text-[11px] text-muted">→ deferred</span>
            ) : (
              <span className="font-mono text-[11px] text-muted">→ escalated</span>
            )}
          </div>
        ))}
        {detail.guardrail_checks.length === 0 ? <Empty text="no gate checks yet" /> : null}
      </Block>

      <Block title="Actions & outcomes">
        {detail.actions.map((a, i) => (
          <div key={i} className="mb-1.5 flex flex-wrap items-baseline gap-x-3 last:mb-0">
            <span className="font-mono text-[12.5px]">{ACTION_LABELS[a.action_type] ?? a.action_type}</span>
            <span className="font-mono text-[11px] text-muted">{a.status.toLowerCase()}</span>
            {a.external_ref ? (
              <span className="font-mono text-[11px] text-faint">{a.external_ref}</span>
            ) : null}
            {a.executed_at ? (
              <span className="ml-auto font-mono text-[11px] text-muted">{istTime(a.executed_at)} IST</span>
            ) : null}
          </div>
        ))}
        {detail.outcomes.map((o, i) => (
          <div key={`o${i}`} className="mt-2 border-l-2 border-mark-green pl-3">
            <span className="font-mono text-[12.5px] text-text-green">
              recovered {o.amount_recovered_paise != null ? rupees(o.amount_recovered_paise) : ""}
            </span>
            <p className="font-mono text-[11px] text-muted">
              matched {o.matched_payment_id} · {istTime(o.recovered_at)} IST
            </p>
          </div>
        ))}
        {detail.actions.length === 0 && detail.outcomes.length === 0 ? (
          <Empty text="nothing executed" />
        ) : null}
      </Block>

      {detail.terminal_reason ? (
        <Block title="Terminal state">
          <p className="text-[12.5px] leading-snug">{detail.terminal_reason}</p>
        </Block>
      ) : null}
    </>
  );
}

function StatusPill({ status }: { status: string }) {
  const style =
    status === "EXECUTED"
      ? "text-text-green border-mark-green/60"
      : status === "BLOCKED"
        ? "text-text-rust border-mark-rust/60"
        : status === "DEFERRED"
          ? "text-muted border-hairline-strong"
          : "text-ink border-ink/40";
  return (
    <span className={`border px-1.5 py-px font-mono text-[10.5px] uppercase ${style}`}>
      {status.toLowerCase()}
    </span>
  );
}

function ParamsTable({ params }: { params: Record<string, unknown> }) {
  return (
    <dl className="mt-1.5 grid grid-cols-[max-content_1fr] gap-x-3 gap-y-0.5 border border-hairline bg-paper p-2 font-mono text-[11px]">
      {Object.entries(params)
        .slice(0, 8)
        .map(([k, v]) => (
          <div key={k} className="col-span-2 grid grid-cols-subgrid">
            <dt className="text-faint">{k}</dt>
            <dd className="truncate">{String(v)}</dd>
          </div>
        ))}
    </dl>
  );
}

function AuditTimeline({ detail }: { detail: CaseDetail }) {
  return (
    <section className="px-5 py-4">
      <h4 className="mb-3 text-[11px] uppercase tracking-widest text-faint">
        Audit trail — append only
      </h4>
      <ol className="relative ml-1 border-l border-hairline-strong pl-4">
        {detail.audit_trail.map((row, i) => (
          <li key={i} className="relative mb-3 last:mb-0">
            <span
              aria-hidden
              className={`absolute -left-[21px] top-1 h-[7px] w-[7px] rounded-full ring-2 ring-paper-raise ${
                row.actor === "human"
                  ? "bg-ink"
                  : row.actor === "system"
                    ? "bg-track"
                    : "bg-mark-green"
              }`}
            />
            <div className="flex flex-wrap items-baseline gap-x-2">
              <span className="font-mono text-[10.5px] uppercase tracking-wide text-faint">
                {istTime(row.timestamp)}
              </span>
              <span className="font-mono text-[10.5px] uppercase text-muted">{row.actor}</span>
            </div>
            <p className="text-[12.5px] leading-snug">{row.summary}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}

function Empty({ text }: { text: string }) {
  return <p className="font-mono text-[11.5px] text-faint">{text}</p>;
}
