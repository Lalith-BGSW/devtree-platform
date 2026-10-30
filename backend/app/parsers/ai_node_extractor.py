"""
AI-assisted fallback for parsing DTS node text that doesn't match the
strict, proven shape (see app/parsers/dts_parser.py). This exists for
exactly one reason: a real reference file's FORMAT can differ from the
one confirmed format our strict parser understands - different property
names (qcom,pins vs pins), a different block structure, different
conventions. When that happens today, the strict parser correctly and
safely leaves that text completely untouched - but that also means
nothing in it ever gets updated, which is a real limitation, not a
made-up one.

The boundary this module exists to protect, restated because it matters:
this ONLY reads what a node's text already says (which pins, what
function/values are currently written) - it NEVER decides what those
values SHOULD be. Correctness still comes exclusively from Excel, via
the exact same deterministic comparison logic every other node goes
through. If this module is wrong about what a node currently contains,
the result is a wasted/incorrect diff comment, not a wrong hardware
value silently accepted as truth - and every node it touches is tagged
"AI-ASSISTED PARSE" in the output specifically so that risk is visible,
never silent.

Requires GROQ_API_KEY. Returns None (never guesses) if unavailable, if
the call fails, or if the response doesn't parse into something usable -
callers must treat None exactly like "couldn't extract this one, leave
it untouched", the same as if this module didn't exist.
"""

from __future__ import annotations
import json
from dataclasses import dataclass

from app.llm_client import get_client, get_model_name


@dataclass
class ExtractedNodeData:
    pins: list[str]
    function: str | None
    drive_strength: str | None  # raw number as a string, e.g. "8"
    bias_directive: str | None  # normalized to one of: bias-pull-up /
                                  # bias-pull-down / bias-disable, or None
    direction_directive: str | None  # normalized to input-enable /
                                       # output-high / output-low, or None
    confidence: str  # "high" | "low" - the model's own assessment


_EXTRACT_SYSTEM_PROMPT = """You extract structured data from a single \
Linux devicetree pin-configuration node. Your ONLY job is to read what is \
ALREADY WRITTEN in the text and report it - never infer, guess, or fill in \
a value that isn't actually present. If a property isn't in the text, \
report it as null - do not invent a plausible-looking default.

Respond with ONLY valid JSON, no prose, no markdown fences, matching this \
exact shape:
{
  "pins": [<list of pin name strings this node configures, e.g. "gpio5">],
  "function": <string or null - the mux function this node selects>,
  "drive_strength": <string or null - drive strength as a plain number, e.g. "8", with no unit>,
  "bias_directive": <one of "bias-pull-up", "bias-pull-down", "bias-disable", or null>,
  "direction_directive": <one of "input-enable", "output-high", "output-low", or null>,
  "confidence": <"high" if you're confident you found and correctly read every \
property present, "low" if the format is unusual enough that you're not sure \
you read it correctly>
}

If the node uses a raw register/cell format you can't confidently decode \
(e.g. "qcom,pins = <&gpio 5 0>;" - the numbers inside <> may not mean what \
you assume), still extract what you clearly CAN read (like the pin number \
if identifiable) but set confidence to "low" rather than guessing at \
unclear parts."""


def extract_node_data(raw_text: str) -> ExtractedNodeData | None:
    """Attempts to extract pin/function/config data from DTS node text the
    strict parser couldn't recognize. Returns None if unavailable/failed -
    callers must treat that as "leave this node untouched", never as
    license to guess on its behalf."""
    client = get_client("node_extractor")
    if client is None:
        return None
    try:
        resp = client.chat.completions.create(
            model=get_model_name(),
            messages=[
                {"role": "system", "content": _EXTRACT_SYSTEM_PROMPT},
                {"role": "user", "content": raw_text},
            ],
            temperature=0,
            max_tokens=400,
        )
        raw = (resp.choices[0].message.content or "").strip()
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
        data = json.loads(raw)

        pins = data.get("pins")
        if not isinstance(pins, list) or not all(isinstance(p, str) for p in pins) or not pins:
            return None  # nothing usable was extracted - don't fabricate a fallback

        def _clean_str(v):
            return v if isinstance(v, str) and v.strip() else None

        bias = _clean_str(data.get("bias_directive"))
        if bias not in ("bias-pull-up", "bias-pull-down", "bias-disable", None):
            bias = None  # model didn't follow the enum - drop rather than pass through garbage

        direction = _clean_str(data.get("direction_directive"))
        if direction not in ("input-enable", "output-high", "output-low", None):
            direction = None

        drive = _clean_str(data.get("drive_strength"))
        if drive is not None and not drive.strip().isdigit():
            drive = None  # not a plain number - don't pass through something that'd render invalid DTS

        confidence = data.get("confidence") if data.get("confidence") in ("high", "low") else "low"

        return ExtractedNodeData(
            pins=[p.strip() for p in pins],
            function=_clean_str(data.get("function")),
            drive_strength=drive,
            bias_directive=bias,
            direction_directive=direction,
            confidence=confidence,
        )
    except Exception:
        return None
