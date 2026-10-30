"""
AI-assisted value-format decoding for a NEWLY confirmed schema (see
app/schema_registry.py). Exists for one reason: the SA8155P schema's
bias/drive-strength decoding is hardcoded against ITS confirmed "N:
LABEL" format (e.g. "3: PULL_UP") - a genuinely different workbook could
encode the same concepts completely differently ("PULL_UP" alone, "1",
"HIGH", anything). Without this module, a new schema simply falls back
to NEEDS REVIEW for every pin's bias/drive-strength/direction - safe,
but not useful.

Design, matching every other AI-assisted piece in this app:
  - Field IDENTIFICATION (which column is probably "bias") is a
    deterministic keyword heuristic - a SUGGESTION, never silently
    trusted, needs human confirmation to configure a schema's
    schema.py.derive_pin_config() lookup.
  - Once a field is confirmed, AI is asked ONCE per (schema, field,
    concept) to map the DISTINCT real values observed for that field to
    the correct DTS directive - not called live per-row. The resulting
    map is stored on the KnownSchema and reused deterministically
    forever after, and is fully visible/auditable (not a black box).
  - Every value this mechanism produces is clearly traceable to
    ai_assisted_value_decoding=True on the owning schema, so generated
    DTS can flag it distinctly from the fully-verified SA8155P path.

Requires GROQ_API_KEY. Returns None (never guesses) if unavailable, if
the call fails, or if the response doesn't parse into something usable.
"""

from __future__ import annotations
import json

from app.llm_client import get_client, get_model_name

# --- Deterministic field-identification heuristics - suggestion only ---
_BIAS_KEYWORDS = ("pull", "bias")
_DRIVE_STRENGTH_KEYWORDS = ("drive", "strength")
_DIRECTION_KEYWORDS = ("direction", "dir")

_CONCEPT_KEYWORDS = {
    "bias": _BIAS_KEYWORDS,
    "drive_strength": _DRIVE_STRENGTH_KEYWORDS,
    "direction": _DIRECTION_KEYWORDS,
}

_VALID_BIAS_DIRECTIVES = {"bias-pull-up", "bias-pull-down", "bias-disable", None}
_VALID_DIRECTION_DIRECTIVES = {"input-enable", "output-high", "output-low", None}


def suggest_value_field(slug_to_base_field: dict[str, str], concept: str) -> str | None:
    """Returns the field SLUG most likely to encode `concept`
    ("bias" | "drive_strength" | "direction"), based on keyword matching
    against field names - or None if nothing looks like a match. A
    SUGGESTION only - never used to decode anything until a human
    confirms it and an AI value-mapping step has run against it."""
    keywords = _CONCEPT_KEYWORDS.get(concept, ())
    for slug, base_field in slug_to_base_field.items():
        lowered = base_field.lower()
        if any(kw in lowered for kw in keywords):
            return slug
    return None


_DECODE_SYSTEM_PROMPT_TEMPLATE = """You map raw spreadsheet values from a \
hardware pin-configuration workbook to their correct Linux devicetree \
directive. The field is named {field_name!r} and is believed to encode \
{concept_description}.

Respond with ONLY a valid JSON object mapping each raw value (as given) to \
its directive string, no prose, no markdown fences. Example shape:
{{"PULL_UP": "bias-pull-up", "PULL_DOWN": "bias-pull-down", "NONE": "bias-disable"}}

{valid_values_instruction}

If a raw value's meaning is genuinely unclear or doesn't fit any valid \
directive, map it to null rather than guessing - do not invent a directive \
that isn't in the allowed list."""

_CONCEPT_DESCRIPTIONS = {
    "bias": (
        "the pin's pull resistor configuration",
        'Every value in your response MUST be one of: "bias-pull-up", "bias-pull-down", "bias-disable", or null.',
    ),
    "drive_strength": (
        "the pin's output drive strength in milliamps",
        "Every value in your response MUST be a plain number (as a string, e.g. \"10\") with no unit, or null.",
    ),
    "direction": (
        "the pin's signal direction (input vs output)",
        'Every value in your response MUST be one of: "input-enable", "output-high", "output-low", or null.',
    ),
}


def ai_decode_values(field_display_name: str, distinct_raw_values: list[str],
                      concept: str) -> dict[str, str | None] | None:
    """Asks the LLM to map every DISTINCT raw value observed for one
    field to the correct directive for `concept`. Returns the mapping
    dict, or None if unavailable/failed/unusable - callers must treat
    that exactly like "no decode map configured for this field",
    matching the existing NEEDS REVIEW fallback everywhere else."""
    client = get_client("value_decoder")
    if client is None or not distinct_raw_values or concept not in _CONCEPT_DESCRIPTIONS:
        return None

    concept_description, valid_values_instruction = _CONCEPT_DESCRIPTIONS[concept]
    system_prompt = _DECODE_SYSTEM_PROMPT_TEMPLATE.format(
        field_name=field_display_name, concept_description=concept_description,
        valid_values_instruction=valid_values_instruction,
    )
    try:
        resp = client.chat.completions.create(
            model=get_model_name(),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(sorted(set(distinct_raw_values))[:60])},
            ],
            temperature=0,
            max_tokens=1200,
        )
        raw = (resp.choices[0].message.content or "").strip().strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
        mapping = json.loads(raw)
        if not isinstance(mapping, dict):
            return None

        if concept == "bias":
            valid = _VALID_BIAS_DIRECTIVES
        elif concept == "direction":
            valid = _VALID_DIRECTION_DIRECTIVES
        else:
            valid = None  # drive_strength validated separately below

        cleaned: dict[str, str | None] = {}
        for raw_value, directive in mapping.items():
            if directive is None:
                cleaned[raw_value] = None
                continue
            if concept == "drive_strength":
                if isinstance(directive, str) and directive.strip().isdigit():
                    cleaned[raw_value] = directive.strip()
                elif isinstance(directive, (int, float)):
                    cleaned[raw_value] = str(int(directive))
                else:
                    cleaned[raw_value] = None  # not a plain number - don't pass through garbage
            else:
                cleaned[raw_value] = directive if directive in valid else None
        return cleaned
    except Exception:
        return None
