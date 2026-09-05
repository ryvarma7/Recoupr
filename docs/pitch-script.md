# Recoupr — five-minute pitch and demo script

This script is written for the verified seed-42 dashboard state. Keep the dashboard open at `http://localhost:3100`. The final run is already loaded, so the timed demo shows the completed batch instead of starting a second run during recording.

## 0:00–0:25 — Problem and product

**SCREEN ACTION**  
Open the dashboard at `http://localhost:3100`. Stay on **01 overview**.

**WHAT I SAY**  
“Revenue leakage is often a failed payment that nobody follows up on. A dashboard can tell a merchant that money is at risk, but it usually stops there. Recoupr turns that alert into a controlled recovery workflow: detect the event, diagnose it, choose an intervention, execute only if policy allows it, and measure the result.”

## 0:25–1:00 — Overview and measured outcome

**SCREEN ACTION**  
Point to **₹ at risk detected**, **Recovery rate**, **Money recovered**, and **Escalation load**. Then point to the outcome strip and diagnosis/channel tiles.

**WHAT I SAY**  
“This final run contains 200 cases. ₹283,397 is still at risk, and ₹196,854 was recovered. The global recovery rate is 70.16 percent: recovered divided by recovered plus lost. Open, escalated, and stopped cases stay out of that denominator. The more honest settled cohort is 54.32 percent, because those cases have completed their full observation window. The dashboard also shows a 10 percent escalation load, a 49.7 percent synthetic false-positive rate, and how recoveries split between payment links and mandate retries.”

## 1:00–1:35 — Case Feed and filters

**SCREEN ACTION**  
Click **02 cases**. Show the state chips and flow chips. Click **escalated**, then click **CS-000001**. If needed, use the browser find function for `CS-000001` within the case table.

**WHAT I SAY**  
“The Case Feed is the working queue. The state filters answer ‘what needs attention now?’ and the A, B, and C filters answer ‘which recovery flow is involved?’ A row opens the full case drawer. This case is a deliberate safe failure: its mandate was revoked, so Recoupr escalates instead of inventing a renewal link or retrying an authorization that no longer exists.”

## 1:35–2:15 — Drawer, AI judgment, and bounded action

**SCREEN ACTION**  
Close the drawer. Filter back to **all**, then open **CS-000030**. Scroll the drawer through **Diagnosis**, **Decision**, **Guardrail gate**, and **Actions & outcomes**.

**WHAT I SAY**  
“Here is the AI path. This case has an unfamiliar failure, so the deterministic rule table could not classify it. Groq, using `openai/gpt-oss-20b`, returned a structured diagnosis recorded as `method=llm`, here as `technical_drop`. The model receives the event, amount, error details, flow, attempt number, and a fixed candidate list. It can choose among bounded options, but it cannot execute, change the amount, or bypass policy. Known codes, state transitions, amounts, quiet hours, consent, and the guardrail gate do not need AI.”

## 2:15–2:55 — Batch Simulator

**SCREEN ACTION**  
Click **06 batch**. Show the fields **events**, **seed**, **run batch**, the completed log, and the six result cells. Point to the settled-cohort note.

**WHAT I SAY**  
“The batch simulator is the measured-money proof. Events controls the replay size, from 10 to 300; seed makes the result repeatable; Run batch sends synthetic A, B, and C events through the same diagnosis, decision, gate, execution, and outcome code. The report shows case count, recovery rate, recovered rupees, mean time to recovery, false positives, and violations. This run produced 87 recovered, 37 lost, 20 escalated, 13 stopped, 43 pending, and zero guardrail violations.”

## 2:55–3:35 — Compliance and stopping rules

**SCREEN ACTION**  
Click **04 guardrail**. Point to **checks logged**, **blocked**, **violations**, and a blocked-rule row.

**WHAT I SAY**  
“The gate is intentionally deterministic. It checks that the flow owns the action, the amount is unchanged, retry and message caps are respected, the case is inside its TTL, the merchant’s local quiet hours allow contact, consent and sender identity exist, the link is trusted and single-use, and the final rendered message contains no credential request. Timing problems defer. Other violations escalate. A block is the gate working; a violation would mean an executed action bypassed a passing check. The target is zero, and this run has zero.”

## 3:35–4:10 — Audit and human approval

**SCREEN ACTION**  
Click **05 audit** and scroll a few entries. Then return to **03 exceptions** and open an escalated card with **Approve & Send** visible, without confirming it unless you want to demonstrate the action.

**WHAT I SAY**  
“The audit stream records the state transition, diagnosis, proposal, gate verdict, action, actor, timestamp, and outcome. For an escalated case, Approve & Send is the human boundary. Approval creates the bounded action and records its external reference, attempt count, cooldown, and human actor. Recovery is not stamped because a link was created; it requires a matching payment event with the exact case amount.”

## 4:10–4:40 — What broke at 2 AM

**SCREEN ACTION**  
Keep the audit stream visible, or show the case drawer’s action section.

**WHAT I SAY**  
“The most important development failure was in approval. The first version of `approve_and_send()` only flipped the state. A database trace showed no external reference and no consumed attempt. We followed the path into the execution layer and fixed approval to create and execute the bounded payment action. We found a second related gap: the content scan checked action parameters before the message existed. We moved rendering before the gate and added a final scan before send. Both paths now have regression tests.”

## 4:40–5:00 — Close

**SCREEN ACTION**  
Return to **01 overview** and leave the metrics visible.

**WHAT I SAY**  
“This is why Recoupr uses AI selectively. AI handles ambiguity, deterministic policy controls money movement, and humans handle exceptions. The result is not just a list of failed payments: it is measured recovery, compliant stopping, and an audit trail that explains every decision. That is Recoupr for Track 03 — AI Revenue Recovery.”

## Recording runbook

1. Start the verified stack and open `http://localhost:3100`.
2. Overview: show ₹ at risk, recovered money, global rate, settled rate, and zero violations.
3. Cases: filter `escalated`, open `CS-000001`, show revoked-mandate escalation.
4. Cases: open `CS-000030`, show `method=llm` and the technical-drop diagnosis.
5. Batch: show events `200`, seed `42`, completed report, settled cohort, and result cells.
6. Guardrail: show checks, blocked proposals, zero violations, and a rule row.
7. Audit: show timestamped diagnosis → decision → gate → action history.
8. Return to Overview and close on the measured result.
