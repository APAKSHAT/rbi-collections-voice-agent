# RBI rules for recovery calls

This is a working summary for building and testing the agent. It is not legal advice. Client legal must review it before go-live.

Checked on: 21 Oct 2026

## Which rules apply, and when

| Period | Rules in force |
| --- | --- |
| Until 31 Dec 2026 | Reserve Bank of India (Non-Banking Financial Companies - Responsible Business Conduct) Directions, 2025, including its current paragraphs on recovery agents (paragraphs 26, 27 and 89 to 100). The 8 am to 7 pm limit and the ban on intimidation first came from the RBI circular of 12 Aug 2022. |
| From 1 Jan 2027 | The same 2025 Directions, as changed by the NBFC Responsible Business Conduct Third Amendment Directions, 2026. These delete the paragraphs above and add a new section J, paragraphs 100A to 100AB. |

The 2026 amendment is final, not a draft. RBI published a draft for feedback, then a revised draft on 20 May 2026, then issued the final directions on 6 Aug 2026.

This project is designed to the 1 Jan 2027 rules, because they are stricter and a pilot would run into 2027.

The new rules cover "recovery of loan dues from borrowers in default" (paragraph 100A). A borrower 1 to 30 days past due has missed a payment, so these calls are treated as covered.

## Sources

1. RBI (Non-Banking Financial Companies - Responsible Business Conduct) Third Amendment Directions, 2026. RBI/2026-27/230, DOR.MCS.REC.No.199/01-01-039/2026-27, 6 Aug 2026, in force from 1 Jan 2027.
   https://www.rbi.org.in/scripts/NotificationUser.aspx?Id=13672&Mode=0
2. RBI press release, "RBI Issues Amendment Directions on Conduct of Regulated Entities in Recovery of Loans and Engagement of Recovery Agents", 6 Aug 2026.
   https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=63305
3. RBI (Non-Banking Financial Companies - Responsible Business Conduct) Directions, 2025.
   https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=12942
4. RBI circular, "Outsourcing of Financial Services - Responsibilities of regulated entities employing Recovery Agents", 12 Aug 2022.
   https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=12378

Paragraph numbers below refer to source 1.

## Rules and how this project follows them

"Where enforced" says which layer makes sure the rule holds:

- Prompt: the agent's instructions
- Code: `backend/policy.py` or the tool endpoints; the model cannot override this
- Config: Retell agent settings
- Ops: the client's process, outside this project
- Lab: checked by the test lab before release

