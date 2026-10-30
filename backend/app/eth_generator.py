"""
ETHERNET pin states from the PinMux workbook - Python generates, a second, independent reader verifies.

Which pins are Ethernet?  Only those whose SELECTED function says so (columns `selected_interface` / `pinmuxed_name`).  The workbook's
`interfaces` column lists every function a ball *could* do; it is never used to pick pins (GPIO_7 lists Ethernet as an unused alternate
function, and must not be pulled in because of it).  A pin whose NAME says Ethernet (signal / interface group) but whose selected function is
something else (a PHY reset or interrupt wired as a plain GPIO, MDC/MDIO left on GPIO) is reported as "left out" and can be added by a person
with `include_pins`.

Filter families (what the person asked for): RGMII, RMII/MII, MDIO/MDC, EMAC/Ethernet, SGMII.  SGMII is a SerDes lane pair, not TLMM pins - it
produces a note, never a pin state.

Function names come from the pin table of the SoC (`eth_profiles.json`, read from the Linux pinctrl driver).  The workbook's own mux value
(`TLMM_GPIOCFGn(5:2)=k`) is compared with the table; a disagreement is written on the pin as NEEDS REVIEW, never resolved silently.  A SoC without
a table still gets pins for RGMII / RMII (function = the family name) with a review mark; other functions are not generated.
"""
from __future__ import annotations

import json
import os
import re

from app import pin_hints
from app.generators.dts_generator import _first_pin_identifier, _to_dts_pin_name, derive_pin_config

_HERE = os.path.dirname(os.path.abspath(__file__))
ORDER = ["rgmii", "rmii", "mdio", "sgmii", "emac"]        # a pin that matches several families belongs to the first
_PROFILES = None


def profiles() -> dict:
    global _PROFILES
    if _PROFILES is None:
        with open(os.path.join(_HERE, "eth_profiles.json"), encoding="utf-8") as f:
            _PROFILES = json.load(f)
    return _PROFILES


def families() -> list[dict]:
    p = profiles()["families"]
    return [{"key": k, "label": p[k]["label"], "tlmm": p[k]["tlmm"]} for k in ORDER]


def soc_profile(soc: str | None):
    s = re.sub(r"[^A-Z0-9]", "", (soc or "").upper())
    for name, prof in profiles()["socs"].items():
        names = [name] + prof.get("aliases", [])
        if any(re.sub(r"[^A-Z0-9]", "", n.upper()) == s for n in names):
            return name, prof
    return None, None


def _fam_match(key: str, text: str) -> bool:
    return bool(re.search(profiles()["families"][key]["pattern"], text or "", re.I))


def _family_of(text: str):
    for k in ORDER:
        if _fam_match(k, text):
            return k
    return None


def _gpio(pin: str | None):
    m = re.fullmatch(r"gpio(\d+)", pin or "")
    return m.group(1) if m else None


def _mux_value(fields: dict, gpio: str | None):
    """The mux value of the SELECTED function. In these workbooks the 'PinMux Setting' cell is the slot number N of the selected function and
    `regsettingN` holds `TLMM_GPIOCFGn(5:2)=k` for that slot (verified on the Nissan CCS20 workbook: gpio4 selects slot 3, regsetting3 = 2 = RGMII).
    Taking the first regsetting would give the GPIO slot (0) for every pin, so it is never done: no slot number, no value."""
    slot = str(fields.get("pinmux_setting") if fields.get("pinmux_setting") is not None else "").strip()
    if not re.fullmatch(r"\d+", slot):
        return None
    m = re.search(r"CFG_?(\d+)\s*\(5:2\)\s*=\s*(\d+)", str(fields.get(f"regsetting{int(slot)}") or ""), re.I)
    if m and (gpio is None or m.group(1) == gpio):
        return int(m.group(2))
    return None


def _signal(fields: dict) -> str:
    return str(fields.get("gen5_signal_name") or fields.get("pinmuxed_name") or "").strip()


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _selected_text(fields: dict) -> str:
    return " ".join(str(fields.get(k) or "") for k in ("selected_interface", "pinmuxed_name"))


def _named_text(fields: dict) -> str:
    return " ".join(str(fields.get(k) or "") for k in ("gen5_signal_name", "interface_group"))


