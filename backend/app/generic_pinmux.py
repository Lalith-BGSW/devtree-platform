"""
Generic (non-Qualcomm) pin-state PREVIEW - opt-in, never used by the Qualcomm engines.

For a workbook whose pins are not TLMM pins (e.g. Telechips TCC8050: GPIO_A06, GPSB_06, UART_20, I2C_03) the Qualcomm engines
correctly produce nothing.  This module writes a REVIEW-ONLY preview in Qualcomm-like syntax from the workbook alone:

  * pins and controllers are taken from SELECTED INTERFACE (`GPSB_06`, `UART_20`, `I2C_03`) - same rule as the other engines
  * pin name = the workbook's ball name (`GPIO_A06`), function = the selected interface text, mux = the workbook's mux number
  * bias from the pull columns; drive strength is only a comment (the workbook's "x4" is not milliamps)
  * every block says UNVERIFIED: the real pinctrl binding of the SoC is not known to this tool

check() re-reads the produced text and compares it with the workbook, so a mismatch with the workbook is caught; it cannot
say whether the SoC's kernel accepts this syntax.
"""
from __future__ import annotations

import json
import re

FAMILIES = {"spi": ("GPSB",), "uart": ("UART",), "i2c": ("I2C",)}
ORDER = ["spi", "uart", "i2c"]
BANNER = ("UNVERIFIED PREVIEW - the pin names, function strings and property names below come from the workbook, written in "
          "Qualcomm-style syntax. The real pinctrl binding of this SoC is not known to the tool: replace before use.")


def _one(s) -> str:
    return re.sub(r"\s+", " ", str(s if s is not None else "")).strip().replace("*/", "* /")


def _num(s):
    m = re.match(r"\s*(\d+)\s*:", str(s or ""))
    return int(m.group(1)) if m else None


def _word(s) -> str:
    return _one(re.sub(r"^\s*\d+\s*:", "", str(s or "")))


def _family(inst: str, prefixes=None):
    m = re.fullmatch(r"([A-Za-z0-9]+?)_?(\d+)", inst or "")
    if not m:
        return None, None
    pre = m.group(1).upper()
    for fam, pres in (prefixes or FAMILIES).items():
        if pre in pres:
            return fam, int(m.group(2))
    return None, None


def _bias(f):
    en = _word(f.get("pull_enable")).lower()
    if not en:
        return None, "pull columns empty"
    if en.startswith("dis"):
        return "bias-disable", ""
    sel = _word(f.get("pull_select")).lower()
    if "up" in sel:
        return "bias-pull-up", ""
    if "down" in sel:
        return "bias-pull-down", ""
    return None, f"pull enabled but pull select '{sel}' not understood"


def _role(name: str):
    n = (name or "").upper()
    for key, role in (("TXD", "tx"), ("RXD", "rx"), ("RTS", "rts"), ("CTS", "cts"), ("SCL", "scl"), ("SDA", "sda"),
                      ("SCK", "clk"), ("SDO", "mosi"), ("SDI", "miso"), ("CMD", "cs")):
        if key in n:
            return role
    return None


def _gen5_role(sig: str):
    tail = (sig or "").split(":")[-1].upper()
    for key, role in (("TXD", "tx"), ("RXD", "rx"), ("RTS", "rts"), ("CTS", "cts"), ("SCL", "scl"), ("SDA", "sda"),
                      ("CLK", "clk"), ("MOSI", "mosi"), ("MISO", "miso"), ("CS", "cs")):
        if key in tail:
            return role
    return None


_OPP = {"tx": "rx", "rx": "tx", "rts": "cts", "cts": "rts"}


