# Recoupr

**Track 03 — AI Revenue Recovery**
**Find revenue that’s slipping away and win it back.**

Recoupr is a small revenue-recovery console for failed payments, abandoned checkouts, and failed subscription charges. It turns each event into a case, diagnoses the likely cause, proposes one bounded intervention, and records what happened. The demo is deliberately focused: it proves the decision boundary and the safety controls instead of pretending to be a complete collections platform.

## The problem

A payment failure is not the same thing as a lost customer. It may be a temporary bank issue, an expired card, an abandoned checkout, or a revoked mandate. Most dashboards stop at “failed” and leave somebody to decide what to do next. Recoupr closes that gap while keeping money movement and customer contact under explicit rules.

## What the product does

The pipeline is:

`event → case → diagnosis → decision → guardrail gate → action → outcome → audit`

Known Razorpay-shaped failures are classified by a deterministic rule table. Unknown failures go to Groq’s `openai/gpt-oss-20b`, which returns a structured diagnosis. The decision step chooses from a fixed candidate list; it cannot invent an action or change the amount. A plain-Python guardrail gate checks the proposal before anything executes.

The implemented recovery flows are:

- Flow A: payment failure → Razorpay payment link
- Flow B: checkout abandonment → Razorpay payment link
- Flow C: subscription charge failure → stored-mandate retry

Recovery is only counted when a matching payment event arrives with the case reference and exact case amount. Synthetic batches use a mock Razorpay-shaped transport so they cannot spend a real account’s quota. Live webhook events use the configured Razorpay test-mode client.

## AI usage and engineering judgment

AI is used where the input is ambiguous: diagnosis of an unknown failure and, when appropriate, choosing between bounded candidate actions and message tone/language. The model receives event type, amount, error code/description, recent retry context, case flow, attempt number, and the candidate actions.

AI is intentionally not used for known error classification, guardrails, state transitions, amount checks, retry limits, quiet hours, consent, link trust, or audit writes. Those paths need predictable behaviour and must remain safe if the model is unavailable. A diagnosis or decision failure escalates to a human; it never falls back to an executable action.

## Guardrails

The gate enforces flow authority, exact amount immutability, retry caps, cooldowns, case TTL, message caps, merchant-local quiet hours, consent, verified senders, trusted `rzp.io` links, single-use links, order references, and sensitive-content scanning on the rendered message. Timing-only blocks defer. Other blocks escalate. Human approval is required for an escalated case and is audit logged before the approved bounded action executes.

Every state transition, diagnosis, proposal, gate verdict, action, and outcome is recorded. `RECOVERED` requires a matched payment. `LOST` and `STOPPED_UNRECOVERABLE` remain distinct terminal outcomes. A payment received after a case was marked lost stays late and is not counted in the recovery rate.

## Dashboard

The dashboard has seven sections:

1. **Overview** — recovery rate, money recovered, escalation load, false-positive rate, ₹ at risk detected, a twelve-day intake sparkline, outcome distribution, diagnosis split, resolution channel split, and gate activity. Open cases are excluded from the recovery-rate denominator.
2. **Case feed** — every case in the current simulation run. Filter by state (`all`, `open`, `recovered`, `lost`, `escalated`, `stopped`) and flow (`all`, A, B, C). Click a row to open its reasoning drawer.
3. **Exceptions** — escalated and stopped cases. Click a card to inspect the reason and audit chain.
4. **Guardrail activity** — checks logged, blocked proposals, violations, and the rule-level verdict table. A block is the gate working; a violation means an executed action lacked a passing check and must remain zero.
5. **Audit stream** — timestamped append-only operational history.
6. **Batch simulator** — set event count from 10 to 300, set a seed, and click **Run batch**. It runs synthetic A/B/C events through the real pipeline and reports recovered cases, rate, recovered money, mean time to recovery, false positives, violations, and the settled cohort.
7. **Guardrail policy** — current policy and the snapshots held by open cases.

The case drawer shows diagnosis method and reasoning, the proposed decision and parameters, guardrail verdicts, action status and external reference, outcomes, and the audit timeline. An escalated case exposes **Approve & Send**. Confirming it executes the bounded action and records the human actor.

## Metrics

- **Recovery rate** = recovered cases ÷ (recovered + lost). Pending, escalated, and stopped cases are not in this denominator.
- **Money recovered** = exact matched recovered amounts, excluding late payments after LOST.
- **₹ at risk detected** = case amounts not currently in `RECOVERED`.
- **Mean TTR** = average hours from case creation to matched recovery.
- **False-positive rate** = acted-on synthetic cases whose generator label was unrecoverable.
- **Guardrail violations** = executed non-escalation actions without a passing guardrail check. The target is zero.
- **Settled cohort** = cases whose full TTL observation window has elapsed. This avoids overstating performance from recent cases that are still open.

## Run locally

Requirements: Python 3.14+, Docker Desktop, and Node.js for the dashboard.

```bash
docker compose up
```

Open `http://localhost:3100`. The backend is on `http://localhost:8000`. Docker starts Postgres, runs Alembic migrations, starts FastAPI, and starts the Next.js dashboard. For a native backend, use `\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000`.

## Environment

Copy `.env.example` to `.env`. The important settings are:

- `DATABASE_URL` — Postgres URL, or omit it for local SQLite.
- `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` — test-mode only; `rzp_live` keys refuse startup.
- `RAZORPAY_WEBHOOK_SECRET` — signature verification for real webhooks.
- `GROQ_API_KEY` and `GROQ_MODEL` — Groq access and model; without a key the system is deterministic-only.
- `SMS_SENDER_VERIFIED`, `WHATSAPP_SENDER_VERIFIED` — sender identity controls.
- `MERCHANT_TIMEZONE` — default merchant timezone; quiet hours ultimately read `Merchant.timezone`.

Never commit `.env` or credentials.

## Project structure

`app/agents` contains diagnosis, decision, and state-machine logic. `app/guardrails` contains the pure gate. `app/payments` contains mock and Razorpay test-mode clients and message rendering. `app/services` contains the pipeline and metrics. `app/api` contains dashboard and webhook routes. `app/simulation` contains the seeded replay. `dashboard/src` contains the console. `tests` contains the unit and end-to-end coverage. `docs/final-verification.md` contains the verified demo numbers and runbook; `docs/pitch-script.md` is the spoken video script.

## What broke, and how we got out

The most important bugs found in the final pass were real implementation gaps. `approve_and_send()` originally changed state without calling a payment client, so there was no external reference and no consumed attempt. The message scan originally inspected `action_params` before the final message existed. We fixed both and added tests around execution and rendered content. During verification, the old SQLite file also lacked the new schema column; wiping it for the requested clean demo state exposed that migration problem, which was fixed with an Alembic migration. The project’s provider documentation was also stale after the move to Groq, so the public docs now describe the implementation that actually runs.

## Submission

- Track: 03 — AI Revenue Recovery
- Project name: Recoupr
- Public repository: https://github.com/ryvarma7/Recoupr
- Final verification and recording material: [docs/final-verification.md](docs/final-verification.md) and [docs/pitch-script.md](docs/pitch-script.md)
