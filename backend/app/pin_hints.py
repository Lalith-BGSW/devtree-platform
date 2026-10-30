"""
Evidence from a pin's schematic net and Excel remark, for a HUMAN deciding a level. Never used to set a value.

The workbook's net trace lists the parts on a pin's net, e.g.  "D5700(GPIO_56) ...VBUS_PWR_EN_B, >> R5750(2.2k) PWR:U18S_IO_SOCPMIC".
A resistor that goes to a PWR: rail is a pull-up, one that goes to GND is a pull-down. That tells what the line does while the SoC pin
is NOT driving it (in reset, before a driver loads) - it does not tell what level the driver should set: an enable with a pull-up can
still be driven low on purpose (the accepted change does exactly that for an active-high VBUS enable). So this module reports facts
and what they mean while undriven, and passes no verdict on the driven level.
"""
from __future__ import annotations

import re

_RES = re.compile(r"\b(R\d+)\s*[=(]\s*([\d.]+\s*[kKMm]?)\s*\)?(?:\(\d+\))?\s+(PWR:[A-Za-z0-9_]+|GND)\b")


def pulls(trace: str) -> list[dict]:
    """[{ref, value, kind: 'pull-up'|'pull-down', target}] for every resistor on the net that goes to a power rail or to GND."""
    seen, out = set(), []
    for m in _RES.finditer(re.sub(r"_x000D_|\s+", " ", trace or "")):
        ref, value, target = m.group(1), re.sub(r"\s+", "", m.group(2)), m.group(3)
        if (ref, target) in seen:
            continue
        seen.add((ref, target))
        out.append({"ref": ref, "value": value, "kind": "pull-down" if target == "GND" else "pull-up", "target": target})
    return out


def level_evidence(trace: str, remark: str, active_low: bool | None, reset_value: str | None = None) -> str | None:
    """One sentence of evidence, or None when the workbook says nothing useful. active_low: the enable's polarity from its signal
    name (True/False), or None when the pin is not an enable (then only the facts are given)."""
    ps = pulls(trace)
    remark = re.sub(r"\s+", " ", remark or "").strip()
    reset_value = re.sub(r"\s+", " ", reset_value or "").strip()
    if not ps and not remark and not reset_value:
        return None
    parts = []
    if remark:
        parts.append(f'Excel remark "{remark}"')
    if reset_value:
        parts.append(f'Excel ResetValue "{reset_value}"')
    if ps:
        parts.append("; ".join(f"{p['value']} {p['kind']} ({p['ref']}) to {p['target']}" for p in ps))
        kinds = {p["kind"] for p in ps}
        if len(kinds) == 1 and active_low is not None:
            idle = "high" if "pull-up" in kinds else "low"
            state = "disabled" if (idle == "high") == active_low else "enabled"
            parts.append(f"undriven, the line idles {idle} = the enable is {state}")
    if active_low is not None:
        # what each choice does to the supply - the fact a person needs to decide
        parts.append("driven at boot: output-high = " + ("supply OFF" if active_low else "supply ON") + ", output-low = "
                     + ("supply ON" if active_low else "supply OFF"))
    return " - ".join(parts)


def reset_level(fields: dict):
    """(directive, raw) from the workbook's live ResetValue column ("0: low" / "1: high"), or None. The level the pin has at reset - evidence for an
    output's boot level, never proof of what the driver should set, so callers keep it flagged."""
    raw = str(fields.get("resetvalue") or "").strip()
    if not raw:
        return None
    m = re.match(r"^([01])\b", raw)
    if m:
        return ("output-high" if m.group(1) == "1" else "output-low"), raw
    if re.search(r"\bhigh\b", raw, re.I):
        return "output-high", raw
    if re.search(r"\blow\b", raw, re.I):
        return "output-low", raw
    return None


def is_irq(fields: dict) -> bool:
    """True when the workbook's interrupt column flags the pin (irq = INT)."""
    return bool(re.search(r"\bint", str(fields.get("irq") or ""), re.I))


def output_level_line(fields: dict) -> str:
    """The line for an OUTPUT GPIO: the ResetValue level, flagged, or the plain 'level not derivable' placeholder."""
    lv = reset_level(fields)
    if lv:
        return (f'{lv[0]};\t/* NEEDS REVIEW (evidence: Excel ResetValue "{lv[1]}"): the level the pin has at reset - confirm it is also the level the driver should set */')
    return "/* NEEDS REVIEW: output pin - add output-high or output-low per hardware design (level is not derivable from Excel) */"