def generate(records, project, families=None, prefixes=None):
    fams = [f for f in (families or ORDER) if f in ORDER]
    groups, items, left_out = {}, [], []
    for r in records:
        f = r.fields
        inst = _one(f.get("selected_interface"))
        fam, n = _family(inst, prefixes)
        ball = _one(f.get("ball_name") or f.get("port0"))
        if fam is None or fam not in fams or not ball:
            continue
        groups.setdefault((fam, inst), []).append(r)
    pins_out, blocks, ctrl = [], [], []
    for (fam, inst) in sorted(groups, key=lambda k: (ORDER.index(k[0]), k[1])):
        rs = sorted(groups[(fam, inst)], key=lambda r: str(r.fields.get("ball_name")))
        label = re.sub(r"[^a-z0-9]+", "_", inst.lower()).strip("_")
        state = f"{label}_default"
        lines = [f"\t{state}: {label}-default {{", "\t\t/* UNVERIFIED PREVIEW - see the note at the top of the file */"]
        names = []
        for r in rs:
            f = r.fields
            ball, mux, sig = _one(f.get("ball_name")), f.get("pinmux_setting"), _one(f.get("gen5_signal_name"))
            bias, bnote = _bias(f)
            drive = _word(f.get("drive_strength"))
            names.append(ball)
            notes = []
            if bnote:
                notes.append(f"NEEDS REVIEW: {bnote}")
            wr, gr = _role(f.get("pinmuxed_name")), _gen5_role(sig)
            if wr and gr and wr != gr:
                notes.append(f"NEEDS REVIEW: the pin is {_one(f.get('pinmuxed_name'))} but the Gen5 signal says {sig.split(':')[-1]}"
                             + (" - looks named from the other end of the link; confirm" if _OPP.get(wr) == gr else ""))
            pins_out.append({"pin": ball, "ball_id": _one(f.get("ball_id")), "signal": sig, "function": inst, "mux": mux, "role": wr,
                             "family": fam, "instance": inst, "label": state, "bias": bias, "drive": drive or None, "notes": notes,
                             "pinmuxed_name": _one(f.get("pinmuxed_name"))})
        lines.append("\t\tmux {")
        lines.append("\t\t\tpins = " + ", ".join(f'"{n}"' for n in names) + ";")
        lines.append(f'\t\t\tfunction = "{inst}";')
        mx = sorted({str(p["mux"]) for p in pins_out if p["instance"] == inst})
        lines.append(f"\t\t\t/* workbook mux value: {', '.join(mx)} */")
        lines.append("\t\t};")
        for p in [p for p in pins_out if p["instance"] == inst]:
            cfg = [f"\t\tconfig_{re.sub(r'[^a-z0-9]+', '_', p['pin'].lower())} {{", f'\t\t\tpins = "{p["pin"]}";']
            cfg.append(f"\t\t\t{p['bias']};" if p["bias"] else "\t\t\t/* NEEDS REVIEW: bias not set */")
            if p["drive"]:
                cfg.append(f"\t\t\t/* workbook drive strength '{p['drive']}' - units unconfirmed, not converted */")
            for n_ in p["notes"]:
                cfg.append(f"\t\t\t/* {n_} */")
            cfg.append(f"\t\t\t/* {p['signal']} */")
            cfg.append("\t\t};")
            lines += cfg
        lines.append("\t};")
        blocks.append("\n".join(lines))
        ctrl.append(f"/* UNVERIFIED PREVIEW */\n&{label} {{\n\tstatus = \"okay\";\n\tpinctrl-names = \"default\";\n\tpinctrl-0 = <&{state}>;\n"
                    f"\t/* NEEDS REVIEW: controller node name and any other properties are not known to the tool */\n}};")
    text = ""
    if blocks:
        text = (f"/* Generic pin-state preview - {project}\n * {BANNER}\n */\n\n&pinctrl {{\n\t/* NEEDS REVIEW: the pin controller node label is assumed */\n"
                + "\n\n".join(blocks) + "\n};\n\n" + "\n\n".join(ctrl) + "\n")
    counts = {fam: sum(1 for p in pins_out if p["family"] == fam) for fam in ORDER}
    if not pins_out:
        items.append({"kind": "info", "text": "No pin in this project has SPI (GPSB_n), UART (UART_n) or I2C (I2C_n) selected."})
    return {"text": text, "pins": pins_out, "counts": counts, "items": items, "left_out": left_out,
            "controllers": len(groups), "families": fams}


