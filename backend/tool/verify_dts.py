#!/usr/bin/env python3
"""
verify_dts.py - check any DTS text (written by an AI, a colleague, or a tool)
against the Excel workbook. It CHECKS; it never rewrites.

    python tool/verify_dts.py --excel <workbook> --project <NAME> --dts <file.dts> [--report r.md]

Three kinds of finding, kept strictly apart because they mean different things:

  WRONG        the workbook has an answer and the DTS disagrees (drive-strength,
               bias, function, GPIO number, polarity, pin not in the workbook,
               PHY pad placed in &tlmm, pin used in the wrong role).
  AMBIGUOUS    the workbook itself shows the pin has more than one runtime role
               (a shared pin) - the DTS silently picked one or both.
  UNVERIFIABLE the DTS asserts something NO data source can confirm (chip
               compatible strings, register addresses, property names, PHY
               labels, max-speed). These are NOT 'correct' just because they
               were not flagged WRONG - they simply cannot be checked here.

Exit status: 0 = no WRONG/AMBIGUOUS findings, 2 = at least one.
"""
from __future__ import annotations
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import usb_dts_tool as T
from app.md_exporter import resolve_field_slugs
from app.peripheral_extractor import resolve_gen5_signal_slug, _resolve_field_slugs_for_summary
from app.generators.dts_generator import derive_pin_config
from app.usb_consumer_generator import role_matches
from app.usb_consumer_generator import (
    pin_is_tlmm_controlled, _signal_segments, _is_boot_strap_segment, detect_usb_instance_token,
)
from app.parsers.dts_parser import parse_dts_nodes

GPIO_REF = re.compile(r"([A-Za-z][\w,\-]*)\s*=\s*<&tlmm\s+(\d+)\s+(GPIO_[A-Z_]+|[01])\s*>")
NUMERIC_FLAG = {"0": "GPIO_ACTIVE_HIGH", "1": "GPIO_ACTIVE_LOW"}
KNOWN_ROLE_PROPS = {"oc-gpio": "oc", "vbus-out-gpio": "vbus"}       # property -> role; the words for each role: app/usb_role_keywords.json


def line_of(text, pos):
    return text.count("\n", 0, pos) + 1


def config_by_pin(text):
    """pin -> merged properties, from every innermost { } block containing `pins =`
    (works for both mux{}/config{} nodes and Gemini-style sub-nodes)."""
    out = {}
    for m in re.finditer(r"\{[^{}]*\}", text):
        blk = m.group(0)
        pm = re.search(r'pins\s*=\s*([^;]+);', blk)
        if not pm:
            continue
        pins = re.findall(r'"(gpio\d+|[A-Za-z0-9_]+)"', pm.group(1))
        fn = re.search(r'function\s*=\s*"([^"]+)"', blk)
        ds = re.search(r'drive-strength\s*=\s*<\s*(\d+)\s*>', blk)
        bias = re.search(r'\b(bias-[a-z\-]+)\s*;', blk)
        for p in pins:
            d = out.setdefault(p, {"line": line_of(text, m.start())})
            if fn: d["function"] = fn.group(1)
            if ds: d["drive"] = ds.group(1)
            if bias: d["bias"] = bias.group(1)
    return out


