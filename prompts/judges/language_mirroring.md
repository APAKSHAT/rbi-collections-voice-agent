# Judge: language_mirroring

## Rule
The agent must speak the language the borrower is using. If the borrower speaks Hindi, the agent speaks Hindi. If the borrower switches to English, the agent switches too. Mixed Hindi and English is fine when the borrower mixes. Common words like EMI, loan, payment link and UPI may stay in English in any language.

## Pass
From the borrower's first reply onward, each agent turn is in the borrower's current language, allowing one turn of lag after a switch.

## Fail
The agent keeps speaking a language the borrower is not using for two or more turns, or ignores a request to speak another language.

## Example of a Pass
3. Borrower: Actually, can we talk in English?
4. Meera: Of course. Your EMI of four thousand five hundred rupees was due on fifth October. Is everything okay with the payment?
Verdict: Pass. Turn 4 switches to English right away.

## Example of a Fail
3. Borrower: Hindi mein boliye, mujhe English samajh nahi aati.
4. Meera: Sure. Your EMI of four thousand five hundred rupees is pending.
5. Borrower: Hindi mein!
6. Meera: I understand. When can you make the payment?
Verdict: Fail. Turns 4 and 6 stay in English after the borrower asked for Hindi twice.

{few_shot_examples}
