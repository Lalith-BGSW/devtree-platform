#!/usr/bin/env python3
"""
Generic (non-Qualcomm) pin-state PREVIEW tool - opt-in, separate from the Qualcomm engines.

  python generic_dts_tool.py --excel board.xlsm --project A0_sample --out-dir out/
  python generic_dts_tool.py --excel board.xlsm --project A0_sample --ai-draft      # also ask the AI for a draft (marked unverified)

For workbooks whose pins are not TLMM pins (e.g. Telechips TCC8050).  Everything it writes is marked UNVERIFIED; see app/generic_pinmux.py.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import usb_dts_tool as _u
from app import generic_pinmux as gp


class GenericResult:
    def __init__(self):
        self.text, self.pins, self.counts, self.items, self.left_out = "", [], {}, [], []
        self.verification, self.ai_draft, self.report_text, self.overview = None, None, "", {}
        self.files, self.soc, self.exit_code = [], None, 0


def run(args) -> GenericResult:
    res = GenericResult()
    if getattr(args, "list_projects", False):
        print("Projects in this workbook: " + ", ".join(_u.list_projects(args.excel)))
        return res
    if not args.project:
        raise SystemExit("--project is required (use --list-projects to see the names)")
    parsed, records, soc = _u.load_records(args.excel, args.project)
    res.soc = soc
    fams = [f.lower() for f in (getattr(args, "family", None) or [])] or list(gp.ORDER)
    bad = [f for f in fams if f not in gp.ORDER]
    if bad:
        raise SystemExit(f"unknown family {bad}; choose from {gp.ORDER}")
    g = gp.generate(records, args.project, fams)
    res.text, res.pins, res.counts, res.items, res.left_out = g["text"], g["pins"], g["counts"], g["items"], g["left_out"]
    res.verification = gp.check(res.text, records) if res.text else {"wrong": [], "ok": 0, "checked": 0}
    review = res.text.count("NEEDS REVIEW")
    if res.text:
        res.files = [{"name": f"{args.project}_generic_preview.dtsi", "text": res.text}]
    if getattr(args, "ai_draft", False):
        res.ai_draft = gp.ai_draft(res.pins, args.project, soc) if res.pins else {"status": "none", "text": "", "note": "no pins to draft", "calls": 0}
        if res.ai_draft["text"]:
            res.files.append({"name": f"{args.project}_generic_AI_DRAFT_unverified.dtsi", "text": res.ai_draft["text"]})
    v = res.verification
    if v["wrong"]:
        status, head = "problems", f"{len(v['wrong'])} problem(s) found - read them before using the file"
    elif not res.pins:
        status, head = "nothing", "No SPI, UART or I2C pin is selected in this project"
    else:
        status, head = "preview", f"UNVERIFIED preview: {len(res.pins)} pin(s) in {g['controllers']} controller(s); {review} line(s) marked NEEDS REVIEW"
    res.overview = {"status": status, "headline": head, "pin_states": len(res.pins), "review_lines": review, "families": res.counts,
                    "verifier": {"wrong": len(v["wrong"]), "ok": v["ok"], "checked": v["checked"]}, "files": [f["name"] for f in res.files],
                    "unverified": True}
    L = [f"# Generic pin preview - {args.project}", "", f"**{head}**", "", f"SoC: {soc or 'unknown'}", "",
         "Pins are written exactly as the workbook names them; the SoC's real pinctrl binding is unknown, so nothing here is verified.", "",
         "| Pin | Ball | Signal | Controller | Mux | Bias | Drive (workbook) |", "|---|---|---|---|---|---|---|"]
    L += [f"| {p['pin']} | {p['ball_id']} | {p['signal']} | {p['instance']} | {p['mux']} | {p['bias'] or '?'} | {p['drive'] or '-'} |" for p in res.pins]
    flagged = [p for p in res.pins if p["notes"]]
    if flagged:
        L += ["", "## Notes per pin", ""] + [f"- {p['pin']}: " + "; ".join(p["notes"]) for p in flagged]
    L += ["", "## Independent check", "", f"{len(v['wrong'])} wrong | {v['ok']} of {v['checked']} workbook pins confirmed in the right block"]
    L += [f"- {w}" for w in v["wrong"]]
    if res.ai_draft:
        L += ["", "## AI draft", "", f"{res.ai_draft['status']}: {res.ai_draft.get('note', '')}"]
    res.report_text = "\n".join(L) + "\n"
    od = getattr(args, "out_dir", None)
    if od:
        os.makedirs(od, exist_ok=True)
        for f in res.files:
            with open(os.path.join(od, f["name"]), "w", encoding="utf-8", newline="") as fh:
                fh.write(f["text"])
    if status == "problems":
        res.exit_code = 2
    return res


def build_argparser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--excel")
    ap.add_argument("--project")
    ap.add_argument("--list-projects", action="store_true")
    ap.add_argument("--family", action="append", help="spi, uart or i2c (default: all)")
    ap.add_argument("--ai-draft", action="store_true", help="also ask the AI for a draft (needs generic_draft in LLM_FEATURES)")
    ap.add_argument("--out-dir")
    return ap


def main(argv=None):
    args = build_argparser().parse_args(argv)
    if not args.excel:
        raise SystemExit("--excel is required")
    res = run(args)
    if res.report_text:
        print(res.report_text)
    return res.exit_code


if __name__ == "__main__":
    sys.exit(main())
