"""
LLM usage is deliberately narrow, per the spec:

    AI should NOT directly generate DTS blindly.
    AI should only: understand intent, retrieve data (via RAG), explain configuration.

So this module has exactly two jobs:
  1. parse_intent()  - turn a free-text query into structured search terms
  2. explain()        - answer a question ONLY using the records RAG retrieved

Neither function ever emits DTS syntax. app/generators/dts_generator.py does
that deterministically from Jinja2 templates.

Both functions now accept grounding context - a data dictionary (what every
field means, from app/schema.py) and, for parse_intent(), the file's real
vocabulary (distinct values actually seen in key fields). This is what lets
the LLM translate "bluetooth" into the real abbreviation "BTWLAN" instead of
guessing, and answer "what does IO_GPIO_PULL mean" from a real definition
instead of inferring one from the field name. The no-LLM fallback path
(_fallback_intent) can't use this context - bridging "bluetooth" -> "BTWLAN"
needs actual reasoning, not string matching - so it's a plain keyword
extractor same as before; the grounding only helps once a real LLM call
succeeds.

Requires GROQ_API_KEY in the environment. If it's not set, parse_intent()
falls back to naive keyword extraction and explain() falls back to a
templated summary, so the rest of the app still works without a key.
"""

from __future__ import annotations
import json
import re

from app.llm_client import get_client, get_model_name


INTENT_SYSTEM_PROMPT_BASE = """You convert a hardware engineer's question about a \
PinMux/Device-Tree workbook into a small JSON object. Do not generate any \
Device Tree syntax. Respond with ONLY valid JSON, no prose, no markdown fences.

Schema:
{
  "action": "query" | "generate_dts" | "compare" | "explain",
  "keywords": [<short lowercase search terms - see instructions below>],
  "target_projects": [<project names mentioned, empty if none/current project only>]
}

IMPORTANT for keywords: this workbook uses its own internal abbreviations \
that don't always match everyday terms - a reference list of the file's \
REAL field-value vocabulary is provided below when available. If the \
engineer's question uses a common term (e.g. "bluetooth", "wifi", \
"ethernet") that doesn't literally appear in the data, check the \
vocabulary list for the closest real abbreviation (e.g. "BTWLAN" for \
Bluetooth/WLAN) and include THAT as a keyword instead of, or in addition \
to, the literal word from the question. If nothing in the vocabulary is a \
clear match, fall back to the literal words from the question."""


def _build_intent_system_prompt(data_dictionary: str, vocabulary: dict[str, list[str]] | None) -> str:
    parts = [INTENT_SYSTEM_PROMPT_BASE]
    if vocabulary:
        parts.append("\n=== Real vocabulary found in this workbook ===")
        for field, values in vocabulary.items():
            if values:
                parts.append(f"{field}: {', '.join(values)}")
    if data_dictionary:
        parts.append(f"\n=== Field meanings (for context) ===\n{data_dictionary}")
    return "\n".join(parts)


def _fallback_intent(query: str) -> dict:
    q = query.lower()
    if any(w in q for w in ("generate", "produce", "create dts", "create .dts", "dtsi")):
        action = "generate_dts"
    elif any(w in q for w in ("compare", "difference", "vs ", "versus")):
        action = "compare"
    elif any(w in q for w in ("why", "explain", "what changed", "what is")):
        action = "explain"
    else:
        action = "query"
    # crude keyword extraction: words 3+ chars, minus stopwords
    stop = {"the", "for", "and", "all", "pin", "pins", "show", "generate", "what",
            "why", "does", "with", "from", "this", "that"}
    words = re.findall(r"[a-zA-Z0-9]+", q)
    keywords = [w for w in words if len(w) >= 3 and w not in stop]
    return {"action": action, "keywords": keywords[:8], "target_projects": []}


def parse_intent(query: str, data_dictionary: str = "",
                  vocabulary: dict[str, list[str]] | None = None) -> dict:
    client = get_client("chat")
    if client is None:
        return _fallback_intent(query)
    try:
        system_prompt = _build_intent_system_prompt(data_dictionary, vocabulary)
        resp = client.chat.completions.create(
            model=get_model_name(),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": query},
            ],
            temperature=0,
            max_tokens=300,
        )
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
        parsed = json.loads(raw)
        parsed.setdefault("action", "query")
        parsed.setdefault("keywords", [])
        parsed.setdefault("target_projects", [])
        return parsed
    except Exception:
        return _fallback_intent(query)


EXPLAIN_SYSTEM_PROMPT_BASE = """You are a hardware/firmware assistant. Answer the \
engineer's question using ONLY the workbook records provided below. Never \
invent GPIO numbers, signal names, or values that are not present in the \
records. If the records don't contain enough information to answer, say so \
plainly. Do not output Device Tree syntax - describe the configuration in \
plain language. Keep the answer concise.

A data dictionary explaining what each field means is provided below when \
available - use it to give grounded, accurate explanations of field \
meanings rather than guessing from field names."""


def explain(query: str, records_text: str, data_dictionary: str = "") -> str:
    client = get_client("chat")
    if client is None or not records_text.strip():
        if not records_text.strip():
            return "No matching records were found in the workbook for that query."
        return f"Matching records:\n{records_text}"
    try:
        system_prompt = EXPLAIN_SYSTEM_PROMPT_BASE
        if data_dictionary:
            system_prompt += f"\n\n=== Field meanings ===\n{data_dictionary}"
        resp = client.chat.completions.create(
            model=get_model_name(),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Question: {query}\n\nRecords:\n{records_text}"},
            ],
            temperature=0.2,
            max_tokens=600,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"(LLM explanation unavailable: {e})\n\nMatching records:\n{records_text}"
