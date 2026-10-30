#!/usr/bin/env python3
"""
Combined DTS tool - runs the USB, Ethernet, SPI, UART and I2C engines for one project and merges their output into ONE pin-state file and ONE consumer file.

  python combined_dts_tool.py --excel board.xlsx --project CCS20 --kernel 6.6 --out-dir out/
  python combined_dts_tool.py --excel board.xlsx --project CCS20 --peripheral usb --peripheral spi --usb-pattern regulator

Each engine keeps its own rules, filters and independent verifier; this tool only merges and then checks the cross-peripheral things (see app/combined_dts.py).
Options shared by all engines: --kernel, --kernel-base, --template. USB options: --usb-pattern, --usb-vbus-pin. Per-engine pin choices are made in the single-peripheral tools.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import usb_dts_tool as _usb
import eth_dts_tool as _eth
import spi_dts_tool as _spi
import uart_dts_tool as _uart
import i2c_dts_tool as _i2c
from app import combined_dts, dts_style

PERIPHERALS = ("usb", "ethernet", "spi", "uart", "i2c")


class CombinedResult:
    def __init__(self):
        self.files, self.sections, self.conflicts, self.notes = [], [], [], []
        self.peripherals = []          # [{peripheral, status, headline, pin_states, files, notes, decisions, left_out}]
        self.decisions, self.left_out = [], []     # across all peripherals: [{peripheral, text}] / [{peripheral, pin, signal, reason}]
        self.report_text = ""
        self.exit_code = 0
        self.overview = {}


def _ns(parser, argv):
    return parser.parse_args(argv)


def _collect(entry, r):
    """Everything an engine did NOT generate or could not decide, kept per peripheral: its open decisions and its left-out pins."""
    entry["decisions"] += [i["text"] for i in (r.items or []) if i.get("kind") == "decision"]
    entry["notes"] += [i["text"] for i in (r.items or []) if i.get("kind") == "info"]
    entry["left_out"] += [{"pin": x.get("pin", ""), "signal": x.get("signal", ""), "reason": x.get("reason", ""), "tier": x.get("tier")} for x in (r.left_out or [])]


def _footer(res) -> str:
    """A comment block for the end of the pin-state file: what was left out, so a reader of the file alone sees it (details are in the report)."""
    rows = []
    for p in res.peripherals:
        strong = [x for x in p["left_out"] if x.get("tier") != "possible"]     # a stated reason or a likely candidate; wording-only 'possible' ones stay in the report
        weak = len(p["left_out"]) - len(strong)
        for x in strong[:12]:
            line = f"{p['peripheral'].upper()}: {x['pin']} {x['signal']} - {x['reason']}".replace("*/", "* /")
            rows.append(" *   " + (line if len(line) <= 170 else line[:167] + "..."))
        if len(strong) > 12:
            rows.append(f" *   {p['peripheral'].upper()}: ... and {len(strong) - 12} more (see the combined report)")
        if weak:
            rows.append(f" *   {p['peripheral'].upper()}: {weak} weak candidate(s) (matching wording only) are listed in the combined report")
    if not rows:
        return ""
    return "\n/*\n * Left out of this file (not generated; details in the combined report):\n" + "\n".join(rows) + "\n */\n"


def _gpio_no(pin) -> str:
    return re.sub(r"\D", "", str(pin or ""))


def _drop_claimed_candidates(peripherals, parts):
    """A wording-only 'possible' candidate of one engine ("I2C wording in ..., not selected as I2C, add it if it belongs") is wrong in the COMBINED view when
    another engine already wrote a pin state for that GPIO, or already left it out for a stated reason (CCI ...): it would suggest adding a pin that is done.
    Only 'possible'-tier rows are dropped, and only for a GPIO claimed by ANOTHER peripheral; stated reasons are never dropped."""
    made = {}
    for part in parts:
        for f in part["files"]:
            if f.get("role") == "pinmux":
                for g in re.findall(r'pins\s*=\s*"gpio(\d+)"', f["text"]):
                    made.setdefault(g, set()).add(part["peripheral"])
    stated = {}
    for p in peripherals:
        for x in p["left_out"]:
            if x.get("tier") != "possible":
                stated.setdefault(_gpio_no(x.get("pin")), set()).add(p["peripheral"])
    for p in peripherals:
        keep = []
        for x in p["left_out"]:
            g = _gpio_no(x.get("pin"))
            others = (made.get(g, set()) | stated.get(g, set())) - {p["peripheral"]}
            if x.get("tier") == "possible" and g and others:
                continue
            keep.append(x)
        p["left_out"] = keep


def run(args) -> CombinedResult:
    res = CombinedResult()
    want = [p.lower() for p in (getattr(args, "peripheral", None) or [])] or list(PERIPHERALS)
    bad = [p for p in want if p not in PERIPHERALS]
    if bad:
        raise SystemExit(f"unknown peripheral {bad}; choose from {list(PERIPHERALS)}")
    common = []
    if getattr(args, "kernel", None):
        common += ["--kernel", args.kernel]
    if getattr(args, "kernel_base", None):
        common += ["--kernel-base", args.kernel_base]
    for t in (getattr(args, "template", None) or []):
        common += ["--template", t]
    tmp = tempfile.mkdtemp(prefix="combined_")
    parts = []
    for per in want:
        d = os.path.join(tmp, per)
        os.makedirs(d, exist_ok=True)
        base = ["--excel", args.excel, "--project", args.project, "--out-dir", d] + common
        entry = {"peripheral": per, "status": "", "headline": "", "pin_states": 0, "files": [], "notes": [], "decisions": [], "left_out": []}
        try:
            if per == "usb":
                def _usb_run(pattern):
                    extra = ["--out", os.path.join(d, "usb.dtsi"), "--report", os.path.join(d, "usb_report.md"), "--pattern", pattern]
                    for v in (getattr(args, "usb_vbus_pin", None) or []):
                        extra += ["--vbus-pin", v]
                    return _usb.run(_ns(_usb.build_argparser(), base + extra))
                asked = getattr(args, "usb_pattern", None) or "auto"
                r = _usb_run(asked)
                if asked == "auto" and not (r.summary or {}).get("consumer_nodes") and not (r.summary or {}).get("regulators"):
                    # nothing in the supplied files says how VBUS / over-current are wired: use the controller-node style of the accepted examples, stated as an assumption
                    r2 = _usb_run("controller")
                    if (r2.summary or {}).get("consumer_nodes"):
                        r = r2
                        entry["notes"].append("ASSUMED: USB wiring style 'controller' (&usbN nodes with oc-gpio / vbus-out-gpio, the style of the accepted examples) - nothing in the supplied files confirmed a style; choose 'regulator' if your BSP wires VBUS through regulator nodes")
                ov = r.overview or {}
                entry.update(status=ov.get("status", ""), headline=ov.get("headline", ""))
                sm = r.summary or {}
                if sm.get("unsupported_controller"):
                    entry["notes"].append(f"{sm['unsupported_controller']} non-TLMM USB controller pin(s) were left out (no confirmed DTS binding) - see the USB report")
                for sp in getattr(r, "shared_pins", []):
                    if sp.get("owner"):
                        entry["notes"].append(f"{sp['pin']} is ONE GPIO on {len(sp['signals'])} nets ({'; '.join(sp['signals'])}): decided - it is the hub line \"{sp['owner']}\" (the only net naming the hub), the other net(s) depend on it; confirm on the schematic")
                        continue
                    entry["decisions"].append(f"{sp['pin']} is ONE GPIO on {len(sp['signals'])} nets ({'; '.join(sp['signals'])}): its pin state is generated once; decide which node owns the GPIO (only one driver can claim it) and how the other depends on it.")
                if sm.get("unconfirmed_controller"):
                    entry["notes"].append(f"{sm['unconfirmed_controller']} USB pin(s) have an unconfirmed controller - see the USB report")
                if entry["status"] == "problems" and not r.files and sm.get("unsupported_controller"):
                    # the USB engine's only complaint is that the USB pins sit on a non-TLMM controller it has no binding for: that is a pin left out, not a broken file
                    n = sm["unsupported_controller"]
                    entry.update(status="needs_decisions", headline=f"{n} USB pin(s) are on a non-TLMM controller - no &tlmm pin state can be generated for them")
                    entry["decisions"].append(f"{n} USB pin(s) belong to a non-TLMM pin controller (for example the safety island): they need a node in that controller's own binding, which this tool has no confirmed form for - see the USB report")
                if r.excluded_pins:
                    entry["left_out"] += [{"pin": x, "signal": "", "reason": "excluded by you"} for x in r.excluded_pins]
                files = r.files
            elif per == "ethernet":
                r = _eth.run(_ns(_eth.build_argparser(), base))
                entry.update(status=r.overview.get("status", ""), headline=r.overview.get("headline", ""), pin_states=len(r.pins))
                _collect(entry, r)
                files = r.files
            elif per == "uart":
                r = _uart.run(_ns(_uart.build_argparser(), base))
                entry.update(status=r.overview.get("status", ""), headline=r.overview.get("headline", ""), pin_states=len(r.pins))
                _collect(entry, r)
                files = r.files
            elif per == "i2c":
                r = _i2c.run(_ns(_i2c.build_argparser(), base))
                entry.update(status=r.overview.get("status", ""), headline=r.overview.get("headline", ""), pin_states=len(r.pins))
                _collect(entry, r)
                files = r.files
            else:
                r = _spi.run(_ns(_spi.build_argparser(), base))
                entry.update(status=r.overview.get("status", ""), headline=r.overview.get("headline", ""), pin_states=len(r.pins))
                _collect(entry, r)
                files = r.files
        except SystemExit as e:
            entry.update(status="error", headline=str(e))
            files = []
        entry["files"] = [f["name"] for f in files]
        if not files and not entry["decisions"] and not entry["left_out"]:
            entry["notes"].append("no file from this engine (nothing selected for it in this project, or it stopped - see its headline)")
        parts.append({"peripheral": per, "files": files})
        res.peripherals.append(entry)
    m = combined_dts.merge(parts, args.project)
    res.files, res.sections, res.conflicts, res.notes = m["files"], m["sections"], m["conflicts"], m["notes"]
    res.decisions = [{"peripheral": p["peripheral"], "text": t} for p in res.peripherals for t in p["decisions"]]
    _drop_claimed_candidates(res.peripherals, parts)
    res.left_out = [{"peripheral": p["peripheral"], **x} for p in res.peripherals for x in p["left_out"]]
    foot = _footer(res)
    if foot:
        for f in res.files:
            if f["role"] == "pinmux":
                f["text"] = f["text"].rstrip("\n") + "\n" + foot
    counts = {x["peripheral"]: x["pin_states"] for x in res.sections}
    for p in res.peripherals:
        p["pin_states"] = counts.get(p["peripheral"], 0)
    problems = [p for p in res.peripherals if p["status"] in ("problems", "error")]
    if res.conflicts or problems:
        status, head = "problems", f"{len(res.conflicts) + len(problems)} problem(s) across the peripherals - read them before using the files"
    elif not res.files:
        status, head = "nothing", "None of the peripherals produced a file for this project"
    else:
        open_ = [p for p in res.peripherals if p["status"] == "needs_decisions"]
        status = "needs_decisions" if open_ else "complete"
        head = f"Combined {', '.join(p['peripheral'].upper() for p in res.peripherals if p['files'])} into {len(res.files)} file(s), {m['pin_states']} pin states" + (f" - {len(open_)} peripheral(s) still have open decisions" if open_ else "")
    res.overview = {"status": status, "headline": head, "pin_states": m["pin_states"], "files": [f["name"] for f in res.files]}
    od = getattr(args, "out_dir", None)
    if od and res.files:
        os.makedirs(od, exist_ok=True)
        for f in res.files:
            with open(os.path.join(od, f["name"]), "w", encoding="utf-8", newline="") as fh:
                fh.write(f["text"])
    res.report_text = render_report(res, args)
    res.exit_code = 2 if status == "problems" else 0
    return res


def render_report(res, args) -> str:
    L = [f"# Combined DTS report - {args.project}", "", f"**{res.overview['headline']}**", "", "## Per peripheral", "",
         "| Peripheral | Result | Pin states | Files |", "|---|---|---|---|"]
    for p in res.peripherals:
        L.append(f"| {p['peripheral'].upper()} | {p['status'] or '-'}: {p['headline']} | {p['pin_states']} | {', '.join(p['files']) or '-'} |")
    L += ["", "## Conflicts between peripherals", ""] + ([f"- {c}" for c in res.conflicts] or ["None."])
    L += ["", "## Open decisions (what a person still has to decide)", ""] + ([f"- [{d['peripheral'].upper()}] {d['text']}" for d in res.decisions] or ["None."])
    L += ["", "## Left out (not generated)", ""]
    if res.left_out:
        L += ["| Peripheral | Pin | Signal | Why |", "|---|---|---|---|"]
        L += [f"| {x['peripheral'].upper()} | {x['pin']} | {x['signal']} | {str(x['reason']).replace('|', '/')} |" for x in res.left_out]
    else:
        L += ["Nothing."]
    if res.notes or any(p["notes"] for p in res.peripherals):
        L += ["", "## Notes", ""] + [f"- {n}" for n in res.notes] + [f"- {p['peripheral'].upper()}: {n}" for p in res.peripherals for n in p["notes"]]
    L += ["", "Each engine's own report (pins, left out, independent check) is produced by its single-peripheral tool; the open NEEDS REVIEW comments are kept in the combined files."]
    return "\n".join(L) + "\n"


def build_argparser():
    ap = argparse.ArgumentParser(description="Combined DTS (USB + Ethernet + SPI + UART + I2C) - merges the engines' output.")
    ap.add_argument("--excel", required=True)
    ap.add_argument("--project")
    ap.add_argument("--list-projects", action="store_true")
    ap.add_argument("--peripheral", action="append", choices=PERIPHERALS, help="repeatable; default all")
    ap.add_argument("--kernel")
    ap.add_argument("--kernel-base")
    ap.add_argument("--template", action="append")
    ap.add_argument("--usb-pattern", choices=["auto", "controller", "regulator"])
    ap.add_argument("--usb-vbus-pin", action="append")
    ap.add_argument("--out-dir", default="combined_out")
    ap.add_argument("--report")
    return ap


def main(argv=None):
    args = build_argparser().parse_args(argv)
    if args.list_projects:
        print("Projects in this workbook: " + ", ".join(_usb.list_projects(args.excel)))
        return 0
    if not args.project:
        raise SystemExit("--project is required (use --list-projects to see the names)")
    res = run(args)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(res.report_text)
    print(res.report_text)
    return res.exit_code


if __name__ == "__main__":
    sys.exit(main())
