"""Prompt templates for the LLM-enhanced reply draft.

These prompts are written to keep the LLM inside a strict, safe boundary:

- The LLM may ONLY use the facts provided (customer message, order facts,
  logistics facts, rule summary) — it must not invent orders, logistics,
  policies or processing outcomes.
- It must never promise a refund amount, a refund processing time, an
  already-executed reshipment, or a guaranteed / specific delivery date.
- Output must be strict JSON, without Markdown fences.
- The LLM must NOT emit final business decision fields (``risk_level``,
  ``handoff_required``, ``approval``, ``resolve``) — those remain the
  responsibility of the deterministic rule engine in ``service.py``.
"""
from __future__ import annotations

import json
from typing import Any

SYSTEM_PROMPT = (
    "You are a careful after-sales customer-service drafting assistant for a "
    "cross-border e-commerce demo.\n\n"
    "Hard rules (must be followed exactly):\n"
    "1. Base your reply ONLY on the facts provided below: the customer message, "
    "the order facts, the logistics facts, and the rule summary. Do NOT invent "
    "any order number, item, carrier, tracking event, policy, refund, "
    "compensation, or processing outcome that is not present in the provided facts.\n"
    "2. NEVER promise or state: a refund amount, a refund processing time, that a "
    "reshipment/replacement has already been sent, or a guaranteed / specific "
    "delivery or arrival time.\n"
    "3. Do not make any final business decision. Do NOT include fields or "
    "language about risk level, whether to create a ticket, whether approval is "
    "required, or whether a case may be resolved/closed.\n"
    "4. Be polite and empathetic, and write in the customer's language (English "
    "by default). Keep the reply concise and factual.\n"
    "5. Output ONLY a single strict JSON object. Do NOT wrap it in Markdown code "
    "fences. Do NOT add any text before or after the JSON.\n\n"
    "The JSON object must contain exactly these keys:\n"
    '- "intent": a short lower_snake_case label for the customer\'s intent.\n'
    '- "sentiment": one of "neutral", "negative", "angry", "positive".\n'
    '- "extracted_order_id": the order number found in the message if any, '
    "otherwise null.\n"
    '- "missing_information": an array of strings listing information still '
    'needed from the customer (for example ["full order number"]), or [] if '
    "nothing is missing.\n"
    '- "reply_draft": the complete English reply draft, compliant with the hard '
    "rules above.\n"
    '- "disclaimer": a one-line note stating the draft is a suggestion requiring '
    "human review and does not confirm any refund, reshipment, or delivery date.\n"
)


def _facts_block(
    customer_alias: str,
    country: str,
    language: str,
    order: dict | None,
    logistics: dict | None,
    rules_summary: list[dict[str, Any]],
) -> str:
    return json.dumps(
        {
            "customer_alias": customer_alias,
            "country": country,
            "language": language,
            "order": order or None,
            "logistics": logistics or None,
            "rule_summary": rules_summary,
        },
        ensure_ascii=False,
        indent=2,
    )


def build_draft_messages(
    *,
    customer_message: str,
    customer_alias: str,
    country: str,
    language: str,
    order: dict | None,
    logistics: dict | None,
    rules_summary: list[dict[str, Any]],
) -> list[dict]:
    """Build the system + user messages for a single draft-generation call."""
    user = (
        "Customer message:\n"
        f"{customer_message}\n\n"
        "Provided facts (use ONLY these):\n"
        f"{_facts_block(customer_alias, country, language, order, logistics, rules_summary)}\n\n"
        "Generate the strict JSON object now."
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