def _one_line(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().replace("*/", "* /")


# columns that list what a ball COULD do, not what it is set to - never evidence
CAPABILITY_COLUMNS = ("interfaces",)
_CAP_COL = re.compile(r"^(interfaces|(qup)?port\d+|(instance)(qup)?\d+|regsetting(qup)?\d+|(port|instance|regsetting)egpio)$")
_STRONG = re.compile(r"rg[mb]{1,2}ii|(?<![a-z])r?mii(?![a-z])|(?<![a-z])(mdio|mdc)(?![a-z])|emac|gmac|sgmii|ethernet", re.I)


def is_capability_column(k: str) -> bool:
    """Columns that list what a ball COULD do (the whole function list, or one mux slot): never evidence of what it IS set to."""
    return bool(_CAP_COL.match(k))


def _hits_elsewhere(fields: dict):
    """Ethernet words in ANY other column: [(column, snippet, family)]. Weaker evidence than the selected function: such a pin is only offered."""
    out = []
    for k, v in fields.items():
        if is_capability_column(k) or k in ("selected_interface", "pinmuxed_name", "gen5_signal_name", "interface_group"):
            continue
        t = _one_line(v)
        if not t or len(t) > 400:
            continue
        fam = _family_of(t) if _STRONG.search(t) else None
        if fam:
            out.append((k, t[:80], fam))
    return out


def column_hits(records) -> dict:
    """{column: number of rows with an Ethernet word in it} - shows the person WHERE Ethernet is written in their workbook."""
    n = {}
    for r in records:
        for k, v in r.fields.items():
            if is_capability_column(k):
                continue
            t = _one_line(v)
            if t and len(t) <= 400 and _family_of(t):
                n[k] = n.get(k, 0) + 1
    return dict(sorted(n.items(), key=lambda kv: -kv[1]))


def _selected_family(f: dict):
    """The Ethernet family a pin is SET to, from the 'Selected Interface' cell alone (never from names, remarks or the capability columns).
    The pin's own name (PinMuxed Name / signal, e.g. RGMII_MDC) only refines the label inside that decision: MDC/MDIO lines are muxed as
    the RGMII function but are their own group for the filter."""
    if re.match(r"\s*(pwr|power|gnd|vdd)", str(f.get("selected_interface") or ""), re.I):
        return None          # a supply pin of the PHY / SerDes block, not a signal
    fam = _family_of(str(f.get("selected_interface") or ""))
    if fam in ("rgmii", "rmii", "emac") and _fam_match("mdio", _signal(f) + " " + str(f.get("pinmuxed_name") or "")):
        fam = "mdio"
    return fam


_ETH_WORD = re.compile(r"(?<![a-z])eth(ernet)?(?![a-z])", re.I)


def _wired_family(f: dict):
    """Board-side evidence for a pin whose Selected Interface is NOT Ethernet: the Gen5 signal name says ETH (e.g. CPU_ETH_CTRL_1V8:MDIO / :RST_B /
    :INT_B) while the pin is set as a plain GPIO / WUP_GPIO. These are the PHY's own control lines wired by the board. A pin set to another
    function (PCIe, SPI, UART ...) is never turned into a GPIO, supply pins are never signals, and a pin already selected as Ethernet is not
    'wired'. The Interface Group is not required (it only labels the block). -> 'mdio' or 'emac', or None."""
    sel = str(f.get("selected_interface") or "")
    if re.match(r"\s*(pwr|power|gnd|vdd)", sel, re.I) or _selected_family(f):
        return None
    if not re.match(r"\s*(wup_)?gpio\b", sel, re.I):
        return None
    if not _ETH_WORD.search(_signal(f)):
        return None
    return "mdio" if _fam_match("mdio", _signal(f) + " " + str(f.get("pinmuxed_name") or "")) else "emac"


def wired_pins(records) -> list:
    """[(rec, family)] of pins the board wires to the Ethernet block while the workbook selects them as something else (see _wired_family)."""
    return [(r, fam) for r in records for fam in [_wired_family(r.fields)] if fam and _gpio_of(r)[1]]


_NEG_NOTE = re.compile(r"not\s+(in\s+use|used|mounted|connected|fitted|populated)|unused|\bdnp\b|do\s+not\s+populate|no\s+connect|\bn/?c\b|\bspare\b|reserved", re.I)
_SIGNALISH = re.compile(r"signal|group|net|schematic")


def _evidence(f: dict) -> list:
    """Ethernet wording a pin carries OUTSIDE its selected function and outside the capability columns (they list what a pin COULD do):
    [{column, value, stale}]. The names of power rails ('PWR:3V3S_MCU_ETH') are removed first, they are not signals."""
    out = []
    for k, v in f.items():
        if k in ("selected_interface", "selectedshdw", "str_selected_interface", "str_selectedshdw") or is_capability_column(k):
            continue
        t = re.sub(r"PWR:\S+", " ", re.sub(r"\s+", " ", str(v or "")).strip())
        if not t or len(t) > 500:
            continue
        hit = _STRONG.search(t) or (_SIGNALISH.search(k) and _ETH_WORD.search(t))
        if hit:
            out.append({"column": k, "value": t[:160], "stale": k.startswith(("old_", "cmp_"))})
    return out


def candidates(records, taken: set, enabled: set) -> tuple:
    """Pins that are NOT generated but carry Ethernet wording elsewhere in the row: ([suggestion rows], [pins with no TLMM number]).
    Only ever SUGGESTIONS (a person adds them, or not). 'likely' = the Interface Group says ETH or two live columns agree, and no note says
    'not in use'; everything else is 'possible'. Wording found only in an old-import column (old_*/cmp_*) is never 'likely'."""
    rows, nopin = [], []
    for r in records:
        f = r.fields
        sel = str(f.get("selected_interface") or "")
        if re.match(r"\s*(pwr|power|gnd|vdd)", sel, re.I) or _selected_family(f) or _wired_family(f):
            continue
        ev = _evidence(f)
        if not ev:
            continue
        pin, g = _gpio_of(r)
        if g is None:
            nopin.append(pin or str(f.get("pinmuxed_name") or "?"))
            continue
        if g in taken:
            continue
        live = [e for e in ev if not e["stale"]]
        text = " ".join(e["value"] for e in ev)
        fam = _family_of(text) or "emac"
        if fam not in enabled:
            continue
        gen5 = str(f.get("gen5_signal_name") or "").strip()
        grp_eth = bool(_ETH_WORD.search(str(f.get("interface_group") or "")))
        neg = bool(_NEG_NOTE.search(text))
        conflict = bool(gen5.strip()) and not _ETH_WORD.search(gen5) and not _STRONG.search(gen5)
        tier = "likely" if (not neg and not conflict and (grp_eth or len({e["column"] for e in live}) >= 2)) else "possible"
        why = [f"Ethernet wording in {e['column']}: \"{e['value'][:60]}\"" for e in ev[:2]]
        why.append(f"Selected Interface is \"{_one_line(sel)}\"")
        if conflict:
            why.append(f"Gen5 signal says \"{gen5[:40]}\"")
        if neg:
            why.append("a note says it is not in use")
        if all(e["stale"] for e in ev):
            why.append("only in an old-import column")
        rows.append({"pin": pin, "gpio": g, "signal": gen5 or _signal(f), "family": fam, "tier": tier, "conflict": conflict, "evidence": ev[:4],
                     "reason": f"{tier}: " + "; ".join(why) + " - not selected as Ethernet, so not generated; add it if it belongs"})
    rows.sort(key=lambda x: (x["tier"] != "likely", int(x["gpio"])))
    return rows, nopin


def classify(records, enabled: set[str]) -> dict:
    """-> {selected: [(rec, family)], disabled: [(rec, family)]}. A pin is Ethernet only when its Selected Interface says so."""
    selected, disabled = [], []
    for r in records:
        fam = _selected_family(r.fields)
        if fam:
            (selected if fam in enabled else disabled).append((r, fam))
    return {"selected": selected, "disabled": disabled}


def _gpio_of(rec):
    pid = _first_pin_identifier(rec.fields)
    pin = _to_dts_pin_name(pid) if pid else None
    return pin, _gpio(pin)


def _label_for(rec, pin, used: set) -> str:
    sig = _signal(rec.fields)
    tail = sig.split(":")[-1] if sig else ""
    base = "eth_" + (_slug(tail) or pin or "pin")
    base = re.sub(r"^(eth_)+eth_", "eth_", base)
    label, n = base, 2
    while label in used:
        label = f"{base}_{pin}" if n == 2 else f"{base}_{pin}_{n}"
        n += 1
    used.add(label)
    return label


def _node(label, pin, function, drive, drive_review, bias, bias_review, notes, extra=()):
    L = [f"\t{label}: {label} {{", "\t\tmux {", f'\t\t\tpins = "{pin}";', f'\t\t\tfunction = "{function}";', "\t\t};", "\t\tconfig {", f'\t\t\tpins = "{pin}";']
    for n in notes:
        L.append(f"\t\t\t/* NEEDS REVIEW: {n} */")
    L.append(f"\t\t\tdrive-strength = <{drive}>;" if drive is not None and not drive_review else "\t\t\t/* NEEDS REVIEW: drive-strength not determined from Excel */")
    L += [f"\t\t\t{x}" for x in extra]
    L.append(f"\t\t\t{bias};" if bias and not bias_review else "\t\t\t/* NEEDS REVIEW: bias not determined from Excel */")
    L += ["\t\t};", "\t};"]
    return "\n".join(L)


def generate(records, soc: str | None, project: str, enabled=None, include_pins=(), exclude_pins=(), board_wiring=True) -> dict:
    enabled = set(enabled or ORDER)
    soc_name, prof = soc_profile(soc)
    table = (prof or {}).get("pins", {})
    ctrl = (prof or {}).get("controller")
    inc = {re.sub(r"\D", "", str(x)) for x in include_pins if re.sub(r"\D", "", str(x))}
    exc = {re.sub(r"\D", "", str(x)) for x in exclude_pins if re.sub(r"\D", "", str(x))}
    cl = classify(records, enabled)
    items: list[dict] = []

    def item(kind, text):
        items.append({"kind": kind, "text": text})

    pins_out, left_out, used, nodes, labels = [], [], set(), [], []
    cands = [(r, fam, "selected") for r, fam in cl["selected"]]
    picked = {(_gpio_of(r)[1] or "") for r, _f in cl["selected"]}
    cands += [(r, "gpio", "included") for r in records if (_gpio_of(r)[1] or "") in inc and (_gpio_of(r)[1] or "") not in picked]
    if board_wiring:
        have = picked | inc
        for r, fam in wired_pins(records):
            if fam in enabled:
                if (_gpio_of(r)[1] or "") not in have:
                    cands.append((r, fam, "wired"))
                    have.add(_gpio_of(r)[1] or "")
            else:
                left_out.append({"pin": _gpio_of(r)[0], "signal": _signal(r.fields), "family": fam, "reason": f"{fam.upper()} is switched off in your filter"})
    no_pin_ctrl = []
    seen_gpio = set()
    for r, fam, how in cands:
        pin, g = _gpio_of(r)
        sig = _signal(r.fields)
        if g is None:
            no_pin_ctrl.append((r, fam, sig))
            continue
        if g in exc:
            left_out.append({"pin": pin, "signal": sig, "family": fam, "reason": "excluded by you"})
            continue
        if g in seen_gpio:
            continue
        seen_gpio.add(g)
        d = derive_pin_config(r)
        mux = _mux_value(r.fields, g)
        notes, extra = [], []
        tbl = table.get(g)
        if how in ("included", "wired"):
            function = "gpio"
            if how == "wired":
                notes.append(f"board-wired Ethernet control line (Gen5 signal {sig}): the workbook selects \"{_one_line(r.fields.get('selected_interface'))}\", so it is muxed as a GPIO - use it as the PHY reset / interrupt / MDIO line in the PHY node")
            else:
                notes.append(f"added by you: the workbook selects \"{_one_line(r.fields.get('selected_interface'))}\" for this pin, not Ethernet, so it is muxed as a GPIO")
            if d is not None and d.direction_directive:
                extra.append(f"{d.direction_directive};")
            else:
                raw = str(r.fields.get("direction") or "")
                if "OUT" in raw.upper():
                    extra.append(pin_hints.output_level_line(r.fields))
            if pin_hints.is_irq(r.fields):
                notes.append("the workbook flags this pin as an interrupt (irq = INT): it is most likely the PHY interrupt line - use it as interrupts-extended in the PHY node")
            status = "wired" if how == "wired" else "added"
        elif tbl:
            function = tbl["function"]
            if mux is not None and mux != tbl["mux"]:
                notes.append(f"the workbook says mux value {mux} for gpio{g}, but the {soc_name} pin table has \"{tbl['function']}\" at value {tbl['mux']} - confirm which is right")
                item("decision", f"{pin} ({sig}): workbook mux value {mux} disagrees with the {soc_name} pin table ({tbl['function']} = {tbl['mux']}).")
            status = "ok"
        elif fam in ("rgmii", "rmii"):
            function = fam
            notes.append(f"function \"{fam}\" is the family name; gpio{g} is not in the verified {soc_name or 'SoC'} pin table - check it against the SoC pinctrl driver")
            item("decision", f"{pin} ({sig}): function \"{fam}\" is not verified against a pin table for {soc_name or (soc or 'this SoC')}.")
            status = "review"
        else:
            left_out.append({"pin": pin, "signal": sig, "family": fam,
                             "reason": f"function name for {fam.upper()} on {soc_name or (soc or 'this SoC')} is not in the pin table - not guessed"})
            item("decision", f"{pin} ({sig}) is selected as {fam.upper()} but its pinctrl function name is unknown for {soc_name or (soc or 'this SoC')}; add it to eth_profiles.json or write the node by hand.")
            continue
        label = _label_for(r, pin, used)
        nodes.append(_node(label, pin, function, d.drive_strength if d else None, d.drive_strength_needs_review if d else True,
                           d.bias_directive if d else None, d.bias_needs_review if d else True, notes, extra))
        labels.append(label)
        pins_out.append({"pin": pin, "gpio": g, "label": label, "function": function, "mux": mux, "table_mux": tbl["mux"] if tbl else None,
                         "signal": sig, "family": fam, "status": status, "selected_as": _one_line(r.fields.get("selected_interface")),
                         "drive": d.drive_strength if d and not d.drive_strength_needs_review else None,
                         "bias": d.bias_directive if d and not d.bias_needs_review else None})
    for d_ in cl["disabled"]:
        r, fam = d_[0], d_[1]
        pin, _g = _gpio_of(r)
        left_out.append({"pin": pin or "(no pin)", "signal": _signal(r.fields), "family": fam, "reason": f"{fam.upper()} is switched off in your filter"})
    lanes = [x for x in no_pin_ctrl if x[1] == "sgmii"]
    if lanes:
        item("info", f"{len(lanes)} SGMII lane(s) are selected (" + ", ".join(sorted({x[2].split(':')[-1] or '?' for x in lanes}))[:80] + "). " + profiles()["families"]["sgmii"]["note"])
    others = [x for x in no_pin_ctrl if x[1] != "sgmii"]
    if others:
        item("info", f"{len(others)} selected Ethernet row(s) have no TLMM GPIO number, so no pin state: " + ", ".join(x[2] for x in others[:4]))

    taken = {p["gpio"] for p in pins_out} | exc | inc | {x["gpio"] for x in left_out if x.get("gpio")}
    cand_rows, nopin = candidates(records, taken, enabled)
    left_out.extend(cand_rows)
    if nopin:
        item("info", f"{len(nopin)} pin(s) without a TLMM GPIO number carry Ethernet wording (" + ", ".join(nopin[:6]) + (" ..." if len(nopin) > 6 else "") +
             "): they are not TLMM pins (for example the safety island), so Linux has no pin state for them.")

    # the consumer node: needed whenever anything Ethernet is selected - pin states, or an SGMII lane pair that has no pins at all
    over = []
    sgmii_on = bool(lanes) and "sgmii" in enabled
    fams = {p["family"] for p in pins_out}
    if labels or sgmii_on:
        over.append(f"&{ctrl or 'ethernet'} {{")
        if not ctrl:
            over.append(f"\t/* NEEDS REVIEW: the controller label for {soc or 'this SoC'} is not known; \"ethernet\" is a placeholder - use the label from your SoC dtsi */")
            item("decision", f"Controller label for {soc or 'this SoC'} is not known: the overlay uses &ethernet as a placeholder.")
        over.append('\tstatus = "okay";')
        if labels:
            over.append('\tpinctrl-names = "default";')
            over.append("\tpinctrl-0 = <" + " ".join(f"&{x}" for x in labels) + ">;")
        if sgmii_on and not (fams & {"rgmii", "rmii"}):
            lane_names = ", ".join(sorted({x[2].split(':')[-1] or '?' for x in lanes}))[:80]
            over.append(f'\tphy-mode = "sgmii";\t/* decided: the workbook selects SGMII on {lane_names} (SerDes lane pair, no TLMM pins) */')
            item("info", 'phy-mode = "sgmii" was taken from the workbook (SGMII lanes selected).')
        elif "rgmii" in fams:
            over.append('\tphy-mode = "rgmii-id";\t/* NEEDS REVIEW (assumed default): the workbook says RGMII but not who adds the clock delay - "rgmii-id" (PHY adds both delays) is the usual choice; other values: "rgmii", "rgmii-rxid", "rgmii-txid"; it depends on the PHY / board */')
            item("decision", 'phy-mode for RGMII: "rgmii-id" was written as an assumed default; confirm who adds the clock delay (rgmii / rgmii-id / rgmii-rxid / rgmii-txid).')
        elif "rmii" in fams:
            over.append('\tphy-mode = "rmii";\t/* NEEDS REVIEW (assumed default): RMII is what the workbook selects; confirm which side provides the 50 MHz reference clock */')
            item("decision", "RMII: confirm which side provides the 50 MHz reference clock.")
        over.append("\t/* NEEDS REVIEW: phy-handle / PHY address, PHY reset GPIO and link partner (PHY or switch) are not in the pin data */")
        over.append("};")
        item("decision", "PHY address, PHY reset GPIO and whether the partner is a PHY or a switch (fixed-link) are not in the pin data.")
    text_parts = []
    if nodes:
        text_parts.append("&tlmm {\n" + "\n\n".join(nodes) + "\n};")
    if over:
        text_parts.append("\n".join(over))
    alt_only = sum(1 for r in records if _family_of(str(r.fields.get("interfaces") or "")) and not _selected_family(r.fields))
    if not nodes:
        item("info", "No Ethernet pin state was generated: no pin in this project has an Ethernet function SELECTED (for the groups you chose)."
             + (f" {alt_only} pin(s) list Ethernet only as an alternate function they could do (columns 'Interfaces' / PortN); those are not used for Ethernet in this workbook." if alt_only else ""))
    review = sum(t.count("NEEDS REVIEW") for t in text_parts)
    return {"text": "\n\n".join(text_parts), "soc": soc, "soc_known": bool(prof), "soc_table": soc_name, "controller": ctrl,
            "pins": pins_out, "left_out": left_out, "items": items, "labels": labels, "review_lines": review,
            "counts": {fam: sum(1 for p in pins_out if p["family"] == fam) for fam in ORDER}}


# ------------------------------------------------------------------------------------------- independent verification
def verify_text(text: str, records, soc: str | None, include_pins=(), exclude_pins=(), enabled=None, board_wiring=True) -> dict:
    """Reads the finished text again, without using the generator's data, and compares it with the workbook and the SoC table."""
    from app import dts_style
    enabled = set(enabled or ORDER)
    _n, prof = soc_profile(soc)
    table = (prof or {}).get("pins", {})
    inc = {re.sub(r"\D", "", str(x)) for x in include_pins}
    exc = {re.sub(r"\D", "", str(x)) for x in exclude_pins}
    if board_wiring:
        inc |= {_gpio_of(r)[1] for r, fam in wired_pins(records) if fam in enabled}
    by_g = {}
    for r in records:
        _p, g = _gpio_of(r)
        if g:
            by_g[g] = r
    wrong, ambiguous, unverifiable, ok = [], [], [], 0
    t = dts_style._norm(text)
    nodes = []
    for b in dts_style.top_level_blocks(t):
        if b["header"].startswith("&tlmm"):
            n, un = dts_style.parse_pin_nodes(dts_style._inner(b["text"]))
            nodes += n
            for u in un:
                unverifiable.append("a block in &tlmm that is not a plain pin state: " + u.strip().split("\n")[0][:60])
    seen = {}
    for n in nodes:
        gp = re.findall(r"gpio(\d+)", n["mux_pins"])
        cp = re.findall(r"gpio(\d+)", n["cfg_pins"])
        if len(gp) != 1 or gp != cp:
            wrong.append(f"{n['label']}: mux and config must name the same single pin (found {n['mux_pins']} / {n['cfg_pins']})")
            continue
        g = gp[0]
        if g in seen:
            wrong.append(f"gpio{g} appears in both {seen[g]} and {n['label']}")
            continue
        seen[g] = n["label"]
        rec = by_g.get(g)
        if rec is None:
            wrong.append(f"{n['label']}: gpio{g} is not a pin of this project")
            continue
        sel = _selected_family(rec.fields)
        fn = n["function"].strip().strip('"')
        if g in inc and not (sel and sel in enabled):
            if fn != "gpio":
                wrong.append(f"{n['label']}: gpio{g} was added by you and must be muxed as gpio, found \"{fn}\"")
                continue
        elif not sel or sel not in enabled:
            wrong.append(f"{n['label']}: gpio{g} is not selected as Ethernet in the workbook (selected: \"{_one_line(rec.fields.get('selected_interface'))}\")")
            continue
        elif g in table:
            want = table[g]["function"]
            if fn != want:
                wrong.append(f"{n['label']}: gpio{g} function is \"{fn}\" but the {soc} pin table says \"{want}\"")
                continue
        elif sel in ("rgmii", "rmii"):
            if fn != sel:
                wrong.append(f"{n['label']}: gpio{g} function \"{fn}\" does not match the workbook's {sel.upper()}")
                continue
            ambiguous.append(f"{n['label']}: function \"{fn}\" cannot be checked against a pin table for {soc or 'this SoC'}")
        else:
            unverifiable.append(f"{n['label']}: no pin table for {soc or 'this SoC'} to check \"{fn}\" against")
        d = derive_pin_config(rec)
        props = " ".join(n["props"])
        if d is not None and d.drive_strength is not None and not d.drive_strength_needs_review:
            m = re.search(r"drive-strength\s*=\s*<(\d+)>", props)
            if not m or m.group(1) != str(d.drive_strength):
                wrong.append(f"{n['label']}: drive-strength is {m.group(1) if m else 'missing'} but the workbook says {d.drive_strength}")
                continue
        if d is not None and d.bias_directive and not d.bias_needs_review:
            if not re.search(r"(?<![\w-])" + re.escape(d.bias_directive) + r"\s*;", props):
                wrong.append(f"{n['label']}: bias should be {d.bias_directive} (workbook)")
                continue
        ok += 1
    # every selected pin must be there; no excluded pin may be
    for r in records:
        _p, g = _gpio_of(r)
        if not g:
            continue
        fam = _selected_family(r.fields)
        if fam and fam in enabled and g not in exc and g not in seen:
            if g in table or fam in ("rgmii", "rmii"):
                wrong.append(f"gpio{g} ({_signal(r.fields)}) is selected as {fam.upper()} in the workbook but has no pin state")
            else:
                ambiguous.append(f"gpio{g} ({_signal(r.fields)}) is selected as {fam.upper()} but was not generated (function name unknown)")
        if g in exc and g in seen:
            wrong.append(f"gpio{g} was excluded but is in the output")
    # consumers: pinctrl-0 must point at labels that exist, and every label must be used
    refs = []
    for m in re.finditer(r"pinctrl-0\s*=\s*<([^>]*)>", t):
        refs += re.findall(r"&([A-Za-z_]\w*)", m.group(1))
    labels = {n["label"] for n in nodes}
    for x in refs:
        if x not in labels:
            wrong.append(f"pinctrl-0 refers to &{x}, which is not defined")
    for x in sorted(labels - set(refs)):
        ambiguous.append(f"{x} is defined but no pinctrl-0 uses it")
    return {"wrong": wrong, "ambiguous": ambiguous, "unverifiable": unverifiable, "ok": ok}


ETH_CHECK_PROMPT = None


def check_prompt() -> str:
    from app import usb_ai_assist as u
    p = u.CHECK_PROMPT.replace("USB-related GPIO pin", "Ethernet-related GPIO pin")
    p = p.replace("a remark contradicts the configuration (e.g. \"unused\", \"not mounted\", \"DNP\", \"NC\", \"spare\") while the pin is configured as a live USB signal.",
                  "a remark contradicts the configuration (e.g. \"unused\", \"not mounted\", \"DNP\", \"NC\", \"spare\") while the pin is configured as a live Ethernet signal.")
    p = p.replace("the signal name says over-current / fault / detect (an input) but Direction says OUT, or an enable / reset / power signal is IN.",
                  "the signal name says RX / interrupt / status (an input) but Direction says OUT, or TX / reset / MDC (an output) is IN.")
    p = p.replace("the signal name belongs to one USB controller (USB20_0) and the interface group or net names another (USB20_1).",
                  "the signal name belongs to one Ethernet port and the interface group or net names another.")
    return p
