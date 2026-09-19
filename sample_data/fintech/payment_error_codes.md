# Kestrel Pay Payment Error Codes

*Synthetic demo document — fictional company, not real data.*

Reference for customer support staff and API integrators. Last updated 1 August 2026. When a payment fails, the Kestrel Pay app and API return one of the codes below. Each entry explains what the code means and how to resolve it.

## E-101 Insufficient funds

**Meaning:** The account does not have enough available funds to cover the payment and any fee.

**Resolution:** Ask the customer to add funds or reduce the amount, then retry. Pending card transactions reduce the available balance, so the available balance may be lower than the current balance.

## E-102 Daily transfer limit exceeded

**Meaning:** The payment would take the customer over their daily outgoing transfer limit.

**Resolution:** The customer can wait until midnight (Sydney time), when the limit resets, or request a higher limit in the app under Settings > Limits. See the Customer FAQ for the default limits.

## E-115 Invalid BSB or account number

**Meaning:** The BSB or account number entered does not exist or has the wrong format.

**Resolution:** Check the details with the payee. A BSB must have 6 digits and an account number between 6 and 9 digits.

## E-130 Payee name mismatch

**Meaning:** The account name entered does not match the name held by the receiving bank (Confirmation of Payee check).

**Resolution:** Ask the customer to confirm the payee's exact account name. The customer may still choose to send the payment, but Kestrel Pay cannot recover funds sent to the wrong person after a mismatch warning.

## E-204 Beneficiary account closed

**Meaning:** The receiving bank reports that the beneficiary's account has been closed, so the payment cannot be credited.

**Resolution:** Ask the customer to contact the recipient and obtain new account details before sending again. The original payment is returned automatically to the customer's account within 3 to 5 business days. For international transfers, the 1.5% transfer fee is refunded, but fees deducted by intermediary banks cannot be recovered.

## E-205 Rejected by beneficiary bank

**Meaning:** The receiving bank rejected the payment without giving a specific reason.

**Resolution:** Ask the customer to confirm the details with the recipient, who should contact their own bank. Funds are returned within 3 to 5 business days.

## E-219 Compliance screening hold

**Meaning:** The payment matched a sanctions or fraud screening rule and is on hold for manual review.

**Resolution:** No action is required from the customer. The Compliance team reviews held payments within 2 business days. Support staff must not tell the customer which screening rule was matched.

## E-301 Card expired

**Meaning:** The card used for the payment has passed its expiry date.

**Resolution:** The customer should use the replacement card sent automatically 6 weeks before expiry, or order a new card in the app.

## E-302 Card blocked

**Meaning:** The card has been blocked because it was reported lost or stolen, or frozen by the customer in the app.

**Resolution:** If the card was frozen by the customer, they can unfreeze it in the app. A card reported lost or stolen cannot be reactivated; a replacement card must be ordered.

## E-307 Card authentication failed

**Meaning:** The customer did not complete the 3-D Secure verification step for an online card payment, or entered the wrong one-time code.

**Resolution:** Ask the customer to retry and approve the payment in the Kestrel Pay app. After 3 failed attempts the card is locked for online payments for 30 minutes.

## E-410 FX quote expired

**Meaning:** The foreign exchange quote was not confirmed within 30 seconds.

**Resolution:** Request a new quote and confirm it within 30 seconds.

## E-502 Upstream network timeout

**Meaning:** The payment network did not respond in time. The payment has not been sent.

**Resolution:** Retry after 15 minutes. If the error continues for more than 1 hour, check the Kestrel Pay status page and escalate to the Payments Operations team.
