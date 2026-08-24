# Recoupr

**An AI agent that watches a Razorpay payment stack for revenue-at-risk events, diagnoses why the money stopped, decides one bounded recovery action, executes it through a deterministic guardrail gate — and proves every recovery with a matched payment.**

Built for the Razorpay AI Buildathon 2026 — Track 03. **Razorpay test mode only.**

---

## The problem

Indian merchants lose real revenue to three everyday failure modes:

| Flow | Trigger | What Recoupr does |
|---|---|---|
| **A — Payment failure** | `payment.failed` | Diagnoses the root cause; sends a fresh payment link on the best consented channel |
| **B — Checkout abandonment** | `checkout.abandoned` | Nudges with a single-use hosted-checkout link |
| **C — Subscription/mandate** | `subscription.charge.failed` | The only flow allowed to retry the stored mandate directly |

Every case gets **exactly one bounded action per wake-up** — never a silent card retry, never an unbounded message burst, never an invented action. When the agent can't act safely, it stops or escalates to a human.

## The pipeline

```
webhook ──▶ Event ──▶ Case ──▶ DIAGNOSIS ──▶ DECISION ──▶ GUARDRAIL GATE ──▶ EXECUTION ──▶ OUTCOME
            (raw      (state    (rule table   (template      (deterministic     (Razorpay      (matched
             payload)  machine)  + LLM)        candidates     code only —        test-mode      payment
                                     + LLM choice)  no LLM may         APIs)          event or
                                                    ever sit here)                    TTL loss)
```

- **Diagnosis** — a deterministic rule table over known Razorpay error codes; unknown codes go to Claude (claude-sonnet-5, structured output); unclassifiable failures fall back to *escalate*, never guess.
- **Decision** — a template layer computes the *candidate action set* from policy, consent, verification state and diagnosis; Claude may only choose among candidates and pick message language/tone (English, Hindi, Hinglish).
- **Guardrail gate** — plain Python. Fifteen rules including flow/action authority, retry & message caps, quiet hours, cooldown, consent, sender verification, amount immutability, single-use `rzp.io`-hosted links, and sensitive-content scanning (the message can never ask for OTP, card number, or PIN). Timing-rule violations **defer** to the next legal window; anything else blocks and escalates.
- **Execution** — Razorpay test-mode APIs only. `attempts_count` increments only on real execution, never on a proposal or a block.
- **Proof** — a case is `RECOVERED` only when a real payment event matches it via `notes.reference_id`. Everything else ends `LOST`, `ESCALATED_TO_HUMAN`, or `STOPPED_UNRECOVERABLE`. No dangling cases.

Every state transition and every gate check — pass or block — writes an immutable, append-only `AuditLogEntry`.

## Honest numbers

A 200-case seeded replay (`POST /simulate/batch {"count": 200, "seed": 42}`), 28-day synthetic timeline, ground-truth labels kept for scoring:

| Metric | Value | Reading |
|---|---|---|
| Recovery rate (global) | ~63% | recovered ÷ (recovered + lost) |
| Settled-cohort rate | ~55–62% | cases whose **full** observation window elapsed — censoring-free |
| Mean time to recovery | ~39h | from case creation to matched payment |
| Escalation load | ~13% | anomalies only, not routine non-response |
| False-positive rate | ~49% | share of acted-on cases whose ground truth said unrecoverable |
| Guardrail violations | **0** | the invariant that must never move |
| Guardrail blocks | ~95 | the gate doing its job, reported separately |

A near-90% recovery rate would mean the simulator is broken, not that the agent is good — most failed payments are genuinely unrecoverable (expired cards, cancelled mandates, no balance). The settled-cohort rate exists because the global rate is censored: recoveries resolve at any age while losses need a full TTL, so a bounded history over-weights the loss-free recent tail.

## Run it

Backend (Python 3.12+):

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # Windows
.venv/Scripts/uvicorn app.main:app --port 8000                          # SQLite zero-setup
```

Dashboard (Node 20+):

```bash
cd dashboard && npm install && npm run dev    # http://localhost:3000
```

Or everything at once: `docker compose up --build`.

Then open the console, go to **06 Batch simulator**, and run 200 events with seed 42 — or:

```bash
curl -X POST localhost:8000/simulate/batch -H "content-type: application/json" -d '{"count":200,"seed":42}'
```

Postgres deployments run `alembic upgrade head` (the initial migration is checked in). The embedded APScheduler sweeps TTLs and requeues deferred cases every 5 minutes; disable with `RECOUPR_SCHEDULER_ENABLED=false` and cron `POST /maintenance/tick` instead.

### Configuration

| Env var | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | SQLite `recoupr.db` | Postgres URL for production shapes |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | mock client | a `rzp_live_…` key **refuses to start** |
| `RAZORPAY_WEBHOOK_SECRET` | — | HMAC verification; required for real webhook mode |
| `ANTHROPIC_API_KEY` | — | absent ⇒ deterministic-only mode (rule table + template decisions) |
| `SMS_SENDER_VERIFIED` / `WHATSAPP_SENDER_VERIFIED` | false | DLT/WhatsApp registration simulation for demos |
| `MERCHANT_TIMEZONE` | Asia/Kolkata | quiet-hours evaluation |

## Tests

54 tests, including an adversarial suite that builds **one proposal violating all fifteen rules at once** and asserts the gate catches every one; purity tests (the gate never mutates state, never consumes an attempt); boundary tests (quiet-hour edges, cooldown exact-6h, message-cap 7-day window); flow-authority tests (links only on A/B, mandate retry only on C); and batch honesty tests (zero violations, believable settled-cohort band, no recovery stamped before its case existed or after the replay clock).

```bash
pytest -q && ruff check .
```

## Known limitations

- **Single merchant.** The default-merchant bootstrap fits the buildathon scope; multi-tenancy is a schema change (`merchant_id` already exists on every row).
- **Simulated sender registration & payment links.** Without verified DLT/WhatsApp credentials the demo runs with simulated send IDs; the gate models the real constraint either way.
- **Attribution is conservative but imperfect.** A matched payment proves the customer paid; it can't prove the message *caused* it. The false-positive rate is the honest acknowledgment: ~49% of acted-on cases were likely coming back anyway.
- **Timing rules defer rather than escalate.** Quiet-hours/cooldown blocks reschedule the proposal (no attempt consumed) instead of paging a human — a deliberate deviation from "every block escalates," because a 11pm proposal isn't an emergency.
- **The global recovery rate is censored** on any bounded history; use the settled-cohort rate for decision-making.
- **LLM is optional by design.** Without `ANTHROPIC_API_KEY` the system degrades to fully deterministic behavior; it never fails open.

## Layout

```
app/
  agents/       diagnosis · decision · state machine · LLM client
  api/          routes (dashboard surface) · webhook receiver
  guardrails/   checks (deterministic gate) · policy snapshot
  payments/     Razorpay client wrapper (test-mode + mock)
  services/     pipeline · metrics · maintenance
  simulation/   labeled synthetic event generator · batch replay
  db/           session · append-only guards · Alembic migrations
dashboard/      Next.js 16 · React 19 · Tailwind 4 ledger console
tests/          54 tests incl. adversarial gate suite
docs/           local planning material (gitignored, never committed)
```
