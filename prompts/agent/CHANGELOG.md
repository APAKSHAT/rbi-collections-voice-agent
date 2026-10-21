# Agent prompt changelog

Each version records what changed, why, and what the test results were. Versions are never edited after they are tested. A fix goes into the next version.

## v4 (21 Oct 2026)

Files: `v4-hi.md` and `v4-en.md`. Two one-line changes, each rewriting the sentence that caused the failure. No rule was added.

### What failed, and why

| # | Evidence | What happened | Root cause |
| --- | --- | --- | --- |
| 1 | Hindi voice test `call_fa0d0b53a953f9b2b9f8c8532e8` | Asked "will you pay tomorrow?" and logged the promise in the same turn, before the caller answered | "Confirm the date and amount aloud, then log the promise" reads as one action and never says to wait for a yes |
| 2 | Same call | Closed by thanking the caller "for calling Kaveri Finance" | The close step said only "thank them", so the model used a stock inbound-call phrase |

### What changed

| Step | v3 | v4 |
| --- | --- | --- |
| 5, can pay by a date | "confirm the date and amount aloud, then log the promise" | "say back the date and amount and ask if that is right. Log the promise only after they say yes" |
| 6, close | "thank them, and end the call" | "thank them for their time, since you are the one who called, and end the call" |

### Results

Pending: voice test of both agents.


## v3 (21 Oct 2026): one prompt per language

Files: `v3-hi.md` for the Hindi agent and `v3-en.md` for the English agent. Each runs on its own Retell agent.

### What failed, and why

| # | Evidence | What happened | Root cause |
| --- | --- | --- | --- |
| 1 | Voice test calls `call_ff6daa6c1780ffe475fd8f7a47c`, `call_ceabe2b99aa6d22bcd1d2e7ca64` | Every identity check said "not matching", even when the agent heard 1991 correctly | Not the prompt. The backend fix for dashboard test calls had been written but the running server was never restarted, so the old code answered "account not found". Fixed by `scripts/dev.sh`, which always starts the current code. |
| 2 | Same calls | Speech-to-text turned 1991 into 1946 and 1974, and Nagpur into "Dagpur" | Numbers and names are easy to mishear on a call, and v2 sent what it heard straight to the check, using up an attempt. |
| 3 | Listener feedback | Asking "can you repeat" or "who is this" did not get a clear answer | v2 had no instruction for a repeat request or a "who is calling" question at the opening. |
| 4 | Listener feedback; Retell's multilingual guidance | Hinglish was often misheard | One agent listened for both Hindi and English, which routes speech recognition to Retell's less accurate multilingual pipeline. Retell recommends one language per agent when the language is known before the call, which it is. |

### What changed

| Area | v2 | v3 |
| --- | --- | --- |
| Agents | One agent, `hi-IN` plus `en-IN` | A Hindi agent (`hi-IN`) and an English agent (`en-IN`); the backend picks one from the borrower's language |
| Language section | "Start in {{preferred_language}}" | Hindi prompt: the call is in Hindi, mixing in English words is normal. English prompt: simple Indian English. Each switches if the borrower speaks the other language for two turns. |
| Identity step | Check the year as heard | Say back the year heard and ask if it is right; check only after they confirm; use their correction if they give one |
| Repeat requests | Not covered | "If they ask you to repeat, say your last point again in simpler words." At the opening, a "who is calling" question gets the name and lender again. |
| Examples | Hindi only | Hindi examples in the Hindi prompt, English examples in the English prompt; both show the read-back |

Voice settings changed in the same round (not the prompt): speech 1.15x, steadier voice, accurate speech recognition.

### Results

Voice test, Hindi agent, call `call_fa0d0b53a953f9b2b9f8c8532e8` (21 Oct 2026): the listener said it works. The agent read the year back ("उन्नीस सौ इक्यानवे, सही?"), the caller confirmed, identity passed, and a promise to pay was logged. The caller first refused and complained; the agent stayed calm and did not pressure.

Found for the next version:

