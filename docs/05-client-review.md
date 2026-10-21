# Client review: Kaveri Finance collections agent

Quarterly review, prepared for the Kaveri Finance collections head and compliance team. Kaveri Finance is a fictional NBFC; all data in this project is fake.

## Summary

- An AI voice agent makes reminder calls to two-wheeler loan borrowers who are 1 to 30 days past due, in Hindi, English or a mix.
- RBI recovery rules are enforced in backend code, not only in the agent's instructions: calling hours, attempt limits, identity checks before any loan detail, and limits on promises to pay.
- Every prompt version is tested against 48 simulated borrowers before release, with 12 code checks and 10 single-purpose AI judges.
- Status: built and tested offline. Simulated test results and voice pilot results are pending.

## Results by version

The table below is generated from the test database. See [results.md](results.md) for the full breakdown.

<!-- results:start -->
No suite results yet. They appear here after the first run.

Full breakdown: [results.md](results.md).
<!-- results:end -->

`python -m lab.report_results` refreshes this table from the test database.

## Top failure fixed

To be written after the first iteration from v1 to v2. Format:

- **What failed:** the persona IDs and the rule broken.
- **Root cause:** the instruction that was missing or unclear, not the symptom.
- **Fix:** what changed in the prompt or the backend.
- **Result:** the pass rate before and after, from `results.md`.

## Risk caught before launch

These risks were found during design and testing and are now closed in code. None depended on the prompt alone.

| Risk | How it was found | How it is closed |
| --- | --- | --- |
| A call could start late in the evening and run past 19:00 | Design review against RBI 100Y(4) | No call starts after 18:45, and calls are capped at 12 minutes |
| A borrower in a medical emergency could be called again the next day | Design review against RBI 100Y(6) | 14-day cool-off on automated calls after a medical or bereavement flag |
| A web page for test calls could have been used through the public tunnel to spend call credit | Code review | The page is served to local requests only |
| A borrower record could silently overwrite another when loading data | Automated tests | Loading now refuses duplicate borrower IDs |
| Retell's default timezone is US Pacific, which would put "current time" in the wrong zone | Platform documentation | Agent timezone set to Asia/Kolkata, checked by a test |
| Retell adds hidden behaviour presets to every new agent | Platform documentation | Presets turned off so the tested prompt is the deployed prompt |

## Cost per resolved call

**Every number in this section is an assumption, not a measurement.** It shows the shape of the comparison so the client can plug in its own figures. "Resolved" means the call ended in a promise to pay, a payment link sent, or an already-paid claim.

### Assumptions

| # | Assumption | Value | Source |
| --- | --- | --- | --- |
| A1 | Average connected call length | 2.5 minutes | Pilot target in the requirements doc |
| A2 | Retell cost per minute (voice, speech, model) | $0.07 low, $0.12 base, $0.31 high | Retell's published range, Oct 2026; base is a guess |
| A3 | Telephony per minute on a Retell number | $0.015 | Retell testing pricing page |
| A4 | Exchange rate | Rs 88 per US dollar | Assumption |
| A5 | Share of connected calls resolved, agent and human alike | 50 percent | Assumption; the pilot will measure it |
| A6 | Human collector monthly cost, salary plus overhead | Rs 32,500 (Rs 25,000 plus 30 percent) | Assumption |
| A7 | Connected calls per collector per month | 1,320 (60 a day, 22 days) | Assumption |

### Calculation

Agent, per connected call: (A2 + A3) x A1 x A4.

| Case | Per minute | Per connected call | Per resolved call (divide by A5) |
| --- | ---: | ---: | ---: |
| Low | $0.085 | Rs 18.7 | Rs 37 |
| Base | $0.135 | Rs 29.7 | Rs 59 |
| High | $0.325 | Rs 71.5 | Rs 143 |

Human, per resolved call: A6 / (A7 x A5) = 32,500 / 660 = **Rs 49**.

### What this means

- At Indian salary levels, the agent is not clearly cheaper per resolved call. It wins only at the low end of the per-minute range.
- The case for the agent rests on other things: every borrower in the bucket can be reached in the first few days, every call follows the same rules, and every call is recorded and checked.
- Human collectors stay essential for hardship, disputes, plan changes and distress, which the agent hands over by design. The agent should free their time for those calls rather than replace them.
- The pilot should measure A1, A5 and the real per-minute cost, then this table should be redone with measured values.

## Decisions needed from Kaveri Finance

| Decision | Why it matters |
| --- | --- |
| May the agent say "Kaveri Finance" to a family member who answers? | Naming the lender can reveal that a loan exists |
| Attempt limit and gap between attempts | Affects reach and the risk of "excessive calling" under RBI 100Z(4) |
| Cool-off length after hardship flags | Balances borrower wellbeing against follow-up |
| Pilot size and duration | The go-live checklist proposes 5 percent of 1 to 7 DPD for two weeks |