def check(text, records, prefixes=None):
    """Independent re-read: every selected GPSB/UART/I2C pin must appear once, in the block of its own controller, with the right bias."""
    want = {}
    for r in records:
        f = r.fields
        inst, ball = _one(f.get("selected_interface")), _one(f.get("ball_name") or f.get("port0"))
        fam, _ = _family(inst, prefixes)
        if fam and ball:
            want[ball] = (inst, _bias(f)[0])
    wrong, ok, seen = [], 0, {}
    for m in re.finditer(r"\n\t(\w+): [\w-]+ \{(.*?)\n\t\};", text, re.S):
        body = m.group(2)
        fm = re.search(r'function = "([^"]+)"', body)
        if not fm:
            continue
        for pm in re.finditer(r"\n\t\tmux \{.*?pins = ([^;]+);", body, re.S):
            for p in re.findall(r'"([^"]+)"', pm.group(1)):
                seen.setdefault(p, []).append(fm.group(1))
        for cm in re.finditer(r'config_\w+ \{\s*pins = "([^"]+)";\s*(bias-[a-z-]+);', body):
            exp = want.get(cm.group(1))
            if exp and exp[1] and exp[1] != cm.group(2):
                wrong.append(f"{cm.group(1)}: bias {cm.group(2)} but the workbook says {exp[1]}")
    for ball, (inst, _b) in want.items():
        got = seen.get(ball, [])
        if got.count(inst) != 1:
            if ball in seen or inst:
                wrong.append(f"{ball}: expected once in {inst}, found in {got or 'nothing'}")
        else:
            ok += 1
    for ball, fns in seen.items():
        if ball not in want:
            wrong.append(f"{ball}: in the file but not selected as SPI/UART/I2C in the workbook")
    return {"wrong": wrong, "ok": ok, "checked": len(want)}


# ---------------------------------------------------------------- AI draft (fallback, never replaces Python output) ----------
FEATURE = "generic_draft"
PROMPT = (
    "You are drafting Linux device-tree pinctrl states for an SoC whose pinctrl binding is NOT known to the tool.  "
    "You get a JSON list of pins (ball name, selected interface, workbook mux number, bias, drive, signal).  Write ONE .dtsi fragment.  "
    "Rules: use only the pins and values given; do not invent pins, numbers or controllers; if you do not know a property name of "
    "this SoC's binding, use the generic pinctrl properties (pins, function, bias-*, drive-strength) and say so in a comment; put "
    "'/* UNVERIFIED AI DRAFT */' at the top and '/* NEEDS REVIEW: ... */' on every line you are unsure about.  Output only DTS text.")


def ai_draft(pins, project, soc=None):
    """-> {status, text, note, calls}.  Needs 'generic_draft' in LLM_FEATURES; the text is stamped as an unverified AI draft."""
    from app import llm_client
    client = llm_client.get_client(FEATURE)
    if client is None:
        return {"status": "unavailable", "text": "", "calls": 0,
                "note": "AI is not configured, or 'generic_draft' is not in LLM_FEATURES (add it to .env to allow this draft)"}
    payload = [{"ball": p["pin"], "interface": p["instance"], "mux": p["mux"], "bias": p["bias"], "drive": p["drive"], "signal": p["signal"]}
               for p in pins[:120]]
    try:
        resp = client.chat.completions.create(
            messages=[{"role": "system", "content": PROMPT},
                      {"role": "user", "content": f"PROJECT: {project}\nSOC: {soc or 'unknown'}\nPINS:\n" + json.dumps(payload, indent=1)}],
            temperature=0.0, max_tokens=3000)
    except llm_client.LLMError as e:
        return {"status": "error", "text": "", "calls": 1, "note": str(e)}
    out = (resp.choices[0].message.content or "").strip()
    out = re.sub(r"^```[a-z]*\n|\n```$", "", out)
    if not out:
        return {"status": "error", "text": "", "calls": 1, "note": "the AI returned nothing"}
    if "UNVERIFIED" not in out.upper():
        out = "/* UNVERIFIED AI DRAFT - not checked against any pin table or binding */\n" + out
    given = {p["pin"] for p in pins}
    extra = sorted({b for b in re.findall(r'"(GPIO_[A-Z]+\d+)"', out)} - given)
    note = "AI-written, unchecked against any binding."
    if extra:
        note += " It mentions pins that are not in the workbook selection: " + ", ".join(extra[:8]) + " - do not use those."
    return {"status": "ok", "text": out + "\n", "note": note, "calls": 1}
