"""Prompt assets for the collection-note post-processing workflow."""

ROUTING_SUMMARY_PROMPT = """Summarize the call in {language}.

Output rules:
- Return only the final summary.
- Do not include reasoning, analysis, labels, XML tags, markdown, or quoted input.
- Use 1 or 2 concise sentences.
"""

BASIC_INTENT_PROMPT = """Classify whether the customer intends to repay.

Output rules:
- Return exactly one token: yes or no.
- Do not include reasoning, explanations, XML tags, markdown, punctuation, or quoted input.

Return "yes" if the customer is willing to repay.
Return "yes" if the customer indicates they can repay.
Return "yes" if the contact person states they are not the borrower and do not know the borrower.
Return "yes" if the customer does not have the money now but can repay tomorrow.

Return "no" if the customer is unwilling to repay, indicating they have no money, or if the customer cannot be contacted.
Return "no" if the customer has no clear intention.
Return "no" in all other cases."""

POSITIVE_INTENT_PROMPT = """Classify a positive repayment-intent call.

Output rules:
- Return exactly one valid JSON object.
- Do not include reasoning, explanations, XML tags, markdown, code fences, or quoted input.
- Use only the allowed enum values.

Task 1: Categorize Payment Intent
Analyze the customer's response and categorize their intent under the label collectionResult using one of the following tags:
- yes_partial_repayment
- yes_willing_no_money
- yes_settled_not_updated
- yes_promise_today
- yes_promise_future_days
- yes_contact_not_know_user
- yes_contact_will_inform

Task 2: Evaluate Repeat Borrowing Wish
Determine the customer's inclination to apply for another loan in the future, labeled as repeatBorrowingWish.
Return "high" if the customer shows a strong desire or interest in borrowing again.
Return "low" if the customer shows little to no interest in future loans.

JSON shape:
{
  "repaymentWish": "yes",
  "collectionResult": "category_from_task_1",
  "repeatBorrowingWish": "high_or_low"
}"""

NEGATIVE_INTENT_PROMPT = """Classify a negative or unclear repayment-intent call.

Output rules:
- Return exactly one valid JSON object.
- Do not include reasoning, explanations, XML tags, markdown, code fences, or quoted input.
- Use only the allowed enum values.

Task 1: Determine which of the following categories describes the customer's non-payment intent. This task is labeled as collectionResult.
- no_no_money
- no_not_received_loan
- no_cannot_operate_repayment
- no_no_salary
- no_explicit_refusal
- not_answered_no_service
- not_answered_number_not_exist
- not_answered_power_off
- not_answered_no_answer
- no_other

Task 2: Determine the customer's willingness to apply for another loan. This task is labeled as repeatBorrowingWish.
Return "high" if the customer's willingness to apply for another loan is high.
Return "low" if the customer's willingness to apply for another loan is low.

JSON shape:
{
  "repaymentWish": "no",
  "collectionResult": "category_name",
  "repeatBorrowingWish": "high_or_low"
}"""

PROMISE_DATE_PROMPT = """Determine how many hours from the reference current time the customer promises to repay.

Output rules:
- Return exactly one number in hours, using increments of 0.1 hours.
- Do not include reasoning, explanations, XML tags, markdown, or quoted input.

Examples:
- "马上" or "立即" -> 0.0
- "5分钟后" -> 0.1
- "半小时后" -> 0.5
- "一小时后" -> 1.0
- "5小时后" -> 5.0

Only process repayment promises that fall on the same local calendar day as the reference current datetime.
If the customer says tomorrow, the day after tomorrow, or any later date, return -100.
If no explicit same-day repayment time can be determined, return -100.
Return -100 for any value that would cross local midnight.
"""

VOICEMAIL_PROMPT = """You are a Voice Assistant & Voicemail Detector analyzing ASR (Automatic Speech Recognition) transcripts of phone calls. Determine if the speaker is an automated system, IVR, voice assistant, or voicemail prompt.

Return 'JG-GOOD' if it is an automated system/voicemail, otherwise 'JG-BAD'.

CRITICAL CONTEXT (ASR & Telecom Nature):
- The inputs are ASR transcripts. They frequently contain phonetic misrecognitions (e.g., Spanish "buzón" misrecognized as "buque") and are often cut off/truncated at the beginning of a call.
- Do NOT judge solely by literal translation. Consider the telecommunication context and phonetic similarity.

Detection Criteria (Match AT LEAST ONE):
1. Telecom IVR/System Structures: Short, non-conversational phrases typical of system broadcasts, especially starting with service nouns or status, even if the following words are ASR errors.
   - Patterns: "El servicio...", "Servicio de [X]...", "Su llamada...", "The number...", "Please leave...".
2. ASR Phonetic Hallucinations: Illogical phrases in a telecom context that sound phonetically similar to voicemail/assistant keywords.
   - e.g., "Servicio de buque" (Phonetically matches "Servicio de buzón" - Voicemail).
3. Multi-Language Keywords:
   - EN: voicemail, message box full, leave a message, automated system, virtual assistant.
   - ES: buzón (or ASR variants like buque, buson), contestador, el servicio, deje su mensaje.
   - ZH: 语音助手, 留言, 请按, 语音信箱, 转接人工.
4. Automated Logic Patterns: Self-identification ("Soy un asistente"), offers of help ("en qué puedo ayudar"), status updates, or reciting sequential numbers (e.g., "1, 0, 5, 8...").

Examples returning 'JG-GOOD':
- "Message box is full, please try again later"
- "El servicio." (Truncated telecom prompt)
- "Servicio de buque." (ASR misrecognition for voicemail)
- "Su llamada" (Truncated telecom prompt)
- "The number you have" (Truncated)
- "请按1"

Output strictly 'JG-GOOD' or 'JG-BAD'."""

POSITIVE_REMARK_PROMPT = """You are an intelligent assistant for summarizing collection calls.

Output rules:
- Return only the final remark in {language}.
- Do not include reasoning, analysis, labels, XML tags, markdown, bullets, or quoted input.
- Use exactly 2 concise sentences.
- Escape any double quotes inside the text, or use single quotes for internal mentions.

Summarize the collection call based on:

Extract key information regarding:
* Payment commitment (specific date, time, amount, payment method)
* Reasons for refusal (financial difficulties, dissatisfaction with fees, etc.)
* Customer's current situation (unemployment, illness, cash flow issues, etc.)
* Agreed time for the next communication
* Special requests (requires proof, verification of information, etc.)
* Customer's mood/attitude
* Please preserve specific amounts, dates, phone numbers, and other key information.
"""

NEGATIVE_REMARK_PROMPT = """You are an intelligent assistant for summarizing collection calls.

Output rules:
- Return only the final remark in {language}.
- Do not include reasoning, analysis, labels, XML tags, markdown, bullets, or quoted input.
- Use exactly 2 concise sentences.
- Escape any double quotes inside the text, or use single quotes for internal mentions.

Summarize the collection call based on:

Extract key information regarding:
* Reasons for refusal (financial difficulties, dissatisfaction with fees, etc.)
* Customer's current situation (unemployment, illness, cash flow issues, etc.)
* Agreed time for the next communication
* Special requests (requires proof, verification of information, etc.)
* Customer's mood/attitude
* Please preserve specific amounts, dates, phone numbers, and other key information.
"""
