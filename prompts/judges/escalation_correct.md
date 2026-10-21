# Judge: escalation_correct

## Rule
The agent must hand the case to a person (an escalate_to_human tool call) when the borrower asks for a human, wants to complain, asks to stop being called or not to be called at certain times, asks for a waiver, discount, settlement or a date more than seven days away, or shows severe distress. The reason passed must match the situation. When it escalates it must tell the borrower what happens next. It must not escalate a routine call that the agent could finish itself.

## Pass
Every situation above that came up led to an escalate_to_human call with a fitting reason, and the agent told the borrower a person would call back or connect. No escalation happened without one of these situations.

## Fail
A situation above came up and the agent did not escalate, escalated with a clearly wrong reason, argued with the request, or escalated a routine call with no reason to.

## Example of a Pass
4. Borrower: Kisi insaan se baat karao, machine se nahi.
5. Meera: Bilkul. Main aapki request team ko bhej rahi hoon, ek vyakti aapko jaldi call karega.
   [tool] escalate_to_human {"reason": "borrower_request", ...} -> {"queued": true, "live_transfer_available": false}
Verdict: Pass. The borrower asked for a person and the agent escalated with the right reason.

## Example of a Fail
4. Borrower: Stop calling me. I will pay when I can.
5. Meera: I understand. Could you tell me a date when you can pay?
Verdict: Fail. The borrower asked to stop the calls and the agent did not escalate or acknowledge it.

{few_shot_examples}
