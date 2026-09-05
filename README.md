# Recoupr

Track: 03 — AI Revenue Recovery
Project name: Recoupr

## What it solves

When a digital payment fails, merchants often lose the sale because nobody follows up at the right time. Recoupr turns each failed payment into a bounded recovery case. It diagnoses known failures with deterministic rules and sends only policy-approved recovery actions. Ambiguous failures go to Groq, while every customer-facing action passes a separate guardrail gate.

## Setup

```bash
docker compose up
```

This starts Postgres, the FastAPI backend on `http://localhost:8000`, and the dashboard on `http://localhost:3100`. The compose stack was verified end to end in this final pass. The demo uses mock Razorpay-shaped transport unless test keys are supplied; live Razorpay keys are refused at startup.

## Architecture

Webhook or simulator event → case → rule-first diagnosis → bounded decision → deterministic guardrail gate → mock/test-mode payment action → webhook-matched outcome. The gate checks action authority, amount immutability, retry/cooldown/message limits, quiet hours in the merchant timezone, consent, trusted links, and the final rendered message. It is plain Python and has no LLM or network access.

## What broke, and how we got out

The first implementation of `approve_and_send()` marked the escalation action executed and moved the case to `AWAITING_OUTCOME`, but did not call a payment client. A traced database run showed no external reference and no new attempt. The fix makes human approval create and execute the bounded payment-link or mandate action, then records its real/mock external reference, increments `attempts_count`, and starts cooldown.

The message safety check read `action_params` before the message existed. That meant the normal path checked an empty value instead of the final customer text. The fix renders the message before the gate (with a preview URL whose replacement cannot change safety) and checks the final rendered body again immediately before recording the send.

The project initially described Anthropic/Claude while the implementation had moved to Groq. We removed the stale provider references and made Groq `openai/gpt-oss-20b` the sole configured provider. An unknown-error scenario was run against the real Groq API and produced a `Diagnosis` row with `method=llm`; failures still escalate rather than fall back to an executable template.

## Known limitations

This hackathon build does not cover receivables, promise-to-pay workflows, per-customer message caps, authentication, production infrastructure/operations, or database-level audit immutability. Those are deliberately out of scope so the demo can prove the recovery decision and safety boundary clearly; the application-level audit path is append-only guarded.

Public GitHub repo: [fill in URL]

## Demo proof

Run exactly `POST /simulate/batch` with `{"count":200,"seed":42}` after the stack is up. The API and Overview are scoped to the latest simulation run. Final numbers and the recording script are in [docs/final-verification.md](docs/final-verification.md): global rate 70.16%, settled-cohort rate 54.32%, and 0 guardrail violations.
