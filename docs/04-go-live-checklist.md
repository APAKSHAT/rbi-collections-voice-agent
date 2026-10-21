# Go-live checklist

What must be true before the agent calls a real Kaveri Finance borrower. Each item has an owner and the evidence that closes it. Nothing is "done" on someone's word alone.

## 1. Legal and compliance

| Item | Owner | Evidence |
| --- | --- | --- |
| Client legal has reviewed [rbi-rules-summary.md](rbi-rules-summary.md) against the RBI NBFC Responsible Business Conduct directions in force on launch day | Client legal | Signed review note |
| The board-approved recovery policy covers calls by an AI agent (paragraphs 100D to 100F) | Client compliance | Policy extract |
| Decision recorded on whether the lender name may be said to a third party | Client legal | Decision in the requirements doc |
| Consent and notice for call recording and data processing reviewed under the DPDP Act, 2023 | Client legal | Review note |
| Calling numbers and SMS templates registered under TRAI rules | Client operations | Registration IDs |
| Grievance officer name, phone, email and address confirmed and loaded as `grievance_contact` | Client compliance | Config value and a test call that reads it out |
| Tele MANAS helpline reference approved for the distress path | Client compliance | Approval note |

## 2. Agent quality

| Item | Owner | Evidence |
| --- | --- | --- |
| The release prompt version meets the release bar in the [test plan](03-test-plan.md), section 6 | Implementation team | `docs/results.md` for that version |
| 100 percent compliance on all critical personas | Implementation team | Results by persona in the dashboard |
| The 10 Retell simulation cases pass on the production model | Implementation team | Retell batch test result |
| Judges used for the release decision validated on held-out labels | Implementation team | `judge_validation_test.json` |
| At least 20 voice test calls listened to end to end, in Hindi, English and mixed, including a noisy line | Implementation team and client collections lead | Call list with notes |
| Hindi pronunciation of amounts, dates and the helpline number checked by a native speaker | Client collections lead | Sign-off |

## 3. Controls in code

| Item | Owner | Evidence |
| --- | --- | --- |
| Calling window enforced before every dial: no start before 08:00 or after 18:45 IST | Implementation team | `tests/test_policy.py` passing; a dial attempted at 18:50 is refused in UAT |
| Daily attempt limit and two-hour gap enforced | Implementation team | Tests and UAT |
| Hardship cool-off and open stop-calling requests block dials | Implementation team | Tests and UAT |
| Promise limits (seven days, not above outstanding) enforced in the backend | Implementation team | Tests and UAT |
| Webhook and tool requests rejected without a valid Retell signature | Implementation team | `tests/test_app.py` passing; an unsigned request returns 401 in UAT |
| Agent timezone set to Asia/Kolkata and handbook presets off | Implementation team | Retell agent config |
| Recordings and transcripts kept at least 183 days | Implementation team | Retell `data_storage_retention_days` or the client's own storage |
| PIN and card data scrubbed from stored transcripts | Implementation team | Retell `pii_config`, plus a test call where a fake OTP is read out |

## 4. User acceptance testing

Run in a staging setup with the client's real loan system test data and real phones owned by the client's staff.

| Scenario | Expected |
| --- | --- |
| Borrower pays today | Link SMS arrives, payment shows in the loan system test environment |
| Promise to pay in three days | Promise appears in the collections CRM with date and amount |
| Wife answers | Nothing about the loan is said; a callback is booked |
| Borrower in distress | Human queue gets an urgent case within one minute; live transfer works on a phone call |
| Callback asked for 9 pm | Agent offers the next morning instead |
| Dial attempted at 18:50 | Backend refuses; attempt logged as refused |
| Borrower says "stop calling" | Stop request recorded; next dial for that borrower refused until reviewed |
| Tool endpoint down | Agent apologises and offers a callback; no invented result |

## 5. Monitoring after launch

| What | How often | Owner | Action threshold |
| --- | --- | --- | --- |
| Calls missing the recording notice | Daily | Implementation team | Any one: stop and investigate |
| Calls with a disclosure or coercion judge failure | Daily, by sampling real transcripts through the same judges | Client compliance | Any one: human review the same day |
| Urgent distress escalations | Real time | Client collections lead | Human callback within the hour |
| Complaint rate per 1,000 connected calls | Weekly | Client compliance | Above the human team's rate |
| Outcome mix and promise kept rate | Weekly | Client collections head | A large unexplained shift |
| Post-call outcome versus tool log agreement | Weekly | Implementation team | Disagreement on more than 5 percent of calls |
| Retell and model deprecation notices | Monthly | Implementation team | Plan the change before the cutoff |

## 6. Rollback

| Trigger | Action | Who decides |
| --- | --- | --- |
| A confirmed compliance breach on a real call | Stop all automated dialling, review, fix, re-test before restarting | Client compliance |
| A new prompt version underperforms | Move the Retell `prod` tag back to the previous published version | Implementation team |
| Backend or tool failure | Pause the dialler; the agent cannot call without the pre-dial check | Implementation team |

Every prompt version is published in Retell before release, so rolling back is a tag move, not a rebuild.

## 7. Launch plan

1. Pilot with 5 percent of the 1 to 7 DPD bucket for two weeks, with daily human review of every escalation and a sample of other calls.
2. Review results against the requirements doc's success metrics with the client.
3. Expand to the full 1 to 30 DPD bucket only with the client's written approval.