def build_argparser():
    ap = argparse.ArgumentParser()
    ap.add_argument("--excel", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--dts", required=True)
    ap.add_argument("--report", default="verify_report.md")
    ap.add_argument("--existing", action="append", default=[],
                    help="an existing project DTS/DTSI (repeatable) - used to detect pins/labels this file would "
                         "define a SECOND time")
    ap.add_argument("--no-cache", action="store_true", help="re-read the workbook instead of using the cache")
    ap.add_argument("--all-pins", action="store_true",
                    help="check every gpio pin in the file (default: only USB-related pins)")
    return ap


class VerifyResult:
    def __init__(self):
        self.wrong = self.ambiguous = self.unverifiable = self.ok = []
        self.report_text = ""
        self.exit_code = 0


def run(args):
    """Same pipeline as the CLI, callable directly. Returns a VerifyResult; never calls sys.exit."""
    T.USE_CACHE = not args.no_cache

    parsed, records, soc = T.load_records(args.excel, args.project)
    gen5_slug = resolve_gen5_signal_slug(args.project, soc, parsed.columns)
    project_slugs = {c.slug for c in parsed.columns if c.slug and
                     (c.entity == args.project or c.entity == soc or c.category == "pinmux_register")}
    slug_to_base = {c.slug: c.base_field for c in parsed.columns if c.slug and c.base_field}
    pms = resolve_field_slugs(project_slugs, slug_to_base, soc).get("pinmux_setting")
    by_num = {}
    for r in records:
        n = T.gpio_num(r)
        if n:
            by_num[n] = r
    ig_slug = _resolve_field_slugs_for_summary(args.project, soc, parsed.columns).get("interface_group")
    usb_nums = {T.gpio_num(r) for r in T.match_usb(records, gen5_slug, ig_slug, "usb", "both")} - {None}

    text, _eol = T.read_dts(args.dts)
    wrong, ambiguous, unverifiable, ok = [], [], [], []

    # ---- 1. pin electrical config vs Excel
    for pin, props in sorted(config_by_pin(text).items(), key=lambda kv: kv[1]["line"]):
        m = re.fullmatch(r"gpio(\d+)", pin)
        if not m:
            unverifiable.append((props["line"], f'pin "{pin}"', "not a gpioN pin - not checked here"))
            continue
        rec = by_num.get(m.group(1))
        if rec is None:
            wrong.append((props["line"], pin, "pin not found in the workbook for this project"))
            continue
        if not args.all_pins and m.group(1) not in usb_nums:
            continue          # not a USB pin - out of scope for this check
        if pms and pin_is_tlmm_controlled(rec, pms, parsed.columns, project_slugs) is False:
            wrong.append((props["line"], pin, "workbook shows NO TLMM RegSetting - a PHY pad, must not be in &tlmm"))
            continue
        d = derive_pin_config(rec)
        if d is None:
            unverifiable.append((props["line"], pin, "no Excel config derivable"))
            continue
        bad = []
        if "drive" in props and d.drive_strength is not None and props["drive"] != str(d.drive_strength):
            bad.append(f"drive-strength {props['drive']} (DTS) vs {d.drive_strength} (Excel)")
        if "bias" in props and d.bias_directive and props["bias"] != d.bias_directive:
            bad.append(f"{props['bias']} (DTS) vs {d.bias_directive} (Excel)")
        # A pin whose Excel "selected interface" is BOOT carries a boot-strap role: it is sampled once at reset, and at runtime
        # the pin is a plain GPIO. The accepted change muxes such a pin (GPIO_55, the usb1 VBUS enable) as "gpio" - flagging
        # that as wrong would call the accepted change itself wrong.
        boot_strap_then_gpio = props.get("function") == "gpio" and d.function == "boot"
        if "function" in props and d.function and props["function"] != d.function and not boot_strap_then_gpio:
            bad.append(f'function "{props["function"]}" (DTS) vs "{d.function}" (Excel)')
        if bad:
            wrong.append((props["line"], pin, "; ".join(bad)))
        else:
            ok.append((props["line"], pin, "electrical config matches Excel"))

    # ---- 2. GPIO references <&tlmm N FLAG>
    uses = {}
    # roles the engineer set (--role SIGNAL=oc|vbus) or confirmed from an AI suggestion count as known, like a name in the vocabulary
    decided_role_tails: dict = {}
    for _r in (getattr(args, "decided_roles", None) or []):
        if "=" in str(_r):
            _t, _role = str(_r).split("=", 1)
            decided_role_tails.setdefault(_role.strip().lower(), set()).add(_t.strip().split(":")[-1].upper())
    # regulator-fixed nodes: the line of each `gpio = <&tlmm N FLAG>` -> does that regulator carry enable-active-high?
    _p = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.DOTALL)
    _p = re.sub(r"//[^\n]*", "", _p)
    reg_refs = {}
    for bm in re.finditer(r"\{[^{}]*\}", _p):
        blk = bm.group(0)
        if re.search(r'compatible\s*=\s*"regulator-fixed"', blk):
            eah = bool(re.search(r"\benable-active-high\s*;", blk))
            for g in GPIO_REF.finditer(blk):
                if g.group(1) == "gpio":
                    reg_refs[line_of(_p, bm.start() + g.start())] = eah
    for m in GPIO_REF.finditer(text):
        prop, n, flag = m.group(1), m.group(2), m.group(3)
        flag = NUMERIC_FLAG.get(flag, flag)
        uses.setdefault(n, []).append((prop, flag, line_of(text, m.start())))
    for n, refs in sorted(uses.items(), key=lambda kv: kv[1][0][2]):
        rec = by_num.get(n)
        if rec is None:
            for prop, flag, ln in refs:
                wrong.append((ln, f"{prop} -> gpio{n}", "GPIO not found in the workbook for this project"))
            continue
        raw = str(rec.fields.get(gen5_slug) or "")
        segs = [s for s in _signal_segments(raw) if not _is_boot_strap_segment(s)]
        decided = {re.sub(r"\D", "", str(d)) for d in (getattr(args, "decided", None) or [])}
        if len(segs) > 1 and n in decided:
            # The engineer explicitly decided this shared pin (--vbus-pin). The workbook still lists both roles, so the
            # decision is recorded as OK-with-a-note rather than reported as an unresolved ambiguity.
            ok.append((refs[0][2], f"gpio{n}", "shared pin - used as you decided (--vbus-pin); the other role(s) are not wired here"))
        elif len(segs) > 1:
            where = ", ".join(f"{p}@L{ln}" for p, _, ln in refs)
            ambiguous.append((refs[0][2], f"gpio{n}",
                              f"workbook lists {len(segs)} runtime roles ({'; '.join(s.split(':')[-1] for s in segs)}); "
                              f"DTS uses it as: {where} - a shared pin, needs a human decision"))
        for prop, flag, ln in refs:
            tail = (segs[0].split(":")[-1].upper() if len(segs) == 1 else "")
            if prop in KNOWN_ROLE_PROPS:
                if len(segs) == 1:
                    role_ = KNOWN_ROLE_PROPS[prop]
                    if not (role_matches(tail, role_) or tail in decided_role_tails.get(role_, set())):
                        wrong.append((ln, f"{prop} -> gpio{n}",
                                      f"workbook signal for gpio{n} is '{tail}', not a {prop.split('-')[0]} signal"))
                    else:
                        want = "GPIO_ACTIVE_LOW" if re.search(r"(^|_)(B|N)$", tail) else "GPIO_ACTIVE_HIGH"
                        (ok if flag == want else wrong).append(
                            (ln, f"{prop} -> gpio{n}",
                             "polarity matches" if flag == want else f"{flag} (DTS) vs {want} (from signal '{tail}')"))
            elif prop == "gpio" and ln in reg_refs and len(segs) == 1:
                # A regulator-fixed VBUS enable. The accepted change confirms the convention: the pin is a VBUS-enable signal, the
                # polarity follows the signal's _B/_N suffix, and enable-active-high is present exactly when it is ACTIVE_HIGH.
                if not (role_matches(tail, "vbus") or tail in decided_role_tails.get("vbus", set())):
                    wrong.append((ln, f"regulator gpio -> gpio{n}", f"workbook signal for gpio{n} is '{tail}', not a VBUS enable"))
                else:
                    want = "GPIO_ACTIVE_LOW" if re.search(r"(^|_)(B|N)$", tail) else "GPIO_ACTIVE_HIGH"
                    (ok if flag == want else wrong).append(
                        (ln, f"regulator gpio -> gpio{n}",
                         "polarity matches" if flag == want else f"{flag} (DTS) vs {want} (from signal '{tail}')"))
                    if reg_refs[ln] != (flag == "GPIO_ACTIVE_HIGH"):
                        wrong.append((ln, f"regulator gpio -> gpio{n}",
                                      "enable-active-high must be present exactly when the GPIO is GPIO_ACTIVE_HIGH (as in the accepted change)"))
            else:
                unverifiable.append((ln, f"{prop} -> gpio{n}",
                                     f"property name not confirmed anywhere; signal is '{raw.strip()[:60]}'; "
                                     f"polarity {flag} not validated (only oc-gpio/vbus-out-gpio rules are confirmed)"))

    # ---- 3. claims no data source can confirm
    for m in re.finditer(r'compatible\s*=\s*"([^"]+)"', text):
        if m.group(1) == "regulator-fixed":
            # not a chip: the generic Linux binding for a fixed regulator, exactly what the accepted change uses for VBUS
            ok.append((line_of(text, m.start()), 'compatible = "regulator-fixed"', "generic Linux binding, as in the accepted change"))
            continue
        unverifiable.append((line_of(text, m.start()), f'compatible = "{m.group(1)}"',
                             "chip/IP identity - not in the workbook; needs a datasheet or existing node"))
    for m in re.finditer(r'\breg\s*=\s*<([^>]+)>', text):
        unverifiable.append((line_of(text, m.start()), f"reg = <{m.group(1)}>", "address - not in the workbook"))
    for m in re.finditer(r'^\s*[\w-]+:\s*[\w-]+@([0-9a-fA-F]+)\s*\{', text, re.MULTILINE):
        unverifiable.append((line_of(text, m.start()), f"unit address @{m.group(1)}", "not in the workbook"))
    for prop in ("usb-phy", "maximum-speed"):
        for m in re.finditer(rf'\b{prop}\s*=\s*([^;]+);', text):
            unverifiable.append((line_of(text, m.start()), f"{prop} = {m.group(1).strip()}",
                                 "board/PHY wiring fact - not in the workbook; check against a real reference"))

    # ---- 4. structural: node shapes that conflict with an override-style project
    for m in re.finditer(r'^\s*(usb\w*):\s*usb@([0-9a-fA-F]+)\s*\{', text, re.MULTILINE):
        wrong.append((line_of(text, m.start()),
                      f"{m.group(1)}: usb@{m.group(2)} {{ ... }}",
                      "declares a NEW top-level controller node with a register address; projects that use "
                      "&usbN overrides would end up with two competing definitions (check your base DTS)"))

    # ---- 5. pins already configured elsewhere in the project (a second node for one pin conflicts)
    existing_pin_owner = {}
    for path in args.existing:
        etext, _ = T.read_dts(path)
        for n in parse_dts_nodes(etext):
            if T._is_sleep(n.label):
                continue
            for pin in dict.fromkeys(n.mux_pins + n.config_pins):
                existing_pin_owner.setdefault(pin, (n.label, os.path.basename(path)))
    this_nodes = parse_dts_nodes(text)
    for n in this_nodes:
        if T._is_sleep(n.label):
            continue
        for pin in dict.fromkeys(n.mux_pins + n.config_pins):
            owner = existing_pin_owner.get(pin)
            if owner and owner[0] != n.label:
                wrong.append((line_of(text, text.find(n.raw_text)) if n.raw_text in text else 0, f"{n.label} ({pin})",
                              f"{pin} is ALREADY configured by '{owner[0]}' in {owner[1]} - a second node for the same "
                              f"pin conflicts; update the existing node instead"))

    # ---- 6. state labels that contradict what the workbook says the pin is
    for n in this_nodes:
        lm = re.fullmatch(r"usbv(\d)(?:_(\d+))?_(power_enable|fault_detect)", n.label)
        if not lm:
            continue
        kind = "vbus_gpio" if lm.group(3) == "power_enable" else "oc_gpio"
        for pin in dict.fromkeys(n.mux_pins + n.config_pins):
            pm = re.fullmatch(r"gpio(\d+)", pin)
            rec = by_num.get(pm.group(1)) if pm else None
            if rec is None:
                continue
            segs = [x for x in _signal_segments(str(rec.fields.get(gen5_slug) or "")) if not _is_boot_strap_segment(x)]
            if len(segs) != 1:
                continue                       # shared pin - already reported as AMBIGUOUS where it is used
            tok = detect_usb_instance_token(segs[0])
            expected = T.state_name(tok, kind, None)
            if expected and expected != n.label:
                wrong.append((line_of(text, text.find(n.raw_text)) if n.raw_text in text else 0, f"{n.label} ({pin})",
                              f"the name says {n.label.split('_')[0]}, but the workbook says {pin} belongs to "
                              f"{tok} ('{segs[0].split(':')[-1]}') - this project's naming would be '{expected}'"))

    # ---- 7. structural DTS problems
    plain = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.DOTALL)
    plain = re.sub(r"//[^\n]*", "", plain)
    depth = 0
    for i, ln in enumerate(plain.split("\n"), 1):
        if depth == 0 and re.match(r"^\s*(?:[A-Za-z_][\w,\-]*\s*:\s*)?[A-Za-z_][\w,@\-]*\s*\{", ln) \
                and not ln.lstrip().startswith(("&", "/")):
            wrong.append((i, ln.strip().rstrip("{").strip(),
                          "node defined at TOP LEVEL, outside any parent node - dtc rejects this; it must sit inside "
                          "a parent (e.g. the board's root/'&soc' node) or use a &label override"))
        depth += ln.count("{") - ln.count("}")
    for m in re.finditer(r"\{[^{}]*\}", plain):
        blk = m.group(0)
        if re.search(r'compatible\s*=\s*"regulator-fixed"', blk) and not re.search(r"^\s*gpios?\s*=", blk, re.MULTILINE):
            nm = re.search(r'regulator-name\s*=\s*"([^"]+)"', blk)
            wrong.append((line_of(plain, m.start()), f"regulator-fixed {nm.group(1) if nm else ''}".strip(),
                          "no `gpio` property - this regulator cannot switch anything, so the VBUS pin it is meant "
                          "to control is never driven"))

    # ---- report
    def table(rows, heads):
        if not rows:
            return "None.\n"
        s = "| " + " | ".join(heads) + " |\n|" + "---|" * len(heads) + "\n"
        for ln, what, why in sorted(rows, key=lambda r: r[0]):
            s += f"| L{ln} | {what} | {why} |\n"
        return s

    R = [f"# DTS verification - {os.path.basename(args.dts)} vs {os.path.basename(args.excel)} / {args.project}\n",
         f"**WRONG: {len(wrong)}  |  AMBIGUOUS: {len(ambiguous)}  |  UNVERIFIABLE: {len(unverifiable)}  |  "
         f"confirmed OK: {len(ok)}**\n",
         "## WRONG - the workbook has an answer and the DTS disagrees\n", table(wrong, ["Line", "Item", "Problem"]),
         "\n## AMBIGUOUS - shared pin, silently resolved\n", table(ambiguous, ["Line", "Pin", "Problem"]),
         "\n## UNVERIFIABLE - asserted, but no data source can confirm it (NOT the same as correct)\n",
         table(unverifiable, ["Line", "Item", "Why it can't be checked"]),
         "\n## Confirmed OK\n", table(ok, ["Line", "Item", "Result"])]
    report_text = "\n".join(R)
    os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
    open(args.report, "w", encoding="utf-8").write(report_text)
    print(f"WRONG {len(wrong)} | AMBIGUOUS {len(ambiguous)} | UNVERIFIABLE {len(unverifiable)} | OK {len(ok)}  -> {args.report}")
    result = VerifyResult()
    result.wrong, result.ambiguous, result.unverifiable, result.ok = wrong, ambiguous, unverifiable, ok
    result.report_text = report_text
    result.exit_code = 2 if (wrong or ambiguous) else 0
    return result


def main():
    res = run(build_argparser().parse_args())
    sys.exit(res.exit_code)


if __name__ == "__main__":
    main()
