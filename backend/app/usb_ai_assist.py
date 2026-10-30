"""
AI suggestions that help Python find what it cannot decide - and nothing else. Two jobs, both SUGGESTIONS a person confirms:

1. ROLES. A signal such as "USB_FAULT_B" or "5V0S_USB_EXTERNAL_EN" is a real over-current / VBUS-enable pin, but Python only knows
   the keywords of the namings seen so far, so the pin is generated yet not wired. The AI proposes oc | vbus | other for it.
2. AUDIT. Pins the USB match did not include but that may be USB-related. The AI judges the few unmatched GPIOs; the PHY pads and
   supplies (no pinctrl impact) are only LISTED by Python.

What keeps this safe (all enforced here, in Python, on whatever the model answers):
  - Python decides WHEN to ask: roles only for a signal whose controller instance still lacks that role; audit only for unmatched GPIOs.
  - Every answer must name a pin Python sent, and carry a QUOTE that literally occurs in the workbook text Python sent for that pin.
  - A role must fit the workbook: an over-current pin is an INPUT, a VBUS enable is an OUTPUT (Excel Direction), and the controller
    must not already have that role.
  - Nothing is applied. An accepted suggestion becomes `--role SIGNAL=role` / `--include-pin`, which the person confirms; Python then
    generates every value from the Excel, exactly as if the person had typed it.
"""
from __future__ import annotations

import json
import re

from app import llm_client
from app.usb_ai_advisor import _parse

FEATURE = "usb_suggest"
MAX_ROLE_PINS = 12
MAX_AUDIT_PINS = 20

ROLES_PROMPT = """You help an engineer classify USB-related GPIO signals of an automotive SoC workbook.

You get a list of signals. For each one decide its ROLE for the USB controller it belongs to:
- "oc": the signal reports an over-current / fault condition of that controller's VBUS power switch (an INPUT to the SoC).
- "vbus": the signal enables / powers the VBUS supply of that controller's port (an OUTPUT of the SoC).
- "other": anything else (device detect, reset, mode select, OTG role, ID, ...). When in doubt answer "other".

Rules:
1. Use ONLY the text you are given for that signal. Do not use outside knowledge about the board.
2. For every signal give a QUOTE copied VERBATIM from the signal name you were given (a fragment, at most 60 characters) that justifies the role.
3. Never state polarity, levels, addresses or part numbers.
Reply with a single JSON object and nothing else:
{"suggestions": [{"pin": "<pin as given>", "role": "oc|vbus|other", "quote": "<verbatim fragment of the signal name>", "reason": "<one short sentence>"}]}"""

AUDIT_PROMPT = """You help an engineer find USB-related GPIO pins that an automatic USB match did NOT include.

You get pins, each with the workbook text that made it a candidate. For each pin decide:
- "usb_gpio": the text shows this GPIO is a USB function (a USB controller, port, hub, VBUS, over-current, OTG or Type-C control signal).
- "not_usb": it is something else that merely mentions a USB-looking word.
- "unclear": the text is not enough.

Rules:
1. Use ONLY the text you are given for that pin.
2. For "usb_gpio" give a QUOTE copied VERBATIM from that pin's text (a fragment, at most 80 characters) that shows the USB function.
3. Never state levels, polarity, addresses or part numbers.
Reply with a single JSON object and nothing else:
{"verdicts": [{"pin": "<pin as given>", "verdict": "usb_gpio|not_usb|unclear", "quote": "<verbatim fragment or empty>", "reason": "<one short sentence>"}]}"""


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", str(s or "")).lower()


def _cell(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).replace("|", "/").strip()


# ----------------------------------------------------------------------------------------------------------------- roles
def role_candidates(cat: dict, direction_of, ball_of, remark_of, interface_group_of) -> list[dict]:
    """The 'other GPIO' signals Python could wire if it knew the role: they name a controller instance AND that instance still lacks
    an over-current or a VBUS pin. Anything else gets no AI call - Python already has the answer."""
    taken: dict[str, set] = {}
    for kind, role in (("oc_gpio", "oc"), ("vbus_gpio", "vbus")):
        for e in cat.get(kind, []):
            taken.setdefault(e.get("instance_token") or "", set()).add(role)
    out = []
    for e in cat.get("other_gpio", []):
        tok = e.get("instance_token")
        if not tok:
            continue
        free = [r for r in ("oc", "vbus") if r not in taken.get(tok, set())]
        if not free:
            continue
        sig = e["signal"]
        out.append({"pin": ball_of(e["record"]), "signal": sig, "tail": sig.split(":")[-1].strip(), "instance": tok, "free_roles": free,
                    "direction": direction_of(e["record"]), "interface_group": interface_group_of(e["record"]),
                    "remark": remark_of(e["record"])})
    return out[:MAX_ROLE_PINS]


