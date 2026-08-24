/** Formatting helpers — all numerals render in IBM Plex Mono via CSS classes. */

export function rupees(paise: number, opts: { compact?: boolean } = {}): string {
  const value = paise / 100;
  if (opts.compact && Math.abs(value) >= 1_00_000) {
    if (Math.abs(value) >= 1_00_00_000) return `₹${(value / 1_00_00_000).toFixed(2)}Cr`;
    return `₹${(value / 1_00_000).toFixed(2)}L`;
  }
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: value % 1 === 0 ? 0 : 2,
  }).format(value);
}

export function pct(fraction: number, digits = 1): string {
  return `${(fraction * 100).toFixed(digits)}%`;
}

export function hours(h: number): string {
  if (h === 0) return "—";
  if (h < 48) return `${h.toFixed(1)}h`;
  return `${(h / 24).toFixed(1)}d`;
}

const IST_OPTS: Intl.DateTimeFormatOptions = {
  timeZone: "Asia/Kolkata",
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
};

/** Naive-UTC strings from the API are UTC by convention; label them in IST. */
export function istTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat("en-IN", IST_OPTS).format(d);
}

export function istClock(now: Date): string {
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(now);
}

/** Short relative age for feed rows. */
export function ago(iso: string, now: Date = new Date()): string {
  const then = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`).getTime();
  if (Number.isNaN(then)) return "";
  const mins = Math.max(0, Math.round((now.getTime() - then) / 60_000));
  if (mins < 1) return "now";
  if (mins < 60) return `${mins}m`;
  const h = Math.floor(mins / 60);
  if (h < 48) return `${h}h`;
  return `${Math.floor(h / 24)}d`;
}

/** Human labels for machine states. */
export const STATE_LABELS: Record<string, string> = {
  NEW: "new",
  PROCESSING: "processing",
  AWAITING_OUTCOME: "awaiting outcome",
  RECOVERED: "recovered",
  LOST: "lost",
  ESCALATED_TO_HUMAN: "escalated",
  STOPPED_UNRECOVERABLE: "stopped",
};

export const ACTION_LABELS: Record<string, string> = {
  send_payment_link: "send payment link",
  retry_mandate_charge: "retry mandate charge",
  escalate_human: "escalate to human",
  stop: "stop",
};
