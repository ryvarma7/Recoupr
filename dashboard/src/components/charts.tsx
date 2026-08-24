"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChartTip, LegendDot } from "./ui";

/**
 * Measure a container's pixel width so SVG strokes stay true 2px
 * (no non-uniform viewBox scaling).
 */
function useMeasure<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) setWidth(entry.contentRect.width);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width];
}

/* ------------------------------------------------------------------ */
/* Sparkline — single series, no legend (the title names it).          */
/* 2px line in the de-emphasis hue; current period carries the accent  */
/* end-dot with a 2px surface ring. Crosshair + tooltip on hover.      */
/* ------------------------------------------------------------------ */

export function Sparkline({
  points,
  labels,
  height = 56,
}: {
  points: number[];
  labels: string[];
  height?: number;
}) {
  const [ref, width] = useMeasure<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);

  const padX = 6;
  const padY = 8;
  const max = Math.max(...points, 1);

  const coords = useMemo(() => {
    if (points.length < 2 || width === 0) return null;
    const stepX = (width - padX * 2) / (points.length - 1);
    return points.map((p, i) => ({
      x: padX + i * stepX,
      y: height - padY - (p / max) * (height - padY * 2),
      v: p,
    }));
  }, [points, width, height, max]);

  const onMove = useCallback(
    (e: React.MouseEvent<SVGSVGElement>) => {
      if (!coords) return;
      const rect = e.currentTarget.getBoundingClientRect();
      const x = e.clientX - rect.left;
      let best = 0;
      let bestDist = Infinity;
      coords.forEach((c, i) => {
        const d = Math.abs(c.x - x);
        if (d < bestDist) {
          bestDist = d;
          best = i;
        }
      });
      setHover(best);
    },
    [coords],
  );

  const last = coords ? coords[coords.length - 1] : null;

  return (
    <div ref={ref} className="relative w-full" style={{ height }}>
      {coords ? (
        <>
          <svg
            width={width}
            height={height}
            className="block"
            onMouseMove={onMove}
            onMouseLeave={() => setHover(null)}
          >
            {/* hairline baseline, recessive */}
            <line
              x1={padX}
              y1={height - padY}
              x2={width - padX}
              y2={height - padY}
              className="stroke-hairline"
              strokeWidth={1}
            />
            <polyline
              points={coords.map((c) => `${c.x},${c.y}`).join(" ")}
              fill="none"
              className="stroke-faint"
              strokeWidth={2}
              strokeLinejoin="round"
              strokeLinecap="round"
            />
            {hover !== null && coords[hover] ? (
              <>
                <line
                  x1={coords[hover].x}
                  y1={padY / 2}
                  x2={coords[hover].x}
                  y2={height - padY}
                  className="stroke-hairline-strong"
                  strokeWidth={1}
                />
                <circle
                  cx={coords[hover].x}
                  cy={coords[hover].y}
                  r={4}
                  className="fill-faint stroke-paper"
                  strokeWidth={2}
                />
              </>
            ) : null}
            {last ? (
              <circle
                cx={last.x}
                cy={last.y}
                r={4}
                className="fill-mark-green stroke-paper"
                strokeWidth={2}
              />
            ) : null}
          </svg>
          {hover !== null && coords[hover] ? (
            <ChartTip
              x={coords[hover].x}
              y={coords[hover].y - 6}
              lines={[labels[hover] ?? "", `${coords[hover].v} cases`]}
            />
          ) : null}
        </>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Outcome distribution strip — one horizontal stacked bar (≤24px),    */
/* 2px surface gaps between segments, legend with counts beneath.      */
/* Fixed status order: good → indeterminate → attention → halted → dead*/
/* so green and rust never touch. Identity = gaps + labels, not hue.   */
/* ------------------------------------------------------------------ */

export interface StripSegment {
  key: string;
  label: string;
  color: string; // mark color for the fill
  textColor?: string; // text-safe variant for the legend count
  value: number;
}

const STRIP_ORDER = ["recovered", "pending", "escalated", "stopped", "lost"];

export function OutcomeStrip({ segments }: { segments: StripSegment[] }) {
  const [tip, setTip] = useState<{ key: string; x: number } | null>(null);
  const ordered = [...segments].sort(
    (a, b) => STRIP_ORDER.indexOf(a.key) - STRIP_ORDER.indexOf(b.key),
  );
  const total = ordered.reduce((acc, s) => acc + s.value, 0);

  return (
    <div className="flex flex-col gap-3">
      <div
        className="relative flex h-5 w-full gap-[2px]"
        onMouseLeave={() => setTip(null)}
      >
        {ordered.map((s) =>
          s.value > 0 ? (
            <div
              key={s.key}
              role="img"
              aria-label={`${s.label}: ${s.value}`}
              className="h-full cursor-default transition-[filter] hover:brightness-110"
              style={{ flexGrow: s.value, backgroundColor: s.color }}
              onMouseMove={(e) => {
                const rect = e.currentTarget.parentElement!.getBoundingClientRect();
                setTip({ key: s.key, x: e.clientX - rect.left });
              }}
            />
          ) : null,
        )}
        {total === 0 ? (
          <div className="h-full w-full bg-paper-sink" aria-label="no cases yet" />
        ) : null}
        {tip
          ? (() => {
              const s = ordered.find((x) => x.key === tip.key)!;
              return (
                <ChartTip
                  x={tip.x}
                  y={0}
                  lines={[
                    s.label,
                    `${s.value} case${s.value === 1 ? "" : "s"} · ${total ? ((s.value / total) * 100).toFixed(1) : "0.0"}%`,
                  ]}
                />
              );
            })()
          : null}
      </div>

      {/* Legend: swatch beside text tokens — text never wears the data color. */}
      <ul className="flex flex-wrap gap-x-5 gap-y-1.5">
        {ordered.map((s) => (
          <li key={s.key} className="flex items-center gap-2 text-[12px]">
            <LegendDot color={s.color} />
            <span className="text-muted">{s.label}</span>
            <span className={`font-mono text-[12px] font-medium ${textClassFor(s)}`}>
              {s.value}
              <span className="ml-1 font-normal text-faint">
                {total ? `${((s.value / total) * 100).toFixed(0)}%` : "—"}
              </span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function textClassFor(s: StripSegment): string {
  // Counts sit in text-safe variants of each mark (all ≥4.5:1 on paper);
  // identity still comes from the swatch dot, not the hue.
  switch (s.textColor) {
    case "green":
      return "text-text-green";
    case "rust":
      return "text-text-rust";
    case "brick":
      return "text-text-brick";
    default:
      return "text-ink";
  }
}
