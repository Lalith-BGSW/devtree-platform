"""
Tests the "Result at a glance" overview (tool result.overview): every number is counted from the FINAL files, never from an
intermediate step, so the headline cannot say 'Consumer nodes: 0' while the files contain &usb0 / &usb1.
"""
import os, re, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, ROOT); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(ROOT, "tool"))
import _samples; _samples.need(__file__)
from _mock_llm import MockLLM
import usb_dts_tool as T

XL = os.path.join(ROOT, "samples", "Nissan_CCS20_SA8155PSIP_PinMux.xlsx")
mock = MockLLM(); base = mock.start()
tmp = tempfile.mkdtemp()
FAILS = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)))
    if not cond:
        FAILS.append(name)


class NS:
    def __init__(s, **k):
        d = dict(project="CCS20", dts=None, out=None, report=None, pattern=None, overlay=None, insert_after=None, vbus_pin=[], role=[],
                 include_pin=[], exclude_pin=[], inventory=None, combined=None, no_cache=False, no_update=False, keyword="usb", match="both",
                 list_projects=False, ai_advice=False, ai_max_pins=12, excel=XL, ai_propose=False, ai_suggest=False, facts="",
                 ai_draft=None, ai_proposal_out=None, docs=[], level_source="convention", template=[], out_dir=None, kernel=None,
                 kernel_reference=None, kernel_base=None)
        d.update(k); s.__dict__.update(d)


def run(tag, **k):
    return T.run(NS(out=f"{tmp}/{tag}.dtsi", report=f"{tmp}/{tag}.md", out_dir=f"{tmp}/{tag}_files", ai_proposal_out=f"{tmp}/{tag}.ai.dtsi", **k))


for k in [k for k in os.environ if k.startswith("LLM_")]:
    del os.environ[k]
os.environ.update(LLM_BASE_URL=base, LLM_API_KEY="K", LLM_DEPLOYMENT="gpt-4o-mini-test", LLM_TIMEOUT="5")

print("== Hybrid with no pattern (the screenshot case): the python-only step has 0 consumers, the FILES have &usb0/&usb1")
r = run("hy", ai_propose=True)
o = r.overview
files_text = "\n".join(f["text"] for f in r.files)
check("python-only step really had 0 consumer nodes (the old headline)", r.summary["consumer_nodes"] == 0, r.summary)
check("files really contain &usb0 and &usb1", "&usb0 {" in files_text and "&usb1 {" in files_text)
check("overview counts them from the files", o["consumers"] == ["usb0", "usb1"], o["consumers"])
check("overview counts pin states from the files", o["pin_states"] == len(re.findall(r"^\t[\w]+: [\w\-]+ \{", files_text.split("&usb0")[0], re.M)) and o["pin_states"] > 0, o["pin_states"])
check("wiring says ASSUMED (no project file confirms it)", "ASSUMED" in o["wiring"], o["wiring"])
check("status is needs_decisions, not a green 'complete'", o["status"] == "needs_decisions" and "decision" in o["headline"], o)
check("NEEDS REVIEW lines are counted from the files", o["review_lines"] == files_text.count("NEEDS REVIEW") and o["review_lines"] > 0)
check("open decisions + information equal the listed items", o["open_decisions"] + o["open_info"] == len((r.ai_proposal or {}).get("open_items") or []))
check("AI did not change the output", o["ai"]["changed_output"] is False)
check("verifier numbers present and nothing wrong", o["verifier"] is not None and o["verifier"]["wrong"] == 0, o["verifier"])
check("file names listed", len(o["files"]) == 2)

print("== Python-only run (no AI): same honesty")
r2 = run("py", pattern="controller")
o2 = r2.overview
check("controller consumers counted", o2["consumers"] == ["usb0", "usb1"], o2["consumers"])
check("explicit pattern is not called 'ASSUMED'", "ASSUMED" not in o2["wiring"], o2["wiring"])
check("AI line: not called", o2["ai"]["ran"] == [] and o2["ai"]["changed_output"] is False)

print("== no pattern, no AI: no consumers -> the overview says so instead of implying wiring")
r3 = run("nopat")
check("no consumers, wiring explains why", r3.overview["consumers"] == [] and "not confirmed" in r3.overview["wiring"], r3.overview)

print("== the THREE parts: regulators / pin states / USB nodes")
P = {x["key"]: x for x in o["parts"]}
check("three parts, in the order of the accepted change", [x["key"] for x in o["parts"]] == ["regulators", "pin_states", "usb_nodes"])
check("controller style: regulators absent WITH a reason (not silently missing)", P["regulators"]["count"] == 0 and "controller style" in (P["regulators"]["why_not"] or ""), P["regulators"])
check("pin states part: counted, in the pinmux file, text is &tlmm", P["pin_states"]["count"] == o["pin_states"] and P["pin_states"]["file"].endswith("_usb_pinmux.dtsi") and P["pin_states"]["text"].startswith("&tlmm"))
check("usb nodes part: 2, in the overlay file, text has &usb0 and &usb1", P["usb_nodes"]["count"] == 2 and P["usb_nodes"]["file"].endswith("_usb_overlay.dts") and "&usb0" in P["usb_nodes"]["text"] and "&usb1" in P["usb_nodes"]["text"])
rr = run("regs", pattern="regulator")
PR = {x["key"]: x for x in rr.overview["parts"]}
check("regulator style: regulators counted, text is regulator-fixed, file = overlay", PR["regulators"]["count"] >= 1 and "regulator-fixed" in PR["regulators"]["text"] and PR["regulators"]["why_not"] is None, PR["regulators"])
ov = next(f["text"] for f in rr.files if f["role"] == "overlay")
check("overlay lists the regulators BEFORE the &usbN nodes (as the accepted change does)", ov.index("regulator-fixed") < ov.index("&usb0 {"))
check("every regulator has its supply wired from a &usbN node", all(f"&{m}" in PR["usb_nodes"]["text"] for m in re.findall(r"^\t(\w+): \1 \{", PR["regulators"]["text"], re.M)), PR)
check("no-pattern run: all three parts explain what is missing", all(x["count"] > 0 or x["why_not"] for x in run("np2").overview["parts"]))