def validate_role(sug: dict, by_pin: dict) -> dict:
    """One model suggestion -> {pin, tail, role, quote, reason, status, why, role_arg}. status: accepted | rejected | other."""
    pin = str(sug.get("pin") or "").strip()
    row = {"pin": pin, "role": str(sug.get("role") or "").strip().lower(), "quote": str(sug.get("quote") or "").strip(),
           "reason": str(sug.get("reason") or "").strip(), "tail": "", "status": "rejected", "why": "", "role_arg": ""}
    cand = by_pin.get(pin)
    if cand is None:
        row["why"] = "not one of the pins that were sent"
        return row
    row["tail"] = cand["tail"]
    row["instance"] = cand["instance"]
    if row["role"] not in ("oc", "vbus", "other"):
        row["why"] = f"unknown role {row['role']!r}"
        return row
    if row["role"] == "other":
        row["status"], row["why"] = "other", "the AI says it is neither an over-current nor a VBUS-enable pin"
        return row
    q = _squash(row["quote"])
    if not q or q not in _squash(cand["signal"]):
        row["why"] = "the quote is not in the signal name"
        return row
    if row["role"] not in cand["free_roles"]:
        row["why"] = f"{cand['instance']} already has a {row['role']} pin"
        return row
    d = str(cand.get("direction") or "").upper()
    is_in, is_out = bool(re.search(r"\bIN\b", d)), "OUT" in d
    row["role_arg"] = f"{cand['tail'].upper()}={row['role']}"
    # The NAME says one thing and the workbook's direction another. Neither the AI nor Python can settle which is wrong: a person who
    # knows the board can. So it is a CONFLICT to confirm (with the contradiction stated on the pin), never a silent rejection.
    if row["role"] == "oc" and is_out and not is_in:
        row["status"] = "conflict"
        row["why"] = "its name reads as an over-current pin (an input), but the workbook says its direction is OUTPUT"
        return row
    if row["role"] == "vbus" and is_in and not is_out:
        row["status"] = "conflict"
        row["why"] = "its name reads as a VBUS enable (an output), but the workbook says its direction is INPUT"
        return row
    row["status"] = "accepted"
    row["why"] = "" if (is_in or is_out) else "direction is not stated in the workbook - check it"
    return row


def suggest_roles(cands: list[dict], none_reason: str | None = None) -> dict:
    """{status, rows, usage_calls, note}. status: none (nothing to ask) | unavailable | ok | error."""
    if not cands:
        return {"status": "none", "rows": [],
                "note": none_reason or "every controller instance already has its over-current and VBUS pin, or no signal names an instance"}
    client = llm_client.get_client(FEATURE)
    if client is None:
        return {"status": "unavailable", "rows": [], "candidates": cands, "note": _why_unavailable()}
    payload = [{"pin": c["pin"], "signal": c["signal"], "controller": c["instance"], "interface_group": c["interface_group"],
                "direction_in_workbook": c["direction"], "remark": c["remark"]} for c in cands]
    try:
        resp = client.chat.completions.create(
            messages=[{"role": "system", "content": ROLES_PROMPT}, {"role": "user", "content": "SIGNALS:\n" + json.dumps(payload, indent=1)}],
            temperature=0.0, max_tokens=900)
    except llm_client.LLMError as e:
        return {"status": "error", "rows": [], "candidates": cands, "note": str(e)}
    parsed = _parse(resp.choices[0].message.content)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("suggestions"), list):
        return {"status": "error", "rows": [], "candidates": cands, "note": "the AI did not return usable JSON"}
    by_pin = {c["pin"]: c for c in cands}
    rows, seen = [], set()
    for s in parsed["suggestions"]:
        if isinstance(s, dict):
            r = validate_role(s, by_pin)
            if r["pin"] not in seen:
                seen.add(r["pin"])
                rows.append(r)
    # one controller has ONE over-current pin and ONE VBUS enable: if the AI claims the same slot for two pins it is guessing - reject both
    claims: dict[tuple, list] = {}
    for r in rows:
        if r["status"] in ("accepted", "conflict"):
            claims.setdefault((r["instance"], r["role"]), []).append(r)
    for (inst, role), rs in claims.items():
        if len(rs) > 1:
            for r in rs:
                r["status"], r["role_arg"] = "rejected", ""
                r["why"] = f"the AI proposed {len(rs)} pins as the {role} pin of {inst} ({', '.join(x['pin'] for x in rs)}) - ambiguous, decide it yourself"
    return {"status": "ok", "rows": rows, "candidates": cands, "note": ""}


