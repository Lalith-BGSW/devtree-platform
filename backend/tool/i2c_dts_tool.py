#!/usr/bin/env python3
"""
I2C DTS tool - the I2C counterpart of uart_dts_tool.py.

  python i2c_dts_tool.py --excel board.xlsx --project CCS20 --kernel 6.6 --out-dir out/
  python i2c_dts_tool.py --excel board.xlsx --list-projects

Pins are chosen by the workbook's SELECTED function (never the capability list) and filtered by family:
  --family i2c       (the only family: I2C_QUPn / I2C_QUPn_SEk; CCI, SSC, SAIL and other SoC families are listed as left out)
  --include-pin GPIO_124     add a pin the workbook does not select as I2C (it is muxed as gpio)
  --exclude-pin GPIO_81      drop a pin
Output: <project>_i2c_pinmux.dtsi (&tlmm pin states) and <project>_i2c_overlay.dts (&i2cN consumers), in the format of
--template files, else of --kernel, else the default format. The finished files are read again by an independent checker.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import usb_dts_tool as _u                      # workbook reading, dtc, shared helpers
from app import dts_style, i2c_generator as eg


class I2cToolResult:
    def __init__(self):
        self.text = ""
        self.files, self.bundle_notes, self.items = [], [], []
        self.pins, self.left_out, self.counts = [], [], {}
        self.verification = None
        self.kernel_resolution = None
        self.soc = None
        self.soc_known = False
        self.report_text = ""
        self.dtc_result = None
        self.validation_problems = []
        self.ai_check = None
        self.ai_facts = []
        self.ai_ledger = []
        self.overview = {}
        self.column_hits = {}
        self.exit_code = 0


def _styles_from(args, soc, result):
    styles, skipped = {}, []
    for tp in (getattr(args, "template", None) or []):
        try:
            from app.usb_ai_proposer import read_document
            tt = read_document(tp)
        except OSError as e:
            raise SystemExit(f"--template {tp!r}: cannot read it ({e})")
        name = os.path.basename(tp)
        st = dts_style.learn_style(tt, name)
        if st.unrecognized_pin_states:
            result.bundle_notes.append(f"template '{name}' has {st.unrecognized_pin_states} unrecognized pin-state block(s); ignored so its format is not learned from incomplete evidence")
            continue
        if st.role == "unknown":
            result.bundle_notes.append(f"template '{name}' has no recognisable pin states or overlay content: ignored")
            continue
        if st.role in styles:
            result.bundle_notes.append(f"a second {st.role} template ('{name}') was ignored: the first one is used")
            continue
        styles[st.role] = st
        skipped += [f"{name}: {x}" for x in st.skipped_includes]
    tmpl_roles = set(styles)
    if getattr(args, "kernel", None):
        from app import kernel_format
        try:
            kr = kernel_format.resolve(args.kernel, soc, None, base=getattr(args, "kernel_base", None))
        except ValueError as e:
            raise SystemExit(str(e))
        result.kernel_resolution = {"version": kr["version"], "kind": kr["kind"], "source": kr["source"], "notes": kr["notes"]}
        if "pinmux" in styles:
            result.bundle_notes.append(f"--kernel {args.kernel} was not used for the pinmux file: the template '{styles['pinmux'].source}' wins")
        elif kr["style"] is not None:
            styles["pinmux"] = kr["style"]
            result.bundle_notes.append(f"pinmux format taken from Linux {kr['version']}: {kr['source']}")
        result.bundle_notes += [f"kernel: {x}" for x in kr["notes"]]
    for role in ("pinmux", "overlay"):
        if role not in styles and (getattr(args, "template", None) or getattr(args, "kernel", None)):
            if not (role == "overlay" and getattr(args, "kernel", None)):
                result.bundle_notes.append(f"no {role} template/kernel format: the {role} file uses the default format")
    return styles, skipped, tmpl_roles


def run(args) -> I2cToolResult:
    res = I2cToolResult()
    if getattr(args, "list_projects", False):
        print("Projects in this workbook: " + ", ".join(_u.list_projects(args.excel)))
        return res
    if not args.project:
        raise SystemExit("--project is required (use --list-projects to see the names)")
    parsed, records, soc = _u.load_records(args.excel, args.project)
    fams = [f.lower() for f in (getattr(args, "family", None) or [])] or list(eg.ORDER)
    bad = [f for f in fams if f not in eg.ORDER]
    if bad:
        raise SystemExit(f"unknown family {bad}; choose from {eg.ORDER}")
    inc = [p for raw in (getattr(args, "include_pin", None) or []) for p in str(raw).split(",") if p.strip()]
    exc = [p for raw in (getattr(args, "exclude_pin", None) or []) for p in str(raw).split(",") if p.strip()]
    for p in inc + exc:
        if not re.fullmatch(r"(?i)\s*gpio[_ ]?\d+\s*|\s*\d+\s*", p):
            raise SystemExit(f"pin {p!r}: expected GPIO_N")
    bw = not getattr(args, "no_board_wiring", False)
    g = eg.generate(records, soc, args.project, fams, inc, exc, board_wiring=bw)
    res.soc, res.soc_known = soc, g["soc_known"]
    res.pins, res.left_out, res.counts, res.items = g["pins"], g["left_out"], g["counts"], g["items"]
    if not g["soc_known"] and g["pins"]:
        res.items.insert(0, {"kind": "decision", "text": f"No verified I2C pin table exists for {soc or 'this SoC'}: the workbook's QUP name is used as the function and every pin is marked for review."})
    final = g["text"]
    res.text = final
    problems, res.dtc_result = _u.validate(final) if final else ([], None)
    res.validation_problems = problems
    styles, skipped, tmpl_roles = _styles_from(args, soc, res)
    if final:
        bundle = dts_style.build_bundle(final, args.project, styles, generator="i2c_dts_tool", peripheral="i2c")
        res.files = bundle["files"]
        res.bundle_notes += bundle["notes"]
        if skipped:
            res.bundle_notes.append("template #include lines were NOT copied (SoC / project specific): " + "; ".join(skipped[:8]))
        od = getattr(args, "out_dir", None)
        if od:
            os.makedirs(od, exist_ok=True)
            for f in res.files:
                with open(os.path.join(od, f["name"]), "w", encoding="utf-8", newline="") as fh:
                    fh.write(f["text"])
    written = "\n".join(f["text"].replace("\r\n", "\n") for f in res.files) if res.files else final
    res.verification = eg.verify_text(written, records, soc, inc, exc, fams, board_wiring=bw)

    # AI: the data check only. Findings are questions for a person; nothing in the files changes.
    from app import llm_client as _llm
    if getattr(args, "ai_check", False):
        from app import usb_ai_assist as ua
        before = dict(_llm.usage_totals)
        items = []
        for p in g["pins"]:
            r = next((x for x in records if eg._gpio_of(x)[1] == p["gpio"]), None)
            if r is None:
                continue
            f = r.fields
            cells = [f"Signal: {f.get('gen5_signal_name') or ''}", f"Interface group: {f.get('interface_group') or ''}",
                     f"Pinmuxed name: {f.get('pinmuxed_name') or ''}", f"Selected: {f.get('selected_interface') or ''}",
                     f"Direction: {f.get('direction') or ''}", f"Pull: {f.get('io_gpio_pull') or ''}", f"Drive strength: {f.get('io_drv_strength') or ''}",
                     f"ResetValue: {f.get('resetvalue') or ''}", f"Remark: {_u.remark(r) or ''}", f"Net: {_u.net_trace_full(r)}"]
            txt = re.sub(r"\s+", " ", " | ".join(c for c in cells if c.split(": ", 1)[1].strip()))[:700]
            items.append({"pin": _u._ball_name(r) or p["pin"], "text": txt})
        res.ai_check = ua.check(ua.check_candidates(items), prompt=eg.check_prompt(), what="I2C")
        now = dict(_llm.usage_totals)
        calls = now["calls"] - before["calls"]
        toks = (now["prompt_tokens"] - before["prompt_tokens"]) + (now["completion_tokens"] - before["completion_tokens"])
        nf = sum(r["status"] == "finding" for r in res.ai_check["rows"])
        res.ai_ledger.append({"feature": "i2c_check", "label": "I2C data check", "switch": "On" , "calls": calls, "tokens": toks,
                              "status": "ran" if calls else ("nothing to check" if res.ai_check["status"] == "none" else "not available: " + res.ai_check["note"]),
                              "effect": f"{res.ai_check['checked']} pin(s) read; {nf} verified finding(s) - nothing in the files was changed"})
    v = res.verification
    review = sum(f["text"].count("NEEDS REVIEW") for f in res.files) if res.files else g["review_lines"]
    decisions = sum(1 for i in res.items if i["kind"] == "decision")
    if problems or v["wrong"]:
        status, head = "problems", f"{len(problems) + len(v['wrong'])} problem(s) found - read them before using the files"
    elif not res.pins:
        nopin = any("no TLMM GPIO number" in i["text"] for i in res.items)
        status, head = "nothing", ("I2C in this project uses a pin controller other than TLMM (or a subsystem QUP): no pinctrl state was generated" if nopin else "No I2C pin is selected in this project")
    elif decisions or review:
        status, head = "needs_decisions", f"Generated and verified - {decisions} decision(s) still need a person" if decisions else f"Generated and verified - {review} line(s) marked NEEDS REVIEW"
    else:
        status, head = "complete", "Generated and verified - nothing left to decide"
    res.overview = {"status": status, "headline": head, "pin_states": len(res.pins), "left_out": len(res.left_out), "review_lines": review,
                    "open_decisions": decisions, "families": res.counts, "enabled": fams, "verifier": {k: (len(v[k]) if isinstance(v[k], list) else v[k]) for k in v},
                    "files": [f["name"] for f in res.files], "controller": ", ".join(g["controllers"]), "soc_table": g["soc_table"]}
    res.report_text = render_report(res, args)
    if status == "problems":
        res.exit_code = 2
    return res


def render_report(res, args) -> str:
    L = [f"# I2C DTS report - {args.project}", "", f"**{res.overview['headline']}**", "",
         f"SoC: {res.soc or 'unknown'} ({'verified pin table: ' + res.overview['soc_table'] if res.soc_known else 'no pin table'})", "",
         "## Pins generated", ""]
    if res.pins:
        L += ["| Pin | Signal | Selected in workbook | Role | Function | Mux (workbook / table) | Drive | Bias |", "|---|---|---|---|---|---|---|---|"]
        for p in res.pins:
            L.append(f"| {p['pin']} | {p['signal']} | {p['selected_as']} | {p['role'] or 'i2c'} | {p['function']} | {p['mux'] if p['mux'] is not None else '-'} / {p['table_mux'] if p['table_mux'] is not None else '-'} | {p['drive'] or '?'} | {p['bias'] or '?'} |")
    else:
        L.append("None.")
    L += ["", "## Left out", ""]
    L += [f"- {x['pin']} {x['signal']}: {x['reason']}" + (f" | AI: {x['ai']['verdict']}" + (f" ({x['ai']['role']})" if x['ai'].get('role') else "") + (f" - \"{x['ai']['quote']}\"" if x['ai'].get('quote') else "") + (f" [{x['ai']['note']}]" if x['ai'].get('note') else "") if x.get("ai") else "") for x in res.left_out] or ["Nothing."]
    if getattr(res, "ai_facts", None):
        L += ["", "## Board facts the AI found written in the workbook (suggestions - confirm before use)", ""] + [f"- {f['topic']}: {f['value']} ({f['pin']}: \"{f['quote']}\")" for f in res.ai_facts]
    L += ["", "## Open items", ""]
    L += [f"- [{i['kind']}] {i['text']}" for i in res.items] or ["None."]
    v = res.verification
    L += ["", "## Independent check", "", f"{len(v['wrong'])} wrong | {len(v['ambiguous'])} ambiguous | {len(v['unverifiable'])} unverifiable | {v['ok']} confirmed OK"]
    L += [f"- WRONG: {x}" for x in v["wrong"]] + [f"- ambiguous: {x}" for x in v["ambiguous"]] + [f"- unverifiable: {x}" for x in v["unverifiable"]]
    if res.dtc_result:
        L += ["", res.dtc_result]
    return "\n".join(L) + "\n"


def build_argparser():
    ap = argparse.ArgumentParser(description="I2C DTS generator (deterministic).")
    ap.add_argument("--excel", required=True)
    ap.add_argument("--project")
    ap.add_argument("--list-projects", action="store_true")
    ap.add_argument("--family", action="append", choices=eg.ORDER, help="repeatable; default all")
    ap.add_argument("--include-pin", action="append")
    ap.add_argument("--exclude-pin", action="append")
    ap.add_argument("--no-board-wiring", action="store_true", help="accepted for compatibility with the other tools; I2C has no board-wired control lines")
    ap.add_argument("--template", action="append")
    ap.add_argument("--kernel")
    ap.add_argument("--kernel-base")
    ap.add_argument("--out-dir", default="i2c_out")
    ap.add_argument("--report", default=None)
    ap.add_argument("--ai-check", action="store_true", help="AI reads the workbook cells of each pin and reports contradictions (suggestions only)")
    return ap


def main(argv=None):
    args = build_argparser().parse_args(argv)
    res = run(args)
    if args.list_projects:
        return 0
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(res.report_text)
    print(res.report_text)
    return res.exit_code


if __name__ == "__main__":
    sys.exit(main())
