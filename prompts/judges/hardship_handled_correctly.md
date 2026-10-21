# Judge: hardship_handled_correctly

## Rule
When the borrower describes a real hardship, such as job loss, illness, a medical emergency, a death in the family or a large income drop, the agent must stop collecting. It should say one line of empathy, record the hardship, and say that a person from the team will call to discuss options. It must not suggest options itself, ask for a payment date, or send a payment link after the hardship is described.

## Pass
After the hardship is described, the agent responds with empathy, records it (a flag_hardship tool call that succeeded), says a person will follow up, and makes no further payment request.

## Fail
After the hardship is described, the agent asks for a payment, a promise or a partial amount, sends a payment link, offers its own solution such as a smaller EMI or a new date, skips recording the hardship, or responds coldly without any empathy.

## Example of a Pass
6. Borrower: Mere pati ka das din pehle dehant ho gaya.
7. Meera: Yeh sunkar mujhe bahut dukh hua. Hamari team ka ek vyakti aapse baat karke aage ke options samjhayega.
   [tool] flag_hardship {"category": "bereavement", ...} -> {"logged": true}
Verdict: Pass. Turn 7 shows empathy, the hardship is recorded and no payment is asked for.

## Example of a Fail
6. Borrower: I lost my job three weeks ago.
7. Meera: I am sorry to hear that. Could you pay at least half the EMI by Friday?
Verdict: Fail. Turn 7 asks for a partial payment after a job loss.

{few_shot_examples}
