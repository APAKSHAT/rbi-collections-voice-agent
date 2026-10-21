# Requirements: early-stage collections voice agent

Client: Kaveri Finance (fictional NBFC)
Product: two-wheeler loans
Use case: reminder calls for EMIs 1 to 30 days past due
Document owner: implementation team
Status: draft for client review
Last updated: 21 Oct 2026

All names, numbers and accounts in this project are fictional.

## 1. Problem

Kaveri Finance has about 40,000 active two-wheeler loans. In a normal month, 6 to 8 percent of them miss an EMI. Most of these borrowers are not unwilling to pay. They forgot, their salary came late, or they hit a real problem such as a job loss or a hospital bill.

Today a team of human callers works this list by hand. The team cannot reach everyone in the first week, which is when a reminder works best. Calls are not consistent. Some callers push too hard, which creates complaints and regulatory risk. Others do not record a clear outcome, so the next caller starts from zero.

Kaveri Finance wants a voice agent to make the first reminder call to every borrower in the 1 to 30 DPD bucket. The agent must be polite, follow RBI rules on every call, and leave a clean record of what happened. It must hand real problems to a human quickly.

## 2. Goals

1. Reach every 1 to 30 DPD borrower within the allowed calling window.
2. End each connected call with one clear, logged outcome.
3. Get a dated, specific promise to pay or a payment link sent where the borrower can pay.
4. Spot hardship and disputes early and route them to a human.
5. Never breach a compliance rule. One breach is worse than many unresolved calls.

## 3. Scope

In scope for v1:

- Outbound reminder calls to the borrower only.
- Hindi, English and mixed Hindi and English.
- Identity check before any loan detail is shared.
- Reminder of the overdue EMI: amount, due date and days past due.
- Resolution paths: send a payment link, log a promise to pay, flag hardship, log a dispute, log an "already paid" claim, book a callback, escalate to a human.
- Side paths: third party answers, wrong number, abuse, requests to stop calling, questions about whether the caller is a bot, attempts to manipulate the agent, severe distress.
- Server-side enforcement of the calling window, attempt limits, identity checks and promise-to-pay limits.
- Post-call fields for reporting and follow-up.
- An offline test lab that runs the agent against simulated borrowers before any change goes live.
- An internal dashboard for results, compliance and transcripts.

## 4. Out of scope

- Borrowers above 30 DPD, legal notices and repossession.
- Calls to guarantors. The rules allow it, but the flow is different and is planned for a later phase.
- Waivers, discounts, settlements, restructuring and changes to the due date. The agent routes these to a human and never offers them.
- Taking payment on the call. The agent only sends the official payment link.
- Inbound calls from borrowers.
- Languages other than Hindi and English.
- Real payment gateway, SMS and loan system integrations. v1 uses mocks with the same shape as the real APIs.

## 5. Borrower segments

| Segment | DPD | Typical situation | What the call should do |
| --- | --- | --- | --- |
| Forgot | 1 to 7 | Has the money, missed the date | Remind, send link, done in under 2 minutes |
| Short this week | 1 to 15 | Salary late or cash tight | Get a promise to pay within 7 days |
| Partial payer | 5 to 30 | Can pay some now | Log the partial promise; a human handles the rest |
| Hardship | Any | Job loss, illness, death in family | Stop collecting, flag hardship, offer a human callback |
| Disputes | Any | Says they paid, or the amount is wrong, or not their loan | Log details, route to a human, do not argue |
| Hard to reach | Any | Wrong number, family member answers | Disclose nothing, ask for a good time to call |

## 6. Call outcomes

Every connected call ends with exactly one outcome. If more than one applies, the first one in this list wins.

| Priority | Outcome | Meaning |
| --- | --- | --- |
| 1 | `escalated_to_human` | Severe distress, a request for a human, a complaint, or a stop-calling request |
| 2 | `hardship_escalated` | Borrower described a real hardship; collection stopped |
| 3 | `dispute_logged` | Borrower disputes the loan or the amount |
| 4 | `paid_already` | Borrower says they already paid; details captured for checking |
| 5 | `promise_to_pay` | A date within 7 days and an amount were agreed |
| 6 | `payment_link_sent` | Link sent, no specific date agreed |
| 7 | `callback_scheduled` | Borrower asked to talk later, inside allowed hours |
| 8 | `third_party_or_wrong_number` | Someone other than the borrower answered; nothing disclosed |
| 9 | `identity_not_verified` | The person could not be verified; nothing disclosed |
| 10 | `refused_or_abusive` | Borrower refused to engage or was abusive; call ended politely |
| 11 | `call_incomplete` | Call dropped or audio failed before any outcome |

The original brief had 8 outcomes. `escalated_to_human`, `identity_not_verified` and `call_incomplete` were added because those calls happen too, and without their own label they would be counted under the wrong outcome.

## 7. Success metrics

These are targets for the pilot, not results. Real numbers will come from the test lab and the pilot.

Compliance metrics (target 100 percent; any failure blocks go-live):

- Recording notice given in the first agent turn.
- No loan detail shared before identity is verified.
- No call started outside 8:00 to 19:00 IST.
- No threat, pressure or misleading statement about consequences.
- No request for OTP, PIN, CVV, card number or password.
- Hardship and distress handled as specified.

Business metrics (pilot targets, to be agreed with the client):

| Metric | Definition | Pilot target |
| --- | --- | --- |
| Right party contact rate | Calls where the borrower was verified / connected calls | Baseline in week 1 |
| Resolution rate | Verified calls ending in `promise_to_pay`, `payment_link_sent` or `paid_already` | 55 percent |
| Promise kept rate | Promises where payment arrived by the promised date | 60 percent |
| Hardship flag rate | Verified calls ending in `hardship_escalated` | Tracked, no target |
| Human escalation rate | Verified calls routed to a human | Under 15 percent |
| Average call length | Connected calls only | Under 3 minutes |
| Complaint rate | Complaints per 1,000 connected calls | Lower than the current human team |