print("== open items: decisions vs information")
ap = r.ai_proposal
items, kinds = ap["open_items"], ap["open_item_kinds"]
check("kinds are aligned with the items", len(items) == len(kinds) and set(kinds) <= {"decision", "info"}, kinds)
dec = [it for it, k in zip(items, kinds) if k == "decision"]
inf = [it for it, k in zip(items, kinds) if k != "decision"]
has = lambda lst, frag: any(frag in x for x in lst)
check("a missing VBUS-enable pin is a DECISION", has(dec, "has no VBUS-enable pin"))
check("output pins without a level are a DECISION", has(dec, "have no level yet"))
check("cross-sheet variants are a DECISION", has(dec, "are placed differently"))
check("deliberately-not-generated notes are INFORMATION", has(inf, "Boot-strap pin(s)") and has(inf, "hub IC's own node") and has(inf, "Shared I2C bus"))
check("the ResetValue explanation is INFORMATION, not a decision", has(inf, "About the Excel's ResetValue column") and not has(dec, "About the Excel's ResetValue column"))
check("overview counts decisions and information separately", o["open_decisions"] == len(dec) and o["open_info"] == len(inf), (o["open_decisions"], len(dec), o["open_info"], len(inf)))
check("headline uses the DECISION count, not all items", f"{len(dec)} decision(s)" in o["headline"], o["headline"])
rep_txt = open(f"{tmp}/hy.md", encoding="utf-8").read()
check("report has both headings", "Needs a decision from a person" in rep_txt and "For information - deliberately not generated" in rep_txt)
check("report no longer says 'The AI was NOT used'", "The AI was NOT used" not in rep_txt)
check("default format summary does not say '0 state(s) analyzed'", all("0 state(s) analyzed" not in f["style_summary"] for f in r.files), [f["style_summary"] for f in r.files])
check("wiring advice is part of the overview", "choose a pattern or attach" in o["wiring"], o["wiring"])

print("== --exclude-pin: a pin that belongs to another project")
rx = run("ex", pattern="controller", exclude_pin=["GPIO_123"], ai_propose=True)
rep_x = open(f"{tmp}/ex.md", encoding="utf-8").read()
rep_b = open(f"{tmp}/hy.md", encoding="utf-8").read()
check("baseline lists GPIO_123 as a cross-sheet conflict", "GPIO_123" in rep_b.split("Cross-sheet conflicts")[-1].split("## PHY")[0], "no baseline conflict")
check("excluded pin is gone from the cross-sheet table", "| GPIO_123 |" not in rep_x)
check("excluded pin is listed as excluded", rx.excluded_pins == ["GPIO_123"] and "Pins you excluded" in rep_x, rx.excluded_pins)
nb = len(re.findall(r"\| GPIO_\d+ \|", rep_b.split("Cross-sheet conflicts")[-1].split("## PHY")[0]))
nx = len(re.findall(r"\| GPIO_\d+ \|", rep_x.split("Cross-sheet conflicts")[-1].split("## PHY")[0]))
check("only that pin was dropped", nx == nb - 1, (nb, nx))
check("generated files are unchanged by the exclusion", [f["text"] for f in rx.files] == [f["text"] for f in r.files])

print("== review markers survive into the deliverable files")
pm = next(f["text"] for f in r.files if f["role"] == "pinmux")
# 4 output pins: the three single-purpose ones plus GPIO_92, the shared hub-enable / USB-power pin, whose pin state is now generated once (flagged shared)
check("every output pin without a level carries its NEEDS REVIEW marker in the pinmux FILE", pm.count("NEEDS REVIEW: output pin") == 4, pm.count("NEEDS REVIEW: output pin"))
check("the file header promising those markers is truthful", "Every NEEDS REVIEW comment" in pm and "NEEDS REVIEW" in pm.split("*/", 1)[1])

print("== accepting the Database sheet for every cross-sheet conflict")
check("open conflicts are exposed for the one-click button", sorted(r.sheet_conflict_pins) == ["GPIO_102", "GPIO_113", "GPIO_123"], r.sheet_conflict_pins)
ra = run("acc", pattern="controller", exclude_pin=r.sheet_conflict_pins, ai_propose=True)
ov_a = next(f["text"] for f in ra.files if f["role"] == "overlay")
ov_b = next(f["text"] for f in r.files if f["role"] == "overlay")
check("before: the overlay carries the USBHUB-sheet marker", "sheet 'USBHUB'" in ov_b)
check("after: that marker is gone from the overlay", "sheet 'USBHUB'" not in ov_a)
check("after: no conflicts left, so no button", ra.sheet_conflict_pins == [])
check("after: the 'placed differently' decision is gone", not any("are placed differently" in x for x in ra.ai_proposal["open_items"]))
check("after: pin states are identical", [f["text"] for f in ra.files if f["role"] == "pinmux"] == [f["text"] for f in r.files if f["role"] == "pinmux"])

print("\n%s: %d failure(s)" % ("FAILED" if FAILS else "ALL PASSED", len(FAILS)))
sys.exit(1 if FAILS else 0)
