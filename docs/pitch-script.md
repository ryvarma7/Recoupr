# Recoupr — five-minute pitch and live demo script

This script follows the actual console shown in the screenshots and the final verified seed-42 run. It is written for a live recording at `http://localhost:3100`. Keep one browser tab open and do not press **Run batch** during the recording; the final batch is already loaded.

The verified final run is: 200 cases, ₹283,397 at risk, ₹196,854 recovered, 70.16% global recovery, 54.32% settled-cohort recovery, and 0 guardrail violations. If the screen is showing an older 150-case snapshot, refresh the page before recording and use the final 200-case state.

## 0:00–0:30 — Problem and opening

**SCREEN ACTION**

Open the console and leave it on **01 OVERVIEW**. Point to the Recoupr name, the `razorpay · test mode` badge, and the live status indicator.

**WHAT I SAY**

“Revenue leakage is usually not one dramatic event. It is a failed payment, an abandoned checkout, a revoked mandate, or an invoice that quietly becomes overdue. A normal dashboard can show that money is at risk, but it does not decide what to do next. Recoupr is a bounded recovery console: it detects the event, diagnoses the reason, chooses an appropriate intervention, checks that intervention against policy, executes it when allowed, and measures the outcome. The goal is not to send more messages. The goal is to recover more money safely.”

## 0:30–1:15 — Overview: measured money, not just alerts

**SCREEN ACTION**

Point across the five Overview cards: **Recovery rate**, **₹ at risk detected**, **Money recovered**, **Escalation load**, and **False-positive rate**. Then point to the date-range inputs, the 12-day intake chart, outcome distribution, diagnosis engine, resolution channel, and gate activity cards.

**WHAT I SAY**

“The headline result is measured across a reproducible batch. ‘₹ at risk detected’ is unresolved case value, and ‘Money recovered’ is successful recovery value. Recovery rate is recovered divided by recovered plus lost; open work is deliberately excluded. In the final 200-case run, the global rate is 70.16 percent, while the settled cohort is 54.32 percent because its full observation window has elapsed. That keeps recent open cases from flattering the result.

“Escalation load shows human work, false-positive rate shows actions later labelled unrecoverable, and the date range limits the view. The outcome strip separates recovered, open, escalated, stopped, and lost. The lower tiles show rules versus LLM, payment link versus mandate retry, and gate blocks versus violations.”

## 1:15–1:55 — Case Feed and filters

**SCREEN ACTION**

Click **02 CASES**. Show the state buttons **all**, **open**, **recovered**, **lost**, **escalated**, and **stopped**. Then show **flows**, **flow A**, **flow B**, and **flow C**. Click **escalated** and open `CS-000001` from the verified seed-42 run.

**WHAT I SAY**

“The Case Feed is the operational queue. State filters answer what needs attention: open work, recovered outcomes, losses, escalations, or deliberate stops. Flow filters separate the three supported recovery patterns. Every row exposes the case reference, flow, state, amount, attempts, messages, and last update. Clicking a row opens the reasoning chain instead of forcing the operator to guess from a status label.

“This case is the deliberate failure I want to show. The mandate was revoked. Recoupr does not pretend it can repair that authorization, and it does not invent a renewal flow that is outside the product. It escalates or stops honestly, with the reason visible.”

## 1:55–2:45 — Drawer: diagnosis, decision, gate, and outcome

**SCREEN ACTION**

In the drawer, scroll through **Diagnosis**, **Decision**, **Guardrail gate**, **Actions & outcomes**, **Terminal state**, and **Audit trail — append only**. Close it, return to **all**, and open `CS-000030`.

**WHAT I SAY**

“The drawer is the complete case story. Diagnosis records root cause, confidence, method, and reasoning. Decision records the action, language, tone, and parameters. The gate shows whether it passed. Actions and outcomes show external references and recovered value, while terminal state explains how the case ended.

“This second case demonstrates AI judgment. Its error is not in the known rule table, so it reaches Groq with `openai/gpt-oss-20b`. The diagnosis is recorded as `method=llm`, here `technical_drop`. The model receives payment context and a fixed candidate list. It recommends; it cannot move money, change the amount, or bypass policy. Known codes, amounts, quiet hours, consent, retries, and message safety stay deterministic because they must be predictable and auditable.”

## 2:45–3:25 — Batch Simulator: the business result

**SCREEN ACTION**

Click **06 BATCH**. Point to **events**, **seed**, and **RUN BATCH**, then to the completed report cards and the settled-cohort note.