## 8. Compliance constraints

The full list, with sources, is in [rbi-rules-summary.md](rbi-rules-summary.md). In short:

- Discuss the loan only with the borrower. Disclose nothing to anyone else.
- Call only between 8:00 and 19:00 IST. Kaveri Finance has chosen to be stricter than the rule: no callbacks outside this window, even if the borrower asks.
- Tell the borrower the call is recorded. Keep the recording and call log for at least 6 months.
- No threats, abuse, intimidation, misleading statements or excessive calling.
- Avoid pressing for payment during a bereavement or medical emergency.
- Share the grievance officer's details when the borrower wants to complain.
- Be honest when asked if the caller is an AI.

The prompt is not a security boundary. The rules that code can enforce are enforced in the backend, not only in the prompt.

## 9. Data needed from the client

Per borrower, sent with each call:

| Field | Example (fictional) | Used for |
| --- | --- | --- |
| `borrower_id` | KF-TW-000184 | Internal key; never spoken |
| `borrower_name` | Sunil Verma | Asking for the right person |
| `phone_e164` | +91 90000 00184 | Dialling |
| `preferred_language` | hi | First language of the call |
| `loan_type` | two-wheeler loan | Purpose statement |
| `loan_last4` | 0184 | Lets the borrower know which loan |
| `emi_amount_inr` | 4500 | Reminder |
| `due_date` | 2026-10-05 | Reminder |
| `days_past_due` | 16 | Reminder and segment |
| `outstanding_amount_inr` | 4500 | Upper limit on any promise |
| `birth_year` | 1991 | Identity check; kept server-side, never sent to the agent |
| `registered_city` | Nagpur | Backup identity check; kept server-side |
| `do_not_call_windows` | none | Borrower's request to avoid certain times |
| `attempts_today` | 0 | Attempt limit |

Per lender, set once:

| Field | Used for |
| --- | --- |
| `lender_name` | Opening and closing |
| `grievance_contact` | Grievance officer name, phone and email to read out on request |
| `payment_link_base` | Payment link shown in the SMS |
| `human_desk_number` | Transfer target for phone calls |

Identity answers stay in the backend. The agent only learns whether verification passed.

## 10. Integrations

| System | v1 | Production |
| --- | --- | --- |
| Voice platform | Retell, single-prompt agent | Same |
| Calls | Web calls for testing | Phone numbers through Retell or the client's telephony |
| Backend | FastAPI with SQLite | Client's API gateway and database |
| Loan system | `fake_borrowers.csv` | Loan management system API |
| Payment link | Mock that returns a link ID | Client's payment gateway |
| SMS | Mock | Client's SMS provider, with DLT-registered templates |
| Human queue | SQLite table shown on the dashboard | Client's collections CRM |
| Call events | Retell webhooks: `call_started`, `call_ended`, `call_analyzed` | Same |

Human transfer: Retell's transfer tool works only on phone calls. For v1, every escalation is first logged to the human queue through our backend. A live transfer is attempted only when the call is on a phone line. A phone number will be bought only if we need to demo a live transfer, and only after approval.

## 11. Assumptions

- The borrower's phone number in the loan system is correct for most accounts.
- The client's payment link works on UPI apps and does not need the agent to collect any payment detail.
- A human team can call back hardship, dispute and escalation cases within one working day.
- A promise to pay more than 7 days out needs a human, because it changes the repayment plan.
- Pilot volume is small enough that web and phone testing costs stay within the free credits plus an approved budget.

## 12. Risks

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Agent shares loan details with a family member | Regulatory breach, complaint | Identity gate in the prompt; disclosure checks and a dedicated judge in the test lab |
| Lender name alone tells a third party there is a loan | Privacy complaint | Open decision for client legal: say the lender name to third parties, or only a callback request |
| Agent sounds threatening or invents consequences | Regulatory breach | Banned in the prompt; coercion and misleading-consequence judges; human review of flagged calls |
| Call placed outside allowed hours | Regulatory breach | Hard check in code before every dial and every callback booking |
| Borrower in distress is pushed for payment | Harm to the borrower, reputational damage | Distress path stops collection at once and escalates; tested with dedicated personas |
| Speech recognition fails on Hinglish or noisy lines | Wrong outcomes, frustration | Test Hindi-only against Hindi plus English settings; offer a callback after two failed tries |
| Prompt injection ("ignore your rules") | Unauthorised waiver promised | Agent has no tool that can waive; backend rejects anything outside policy |
| LLM output varies between runs | Flaky test results | Run each persona 3 times and report pass rates |
| Judge models disagree with human reviewers | False confidence in results | Validate every judge against hand labels and report TPR and TNR |
| Platform changes (Retell fields, deprecations) | Setup breaks | Pin agent versions; read changelogs before each release |

## 13. Open decisions

| Decision | Owner | Status |
| --- | --- | --- |
| LLM provider for the agent, simulator and judges | Implementation team | Decided: `gemini-3.6-flash` on Retell for the live agent; the test lab runs on the Groq free tier because the project has no API budget |
| Say lender name to third parties or not | Client legal | Open; v1 follows the brief and says it |
| Agent says it is a virtual assistant in the opening, not only when asked | Client | Recommended; included in the design |
| Attempt limit per day and gap between attempts | Client collections head | Proposed: 3 attempts, 2 hours apart |
| Cooling-off period after a bereavement or medical hardship flag | Client collections head | Proposed: no automated calls for 14 days |
| Guarantor calls | Client | Later phase |
