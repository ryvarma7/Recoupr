"use client";

import { useEffect, useState } from "react";
import { istClock } from "@/lib/format";

export function Masthead({ healthy }: { healthy: boolean }) {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    setNow(new Date());
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  return (
    <header className="border-b border-hairline">
      <div className="mx-auto flex max-w-7xl flex-wrap items-baseline gap-x-5 gap-y-2 px-6 pb-4 pt-7">
        <h1 className="font-display text-[30px] font-semibold leading-none tracking-tight">
          Recoupr
        </h1>
        <p className="text-[13px] text-muted">revenue recovery console</p>

        <span className="ml-auto flex items-center gap-5 font-mono text-[11.5px] text-muted">
          <span
            title="Razorpay test mode only — live keys are refused at startup"
            className="border border-hairline-strong px-1.5 py-px uppercase tracking-wide"
          >
            razorpay · test mode
          </span>
          <span className="flex items-center gap-1.5" title={healthy ? "API reachable on :8000" : "API unreachable"}>
            <span
              aria-hidden
              className={`inline-block h-[7px] w-[7px] rounded-full ring-2 ring-paper ${
                healthy ? "bg-mark-green" : "bg-mark-brick"
              }`}
            />
            {healthy ? "live" : "offline"}
          </span>
          <span suppressHydrationWarning>{now ? `${istClock(now)} IST` : "--:--:-- IST"}</span>
        </span>
      </div>
    </header>
  );
}
