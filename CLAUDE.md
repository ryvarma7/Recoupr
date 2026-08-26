# Recoupr — Project Memory

Read /docs/recoupr-project-spec.md in full before making architectural changes.
Build sequence and per-phase prompts: /docs/build-guide.md.

## Non-negotiable rules

- Test mode only. Never use or request live Razorpay keys. No code path where a live
  key would do anything other than fail a startup check.
- The guardrail gate (app/guardrails/checks.py) is plain deterministic code.
  No LLM call may ever sit in its execution path.
- Every case ends in exactly one terminal state: RECOVERED, LOST,
  ESCALATED_TO_HUMAN, STOPPED_UNRECOVERABLE. No dangling cases.
- Flows A & B (payment failure, checkout abandonment) only ever send a payment
  link — never attempt to silently retry a customer's card.
- Flow C (subscription/mandate) is the only flow allowed to execute
  retry_mandate_charge directly (mandate = stored authorization).
- A case is RECOVERED only when a real, matched payment event exists for it.
  Never infer, estimate, or round up.
- Recovery messages never request OTP, card number, or PIN. They only ever link
  to Razorpay's own hosted checkout (rzp.io).
- Every state transition and every guardrail check (pass or block) writes an
  immutable AuditLogEntry row. AuditLogEntry is append-only.
- attempts_count increments only on actual execution — never on proposal or block.

## Stack pins

Python 3.12+, FastAPI, SQLModel, Alembic, PostgreSQL 16, Groq API
(openai/gpt-oss-20b via httpx, OpenAI-compatible), razorpay 2.x, tenacity,
APScheduler 3.x, Faker,
pytest + pytest-asyncio + httpx. Frontend: Next.js, React 19, Tailwind 4,
Recharts (sparklines only).
No LangGraph or any agent framework — hand-rolled state machine only.

## Design tokens

Ledger aesthetic. See /docs/build-guide.md Part 1 "UI design tokens".
Paper #F0F2EC · Ink #10241C · ledger green #1F5C43 · rust amber #A6672E ·
brick #9B3A32 · hairline #8A8B80. Fraunces = words only; IBM Plex Mono =
every number/ID/timestamp including hero KPIs. 0px radius; 1px hairlines,
never shadows; bracketed state tags `[RECOVERED]`; no gradients, no blue,
no icon libraries, no centered modals.

## Repo conventions

- docs/ is gitignored — planning material, local reference only. Never commit it.
- Commits are authored by the user only (ryvarma7). No AI co-author trailers.
