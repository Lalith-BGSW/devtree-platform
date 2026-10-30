"""
AI for Ethernet - two jobs, both SUGGESTIONS a person confirms; Python still generates every value.

1. JUDGE the candidates. Python lists pins that are not selected as Ethernet but carry Ethernet wording somewhere in their row. The AI reads
   that row and says: ethernet_gpio (an Ethernet control line: MDIO, reset, interrupt ...), not_ethernet, or unclear - with a QUOTE.
2. FACTS the workbook states in words and Python cannot parse: phy-mode, RGMII clock delay, PHY address, PHY reset, link partner, speed.
   They are shown beside the open decisions. Nothing is written into the files.

Enforced here, in Python, on whatever the model answers:
  - every answer names a pin Python sent and carries a QUOTE that literally occurs in the text Python sent for that pin;
  - a fact's value must literally occur inside its own quote (the AI cannot invent a number or a mode);
  - a note that says "not in use" / "spare" / "reserved" caps the verdict at "unclear";
  - nothing is applied: an accepted pin becomes include_pins, which the person clicks, and Python generates it from the Excel.
"""
from __future__ import annotations

import json
import re

from app import llm_client
from app.usb_ai_advisor import _parse
from app.usb_ai_assist import FEATURE, _squash, _why_unavailable

MAX_PINS = 20
TOPICS = {"phy_mode", "clock_delay", "phy_address", "phy_reset", "link_partner", "speed", "other"}
VERDICTS = {"ethernet_gpio", "not_ethernet", "unclear"}
ROLES = {"mdio", "mdc", "reset", "interrupt", "data", "clock", "other", ""}
_NEG = re.compile(r"not\s+(in\s+use|used|mounted|connected|fitted|populated)|unused|\bdnp\b|do\s+not\s+populate|no\s+connect|\bn/?c\b|\bspare\b|reserved", re.I)

PROMPT = """You help an engineer read an automotive SoC pin workbook for ETHERNET.

You get PINS. Each has "workbook_cells": the workbook's own text for that pin (signal name, interface group, selected function, remarks, nets).
These pins are NOT selected as an Ethernet function in the workbook; they were listed because Ethernet-like words appear in their row.

Job 1 - for each pin decide:
- "ethernet_gpio": the cells show this pin is an Ethernet control line wired to a PHY or switch (MDIO / MDC, PHY reset, PHY interrupt, link or clock control, an RGMII/SGMII data or clock line).
- "not_ethernet": the word only appears in a power rail name, a capability list, an old note, or something unrelated.
- "unclear": the text is not enough, it says the pin is not in use / spare / reserved, or the pin's own "Signal" names a DIFFERENT function (a camera, UART, SPI ...) than the Ethernet-like wording, which then comes only from a reference or old column. When in doubt answer "unclear".
Give "role": mdio (the MDIO data line) | mdc (the MDIO clock line) | reset | interrupt | data (an RGMII/SGMII data line) | clock (an RGMII/SGMII clock) | other (empty when not ethernet_gpio).
Give a QUOTE copied VERBATIM from that pin's cells (a fragment, at most 80 characters).

Job 2 - FACTS: board facts the cells state IN WORDS about Ethernet: phy_mode (rgmii, rgmii-id, sgmii ...), clock_delay, phy_address, phy_reset, link_partner (PHY or switch name), speed.
Only state a fact that is written in the cells. "value" must be copied from the quote. Never infer, never use outside knowledge.

Rules: use ONLY the text given; never state levels, polarity or part numbers that are not in the text.
Reply with a single JSON object and nothing else:
{"verdicts": [{"pin": "<pin as given>", "verdict": "ethernet_gpio|not_ethernet|unclear", "role": "...", "quote": "<verbatim or empty>", "reason": "<one short sentence>"}],
 "facts": [{"pin": "<pin as given>", "topic": "phy_mode|clock_delay|phy_address|phy_reset|link_partner|speed|other", "value": "<copied from quote>", "quote": "<verbatim>"}]}"""


def _cells(rec, row: dict) -> str:
    f = rec.fields
    cells = [f"Signal: {f.get('gen5_signal_name') or ''}", f"Interface group: {f.get('interface_group') or ''}", f"Selected: {f.get('selected_interface') or ''}",
             f"Pinmuxed name: {f.get('pinmuxed_name') or ''}", f"Direction: {f.get('direction') or ''}"]
    cells += [f"{e['column']}: {e['value']}" for e in row.get("evidence", [])]
    for k, v in f.items():                       # free-text columns often hold the board facts
        if re.search(r"remark|comment|note|description", k) and str(v or "").strip():
            cells.append(k + ": " + re.sub(r"\s+", " ", str(v)).strip()[:200])
    return re.sub(r"\s+", " ", " | ".join(c for c in cells if c.split(": ", 1)[1].strip()))[:900]


