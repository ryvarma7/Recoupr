# Final verification handoff

Verified 2026-09-05 against native SQLite with the real Groq key enabled. The final DB was wiped first, then exactly one batch was run: `POST /simulate/batch` with `{"count":200,"seed":42}`.

## Recorded results

- Run ID: `1`
- Cases: `200`
- Global recovery rate: `70.16%` (`87 recovered / 124 recovered+lost`)
- Settled-cohort recovery rate: `54.32%` (`44 / 81`), the uncensored rate used for the believable 35–55% demo band
- Guardrail violations: `0`
- Guardrail blocks: `113`
- Diagnosis split: `421 rule`, `21 llm`, `0 fallback`
- At-risk headline: `₹283,397`

## Recording cases

- Deliberate failure: `CS-000001` / case `1`. It escalates immediately because the mandate was revoked and customer re-authorisation is outside the bounded action set. Open its drawer to show `ESCALATED_TO_HUMAN`, `attempts_count=0`, and the honest terminal reason.
- Real Groq diagnosis: `CS-000030` / case `30`. Database query returned `Diagnosis.id=30`, `method=llm`, category `technical_drop`.

## Direct evidence from the verification pass

- `/health` returned `{"status":"ok"}` natively and in Docker.
- Real Groq unknown-code scenario returned `case_id=1`, `Diagnosis.id=1`, `method=llm`, category `unknown`.
- Approved escalated case query returned `case_id=2`, `external_ref=plink_5005c3579c`, `attempts_count=4`, `cooldown_until=2026-08-21T03:45:00`.
- Wrong amount query attempted `120001` against case amount `120000`; it raised `payment amount 120001 does not exactly match case amount 120000`, with outcomes unchanged at `1` and state `AWAITING_OUTCOME`.
- Full test suite: `68 passed in 6.89s`.
- Docker: `recoupr-db-1` healthy; backend and dashboard running; backend HTTP 200; dashboard HTTP 200.

## Five-minute recording script

1. **0:00–0:40 — Overview.** Open `http://localhost:3100`. Say: “Failed payments are revenue at risk; Recoupr turns each into a bounded case and only executes actions that pass the deterministic gate.” Point to the `₹ at risk detected` headline and `54.32%` settled-cohort rate.
2. **0:40–1:30 — Live run.** Open Batch Simulator, show `count 200` and `seed 42`, run it, then point to `0 guardrail violations`, the rule/LLM split, and the outcome strip.
3. **1:30–2:20 — Architecture.** Scroll to the guardrail and audit sections. Say: “Known failures stay in rules; ambiguous failures go to Groq; the LLM proposes but cannot execute; the plain-Python gate checks authority, amount, timing, consent, link trust, and the final message.”
4. **2:20–3:20 — Deliberate failure.** In Exceptions open `CS-000001`. Say: “This is a deliberate safe stop: the mandate was revoked, so the system escalates instead of pretending a retry or renewal link exists.” Show the distinct escalation label and audit reason.
5. **3:20–4:20 — AI judgment.** In the case feed open `CS-000030`. Show Diagnosis `technical_drop · llm` and say: “This was not a known error code, so the real Groq path ran; the resulting diagnosis is auditable.”
6. **4:20–5:00 — Close.** Show the guardrail count and audit stream. Say: “The important boundary is that AI handles ambiguity, while deterministic policy controls money movement. When either step fails, the case escalates and nothing executes.”
