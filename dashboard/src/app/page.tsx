"use client";

import { useCallback, useRef, useState } from "react";
import { BatchRunner } from "@/components/BatchRunner";
import { CaseDrawer } from "@/components/CaseDrawer";
import { CaseFeed } from "@/components/CaseFeed";
import { Masthead } from "@/components/Masthead";
import { Overview } from "@/components/Overview";
import { AuditStream, Exceptions, GuardrailSection } from "@/components/Sections";
import { PolicyView } from "@/components/PolicyView";
import { SectionHeading } from "@/components/ui";
import type { CaseSummary } from "@/lib/types";
import { useDashboard } from "@/lib/useData";

const NAV = [
  ["overview", "01 overview"],
  ["cases", "02 cases"],
  ["exceptions", "03 exceptions"],
  ["guardrail", "04 guardrail"],
  ["audit", "05 audit"],
  ["batch", "06 batch"],
  ["policy", "07 policy"],
] as const;

export default function Home() {
  const data = useDashboard();
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const selectedRef = useRef<CaseSummary | null>(null);

  const openCase = useCallback((c: CaseSummary) => {
    selectedRef.current = c;
    setSelectedId(c.id);
  }, []);
  const close = useCallback(() => setSelectedId(null), []);

  return (
    <div className="min-h-screen">
      <Masthead healthy={data.healthy} />

      <nav aria-label="Sections" className="sticky top-0 z-20 border-b border-hairline bg-paper/95 backdrop-blur-sm">
        <ul className="mx-auto flex max-w-7xl gap-x-1 overflow-x-auto px-6 py-2 font-mono text-[11.5px] slim-scroll">
          {NAV.map(([id, label]) => (
            <li key={id}>
              <a
                href={`#${id}`}
                className="whitespace-nowrap px-2 py-1 uppercase tracking-wide text-muted transition-colors hover:bg-paper-sink hover:text-ink"
              >
                {label}
              </a>
            </li>
          ))}
          <li className="ml-auto hidden pr-2 leading-[26px] normal-case tracking-normal text-faint md:block">
            {data.metrics ? `${data.metrics.cases_total} cases · updated ${data.updatedAt?.toLocaleTimeString("en-IN", { hour12: false })}` : "connecting…"}
          </li>
        </ul>
      </nav>

      {!data.healthy && !data.metrics ? (
        <div className="border-b border-hairline bg-paper-sink/60">
          <p className="mx-auto max-w-7xl px-6 py-3 font-mono text-[12.5px] text-text-rust">
            API unreachable at <span className="text-ink">localhost:8000</span> — start the
            backend with <code>uvicorn app.main:app</code>, then run a batch in section 06.
          </p>
        </div>
      ) : null}

      <main className="mx-auto max-w-7xl px-6">
        <section id="overview" className="scroll-mt-14 py-9 rule-b">
          <SectionHeading num="01" title="Overview" blurb="Honest numbers only — open cases stay out of the rate." />
          {data.metrics ? (
            <Overview metrics={data.metrics} cases={data.cases} />
          ) : (
            <Skeleton rows={2} />
          )}
        </section>

        <section id="cases" className="scroll-mt-14 py-9 rule-b">
          <SectionHeading num="02" title="Case feed" blurb="Every revenue-at-risk case; a row opens its full reasoning chain." />
          <CaseFeed cases={data.cases} onOpen={openCase} selectedId={selectedId} />
        </section>

        <section id="exceptions" className="scroll-mt-14 py-9 rule-b">
          <SectionHeading num="03" title="Exceptions" blurb="Escalations needing a human, and deliberate stops." />
          <Exceptions items={data.exceptions?.exceptions ?? []} onOpen={openCase} />
        </section>

        <section id="guardrail" className="scroll-mt-14 py-9 rule-b">
          <SectionHeading num="04" title="Guardrail activity" blurb="Deterministic gate decisions — no LLM inside." />
          {data.metrics ? (
            <GuardrailSection checks={data.guardrail} metrics={data.metrics} />
          ) : (
            <Skeleton rows={1} />
          )}
        </section>

        <section id="audit" className="scroll-mt-14 py-9 rule-b">
          <SectionHeading num="05" title="Audit stream" blurb="Append-only; every state change is written before it happens." />
          <AuditStream entries={data.audit} />
        </section>

        <section id="batch" className="scroll-mt-14 py-9 rule-b">
          <SectionHeading num="06" title="Batch simulator" blurb="Replay synthetic Razorpay events through the whole pipeline." />
          <BatchRunner onDone={data.refresh} />
        </section>

        <section id="policy" className="scroll-mt-14 py-9">
          <SectionHeading num="07" title="Guardrail policy" blurb="Snapshot semantics: cases keep the rules they were born under." />
          <PolicyView />
        </section>
      </main>

      <footer className="rule-t">
        <div className="mx-auto max-w-7xl px-6 py-6 font-mono text-[11px] leading-relaxed text-faint">
          Recoupr — buildathon track 03 · simulated sender IDs &amp; payment links ·
          every executed action passed the deterministic gate · audit log is append-only
        </div>
      </footer>

      {selectedId !== null ? (
        <CaseDrawer caseId={selectedId} onClose={close} onChanged={data.refresh} />
      ) : null}
    </div>
  );
}

function Skeleton({ rows }: { rows: number }) {
  return (
    <div className="flex flex-col gap-[2px]" style={{ minHeight: rows * 90 }}>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="h-[86px] animate-pulse bg-paper-sink/70" />
      ))}
    </div>
  );
}
