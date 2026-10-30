"""
I2C pin states from the PinMux workbook - Python generates, a second, independent reader verifies.

Which pins are I2C?  Only those whose SELECTED function says so (`selected_interface`, e.g. "I2C_QUP18, QUP18" or "I2C_QUP0_SE1, QUP0_SE1").  The
`interfaces` column and the PortN / RegSettingN columns list what a ball COULD do and are never used to pick pins.

For a QUP I2C the pinctrl function is the same `qupN` as for SPI and UART, so the pin table of the SoC is READ FROM spi_profiles.json (Linux pinctrl
driver) instead of being copied.  The workbook's own mux value is compared with the table; a disagreement is stated on the pin, never resolved silently.
A SoC without a table still gets pins, with the workbook's QUP name as function and a review mark.

The role of a pin (sda / scl) comes from its own PinMuxed Name.  The Gen5 net is used to cross-check the BUS (I2C against UART, SPI ...) and to NAME
THE DEVICES on the bus: one pin can sit on several nets (a shared bus); those nets are listed on the controller node, nothing is picked.

One `&i2cN` controller is produced per QUP instance: status and pinctrl-0 with that instance's pins (and the QUP wrapper to enable).  The devices on
the bus, their addresses and the bus speed are not in the pin data and stay as review comments.
Not generated, listed as left out: I2C of the sensors subsystem (SSC), of the safety island (SAIL), the camera control interface (CCI, own driver and
function) and other SoC families (Telechips I2C_03 ...).
"""
from __future__ import annotations

import json
import os
import re

from app import pin_hints
from app.generators.dts_generator import _first_pin_identifier, _to_dts_pin_name, derive_pin_config

_HERE = os.path.dirname(os.path.abspath(__file__))
ORDER = ["i2c"]
LABELS = {"i2c": "I2C (QUP)"}
_PROFILES = None
_SPI = None


def profiles() -> dict:
    global _PROFILES
    if _PROFILES is None:
        with open(os.path.join(_HERE, "i2c_profiles.json"), encoding="utf-8") as f:
            _PROFILES = json.load(f)
    return _PROFILES


def _spi_profiles() -> dict:
    global _SPI
    if _SPI is None:
        with open(os.path.join(_HERE, "spi_profiles.json"), encoding="utf-8") as f:
            _SPI = json.load(f)
    return _SPI


def families() -> list[dict]:
    return [{"key": k, "label": LABELS[k], "tlmm": True} for k in ORDER]


def soc_profile(soc: str | None):
    """-> (name, profile) with `pins` / `wrappers` filled from the SPI profile of the same SoC (same pinctrl driver table)."""
    s = re.sub(r"[^A-Z0-9]", "", (soc or "").upper())
    for name, prof in profiles()["socs"].items():
        if any(re.sub(r"[^A-Z0-9]", "", n.upper()) == s for n in [name] + prof.get("aliases", [])):
            base = _spi_profiles()["socs"].get(prof.get("pins_from", name), {})
            return name, {**prof, "pins": base.get("pins", {}), "wrappers": base.get("wrappers", {})}
    return None, None


def _gpio(pin):
    m = re.fullmatch(r"gpio(\d+)", pin or "")
    return m.group(1) if m else None


def _gpio_of(rec):
    pid = _first_pin_identifier(rec.fields)
    pin = _to_dts_pin_name(pid) if pid else None
    return pin, _gpio(pin)