# ----------------------------------------------------------------------------------------------------------------- audit
_KW = re.compile(r"HUB|OTG|VBUS|TYPE[-_ ]?C|\bCC[12]\b|EDL|DWC|PPON|FAULT|OVERCURR|OC_B|DEVICE_DET|\bPD_|USB", re.I)


def audit_candidates(records, matched, text_fields_of, ball_of, gpio_of, sheet_pins: dict | None = None) -> dict:
    """Unmatched pins whose workbook text mentions a USB-looking word (or that a USB-named sheet places on a GPIO the Database sheet
    leaves unmatched). GPIOs are returned for the AI to judge; everything else (PHY pads, supplies, reference resistors) is only listed."""
    mids = {id(r) for r in matched}
    gpios, pads, seen_pads = [], [], set()
    strong = {"USB", "HUB", "OTG", "VBUS", "TYPEC", "DWC", "EDL"}      # a pad (not a GPIO) is listed only on a clearly USB word, not on FAULT / PPON / CC1...
    for r in records:
        if id(r) in mids:
            continue
        text = text_fields_of(r)
        hits = sorted({m.upper() for m in _KW.findall(text)})
        n = gpio_of(r)
        sheet_note = (sheet_pins or {}).get(n) if n else None
        if not hits and not sheet_note:
            continue
        ball = ball_of(r)
        if n:
            body = text if not sheet_note else text + "\nUSB-named sheet: " + sheet_note
            gpios.append({"pin": ball, "hits": hits, "text": body[:700], "source": "USB-named sheet" if sheet_note and not hits else "workbook text"})
        else:
            if not ({re.sub(r"[-_ ]", "", h) for h in hits} & strong) or ball in seen_pads:
                continue
            seen_pads.add(ball)
            pads.append({"pin": ball, "hits": hits})
    return {"gpios": gpios[:MAX_AUDIT_PINS], "gpios_total": len(gpios), "pads": pads}


def validate_verdict(v: dict, by_pin: dict) -> dict:
    pin = str(v.get("pin") or "").strip()
    row = {"pin": pin, "verdict": str(v.get("verdict") or "").strip().lower(), "quote": str(v.get("quote") or "").strip(),
           "reason": str(v.get("reason") or "").strip(), "status": "rejected", "why": ""}
    cand = by_pin.get(pin)
    if cand is None:
        row["why"] = "not one of the pins that were sent"
        return row
    if row["verdict"] == "not_usb":
        row["status"] = "not_usb"
        return row
    if row["verdict"] != "usb_gpio":
        row["status"], row["why"] = "unclear", "the AI could not tell"
        return row
    q = _squash(row["quote"])
    if not q or q not in _squash(cand["text"]):
        row["why"] = "the quote is not in the workbook text for this pin"
        return row
    # The only USB evidence is a USB-named sheet (a hardware/mounting variant); the Database sheet gives this pin no signal, so including
    # it could not generate a node. Say that instead of offering a button that would do nothing useful.
    row["status"] = "variant" if cand.get("source") == "USB-named sheet" else "include"
    return row


def audit(cands: dict) -> dict:
    """{status, rows, pads, note}."""
    pads = cands.get("pads", [])
    if not cands.get("gpios"):
        return {"status": "none", "rows": [], "pads": pads, "note": "no unmatched GPIO mentions a USB-looking word"}
    client = llm_client.get_client(FEATURE)
    if client is None:
        return {"status": "unavailable", "rows": [], "pads": pads, "candidates": cands["gpios"], "note": _why_unavailable()}
    payload = [{"pin": c["pin"], "text": c["text"]} for c in cands["gpios"]]
    try:
        resp = client.chat.completions.create(
            messages=[{"role": "system", "content": AUDIT_PROMPT}, {"role": "user", "content": "PINS:\n" + json.dumps(payload, indent=1)}],
            temperature=0.0, max_tokens=1200)
    except llm_client.LLMError as e:
        return {"status": "error", "rows": [], "pads": pads, "candidates": cands["gpios"], "note": str(e)}
    parsed = _parse(resp.choices[0].message.content)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("verdicts"), list):
        return {"status": "error", "rows": [], "pads": pads, "candidates": cands["gpios"], "note": "the AI did not return usable JSON"}
    by_pin = {c["pin"]: c for c in cands["gpios"]}
    rows, seen = [], set()
    for v in parsed["verdicts"]:
        if isinstance(v, dict):
            r = validate_verdict(v, by_pin)
            if r["pin"] not in seen:
                seen.add(r["pin"])
                rows.append(r)
    return {"status": "ok", "rows": rows, "pads": pads, "candidates": cands["gpios"], "note": ""}