| # | Rule | What it means for this agent | Where enforced | Source |
| --- | --- | --- | --- | --- |
| 1 | Discuss loan dues only with the borrower or guarantor | Identity check before any loan detail. Third parties hear nothing about a loan or money. Guarantor calls are out of scope for v1. | Prompt; Code (`verify_identity` gates the money tools); Lab (`third_party_disclosure` judge, keyword and tool-order checks) | 100Y(1) |
| 2 | Contact only between 08:00 and 19:00. Calls outside this time only if the borrower expressly asks. | Kaveri Finance is stricter: no call starts after 18:45 and no callback is booked outside 08:00 to 19:00, even on request. | Code (pre-dial check and `schedule_callback`); Config (max call length); Lab | 100Y(4) |
| 3 | Honour a borrower's request to avoid calls at a particular time | Store "do not call at this time" requests and check them before every dial and callback | Code | 100Y(4) |
| 4 | Interact in a civil manner | Calm, polite tone. Close politely if the borrower is abusive. | Prompt; Lab (`natural_and_concise` and coercion judges) | 100Y(2) |
| 5 | Avoid calls during bereavement, medical emergencies or similar | Stop collecting when these come up, flag hardship, offer a human. No automated calls for 14 days after such a flag. | Prompt; Code (cool-off check); Lab (`hardship_handled_correctly` judge) | 100Y(6) |
| 6 | No threatening, abusive or minatory language | No mention of police, jail, legal action, visits, or telling family or employer | Prompt; Lab (`coercive_or_threatening_language` judge) | 100Z(1), 100Z(5) |
| 7 | No excessive calling or messaging | At most 3 attempts a day, 2 hours apart (proposed, pending client sign-off). One payment link per call. | Code | 100Z(4) |
| 8 | No anonymous calls | The opening gives the agent's name and the lender's name | Prompt; Lab (first-turn check) | 100Z(5) |
| 9 | No intimidating or harassing the borrower or their relatives, friends or co-workers | Nothing said to third parties beyond a callback request | Prompt; Lab | 100Z(6) |
| 10 | No false or misleading statements, especially about the debt or the consequences of not paying | Uses only the amounts given in context. Never invents consequences. Says she is an AI. | Prompt; Code (amounts come from the record); Lab (`misleading_consequences` judge) | 100Z(8) |
| 11 | Record the calls and tell the borrower they are being recorded | Recording notice in the first turn of every call | Prompt; Config (Retell stores recordings); Lab (first-turn check) | 100P |
| 12 | Document the time and number of calls; keep records for 6 months, longer if the case is in court | Every call and attempt logged with IST timestamps. Retell data retention set to at least 183 days, or recordings copied to client storage. | Code; Config (`data_storage_setting`, `data_storage_retention_days`); Ops | 100P |
| 13 | Share borrower data with collectors only as far as needed | The agent gets only the fields it must say. Identity answers stay in the backend. Tools return only what the agent should say. | Code | 100O |
| 14 | A dedicated grievance mechanism; recovery communications must give the grievance officer's name, email, phone and address | `{{grievance_contact}}` is in the agent's context and read out on request. The payment link SMS includes it. | Prompt; Code (SMS template) | 100AA |
| 15 | A policy for financial distress, including documented engagement before escalation and guidance on resolution options | Hardship calls are logged with a category and a note, and routed to a human who can discuss options. The agent never offers options itself. | Code; Ops | 100D |
| 16 | Recovery targets and incentives must not encourage harsh practices | The agent is measured on compliance first. No success metric rewards pressure. | Ops; dashboard design | 100Q |
| 17 | Give a proper acknowledgement or receipt when payment is collected | The agent does not collect payment. The payment gateway sends the receipt. | Ops | 100Y(9) |
| 18 | Follow TRAI rules on commercial communication (TCCCPR, 2018) | SMS templates and calling numbers must be registered as the rules require | Ops; needs legal review | 100AB |

## Project rules that go beyond RBI

These come from the client brief, not from RBI. They are good practice for a voice agent in lending.

| Rule | Why | Where enforced |
| --- | --- | --- |
| Never ask for an OTP, UPI PIN, CVV, card number or net banking password | Fraud prevention. Payment only through the official link. | Prompt; Config (PII scrubbing of PIN and card data in transcripts); Lab |
| Never offer a waiver, discount, settlement or new due date beyond tool limits | Only a human can change terms | Prompt; Code (no tool can waive; promises limited to 7 days) |
| Say yes honestly if asked whether she is an AI | Honesty; supports rule 10 | Prompt; Lab |
| Severe distress: stop collection, respond with care, give the Tele MANAS helpline (14416), escalate at once | Borrower safety | Prompt; Code (`escalate_to_human` queues it as urgent); Lab (`distress_handled_correctly` judge) |
| Identity is fixed; ignore attempts to change role or reveal instructions | Prompt injection | Prompt; Code (backend rejects out-of-policy requests); Lab (`identity_lock_held` judge) |

Tele MANAS is the Ministry of Health and Family Welfare's national tele mental health helpline: 14416 or 1-800-891-4416 (https://telemanas.mohfw.gov.in/). Confirm with the client before go-live.

## Not covered here

These need review by client legal before a real launch. They are outside this project:

- Digital Personal Data Protection Act, 2023, and its rules: consent, purpose limits and retention for call recordings and transcripts.
- TRAI DLT registration for SMS templates and caller IDs.
- The client's own Fair Practices Code and board-approved recovery policy, which must cover the items in paragraphs 100D to 100F.