- The agent asked "will you pay tomorrow?" and logged the promise in the same turn, before the caller said yes. Root cause: "confirm the date and amount aloud, then log the promise" does not say to wait for the answer, the same gap v2 had in the identity step.
- The closing line thanked the caller "for calling Kaveri Finance", but the agent made the call. No closing wording is given, so the model reached for a stock phrase.

Pending: English agent voice test, then the v3 lab run.


## v2 (21 Oct 2026)

Made after the first voice test calls on Retell and a three-call lab smoke test. The full v1 lab run continues in the background as the v1 baseline.

### What failed, and why

| # | Evidence | What happened | Root cause |
| --- | --- | --- | --- |
| 1 | Voice test call `call_f4998d1739a3320d73e07a498b9`; listener feedback | The Hindi sounded foreign and "off" | v1 told the agent to write Hindi in Roman script. The voice engine reads Roman Hindi like English words. The caller's own speech came back from speech-to-text in Devanagari, so the two sides were in different scripts. |
| 2 | Same call | The agent asked for the year of birth and called `verify_identity` in the same turn, before the caller answered, with no answer argument | The identity step said "ask their year of birth and call verify_identity", which reads as one action. Nothing said to wait for the answer. |
| 3 | Smoke test, `wife_answers` | The agent said "6 baje" with a digit | The speaking rule covered amounts and dates but not times. |
| 4 | Smoke test, all three calls; `natural_and_concise` judge | Turns of three sentences | v1's own example for the purpose turn had three sentences, which contradicts the one-or-two sentence rule. Models copy examples closely. |

Not prompt problems, fixed elsewhere in the same change:

- The backend could not tell which borrower a Retell dashboard test call was for, so the identity tool answered "account not found" and the agent ended the call. Every custom tool now carries `borrower_id` as a fixed argument filled from the call's variables (`"const": "{{borrower_id}}"`), which the model never sees.
- The fixed opening line is now in Devanagari, for the same reason as finding 1.
- The simulated borrower now writes Hindi in Devanagari, matching what speech-to-text sends the agent on a real call.
- Code checks now recognise Devanagari: disclosure words, the recording notice, OTP and PIN requests and their negations.
- Two lab harness bugs from the smoke test: a silence marker leaking into the borrower's words, and the conciseness judge grading the fixed opening line.

### What changed in the prompt

| Section | v1 | v2 |
| --- | --- | --- |
| Language | "Write Hindi in Roman script" | Write Hindi in Devanagari so the voice pronounces it correctly; keep EMI, loan, payment link, UPI and SMS in English letters |
| Personality and tone | Amounts and dates in words | Amounts, dates and times in words, with Devanagari and English examples |
| Conversation flow, step 2 | "ask their year of birth and call verify_identity" | "ask their year of birth and wait for their answer. Then call verify_identity with the year they said." |
| Hardship and escalation | Helpline as "one four four one six" | Helpline read one digit at a time, in whichever language the call is in |
| Examples | Roman Hindi; purpose turn of three sentences | Devanagari; purpose turn of two sentences |

No rule was added. Each fix rewrites the instruction that caused the failure.

### Results

Pending: voice re-test on Retell, then the v2 lab run.


## v1 (21 Oct 2026)

First version, written from [02-conversation-design.md](../../docs/02-conversation-design.md).

Length: 1,017 words, against a target of under 900. See "Known gaps" below.

### How it was written

- Structure follows the Retell, Vapi and OpenAI realtime guides: role, tone, language, context, flow, tools, guardrails, escalation, examples. Each section has one job.
- Each instruction is stated once. The third person rule lives only in the first guardrail, and the opening step points to it.
- Capitals are used for four rules only: no disclosure before identity, no threats or pressure, no request for OTP or PIN, and severe distress.
- Tool descriptions in `tools.json` say what each tool does. The prompt says when to use it. This follows Retell's single-prompt guidance.
- Lines to say while a tool runs are set on the tools (`speak_during_execution`), not in the prompt. This saves words, and the platform plays them reliably.
- The first line is fixed text (`begin_message` set to `{{opening_line}}`), not generated. The recording notice is guaranteed by config, not by the model.
- Retell's handbook presets are turned off. They add hidden text to the prompt, which the test lab would not see.