def _why_unavailable() -> str:
    return ("no LLM is configured (set LLM_API_KEY and LLM_DEPLOYMENT in backend/.env)" if not llm_client.is_configured()
            else f"the '{FEATURE}' feature is not enabled in LLM_FEATURES")


# ----------------------------------------------------------------------------------------------------------------- report
def render(roles: dict, aud: dict) -> list[str]:
    L = ["\n## AI suggestions to confirm - nothing here changed the output\n",
         "_Each suggestion was checked by Python: the pin is one it sent, the quote occurs in the workbook text, and a role fits the "
         "workbook's direction and the controller's free slots. Confirm a suggestion to apply it; Python then writes every value from the Excel._\n"]
    # roles
    L.append("### Signal roles\n")
    if roles["status"] == "none":
        L.append(f"Nothing to suggest: {roles['note']}.")
    elif roles["status"] != "ok":
        names = ", ".join(c["tail"] for c in roles.get("candidates", []))
        L.append(f"Not available ({roles['note']}). Signals that could be wired once their role is known: {names}.")
    else:
        L.append("| Pin | Signal | AI says | Quote | Why | Result |")
        L.append("|---|---|---|---|---|---|")
        for r in roles["rows"]:
            res = (f"**accepted** - confirm with `--role {r['role_arg']}`" + (f" ({r['why']})" if r["why"] else "")
                   if r["status"] == "accepted" else
                   f"**CONFLICT - needs you:** {r['why']}. If you know the pin is {'an over-current' if r['role'] == 'oc' else 'a VBUS-enable'} "
                   f"signal, confirm with `--role {r['role_arg']}`; the contradiction stays flagged on the pin"
                   if r["status"] == "conflict" else
                   (f"not applicable - {r['why']}" if r["status"] == "other" else f"rejected - {r['why']}"))
            L.append(f"| {_cell(r['pin'])} | {_cell(r['tail'])} | {r['role']} | {_cell(r['quote'])} | {_cell(r['reason'])} | {_cell(res)} |")
    # audit
    L.append("\n### Possibly missed pins\n")
    if aud["status"] == "none":
        L.append(f"No unmatched GPIO to judge: {aud['note']}.")
    elif aud["status"] != "ok":
        L.append(f"Not available ({aud['note']}). Unmatched GPIOs that mention a USB-looking word: "
                 + ", ".join(c["pin"] for c in aud.get("candidates", [])) + ".")
    else:
        L.append("| Pin | AI says | Quote | Why | Result |")
        L.append("|---|---|---|---|---|")
        for r in aud["rows"]:
            res = {"include": "**likely USB** - confirm with `--include-pin " + r["pin"] + "`", "not_usb": "not USB",
                   "variant": "**the AI thinks it is a USB pin, but only a USB-specific sheet says so**: the main Database sheet gives this pin no "
                              "signal, so the two sheets disagree and nothing is generated for it. Check the schematic; if it is a real USB pin, "
                              "state it as a fact",
                   "unclear": "unclear"}.get(r["status"], f"rejected - {r['why']}")
            L.append(f"| {_cell(r['pin'])} | {r['verdict']} | {_cell(r['quote'])} | {_cell(r['reason'])} | {_cell(res)} |")
    if aud.get("pads"):
        L.append("\nUSB-looking pads and supplies that were not matched (not GPIOs: no pinctrl impact, listed so nothing is invisible): "
                 + ", ".join(p["pin"] for p in aud["pads"]) + ".")
    return L


# ----------------------------------------------------------------------------------------------------------------- data check
MAX_CHECK_PINS = 40
CHECK_TYPES = {"direction", "pull", "drive", "instance", "remark_conflict", "not_used", "other"}

