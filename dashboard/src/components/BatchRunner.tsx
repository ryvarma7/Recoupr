"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { pct, rupees } from "@/lib/format";
import { Field, StatCell, StatStrip } from "./ui";

interface BatchReport {
  cases_total?: number;
  recovered?: number;
  lost?: number;
  escalated?: number;
  stopped_unrecoverable?: number;
  pending?: number;
  recovery_rate?: number;
  false_positive_rate?: number;
  guardrail_violations?: number;
  guardrail_blocks?: number;
  mean_time_to_recovery_hours?: number;
  total_recovered_paise?: number;
  settled_cohort?: {
    cases: number;
    recovered: number;
    lost: number;
    escalated: number;
    stopped: number;
    recovery_rate: number;
  };
}

/** 06 — Batch simulator. Runs the full pipeline over synthetic events and
 * prints an honest report; refreshes every other section when done. */
export function BatchRunner({ onDone }: { onDone: () => void }) {
  const [count, setCount] = useState("150");
  const [seed, setSeed] = useState("42");
  const [running, setRunning] = useState(false);
  const [lines, setLines] = useState<string[]>([]);
  const [report, setReport] = useState<BatchReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [lines]);

  const run = useCallback(async () => {
    const n = Math.max(10, Math.min(300, Number.parseInt(count, 10) || 150));
    setRunning(true);
    setError(null);
    setReport(null);
    const started = Date.now();
    const ticker = setInterval(() => {
      setLines((prev) =>
        prev.length && prev[prev.length - 1].startsWith("… processing")
          ? [...prev.slice(0, -1), `… processing ${Math.round((Date.now() - started) / 100) / 10}s`]
          : [`… processing 0s`],
      );
    }, 100);

    try {
      const parsedSeed = Number.parseInt(seed, 10);
      const result = await api.runBatch(n, Number.isNaN(parsedSeed) ? null : parsedSeed);
      clearInterval(ticker);
      setLines((prev) => [
        ...prev.filter((l) => !l.startsWith("…")),
        `✓ batch complete — ${n} events simulated in ${((Date.now() - started) / 1000).toFixed(1)}s`,
      ]);
      setReport(result as BatchReport);
      onDone();
    } catch (e) {
      clearInterval(ticker);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  }, [count, seed, onDone]);

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end gap-6">
        <Field
          label="events"
          type="number"
          min={10}
          max={300}
          value={count}
          onChange={(e) => setCount(e.target.value)}
        />
        <Field
          label="seed"
          type="number"
          value={seed}
          onChange={(e) => setSeed(e.target.value)}
        />
        <button
          type="button"
          disabled={running}
          onClick={() => {
            setLines([]);
            void run();
          }}
          className={`border px-4 py-1.5 font-mono text-[12px] uppercase tracking-widest transition-colors ${
            running
              ? "cursor-wait border-hairline-strong text-faint"
              : "border-ink bg-ink text-paper hover:bg-transparent hover:text-ink"
          }`}
        >
          {running ? "running…" : "run batch"}
        </button>
        <p className="max-w-[380px] pb-1 font-mono text-[11px] leading-snug text-muted">
          Synthetic events flow A/B/C run through diagnosis → decision → gate → execution,
          with ground-truth labels kept for honesty metrics.
        </p>
      </div>

      <div ref={logRef} className="max-h-[220px] overflow-y-auto border border-hairline bg-paper-sink/40 px-4 py-3 slim-scroll">
        {lines.length === 0 ? (
          <p className="font-mono text-[12px] text-faint">idle — waiting for a run</p>
        ) : (
          lines.map((l, i) => (
            <p key={i} className={`font-mono text-[12px] leading-relaxed ${l.startsWith("✓") ? "text-text-green" : ""}`}>
              {l}
            </p>
          ))
        )}
        {error ? <p className="font-mono text-[12px] text-text-brick">✕ {error}</p> : null}
      </div>

      {report ? (
        <div className="flex flex-col gap-2 fade-up">
          <StatStrip cols="grid-cols-2 sm:grid-cols-3 lg:grid-cols-6">
            <StatCell label="cases" value={String(report.cases_total ?? "—")} />
            <StatCell label="recovery rate" value={report.recovery_rate != null ? pct(report.recovery_rate) : "—"} tone="green" />
            <StatCell label="recovered ₹" value={report.total_recovered_paise != null ? rupees(report.total_recovered_paise, { compact: true }) : "—"} />
            <StatCell label="mean TTR" value={report.mean_time_to_recovery_hours != null ? `${report.mean_time_to_recovery_hours}h` : "—"} />
            <StatCell label="false positives" value={report.false_positive_rate != null ? pct(report.false_positive_rate) : "—"} tone="rust" />
            <StatCell
              label="violations"
              value={String(report.guardrail_violations ?? "—")}
              tone={(report.guardrail_violations ?? 0) === 0 ? "green" : "brick"}
            />
          </StatStrip>
          {report.settled_cohort ? (
            <p className="font-mono text-[11.5px] leading-snug text-muted">
              settled cohort — {report.settled_cohort.cases} cases with their full{" "}
              observation window elapsed:{" "}
              <span className="text-text-green">
                {pct(report.settled_cohort.recovery_rate)} recovery
              </span>{" "}
              ({report.settled_cohort.recovered}÷{report.settled_cohort.recovered + report.settled_cohort.lost}{" "}
              resolved). The headline rate over-weights the loss-free recent tail; this one doesn't.
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