**WHAT I SAY**

“The Batch Simulator proves this is more than a single happy-path demo. Events controls replay size, between 10 and 300. Seed makes it repeatable. Run Batch sends synthetic events through diagnosis, decision, guardrail, execution, and outcome. The report shows case count, recovery rate, recovered rupees, mean time to recovery, false positives, and violations.

“For this submission run, the exact request was 200 events with seed 42. It produced ₹196,854 recovered from ₹283,397 still at risk, with zero guardrail violations. That is the track’s core bar: measurable recovery across a batch, not just identification of a problem.”

## 3:25–4:05 — Guardrails and policy

**SCREEN ACTION**

Click **04 GUARDRAIL**. Show **checks logged**, **blocked**, and **violations**, then one table row with a proposal and **PASS**. Scroll to the Audit stream below. If time allows, show **07 POLICY** and point to max retries, message cap, quiet hours, allowed channels, cooldown, amount immutability, TTL, link expiry, and single-use links.

**WHAT I SAY**

“The guardrail gate is intentionally not an LLM. It checks action ownership, exact amount immutability, retry and message caps, case TTL, merchant-local quiet hours, consent, trusted domains, single-use links, and the final rendered message. A block is safe rejection; a violation would mean an action bypassed a passing check. The target is zero, and the final run has zero.

“Policy is a snapshot because each case keeps the rules it was created under. Here the limits are three retries, two messages in seven days, a six-hour cooldown, a fourteen-day TTL, quiet hours, and immutable amount matching.”

## 4:05–4:35 — Human approval and audit trail

**SCREEN ACTION**

Click **03 EXCEPTIONS** and open an escalated card so **APPROVE & SEND** is visible. Then click **05 AUDIT** and show several timestamped entries.

**WHAT I SAY**

“Exceptions are the explicit human boundary. An escalated case can be reviewed and approved; approval creates the bounded action, records its reference, increments attempts, and applies cooldown. A payment counts as recovered only when its reference and exact amount match. The Audit stream records what happened, when, why, whether the gate passed, and the outcome.”

## 4:35–4:55 — What broke at 2 AM

**SCREEN ACTION**

Leave the Audit stream or the approval drawer visible.

**WHAT I SAY**

“The strongest documented late-night failure was the approval path. The first version of `approve_and_send()` only changed the state; it did not execute the payment action. We found that by tracing the database result: there was no external reference and no consumed attempt. We followed the call into execution, fixed approval to create and execute the bounded action, and added a regression test. We also found that the unsafe-content scan was checking action parameters before the final message existed. We moved rendering before the gate and scan the actual message immediately before send. That changed the design: AI can propose, but only the final rendered artifact can pass the deterministic gate.”

## 4:55–5:00 — Close

**SCREEN ACTION**

Return to **01 OVERVIEW** and leave the measured result visible.

**WHAT I SAY**

“Recoupr uses AI where ambiguity exists, deterministic rules where safety matters, and a human where the action is outside the boundary. It turns revenue risk into measured recovery with stopping rules and an audit trail. That is Recoupr for Track 03 — AI Revenue Recovery.”

## Recording runbook

1. Start the verified stack and open `http://localhost:3100`.
2. **Overview:** show ₹ at risk, recovered money, global rate, settled-cohort explanation, and zero violations.
3. **Cases:** select **escalated**, open `CS-000001`, and show the revoked-mandate failure.
4. **Cases:** select **all**, open `CS-000030`, and show the real `method=llm` diagnosis.
5. **Batch:** show events `200`, seed `42`, the completed report, and the settled-cohort note. Do not click Run Batch.
6. **Guardrail:** show checks logged, blocked proposals, zero violations, and one PASS row.
7. **Policy:** quickly show retry/message/quiet-hour/amount rules if the recording is ahead of time.
8. **Exceptions:** open an escalated card and show **Approve & Send** as the human boundary.
9. **Audit:** show the timestamped diagnosis → decision → gate → action history.
10. Return to **Overview** and close on the measured recovery result.

## Verified recording facts

- Deliberate failure: `CS-000001`, escalated because the mandate was revoked; it does not claim a recovered payment.
- Real Groq diagnosis: `CS-000030`, Diagnosis id `30`, category `technical_drop`, `method=llm`.
- Final batch: 200 cases, seed 42, ₹283,397 at risk, ₹196,854 recovered, 70.16% global recovery, 54.32% settled-cohort recovery, 0 guardrail violations.