CHECK_PROMPT = """You are a careful reviewer of an automotive SoC PinMux workbook. For each USB-related GPIO pin you get the workbook's own
cells (signal name, interface group, direction, pull, drive strength, reset value, remark, schematic net) and what a program generated
from them. Find DATA THAT IS WRONG OR CONTRADICTS ITSELF - not missing data.

Report a finding only when two pieces of text about the SAME pin disagree, or a cell disagrees with the pin's own signal name. Examples:
- "direction": the signal name says over-current / fault / detect (an input) but Direction says OUT, or an enable / reset / power signal is IN.
- "pull": the remark asks for a pull-up / pull-down / no pull and the pull cell says another.
- "drive": the remark names a drive strength and the drive cell says another.
- "instance": the signal name belongs to one USB controller (USB20_0) and the interface group or net names another (USB20_1).
- "remark_conflict": a remark contradicts the configuration (e.g. "unused", "not mounted", "DNP", "NC", "spare") while the pin is configured as a live USB signal.
- "not_used": the remark says the pin is unused / reserved / not mounted for this variant.
- "other": any other direct contradiction between the cells.

Rules:
1. Use ONLY the text you are given. No outside knowledge about the board or the SoC.
2. Every finding needs QUOTE_A, copied VERBATIM from "workbook_cells" of that pin (a fragment of at most 80 characters). When two cells disagree QUOTE_B is the other cell's text, also copied verbatim from "workbook_cells" (never paraphrase, never join two cells). Otherwise leave QUOTE_B empty. Never quote or judge any text that is not in "workbook_cells".
3. Never invent values, levels, addresses or part numbers. Describe the contradiction in one short sentence using only words from the quotes.
4. "not_used" and "remark_conflict" need a quote that itself says unused / reserved / not mounted / NC / DNP / spare. A signal name that merely contains a bus word (UART, USB, SEL) is NEVER such a finding.
5. When in doubt report NOTHING. An empty list is a good answer. Do not list pins that are fine.
Reply with a single JSON object and nothing else:
{"findings": [{"pin": "<pin as given>", "type": "direction|pull|drive|instance|remark_conflict|not_used|other", "quote_a": "<verbatim>", "quote_b": "<verbatim or empty>", "issue": "<one short sentence>"}]}"""


def check_candidates(items: list[dict]) -> dict:
    """items: [{pin, text (the workbook's own cells, the ONLY text a quote may come from), generated (what the program wrote)}]."""
    return {"pins": items[:MAX_CHECK_PINS], "total": len(items)}


_IN_WORDS = re.compile(r"(?<![a-z])(oc|ocp|oc_b|fault|flt|detect|det|status|sense|alert|int|irq|ovc|overcurrent|over[-_ ]?current)(?![a-z])", re.I)
_OUT_WORDS = re.compile(r"(?<![a-z])(en|enable|rst|reset|power|pwr|sel|select|vbus_en)(?![a-z])", re.I)


def _direction_of(text: str) -> str:
    m = re.search(r"Direction:\s*(?:\d+\s*:\s*)?(IN|OUT)\b", text, re.I)
    return m.group(1).upper() if m else ""


def _pull_kinds(q: str) -> set:
    t = q.lower()
    k = set()
    if re.search(r"pull[-_ ]?up|\bpu\b", t): k.add("up")
    if re.search(r"pull[-_ ]?down|\bpd\b", t): k.add("down")
    if re.search(r"no[-_ ]?pull|pull[-_ ]?none|\bnone\b|disable", t): k.add("none")
    return k


_UNUSED_WORDS = re.compile(r"(?<![a-z])(unused|not[-_ ]?used|n/?c|nc|dnp|do not (?:mount|populate)|not (?:mounted|populated|connected|fitted)|"
                           r"reserved|spare|unconnected|not required|deprecated)(?![a-z])", re.I)


def _plausible(row: dict, cand: dict) -> str:
    """Python re-checks the claim itself where it can; returns a reason to drop, or ''."""
    text = cand["text"]
    if row["type"] in ("not_used", "remark_conflict"):
        if not (_UNUSED_WORDS.search(row["quote_a"]) or _UNUSED_WORDS.search(row["quote_b"])):
            return "neither quote says the pin is unused / reserved / not mounted"
    if row["type"] == "direction":
        d = _direction_of(text)
        sig = re.search(r"Signal:\s*([^|]*)", text)
        name = sig.group(1) if sig else ""
        want_in, want_out = bool(_IN_WORDS.search(name)), bool(_OUT_WORDS.search(name))
        if not d or want_in == want_out:
            return "Python cannot confirm a direction contradiction from the signal name"
        if (d == "IN") == want_in:
            return "the Direction cell agrees with what the signal name implies"
    if row["type"] == "pull":
        a, b = _pull_kinds(row["quote_a"]), _pull_kinds(row["quote_b"])
        if not a or not b or a == b:
            return "the two quotes do not name different pull settings"
    return ""


