"use client";

import { useId, useState } from "react";

/* ------------------------------------------------------------------ */
/* Shared primitives for the ledger console.                           */
/* ------------------------------------------------------------------ */

export function SectionHeading({
  num,
  title,
  blurb,
}: {
  num: string;
  title: string;
  blurb?: string;
}) {
  return (
    <div className="mb-5 flex items-baseline gap-4">
      <span className="font-mono text-[13px] tracking-widest text-faint">{num}</span>
      <h2 className="font-display text-[22px] font-medium leading-none">{title}</h2>
      {blurb ? <p className="hidden text-[12.5px] text-muted md:block">{blurb}</p> : null}
    </div>
  );
}

const STATE_STYLE: Record<string, string> = {
  NEW: "border-hairline-strong text-muted",
  PROCESSING: "border-hairline-strong text-muted",
  AWAITING_OUTCOME: "border-ink/40 text-ink",
  RECOVERED: "border-mark-green/60 text-text-green",
  LOST: "border-mark-brick/50 text-text-brick",
  ESCALATED_TO_HUMAN: "border-mark-rust/60 text-text-rust",
  STOPPED_UNRECOVERABLE: "border-slate/50 text-slate",
};

/** State tag — identity from the label, never color alone. */
export function StateTag({ state }: { state: string }) {
  const style = STATE_STYLE[state] ?? "border-hairline-strong text-muted";
  const label =
    state === "AWAITING_OUTCOME"
      ? "awaiting"
      : state === "ESCALATED_TO_HUMAN"
        ? "escalated"
        : state === "STOPPED_UNRECOVERABLE"
          ? "stopped"
          : state.toLowerCase();
  return (
    <span
      className={`inline-block border px-1.5 py-px font-mono text-[10.5px] uppercase leading-[15px] tracking-wide ${style}`}
    >
      {label}
    </span>
  );
}

export function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`border px-2.5 py-1 font-mono text-[11.5px] transition-colors ${
        active
          ? "border-ink bg-ink text-paper"
          : "border-hairline-strong text-muted hover:border-ink hover:text-ink"
      }`}
    >
      {children}
    </button>
  );
}

/**
 * Hover tooltip positioned by the parent chart. Rendered inside a
 * position:relative wrapper; x/y are px offsets within it.
 */
export function ChartTip({
  x,
  y,
  lines,
}: {
  x: number;
  y: number;
  lines: string[];
}) {
  return (
    <div
      className="pointer-events-none absolute z-20 border border-hairline-strong bg-paper-raise px-2 py-1 shadow-[2px_2px_0_rgba(16,36,28,0.08)]"
      style={{
        left: Math.min(x, 9999),
        top: y,
        transform: `translate(${x > 160 ? "-100%" : "8px"}, -110%)`,
      }}
    >
      {lines.map((line) => (
        <div key={line} className="whitespace-nowrap font-mono text-[11px] leading-[15px]">
          {line}
        </div>
      ))}
    </div>
  );
}

export function LegendDot({ color }: { color: string }) {
  return (
    <span
      aria-hidden
      className="inline-block h-2 w-2 shrink-0 rounded-full ring-2 ring-paper"
      style={{ backgroundColor: color }}
    />
  );
}

/** Stat tile per the stat-tile contract: label / value / optional trend slot. */
export function StatTile({
  label,
  value,
  sub,
  hero = false,
  children,
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  hero?: boolean;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-1.5 border border-hairline bg-paper-raise p-4">
      <span className="text-[12px] leading-none text-muted">{label}</span>
      <span
        className={
          hero
            ? "font-mono text-[52px] font-medium leading-none tracking-tight"
            : "font-mono text-[27px] font-medium leading-tight"
        }
      >
        {value}
      </span>
      {sub ? <span className="font-mono text-[11.5px] leading-snug text-muted">{sub}</span> : null}
      {children}
    </div>
  );
}

/** Text input with mono numerals and a ledger underline style. */
export function Field({
  label,
  ...props
}: { label: string } & React.InputHTMLAttributes<HTMLInputElement>) {
  const id = useId();
  return (
    <label htmlFor={id} className="flex flex-col gap-1">
      <span className="text-[11.5px] uppercase tracking-wide text-muted">{label}</span>
      <input
        id={id}
        {...props}
        className="w-32 border-b border-hairline-strong bg-transparent pb-1 font-mono text-[14px] outline-none focus:border-ink"
      />
    </label>
  );
}