def _signal(fields: dict) -> str:
    return re.sub(r"\s+", " ", str(fields.get("gen5_signal_name") or fields.get("pinmuxed_name") or "")).strip()


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _one_line(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().replace("*/", "* /")


def _supply(sel: str) -> bool:
    return bool(re.match(r"\s*(pwr|power|gnd|vdd)", sel, re.I))


# ----------------------------------------------------------------------------------------------------------- what a pin is set to
def selected(fields: dict):
    """-> {mode, inst, n, se, kind, dom} from the SELECTED function alone, or None. mode is always 'i2c'.
    kind: 'qup' (application QUP), 'ssc' / 'sail' (other subsystems), 'cci' (camera control interface), 'other' (another SoC family, e.g. I2C_03)."""
    sel = str(fields.get("selected_interface") or "")
    if _supply(sel):
        return None
    for p in profiles()["selected"]:
        m = re.search(p["pattern"], sel, re.I)
        if m:
            gd = m.groupdict()
            dom = (gd.get("dom") or "").lower()
            inst = re.sub(r"[^a-z0-9]+", "_", ((dom + "_") if dom else "") + (gd.get("inst") or p["name"]).lower()).strip("_")
            kind = dom if dom else p["name"]
            return {"mode": "i2c", "inst": inst, "n": gd.get("n"), "se": gd.get("se"), "kind": kind, "dom": dom}
    return None


def role_of(fields: dict) -> str:
    """sda / scl / '' - from the pin's own name (PinMuxed Name), then Selected Interface. The Gen5 net is not used (named from the peer's side)."""
    rx = profiles()["roles"]
    for text in (str(fields.get("pinmuxed_name") or ""), str(fields.get("selected_interface") or "")):
        for key in ("sda", "scl"):
            if re.search(rx[key], text, re.I):
                return key
    return ""


_I2C_WORD = re.compile(r"(?<![a-z])i2cs?(?![a-z])", re.I)


def wired(fields: dict) -> bool:
    """I2C has no board-wired control-line feature: enable / reset lines of an I2C device are GPIOs of that device (USB hub, touch panel ...) and belong
    to its own engine; listing them here as well would claim one GPIO twice.  They are only suggested (see candidates())."""
    return False


_OTHER_BUS = re.compile(r"(?<![a-z])(uart|hsuart|i3c|spi|can|canfd|i2s|mi2s|tdm|pcie|usb\d*|eth|ethernet|rgmii|sgmii|mdio|sdc\d?|sdio|pwm|gpu|csi|dsi)(?![a-z])", re.I)


def gen5_conflict(fields: dict):
    """Gen5 signal names ANOTHER bus (I2C, SPI, CAN ...) and not I2C -> that bus word, else None. Used to cross-check Selected Interface."""
    sig = _signal(fields)
    if _I2C_WORD.search(sig):
        return None
    m = _OTHER_BUS.search(sig)
    return m.group(1).upper() if m else None


def gen5_only(fields: dict) -> bool:
    """Gen5 signal says I2C, but Selected Interface is neither I2C nor a plain GPIO (e.g. another bus): a disagreement, never generated."""
    sel = str(fields.get("selected_interface") or "")
    return bool(_I2C_WORD.search(_signal(fields))) and not selected(fields) and not _supply(sel) and not re.match(r"\s*(wup_)?gpio\b", sel, re.I) and bool(sel.strip())


# ------------------------------------------------------------------------------------------------ suggestions (never generated)
_STRONG = re.compile(r"(?<![a-z])i2cs?(?![a-z])", re.I)
_NEG_NOTE = re.compile(r"not\s+(in\s+use|used|mounted|connected|fitted|populated)|unused|\bdnp\b|do\s+not\s+populate|no\s+connect|\bn/?c\b|\bspare\b|reserved", re.I)
_CAPABILITY_PHRASE = re.compile(r"(could|can|may|might)\s+(also\s+)?be\s+(used\s+(as|for)|muxed(\s+(as|to|with))?)\s+[^,.;|]*", re.I)


def _evidence(f: dict) -> list:
    """I2C wording a pin carries OUTSIDE its selected function and outside the capability columns: [{column, value, stale}]."""
    from app.eth_generator import is_capability_column
    out = []
    for k, v in f.items():
        if k in ("selected_interface", "selectedshdw", "str_selected_interface", "str_selectedshdw") or is_capability_column(k):
            continue
        t = re.sub(r"PWR:\S+", " ", re.sub(r"\s+", " ", str(v or "")).strip())
        if not t or len(t) > 500:
            continue
        t_ = _CAPABILITY_PHRASE.sub(" ", t)
        if _STRONG.search(t_):
            out.append({"column": k, "value": t[:160], "stale": k.startswith(("old_", "cmp_"))})
    return out


def candidates(records, taken: set) -> tuple:
    """Pins NOT generated that carry I2C wording elsewhere in the row: ([suggestion rows], [pins with no TLMM number]). Only SUGGESTIONS.
    'likely' = two live columns agree (or the interface group itself says I2C), no note says 'not in use', and the Gen5 signal names no other bus."""
    rows, nopin = [], []
    for r in records:
        f = r.fields
        sel = str(f.get("selected_interface") or "")
        if _supply(sel) or selected(f) or wired(f) or gen5_only(f):
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
        neg = bool(_NEG_NOTE.search(text))
        conflict = bool(gen5_conflict(f))
        grp_u = bool(_I2C_WORD.search(str(f.get("interface_group") or "")))
        has_gen5 = bool(str(f.get("gen5_signal_name") or "").strip())
        tier = "likely" if (not neg and not conflict and (grp_u or (not has_gen5 and len({e["column"] for e in live}) >= 2))) else "possible"
        why = [f"I2C wording in {e['column']}: \"{e['value'][:60]}\"" for e in ev[:2]] + [f"Selected Interface is \"{_one_line(sel)}\""]
        if conflict:
            why.append(f"Gen5 signal says \"{_signal(f)[:40]}\"")
        if neg:
            why.append("a note says it is not in use")
        if all(e["stale"] for e in ev):
            why.append("only in an old-import column")
        rows.append({"pin": pin, "gpio": g, "signal": _signal(f), "family": "i2c", "tier": tier, "conflict": conflict, "evidence": ev[:4],
                     "reason": f"{tier}: " + "; ".join(why) + " - not selected as I2C, so not generated; add it if it belongs"})
    rows.sort(key=lambda x: (x["tier"] != "likely", int(x["gpio"])))
    return rows[:60], nopin


def _qup_mux(fields: dict, gpio: str | None):
    """The workbook's mux value for the SELECTED function: `PinMux Setting` = Qk names the slot, `RegSettingQUPk` holds `TLMM_GPIO_CFGn(5:2)=v`.
    No slot or a cell for another pin -> None (never guessed)."""
    slot = str(fields.get("pinmux_setting") if fields.get("pinmux_setting") is not None else "").strip()
    m = re.fullmatch(r"[Qq](\d+)", slot)
    if not m:
        return None
    c = re.search(r"CFG_?(\d+)\s*\(5:2\)\s*=\s*(\d+)", str(fields.get(f"regsettingqup{int(m.group(1))}") or ""), re.I)
    if c and (gpio is None or c.group(1) == gpio):
        return int(c.group(2))
    return None


def classify(records, enabled):
    sel, off = [], []
    for r in records:
        s = selected(r.fields)
        if s:
            (sel if s["mode"] in enabled else off).append((r, s))
    return {"selected": sel, "disabled": off}


def controller_label(prof, inst: dict):
    """-> (label, known). `i2c{n}` for the QUP number; `known` only when the SoC's mainline dtsi really defines that controller."""
    fmt = (prof or {}).get("controller")
    if inst.get("n") is not None:
        n = int(inst["n"])
        if fmt and not inst.get("se"):
            return fmt.replace("{n}", str(n)), n in (prof or {}).get("controller_defined", [])
        return f"i2c{n}" + (f"_se{inst['se']}" if inst.get("se") else ""), False
    return _slug(inst.get("inst") or "i2c"), False


def _inst_name(inst: dict) -> str:
    if inst.get("n") is not None:
        return f"i2c{int(inst['n'])}" + (f"_se{inst['se']}" if inst.get("se") else "")
    return _slug(inst.get("inst") or "i2c")


def _label_for(pin, iname, role, used, sig=""):
    tail = _slug(sig.split(":")[-1].split(",")[0])
    base = f"{iname or 'i2c'}_{role or tail or pin}"
    label, n = base, 2
    while label in used:
        label = f"{base}_{pin}" if n == 2 else f"{base}_{pin}_{n}"
        n += 1
    used.add(label)
    return label


def _node(label, pin, function, drive, drive_review, bias, bias_review, notes, extra=()):
    L = [f"\t{label}: {label} {{", "\t\tmux {", f'\t\t\tpins = "{pin}";', f'\t\t\tfunction = "{function}";', "\t\t};", "\t\tconfig {", f'\t\t\tpins = "{pin}";']
    for n in notes:
        if n.startswith("DECIDED: "):
            L.append(f"\t\t\t/* decided: {n[len('DECIDED: '):]} */")
        else:
            L.append(f"\t\t\t/* NEEDS REVIEW: {n} */")
    L.append(f"\t\t\tdrive-strength = <{drive}>;" if drive is not None and not drive_review else "\t\t\t/* NEEDS REVIEW: drive-strength not determined from Excel */")
    L += [f"\t\t\t{x}" for x in extra]
    L.append(f"\t\t\t{bias};" if bias and not bias_review else "\t\t\t/* NEEDS REVIEW: bias not determined from Excel */")
    L += ["\t\t};", "\t};"]
    return "\n".join(L)


def _devices(fields: dict) -> list:
    """Device groups named by the Gen5 nets of a pin: 'CPU_SUBPCB_I2C_1V8:I2C_SDA, CPU_A2B_I2C_1V8:I2C' -> ['SUBPCB', 'A2B']. One entry per net."""
    out = []
    for seg in re.split(r"[,\n]", str(fields.get("gen5_signal_name") or "")):
        head = seg.split(":")[0].strip().upper()
        head = re.sub(r"_\d+V\d+$", "", head)
        head = re.sub(r"^CPU_", "", head)
        head = re.sub(r"(_CFG)?_I2C\d*$", "", head)
        if head and head not in out:
            out.append(head)
    return out


def _group_of(sig: str) -> str:
    return sig.split(":")[0].strip().lower() if ":" in sig else ""


def _pick_function(table_entries, inst: dict, mux):
    """-> (function, table_mux, note). `qup<n>` for the selected QUP; else the one table entry the workbook's mux value points at; else None."""
    names = {e["function"]: e["mux"] for e in table_entries}
    want = f"qup{int(inst['n'])}" if inst.get("n") is not None else None
    if want in names:
        return want, names[want], None
    if mux is not None:
        hit = [e for e in table_entries if e["mux"] == mux]
        if len(hit) == 1:
            return hit[0]["function"], hit[0]["mux"], f"the selected function is {want or inst.get('inst')}, which this pin does not have; the workbook's mux value {mux} is \"{hit[0]['function']}\" in the pin table - confirm"
    return None, None, None


def generate(records, soc, project, enabled=None, include_pins=(), exclude_pins=(), board_wiring=True) -> dict:
    enabled = set(enabled or ORDER)
    soc_name, prof = soc_profile(soc)
    table = (prof or {}).get("pins", {})
    wrappers = (prof or {}).get("wrappers", {})
    inc = {re.sub(r"\D", "", str(x)) for x in include_pins if re.sub(r"\D", "", str(x))}
    exc = {re.sub(r"\D", "", str(x)) for x in exclude_pins if re.sub(r"\D", "", str(x))}
    cl = classify(records, enabled)
    items: list[dict] = []

    def item(kind, text):
        items.append({"kind": kind, "text": text})

    pins_out, left_out, used, nodes = [], [], set(), []
    cands = [(r, s, "selected") for r, s in cl["selected"]]
    have = {(_gpio_of(r)[1] or "") for r, _s in cl["selected"]}
    cands += [(r, None, "included") for r in records if (_gpio_of(r)[1] or "") in inc and (_gpio_of(r)[1] or "") not in have]
    have |= inc
    if board_wiring:
        for r in records:
            if wired(r.fields) and (_gpio_of(r)[1] or "") and (_gpio_of(r)[1] or "") not in have:
                cands.append((r, None, "wired"))
                have.add(_gpio_of(r)[1])
    no_pin, seen, dir_conflicts, unverified, gen5_bad, cci_pins = [], set(), [], [], [], []
    sel_groups = {}
    for r, s, how in cands:
        if s and s["kind"] == "qup" and _gpio_of(r)[1]:
            sel_groups.setdefault(_group_of(_signal(r.fields)), set()).add(s["inst"])
    for r, s, how in cands:
        pin, g = _gpio_of(r)
        sig = _signal(r.fields)
        role = role_of(r.fields)
        if g is None:
            no_pin.append((r, s, sig))
            continue
        if g in exc:
            left_out.append({"pin": pin, "signal": sig, "family": (s or {}).get("mode") or "control", "reason": "excluded by you"})
            continue
        if g in seen:
            continue
        seen.add(g)
        d = derive_pin_config(r)
        notes, extra = [], []
        mux = _qup_mux(r.fields, g) if s else None
        tbl_mux, status, inst_key = None, "ok", None
        inst = {"inst": None, "n": None, "se": None}
        mode = None
        if how in ("included", "wired"):
            function = "gpio"
            if how == "wired":
                notes.append(f"board-wired I2C control line (Gen5 signal {sig}): the workbook selects \"{_one_line(r.fields.get('selected_interface'))}\", so it is muxed as a GPIO - use it as a debug-port select / I2C enable in the board's device nodes")
            else:
                notes.append(f"added by you: the workbook selects \"{_one_line(r.fields.get('selected_interface'))}\" for this pin, not I2C, so it is muxed as a GPIO")
            if d is not None and d.direction_directive:
                extra.append(f"{d.direction_directive};")
            elif "OUT" in str(r.fields.get("direction") or "").upper():
                extra.append(pin_hints.output_level_line(r.fields))
            if pin_hints.is_irq(r.fields):
                notes.append("the workbook flags this pin as an interrupt (irq = INT): it is most likely an interrupt line - use it as interrupts-extended on the device node")
            status = "wired" if how == "wired" else "added"
            insts = sel_groups.get(_group_of(sig), set()) if how == "wired" else set()
            inst_key = next(iter(insts)) if len(insts) == 1 else None
            if how == "wired" and inst_key is None and not insts:
                stem = "_".join(_group_of(sig).split("_")[:2])
                by_stem = {x for k, v in sel_groups.items() if stem and k.startswith(stem + "_") for x in v}
                if len(by_stem) == 1:
                    inst_key = next(iter(by_stem))
                    notes.append(f"assigned to {inst_key.upper()} only because its Gen5 group starts with \"{stem.upper()}\" like that bus - confirm which I2C this line belongs to")
            if inst_key:
                for r2, s2 in cl["selected"]:
                    if s2["inst"] == inst_key:
                        inst = s2
                        break
        else:
            inst, mode, inst_key = s, s["mode"], s["inst"]
            if s["kind"] in ("ssc", "sail"):
                dom = {"ssc": "sensors subsystem (SSC)", "sail": "safety island (SAIL)"}[s["kind"]]
                left_out.append({"pin": pin, "signal": sig, "family": mode, "reason": f"{s['inst'].upper()} is a QUP of the {dom}, not an application-processor QUP: its pinctrl function is not in the TLMM pin table - not guessed"})
                item("decision", f"{pin} ({sig}) is selected as {s['inst'].upper()} ({dom}); it needs the node of that subsystem's own pin controller, which this tool has no confirmed form for.")
                continue
            if s["kind"] == "qup":
                entries = table.get(g)
                if entries:
                    function, tbl_mux, why = _pick_function(entries, s, mux)
                    if function is None:
                        left_out.append({"pin": pin, "signal": sig, "family": mode, "reason": f"the pin table has no function for {s['inst'].upper()} on gpio{g} (it offers {', '.join(e['function'] for e in entries)}) - not guessed"})
                        item("decision", f"{pin} ({sig}) is selected as {s['inst'].upper()} but gpio{g} has no matching function in the {soc_name} pin table; check the pin or the workbook.")
                        continue
                    if why:
                        notes.append(why)
                        item("decision", f"{pin} ({sig}): {why}")
                    elif mux is not None and mux != tbl_mux:
                        notes.append(f"DECIDED: the workbook says mux value {mux} for gpio{g}, the {soc_name} pin table has \"{function}\" at value {tbl_mux}; the DTS names the function and the kernel derives the mux value from the pin table, so \"{function}\" is used - fix the workbook cell")
                        item("info", f"{pin} ({sig}): workbook mux value {mux} disagrees with the {soc_name} pin table ({function} = {tbl_mux}); the pin table was used (workbook cell to fix).")
                elif table:
                    left_out.append({"pin": pin, "signal": sig, "family": mode, "reason": f"gpio{g} is not a QUP pin in the {soc_name} pin table - not guessed"})
                    item("decision", f"{pin} ({sig}) is selected as {s['inst'].upper()} but gpio{g} has no QUP function in the {soc_name} pin table; check the workbook.")
                    continue
                else:
                    function = s["inst"]
                    status = "review"
                    notes.append(f"function \"{function}\" is the workbook's QUP name; {soc_name or soc or 'this SoC'} has no verified pin table - check it against the SoC pinctrl driver")
                    unverified.append(pin)
            elif s["kind"] == "cci":
                left_out.append({"pin": pin, "signal": sig, "family": mode, "reason": "CCI is the camera control interface: its own driver and pin functions, normally written with the camera nodes - not an I2C QUP, not guessed"})
                cci_pins.append(pin)
                continue
            else:
                left_out.append({"pin": pin, "signal": sig, "family": mode, "reason": f"{s['inst'].upper()} is not a Qualcomm QUP engine: its pinctrl function name is not in any pin table - not guessed"})
                item("decision", f"{pin} ({sig}) is selected as {s['inst'].upper()}; write its pin state by hand or add the SoC to i2c_profiles.json.")
                continue
            gc = gen5_conflict(r.fields)
            if gc:
                notes.append(f"Selected Interface says I2C, but the Gen5 signal \"{sig}\" names {gc} - confirm which is right")
                gen5_bad.append(pin)
            if d is not None and d.bias_directive == "bias-disable" and not d.bias_needs_review:
                notes.append("DECIDED: the workbook sets no internal pull - normal for I2C when the board has an external pull-up on the line (confirm it on the schematic)")
        label = _label_for(pin, _inst_name(inst) if inst_key else "i2c", role if how == "selected" else "", used, sig)
        nodes.append(_node(label, pin, function, d.drive_strength if d else None, d.drive_strength_needs_review if d else True,
                           d.bias_directive if d else None, d.bias_needs_review if d else True, notes, extra))
        pins_out.append({"pin": pin, "gpio": g, "label": label, "function": function, "mux": mux, "table_mux": tbl_mux, "signal": sig,
                         "family": mode or "control", "role": role, "instance": inst_key, "n": (inst or {}).get("n"), "se": (inst or {}).get("se"),
                         "status": status, "selected_as": _one_line(r.fields.get("selected_interface")),
                         "drive": d.drive_strength if d and not d.drive_strength_needs_review else None,
                         "bias": d.bias_directive if d and not d.bias_needs_review else None})
    for r, s in cl["disabled"]:
        left_out.append({"pin": _gpio_of(r)[0] or "(no pin)", "signal": _signal(r.fields), "family": s["mode"], "reason": f"{LABELS[s['mode']]} is switched off in your filter"})
    if no_pin:
        item("info", f"{len(no_pin)} selected I2C row(s) have no TLMM GPIO number, so no pin state: " + ", ".join(sorted({x[2] or x[0].fields.get('pinmuxed_name') or '?' for x in no_pin}))[:120] +
             " (the safety island or a different SoC family uses its own pin controller - write those by hand).")
    for r in records:
        if gen5_only(r.fields) and (_gpio_of(r)[1] or "") not in exc:
            pin_, g_ = _gpio_of(r)
            left_out.append({"pin": pin_ or "(no pin)", "signal": _signal(r.fields), "family": "i2c",
                             "reason": f"the Gen5 signal says I2C but the workbook selects \"{_one_line(r.fields.get('selected_interface'))}\" - not generated; add it yourself if it really is I2C" if g_ else
                                       f"the Gen5 signal says I2C but the workbook selects \"{_one_line(r.fields.get('selected_interface'))}\" (no TLMM GPIO number)"})
    if cci_pins:
        item("info", f"{len(cci_pins)} pin(s) are selected as CCI (camera control interface): " + ", ".join(cci_pins[:8]) + (" ..." if len(cci_pins) > 8 else "") +
             ". They use the pin functions of the camera subsystem and are written with the camera nodes, not here.")
    if gen5_bad:
        item("decision", f"{len(gen5_bad)} pin(s) are selected as I2C but their Gen5 signal names another bus (" + ", ".join(gen5_bad[:6]) + "): Selected Interface was followed, check the workbook.")
    if unverified:
        item("decision", f"{len(unverified)} pin(s) use the workbook's QUP name as function (\"{pins_out[[p['pin'] for p in pins_out].index(unverified[0])]['function']}\" ...) because {soc_name or soc or 'this SoC'} has no verified I2C pin table: " + ", ".join(unverified[:8]) + (" ..." if len(unverified) > 8 else "") + ". Check them against the SoC pinctrl driver.")

    # consumers: one &i2cN per instance
    over, ctl_known = [], True
    by_inst: dict = {}
    for p in pins_out:
        by_inst.setdefault(p["instance"], []).append(p)
    group_of_inst = {}
    for r, s in cl["selected"]:
        group_of_inst.setdefault(s["inst"], _group_of(_signal(r.fields)) or str(r.fields.get("interface_group") or ""))
    devices_of: dict = {}
    for r, s in cl["selected"]:
        for dv in _devices(r.fields):
            devices_of.setdefault(s["inst"], [])
            if dv not in devices_of[s["inst"]]:
                devices_of[s["inst"]].append(dv)
    wrappers_used, unknown_ctl, ctl_done = [], [], []
    for inst_key in sorted(k for k in by_inst if k):
        plist = by_inst[inst_key]
        inst = {"inst": inst_key, "n": plist[0]["n"], "se": plist[0]["se"]}
        lab, known = controller_label(prof, inst)
        ctl_known &= known
        modes = {p["family"] for p in plist if p["family"] in ORDER}
        wr = wrappers.get(str(int(inst["n"]))) if inst["n"] is not None else None
        if wr and wr not in wrappers_used:
            wrappers_used.append(wr)
        over.append(f"&{lab} {{")
        if not known:
            defined = ", ".join("i2c%d" % n for n in (prof or {}).get("controller_defined", []))
            over.append(f"\t/* NEEDS REVIEW: the controller label \"{lab}\" is a guess for {soc or 'this SoC'}" + (f" (the mainline dtsi defines only {defined})" if defined else "") + "; use the label from your SoC / BSP dtsi or add the node */")
            unknown_ctl.append(lab)
        over.append('\tstatus = "okay";')
        over.append('\tpinctrl-names = "default";')
        over.append("\tpinctrl-0 = <" + " ".join(f"&{p['label']}" for p in plist) + ">;")
        roles = {p["role"] for p in plist}
        devs = devices_of.get(inst_key, [])
        wires = "SDA + SCL" if {"sda", "scl"} <= roles else "partial: " + ", ".join(sorted(r for r in roles if r))
        if len(devs) > 1:
            over.append(f"\t/* NEEDS REVIEW: the workbook puts {len(devs)} device groups on this bus: {', '.join(devs)} - their nodes go under &{lab} (addresses are not in the pin data); check on the schematic that they really share it */")
        elif devs:
            over.append(f"\t/* NEEDS REVIEW: device group on this bus (Gen5): {devs[0]} - its node goes under &{lab}; address and compatible are not in the pin data */")
        over.append(f"\t/* NEEDS REVIEW: bus speed (clock-frequency) is not in the pin data; wiring in the workbook: {wires} */")
        over.append("};")
        ctl_done.append(lab)
    if unknown_ctl:
        item("decision", f"Controller label(s) for {soc or 'this SoC'} are not confirmed: the overlay uses " + ", ".join("&" + x for x in unknown_ctl) + " - use the labels from your SoC / BSP dtsi.")
    if ctl_done:
        item("decision", "The devices on each I2C bus (" + ", ".join("&" + x for x in ctl_done) + "), their addresses and the bus speed are not in the pin data.")
    if None in by_inst:
        lines = ", ".join(f"&{p['label']}" for p in by_inst[None])
        over.append(f"/* NEEDS REVIEW: board-wired I2C control pin state(s) with no I2C controller known: {lines} - reference them from the pinctrl-0 of the device that uses them */")
        item("decision", "Board-wired I2C control line(s) could not be tied to one I2C (" + ", ".join(p["pin"] for p in by_inst[None][:6]) + "): say which device uses them.")
    wrap = []
    for w in sorted(wrappers_used):
        wrap.append(f"&{w} {{\n\tstatus = \"okay\";\n}};")
    text_parts = []
    if nodes:
        text_parts.append("&tlmm {\n" + "\n\n".join(nodes) + "\n};")
    if wrap:
        text_parts.append("\n\n".join(wrap))
    if over:
        text_parts.append("\n\n".join(_split_blocks(over)))
    taken = {p["gpio"] for p in pins_out} | exc | inc | {x["gpio"] for x in left_out if x.get("gpio")}
    cand_rows, cand_nopin = candidates(records, taken)
    left_out.extend(cand_rows)
    if not nodes:
        item("info", "No I2C pin state was generated: no pin in this project has an I2C function SELECTED (for the I2C kinds you chose).")
    review = sum(t.count("NEEDS REVIEW") for t in text_parts)
    return {"text": "\n\n".join(text_parts), "soc": soc, "soc_known": bool(prof), "soc_table": soc_name, "controllers": sorted({controller_label(prof, {"inst": k, "n": v[0]["n"], "se": v[0]["se"]})[0] for k, v in by_inst.items() if k}),
            "pins": pins_out, "left_out": left_out, "items": items, "labels": [p["label"] for p in pins_out], "review_lines": review,
            "counts": {**{m: sum(1 for p in pins_out if p["family"] == m) for m in ORDER}, "control": sum(1 for p in pins_out if p["family"] == "control")}}


def _split_blocks(lines):
    out, cur = [], []
    for ln in lines:
        cur.append(ln)
        if ln == "};" or (ln.startswith("/*") and len(cur) == 1):
            out.append("\n".join(cur))
            cur = []
    if cur:
        out.append("\n".join(cur))
    return out


# ------------------------------------------------------------------------------------------- independent verification
def verify_text(text, records, soc, include_pins=(), exclude_pins=(), enabled=None, board_wiring=True) -> dict:
    """Reads the finished text again, without using the generator's data, and compares it with the workbook and the SoC table."""
    from app import dts_style
    enabled = set(enabled or ORDER)
    _n, prof = soc_profile(soc)
    table = (prof or {}).get("pins", {})
    inc = {re.sub(r"\D", "", str(x)) for x in include_pins}
    exc = {re.sub(r"\D", "", str(x)) for x in exclude_pins}
    if board_wiring:
        inc |= {_gpio_of(r)[1] for r in records if wired(r.fields) and _gpio_of(r)[1]}
    by_g = {}
    for r in records:
        _p, g = _gpio_of(r)
        if g:
            by_g[g] = r
    wrong, ambiguous, unverifiable, ok = [], [], [], 0
    t = dts_style._norm(text)
    nodes, ctl_blocks = [], []
    for b in dts_style.top_level_blocks(t):
        if b["header"].startswith("&tlmm"):
            n, un = dts_style.parse_pin_nodes(dts_style._inner(b["text"]))
            nodes += n
            for u in un:
                unverifiable.append("a block in &tlmm that is not a plain pin state: " + u.strip().split("\n")[0][:60])
        else:
            ctl_blocks.append(b)
    seen, label_inst, unchecked = {}, {}, []
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
        s = selected(rec.fields)
        fn = n["function"].strip().strip('"')
        if g in inc and not (s and s["mode"] in enabled):
            if fn != "gpio":
                wrong.append(f"{n['label']}: gpio{g} is a board-wired / added I2C control line and must be muxed as gpio, found \"{fn}\"")
                continue
        elif not s or s["mode"] not in enabled:
            wrong.append(f"{n['label']}: gpio{g} is not selected as I2C in the workbook (selected: \"{_one_line(rec.fields.get('selected_interface'))}\")")
            continue
        elif s["kind"] != "qup":
            wrong.append(f"{n['label']}: gpio{g} is selected as {s['inst'].upper()} ({s['kind']}), which has no application-TLMM function name - it must not be generated")
            continue
        else:
            label_inst[n["label"]] = s["inst"]
            if g in table:
                allowed = {e["function"] for e in table[g]}
                want = f"qup{int(s['n'])}"
                if fn not in allowed:
                    wrong.append(f"{n['label']}: gpio{g} function \"{fn}\" is not a QUP function of this pin in the {soc} pin table ({', '.join(sorted(allowed))})")
                    continue
                if fn != want:
                    ambiguous.append(f"{n['label']}: function \"{fn}\" is not the selected {s['inst'].upper()} (\"{want}\") - allowed only because the workbook's mux value points at it")
            elif not table:
                if fn != s["inst"]:
                    wrong.append(f"{n['label']}: gpio{g} function \"{fn}\" does not match the workbook's {s['inst'].upper()}")
                    continue
                unchecked.append(n["label"])
            else:
                unverifiable.append(f"{n['label']}: gpio{g} is not a QUP pin of the {soc} pin table, cannot check \"{fn}\"")
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
    if unchecked:
        ambiguous.append(f"{len(unchecked)} pin state(s) ({', '.join(unchecked[:5])}{' ...' if len(unchecked) > 5 else ''}): the function name matches the workbook's QUP name but cannot be checked against a pin table for {soc or 'this SoC'}")
    for r in records:
        _p, g = _gpio_of(r)
        if not g:
            continue
        s = selected(r.fields)
        if s and s["mode"] in enabled and g not in exc and g not in seen:
            if s["kind"] == "qup" and (g in table or not table):
                wrong.append(f"gpio{g} ({_signal(r.fields)}) is selected as {s['inst'].upper()} in the workbook but has no pin state")
            elif s["kind"] == "qup":
                ambiguous.append(f"gpio{g} ({_signal(r.fields)}) is selected as {s['inst'].upper()} but was not generated (function name unknown)")
        if g in exc and g in seen:
            wrong.append(f"gpio{g} was excluded but is in the output")
    refs = []
    for b in ctl_blocks:
        m0 = re.match(r"&(\w+)", b["header"])
        body = b["text"]
        for m in re.finditer(r"pinctrl-0\s*=\s*<([^>]*)>", body):
            rr = re.findall(r"&([A-Za-z_]\w*)", m.group(1))
            refs += rr
            insts = {label_inst.get(x) for x in rr if label_inst.get(x)}
            if len(insts) > 1:
                wrong.append(f"&{m0.group(1) if m0 else '?'} uses pins of several I2C engines ({', '.join(sorted(insts))})")
            elif insts and m0:
                num = re.search(r"qup(\d+)", next(iter(insts)))
                cnum = re.search(r"i2c(\d+)", m0.group(1))
                if num and cnum and num.group(1) != cnum.group(1) and _n and prof.get("controller"):
                    wrong.append(f"&{m0.group(1)} uses pins of {next(iter(insts)).upper()} - the controller number does not match")
    labels = {n["label"] for n in nodes}
    for x in refs:
        if x not in labels:
            wrong.append(f"pinctrl-0 refers to &{x}, which is not defined")
    for x in sorted(labels - set(refs)):
        ambiguous.append(f"{x} is defined but no pinctrl-0 uses it")
    return {"wrong": wrong, "ambiguous": ambiguous, "unverifiable": unverifiable, "ok": ok}


def check_prompt() -> str:
    from app import usb_ai_assist as u
    p = u.CHECK_PROMPT.replace("USB-related GPIO pin", "I2C-related GPIO pin")
    p = p.replace("live USB signal", "live I2C signal")
    p = p.replace("the signal name says over-current / fault / detect (an input) but Direction says OUT, or an enable / reset / power signal is IN.",
                  "never report this for I2C: SDA and SCL are bidirectional open-drain lines and IN, OUT or IO on them is normal.")
    p = p.replace("the signal name belongs to one USB controller (USB20_0) and the interface group or net names another (USB20_1).",
                  "the signal name belongs to one I2C bus and the interface group or net names a DIFFERENT I2C bus number.")
    p += ("\n\nI2C-specific rules: (1) a pin that lists several nets (for example a sub-board and a tuner) is a SHARED BUS with several devices - that is NORMAL, never report it. "
          "(2) the Signal names the bus while the Interface group may name the device on it; different words there are normal. Report a bus mismatch only when two cells name DIFFERENT I2C controllers or numbers. "
          "(3) a pull cell of NO_PULL on SDA / SCL is normal when the board has an external pull-up; do not report it.")
    return p