def validate_finding(f: dict, by_pin: dict) -> dict:
    row = {"pin": str(f.get("pin") or "").strip(), "type": str(f.get("type") or "").strip().lower(),
           "quote_a": str(f.get("quote_a") or "").strip(), "quote_b": str(f.get("quote_b") or "").strip(),
           "issue": str(f.get("issue") or "").strip(), "status": "rejected", "why": ""}
    cand = by_pin.get(row["pin"])
    if cand is None:
        row["why"] = "not one of the pins that were sent"
        return row
    if row["type"] not in CHECK_TYPES:
        row["why"] = "unknown finding type"
        return row
    hay = _squash(cand["text"])
    if not row["quote_a"] or _squash(row["quote_a"]) not in hay:
        row["why"] = "the quote is not in the workbook cells for this pin"
        return row
    if row["quote_b"] and _squash(row["quote_b"]) not in hay:
        row["why"] = "the second quote is not in the workbook cells for this pin"
        return row
    if not row["issue"]:
        row["why"] = "no description"
        return row
    why = _plausible(row, cand)
    if why:
        row["why"] = why
        return row
    row["status"] = "finding"
    return row


def check(cands: dict, prompt: str = None, what: str = "USB") -> dict:
    """{status: none|ok|unavailable|error, rows, checked, note}. Findings are SUGGESTIONS: nothing is changed in the output."""
    pins = cands.get("pins", [])
    if not pins:
        return {"status": "none", "rows": [], "checked": 0, "note": f"no {what} pin was generated, so there is nothing to check"}
    client = llm_client.get_client(FEATURE)
    if client is None:
        return {"status": "unavailable", "rows": [], "checked": len(pins), "note": _why_unavailable()}
    payload = [{"pin": c["pin"], "workbook_cells": c["text"]} for c in pins]
    try:
        resp = client.chat.completions.create(
            messages=[{"role": "system", "content": prompt or CHECK_PROMPT}, {"role": "user", "content": "PINS:\n" + json.dumps(payload, indent=1)}],
            temperature=0.0, max_tokens=1500)
    except llm_client.LLMError as e:
        return {"status": "error", "rows": [], "checked": len(pins), "note": str(e)}
    parsed = _parse(resp.choices[0].message.content)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("findings"), list):
        return {"status": "error", "rows": [], "checked": len(pins), "note": "the AI did not return usable JSON"}
    by_pin = {c["pin"]: c for c in pins}
    rows = [validate_finding(f, by_pin) for f in parsed["findings"] if isinstance(f, dict)]
    return {"status": "ok", "rows": rows, "checked": len(pins), "note": ""}


def render_check(res: dict) -> list[str]:
    L = ["\n## Workbook data check - the AI read your data for contradictions\n"]
    if res["status"] == "none":
        return L + [f"Nothing to check: {res['note']}."]
    if res["status"] != "ok":
        return L + [f"Not available ({res['note']}). {res['checked']} pin(s) were not cross-checked by the AI; Python's own checks still ran."]
    good = [r for r in res["rows"] if r["status"] == "finding"]
    bad = [r for r in res["rows"] if r["status"] != "finding"]
    L.append(f"_{res['checked']} pin(s) were read. Python checked each finding: the pin is one it sent and every quote occurs in that pin's own "
             "workbook cells. A finding is a question for you - the generated values still follow the Excel exactly; nothing was changed._\n")
    if not good:
        L.append("No contradictions found in the data that was given.")
    else:
        L.append("| Pin | Type | What looks wrong | Quote(s) from the workbook |")
        L.append("|---|---|---|---|")
        for r in good:
            q = f'"{r["quote_a"]}"' + (f' vs "{r["quote_b"]}"' if r["quote_b"] else "")
            L.append(f"| {_cell(r['pin'])} | {r['type']} | {_cell(r['issue'])} | {_cell(q)} |")
    if bad:
        L.append(f"\n{len(bad)} AI finding(s) were dropped because Python could not verify them: "
                 + "; ".join(f"{_cell(r['pin'])} ({r['why']}; AI said: \"{_cell(r['quote_a'])}\" / \"{_cell(r['quote_b'])}\")" for r in bad) + ".")
    return L