def judge(records, left_out: list, generated_gpios: set) -> dict:
    """-> {status: none|ok|unavailable|error, verdicts: {pin: {...}}, facts: [...], checked, note}. Never changes anything."""
    by_g = {}
    for r in records:
        from app import eth_generator as eg
        g = eg._gpio_of(r)[1]
        if g:
            by_g[g] = r
    sent = {}
    for row in left_out:
        if row.get("candidate") is False or not row.get("gpio") or row["gpio"] not in by_g or len(sent) >= MAX_PINS:
            continue
        sent[row["pin"]] = {"pin": row["pin"], "gpio": row["gpio"], "text": _cells(by_g[row["gpio"]], row)}
    for g in sorted(generated_gpios, key=int)[:6]:        # a few generated pins, only for the facts job (their remarks / nets)
        r = by_g.get(g)
        if r is not None:
            pin = f"gpio{g}"
            sent.setdefault(pin, {"pin": pin, "gpio": g, "text": _cells(r, {})})
    if not sent:
        return {"status": "none", "verdicts": {}, "facts": [], "checked": 0, "note": "nothing to ask the AI"}
    client = llm_client.get_client(FEATURE)
    if client is None:
        return {"status": "unavailable", "verdicts": {}, "facts": [], "checked": len(sent), "note": _why_unavailable()}
    payload = [{"pin": c["pin"], "workbook_cells": c["text"]} for c in sent.values()]
    try:
        resp = client.chat.completions.create(messages=[{"role": "system", "content": PROMPT}, {"role": "user", "content": "PINS:\n" + json.dumps(payload, indent=1)}],
                                              temperature=0.0, max_tokens=1800)
    except llm_client.LLMError as e:
        return {"status": "error", "verdicts": {}, "facts": [], "checked": len(sent), "note": str(e)}
    parsed = _parse(resp.choices[0].message.content)
    if not isinstance(parsed, dict):
        return {"status": "error", "verdicts": {}, "facts": [], "checked": len(sent), "note": "the AI did not return usable JSON"}
    cand_pins = {row["pin"] for row in left_out}
    row_by_pin = {row["pin"]: row for row in left_out}
    verdicts = {}
    for v in parsed.get("verdicts") or []:
        if not isinstance(v, dict):
            continue
        pin = str(v.get("pin") or "").strip()
        c = sent.get(pin)
        if c is None or pin not in cand_pins:
            continue
        verdict = str(v.get("verdict") or "").strip().lower()
        role = str(v.get("role") or "").strip().lower()
        quote = str(v.get("quote") or "").strip()
        out = {"verdict": verdict if verdict in VERDICTS else "unclear", "role": role if role in ROLES else "other", "quote": quote,
               "reason": str(v.get("reason") or "").strip(), "note": ""}
        if out["verdict"] == "ethernet_gpio":
            if not quote or _squash(quote) not in _squash(c["text"]):
                out.update(verdict="unclear", note="the AI's quote is not in the workbook cells, so its answer was not used")
            elif row_by_pin.get(pin, {}).get("conflict"):
                out.update(verdict="unclear", note="the workbook's own Gen5 signal (\"" + str(row_by_pin[pin].get("signal") or "")[:50] + "\") names another function; the Ethernet wording is only in a reference column")
            elif _NEG.search(c["text"]):
                out.update(verdict="unclear", note="a note in the workbook says this pin is not in use / spare / reserved")
        verdicts[pin] = out
    facts = []
    for fct in parsed.get("facts") or []:
        if not isinstance(fct, dict):
            continue
        pin, topic = str(fct.get("pin") or "").strip(), str(fct.get("topic") or "").strip().lower()
        quote, value = str(fct.get("quote") or "").strip(), str(fct.get("value") or "").strip()
        c = sent.get(pin)
        if c is None or topic not in TOPICS or not quote or not value:
            continue
        if _squash(quote) not in _squash(c["text"]) or _squash(value) not in _squash(quote):
            continue
        facts.append({"pin": pin, "topic": topic, "value": value, "quote": quote})
    return {"status": "ok", "verdicts": verdicts, "facts": facts, "checked": len(sent), "note": ""}