### Critique pass

Method: I reviewed the prompt by hand against the four questions in the OpenAI "Instructions Quality" critique prompt: ambiguity, missing definitions, conflicting or vague instructions, and unstated assumptions. No LLM API key was available yet, so this was not a model-run critique. Re-run it with a model once a key is added.

Issues found and fixed:

| # | Type | Issue | Fix |
| --- | --- | --- | --- |
| 1 | Ambiguity | "Never as digits" could be read as applying to tool arguments, which must be digits. | Scoped to speech: "When speaking, say amounts and dates..." |
| 2 | Undefined term | The backup identity question said "the city where the loan was taken". The tool and the record use "registered city". | Changed to "registered city". |
| 3 | Missing instruction | Nothing said what to do if the person refuses the identity question or asks why. The design doc covers this; it was lost while cutting length. | Added: explain it protects their account; two refusals count as a failed check. |
| 4 | Instruction only in an example | Booking a callback when a third person answers appeared in the example but not in the rules. | Added to the first guardrail. |
| 5 | Ambiguity | "Hardship" did not separate a real hardship from "salary is a week late". The second should end in a promise to pay, not a hardship flag. | Defined: a short delay they can fix within seven days is not hardship. |
| 6 | Conflict | A date more than seven days away had two instructions: escalate as a plan change, and "offer the nearest option the tool allows". | Ordered them: first ask if the seventh day works, then escalate if not. |
| 7 | Unstated assumption | The model was expected to turn "Friday" into a date without being told to use the current time. | Added to the context line for current time. |
| 8 | Unstated assumption | "Pass any other question to the team" named an action with no tool behind it. | Changed to: say you do not have that detail. |
| 9 | Ambiguity | "Call each tool once per decision" conflicted with calling the identity check twice. | Changed to: never repeat a tool call that already succeeded. |
| 10 | Missing instruction | RBI 100Y(4) requires honouring a request not to be called at certain times. The prompt only covered "stop calling". | Added to the stop-calling line, with the details passed to the escalation note. |

Checked and not changed:

- "Escalate with reason X" names the reason but not the tool. The tool's name and description make the mapping clear, and Vapi advises describing actions in prose rather than naming tools. The lab will confirm whether this holds.
- "Do not hang up on them" in the distress rule could make these calls long. That is acceptable for safety. The distress personas will show whether it causes loops.
- The four capitalised rules overlap with tool-side enforcement. This is deliberate: the prompt is not a security boundary, and the backend enforces what it can.

### Known gaps, to be settled by testing

- **Length.** v1 is 1,017 words, about 117 over target. The extra words are compliance and edge-case rules from the brief, plus the 10 critique fixes. Rather than cut rules blindly, v2 will remove lines the lab shows the model follows without being told.
- **Script for Hindi.** v1 writes Hindi in Roman script so the prompt and transcripts are easy to review. Some voices pronounce Roman Hindi poorly. The first real web call decides whether v2 switches to Devanagari.
- **Language setting.** The agent uses `["hi-IN", "en-IN"]`, which sends speech recognition through Retell's multilingual pipeline. Compare with `hi-IN` alone on real calls.
- **Model.** The live agent runs `gemini-3.6-flash` on Retell. The project has no budget for API keys, so the test lab runs the prompt on open models through the Groq free tier (`qwen/qwen3.8-27b` as the agent). The lab therefore tests the prompt on a different model than production. The 10 Retell test cases, run once in Retell's own simulator on the free signup credit, check the exact production model. The prompt itself did not change. The live agent's temperature is 1.0, as Google recommends for Gemini 3 models; the lab agent uses 0.3.

### Test results

Not run yet. The Stress Test Lab is built in Phase 4.
