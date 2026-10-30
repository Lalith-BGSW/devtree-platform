"""
Tests the AI-proposed DTS (app/usb_ai_proposer.py + the tool integration) against a local mock gateway. What this
proves is the CONTROL SYSTEM around the AI - not that a real model writes good DTS: Python verifies every draft
against the workbook, sends exact findings back for repair, marks unsupported specifics, and never lets the draft
touch the verified output.
"""
import json, os, re, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, ROOT); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(ROOT, "tool"))
import sys as _s, os as _o; _s.path.insert(0, _o.path.join(_o.path.dirname(_o.path.abspath(__file__)), '..', 'scripts')); import _samples; _samples.need(__file__)
from _mock_llm import MockLLM
from app import usb_ai_proposer as P
import usb_dts_tool as T

XL = os.path.join(ROOT, "samples", "Nissan_CCS20_SA8155PSIP_PinMux.xlsx")
HONDA_XL = os.path.join(ROOT, "samples", "Honda_database_v2.xlsx")
STALE_DRAFT = os.path.join(ROOT, "samples", "ai_draft_usb.dts")      # a real AI draft with 10 wrong values
KEY = "SECRET-KEY-123"
mock = MockLLM(); base = mock.start()
tmp = tempfile.mkdtemp()

def configure(**kw):
    for k in [k for k in os.environ if k.startswith("LLM_")]:
        del os.environ[k]
    os.environ.update(LLM_BASE_URL=base, LLM_API_KEY=KEY, LLM_DEPLOYMENT="gpt-4o-mini-test", LLM_TIMEOUT="5", **kw)
    mock.requests.clear(); mock.script.clear(); mock.reply = None

class NS:
    def __init__(self, **k):
        d = dict(project="CCS20", dts=None, out=None, report=None, pattern="controller", overlay=None, insert_after=None,
                 vbus_pin=[], inventory=None, combined=None, no_cache=False, no_update=False, keyword="usb", match="both",
                 list_projects=False, ai_advice=False, ai_max_pins=12, excel=XL, ai_propose=False, facts="", ai_draft=None,
                 ai_proposal_out=None)
        d.update(k); self.__dict__.update(d)

def run(tag, **kw):
    return T.run(NS(out=f"{tmp}/{tag}.dtsi", report=f"{tmp}/{tag}.md", ai_proposal_out=f"{tmp}/{tag}.ai.dtsi", **kw))

def verified_of(body):
    m = re.search(r"VERIFIED_DTS:\n```dts\n(.*?)\n```", body["messages"][1]["content"], re.S)
    return m.group(1) + "\n"

def block(text):
    return "```dts\n" + text + "```"

def replying(*transforms):
    """The n-th request gets transforms[min(n, last)](verified_text, request_body)."""
    def reply(body):
        n = len([r for r in mock.requests]) - 1
        t = transforms[min(n, len(transforms) - 1)]
        return block(t(verified_of(body), body))
    return reply

base_run = run("base")                       # the verified output WITHOUT any AI

print("=== 1. PYTHON FIRST: a level fact for one named pin is applied mechanically - the AI is not relied on ===")
configure()
mock.reply = replying(lambda v, b: v)                      # the model returns exactly what it was given (as gpt-4o-mini did)
r = run("a", ai_propose=True, facts="usbv3_power_enable (gpio82) boots output-low according to the schematic")
ap = r.ai_proposal
assert ap["status"] == "clean", (ap["status"], ap["open_findings"], ap["unsupported"])
assert ap["facts_applied"] == [{"n": 1, "pin": "gpio82", "level": "output-low"}]
node82 = ap["final_text"].split("usbv3_power_enable: usbv3_power_enable {")[1].split("\n\t};")[0]
assert "output-low;  /* source: FACT 1 */" in node82 and "NEEDS REVIEW: output pin" not in node82
assert ap["final_text"].count("NEEDS REVIEW: output pin") == base_run.out_text.count("NEEDS REVIEW: output pin") - 1, "only gpio82 changed"
assert "ALREADY APPLIED BY PYTHON" in mock.requests[0]["body"]["messages"][1]["content"]
assert "+\t\t\toutput-low;" in ap["diff"] and "-\t\t\t/* NEEDS REVIEW: output pin" in ap["diff"]
assert "## Hybrid output - Python-verified nodes plus the AI additions that passed every check" in open(f"{tmp}/a.md").read()
assert open(ap["path"]).read() == ap["final_text"]
assert r.out_text == base_run.out_text and open(f"{tmp}/a.dtsi").read() == open(f"{tmp}/base.dtsi").read(), "the draft must not touch --out"
assert r.summary == base_run.summary
print("OK")

print("=== 2. REPAIR LOOP: a wrong value is caught by the verifier, sent back with the exact finding, and fixed ===")
configure()
def stale_drive(v, body):
    return v.replace("drive-strength = <10>;", "drive-strength = <2>;", 1)
mock.reply = replying(stale_drive, lambda v, b: v)
r = run("b", ai_propose=True, facts="context: this board has two USB ports")
ap = r.ai_proposal
assert len(mock.requests) == 2 and ap["status"] == "clean" and [x["problems"] > 0 for x in ap["rounds"]] == [True, False], ap["rounds"]
assert "<2>" not in ap["final_text"], "the stale value is gone from the final file"
assert "were found by Python and sent back to the AI to fix" in open(f"{tmp}/b.md").read() and "drive-strength 2 (DTS) vs 10 (Excel)" in open(f"{tmp}/b.md").read()
feedback = mock.requests[1]["body"]["messages"][-1]["content"]
assert "FINDINGS" in feedback and "drive-strength 2 (DTS) vs 10 (Excel)" in feedback, feedback[:400]
print("OK")

print("=== 3. INVENTED specifics are demoted to comments in the final file and listed (the microchip,usb2514 / 0x2d failure) ===")
configure()
HUB = '\n&i2c18 {\n\thub@2d {\n\t\tcompatible = "microchip,usb2514";\n\t\treg = <0x2d>;\n\t};\n};\n'
mock.reply = replying(lambda v, b: v + HUB)
r = run("c", ai_propose=True, facts="context: this board has a USB hub")        # a context fact with NO specifics: the AI is engaged, the invention is its own
ap = r.ai_proposal
kinds = {(u["kind"], u["token"]) for u in ap["unsupported"]}
assert ("I2C address / hex value", "0x2d") in kinds and ("compatible-style string", '"microchip,usb2514"') in kinds and ("base-tree label", "i2c18") in kinds, kinds
fin = ap["final_text"]
assert not re.search(r"^\s*(compatible|reg)\s*=", fin, re.M) and not re.search(r"^&i2c18", fin, re.M), "no invented line may stay ACTIVE"
assert "// UNVERIFIED" in fin and "microchip,usb2514" in fin, "the original text stays visible as a comment"
active3 = "\n".join(l for l in fin.splitlines() if not l.startswith("//"))
assert active3.count("{") == active3.count("}"), "braces of the active text stay balanced"
assert ap["status"] == "clean", (ap["status"], ap["open_findings"]) 
assert open(ap["path"]).read() == fin and "Kept as comments" in open(f"{tmp}/c.md").read()
assert "UNVERIFIED" in ap["text"], "the annotated draft still shows what was flagged"
assert T.run_dtc(fin)[0] in (True, None), "the demoted final file must still compile"
assert r.out_text == base_run.out_text, "the Python-only output is untouched"
print("OK")

print("=== 4. The SAME draft is accepted when the engineer's FACTS supply those values ===")
configure()
mock.reply = replying(lambda v, b: v + HUB)
r = run("d", ai_propose=True, facts="The hub is a microchip,usb2514 at I2C address 0x2d on the QUP18 bus, node label &i2c18 (datasheet).")
ap = r.ai_proposal
assert ap["status"] == "clean" and ap["unsupported"] == [], (ap["status"], ap["unsupported"])
assert re.search(r'^\s*compatible = "microchip,usb2514";', ap["final_text"], re.M) and "// UNVERIFIED" not in ap["final_text"], "backed by facts -> ACTIVE"
print("OK")

print("=== 5. Dropping or editing a VERIFIED node is caught every round; after the cap it is reported, not hidden ===")
configure()
def drop_node(v, body):
    return re.sub(r"\tusbv2_fault_detect: usbv2_fault_detect \{.*?\n\t\};\n", "", v, flags=re.S)
mock.reply = replying(drop_node)
r = run("e", ai_propose=True, facts="context: this board has two USB ports")
ap = r.ai_proposal
assert len(mock.requests) == 3 and ap["status"] == "needs_attention" and ap["verification"]["changed_verified_lines"] > 0
assert any("VERIFIED line" in f for f in ap["open_findings"])
print("OK")

print("=== 6. Not available -> a visible note, nothing written, verified output untouched ===")
for k in [k for k in os.environ if k.startswith("LLM_")]:
    del os.environ[k]
mock.requests.clear()
r = run("f", ai_propose=True)
assert r.ai_proposal["status"] == "unavailable" and not mock.requests and r.ai_proposal["path"] is None and not os.path.exists(f"{tmp}/f.ai.dtsi")
assert "unavailable" in open(f"{tmp}/f.md").read() and r.out_text == base_run.out_text
configure(LLM_FEATURES="usb_advice")
assert run("f2", ai_propose=True).ai_proposal["status"] == "unavailable" and "usb_propose" in run("f3", ai_propose=True).ai_proposal["reason"]
print("OK")

print("=== 7. DRAFT MODE: a real stale AI draft is checked first; its exact findings are what the AI must fix ===")
configure()
mock.reply = replying(lambda v, b: v)
r = run("g", ai_draft=STALE_DRAFT)
ap = r.ai_proposal
assert ap["rounds"][0]["source"] == "your draft" and ap["rounds"][0]["problems"] >= 8, ap["rounds"]
assert "DRAFT to correct" in mock.requests[0]["body"]["messages"][1]["content"] and "usb_0: usb@a600000" in mock.requests[0]["body"]["messages"][1]["content"]
fb = mock.requests[0]["body"]["messages"][-1]["content"]
assert "drive-strength 2 (DTS) vs 10 (Excel)" in fb, fb[:500]
assert ap["status"] == "clean" and r.out_text == base_run.out_text
# a draft that already passes every check needs no AI call at all (case 2's final text is the clean verified output)
configure(); mock.reply = replying(lambda v, b: v)
r = run("g2", ai_draft=f"{tmp}/b.ai.dtsi")
assert not mock.requests and r.ai_proposal["status"] == "clean" and r.ai_proposal["rounds"][0]["source"] == "your draft"
print("OK")

print("=== 9. A flagged line that opens a node demotes the WHOLE node (braces stay balanced) ===")
txt = "&tlmm {\n\ta: a {\n\t};\n};\n&i2c18 {\n\thub@2d {\n\t\treg = <0x2d>;\n\t};\n};\n&usb0 {\n};\n"
flagged = P.source_guard(txt, "&tlmm {\n\ta: a {\n\t};\n};\n", [], "")
out = P.demote(txt, flagged)
lines = out.splitlines()
assert all(l.startswith("// UNVERIFIED") for l in lines[4:11]), out          # &i2c18 {...} and &usb0 {...} (a label no source confirms): whole nodes commented
assert not any(l.startswith("//") for l in lines[:4]) and lines[0] == "&tlmm {", "unflagged text stays active"
active = "\n".join(l for l in lines if not l.startswith("//"))
assert active.count("{") == active.count("}"), active
print("OK")

print("=== 10. AI FIRST, THEN PYTHON: facts read from a document are kept only if their quote is literally in it ===")
DOC = f"{tmp}/hub_notes.md"
open(DOC, "w", newline="\n").write("HUB DATASHEET NOTES\nThe USB hub is a microchip,usb2514 attached to the QUP18 I2C bus (node label &i2c18). Its I2C address is 0x2d.\n")
configure()
def doc_reply(body):
    if "extract engineering FACTS" in body["messages"][0]["content"]:
        return json.dumps({"facts": [
            {"fact": "The hub is a microchip,usb2514 on the &i2c18 bus at address 0x2d.",
             "quote": "The USB hub is a microchip,usb2514 attached to the QUP18 I2C bus (node label &i2c18). Its I2C address is 0x2d."},
            {"fact": "The hub reset is active high.", "quote": "The hub reset is active high"},                       # quote is NOT in the document
            {"fact": "The hub is at address 0x44.", "quote": "Its I2C address is 0x2d."}]})                          # 0x44 is not in its own quote
    return block(verified_of(body) + HUB)
mock.reply = doc_reply
r = run("h", docs=[DOC])
ap = r.ai_proposal
assert ap["facts_rejected"] == 2 and len(ap["facts_extracted"]) == 1 and "microchip,usb2514" in ap["facts_extracted"][0], ap["facts_extracted"]
gen_msg = mock.requests[-1]["body"]["messages"][1]["content"]
assert "FACT 1: The hub is a microchip,usb2514" in gen_msg and "address is 0x44" not in gen_msg and "active high" not in gen_msg, "only the verified fact reaches the generation"
assert ap["status"] == "clean" and ap["unsupported"] == [], ap["unsupported"]
assert re.search(r'^\s*compatible = "microchip,usb2514";', ap["final_text"], re.M) and "// UNVERIFIED" not in ap["final_text"], "a verified document fact makes the hub node ACTIVE"
rep_md = open(f"{tmp}/h.md").read()
assert "## Facts extracted from your documents" in rep_md and "1 fact(s) accepted, 2 rejected" in rep_md
assert r.out_text == base_run.out_text
for k in [k for k in os.environ if k.startswith("LLM_")]:
    del os.environ[k]
mock.requests.clear()
r = run("h2", docs=[DOC])
assert "unavailable" in open(f"{tmp}/h2.md").read() and not mock.requests and r.ai_proposal["facts_extracted"] == []
print("OK")

print("=== 12. Document reading never fails silently: missing file, empty file, unusable reply, empty answer, UTF-16 file ===")
configure(); mock.reply = doc_reply
try:
    run("m1", docs=[f"{tmp}/does_not_exist.md"]); assert False, "a missing --docs file must be an error"
except SystemExit as e:
    assert "file not found" in str(e) and "does_not_exist.md" in str(e)
empty = f"{tmp}/empty.md"; open(empty, "w").write("")
r = run("m2", docs=[empty])
assert "the file is empty" in open(f"{tmp}/m2.md").read() and r.ai_proposal["facts_extracted"] == []
configure(); mock.reply = lambda b: ("not json at all" if "extract engineering FACTS" in b["messages"][0]["content"] else block(verified_of(b)))
r = run("m3", docs=[DOC])
assert "the AI reply was not usable JSON" in open(f"{tmp}/m3.md").read(), "an unusable reply must be visible, not 0/0"
configure(); mock.reply = lambda b: (json.dumps({"facts": []}) if "extract engineering FACTS" in b["messages"][0]["content"] else block(verified_of(b)))
r = run("m4", docs=[DOC])
assert "the AI found no relevant fact in this document" in open(f"{tmp}/m4.md").read() and "characters read" in open(f"{tmp}/m4.md").read()
u16 = f"{tmp}/u16.md"; open(u16, "wb").write(b"\xff\xfe" + open(DOC, newline="").read().encode("utf-16-le"))     # what PowerShell's  >  writes
bom = f"{tmp}/bom.md"; open(bom, "wb").write(b"\xef\xbb\xbf" + open(DOC, "rb").read())
assert P.read_document(u16) == open(DOC, newline="").read() and P.read_document(bom) == open(DOC, newline="").read()
configure(); mock.reply = doc_reply
r = run("m5", docs=[u16])
assert len(r.ai_proposal["facts_extracted"]) == 1, "a UTF-16 document must be readable"
print("OK")

print("=== 13. The model cannot set a level on a pin NO fact names (a fact for gpio82 does not license gpio113) ===")
configure()
def level_everywhere(v, body):
    return v.replace("/* NEEDS REVIEW: output pin - add output-high or output-low per hardware design (level is not derivable from Excel) */", "output-high;")
mock.reply = replying(level_everywhere)
r = run("n", ai_propose=True, facts="usbv3_power_enable (gpio82) boots output-low according to the schematic")
ap = r.ai_proposal
assert any(u["kind"] == "output level" and "gpio113" in u["token"] for u in ap["unsupported"]), ap["unsupported"]
fin = ap["final_text"]
assert "output-low;  /* source: FACT 1 */" in fin, "the stated pin keeps its fact-backed level"
node113 = fin.split("cpu_usbhub_ctrl_rst: cpu_usbhub_ctrl_rst {")[1].split("\n\t};")[0]
assert not re.search(r"^\s*output-(high|low);", node113, re.M), "no active level on the unmentioned pin"
assert "NEEDS REVIEW: output pin" in node113 and "// UNVERIFIED" in node113, "the marker is restored, the AI's attempt stays visible"
assert ap["status"] == "clean"
print("OK")

print("=== 14. EVERY fact is accounted for - none is silently ignored ===")
configure(); mock.reply = replying(lambda v, b: v)
r = run("o", ai_propose=True, facts="gpio82 boots output-low per the schematic\nhub: microchip,usb2514 at 0x2d (datasheet)\nGPIO_999 boots high\nthe board is the CCS20 variant")
fr = {f["n"]: f for f in r.ai_proposal["facts_report"]}
assert fr[1]["status"] == "applied by Python" and "gpio82" in fr[1]["note"]
assert fr[2]["status"] == "NOT used" and "I2C controller label" in fr[2]["note"], fr[2]
assert fr[3]["status"] == "NOT applied" and "gpio999" in fr[3]["note"], fr[3]
assert fr[4]["status"] == "context only"
md = open(f"{tmp}/o.md").read()
assert "What happened to each fact you gave" in md and "| 2 |" in md and "NOT used" in md
configure(); mock.reply = replying(lambda v, b: v + HUB)           # the AI DOES place the hub once the facts supply the bus
r = run("o2", ai_propose=True, facts="The hub is a microchip,usb2514 at 0x2d on the bus with node label &i2c18 (datasheet)")
assert r.ai_proposal["facts_report"][0]["status"] == "used by the AI", r.ai_proposal["facts_report"]
print("OK")

print("=== 14b. A level a fact has settled is no longer listed as an open decision; the ledger counts applied facts ===")
configure(); mock.reply = replying(lambda v, b: v)
r = run("o3", ai_propose=True, facts="gpio82 boots output-low per the schematic")
oi = " ".join(r.ai_proposal["open_items"])
assert "gpio82" not in oi.split("have no level yet")[-1].split(" - the Excel")[0] if "have no level yet" in oi else True, oi
assert not any("gpio82" in i and ("have no level yet" in i or "INFERRED" in i) for i in r.ai_proposal["open_items"]), r.ai_proposal["open_items"]
md = open(f"{tmp}/o3.md").read()
assert "1 fact(s) applied by Python, 0 used by the AI" in md and "0 fact(s) used" not in md
print("OK")

from app.usb_ai_proposer import _fact_text_only as _fto
assert _fto('Switch is enabled on USB0_PWR_EN. (from d.txt: "enabled by a high level on USB0_PWR_EN (GPIO_11).")') == "Switch is enabled on USB0_PWR_EN."
print("OK fact source note with parentheses is stripped whole")

print("=== 15. HYBRID COMBINED VIEW: changed nodes replaced in place, AI additions in their own section ===")
configure(); mock.reply = replying(lambda v, b: v)
py_cmb = run("p15", combined=f"{tmp}/p15_cmb.dts")
assert py_cmb.ai_proposal is None
r = run("q15", combined=f"{tmp}/q15_cmb.dts", ai_propose=True, facts="gpio82 boots output-low per the schematic")
ap = r.ai_proposal
hc = ap["final_combined_text"]
py_text = open(f"{tmp}/q15_cmb.dts").read()
assert hc and ap["combined_changes"] == 1 and os.path.exists(f"{tmp}/q15_cmb.hybrid.dts")
assert "HYBRID: updated from your facts" in hc and "output-low;  /* source: FACT 1 */" in hc
sec2a = hc.split("2a. Without hub")[1].split("2b. With hub")[0]
assert "output-low;  /* source: FACT 1 */" in sec2a and "4. AI-ADDED" not in hc, "nothing new -> no section 4"
assert hc.count("{") == hc.count("}")
assert "source: FACT 1" not in py_text and "HYBRID" not in py_text, "the Python Combined View file itself is untouched"
assert py_text == open(f"{tmp}/p15_cmb.dts").read(), "identical to the Python-only run's Combined View"
assert T.run_dtc("/* 2a." + hc.split("/* 2a.", 1)[1])[0] in (True, None), "the pinctrl sections of the hybrid view compile"
# new material from the AI goes to its own section 4
configure(); mock.reply = replying(lambda v, b: v + HUB)
r = run("q15b", combined=f"{tmp}/q15b_cmb.dts", ai_propose=True, facts="The hub is a microchip,usb2514 at 0x2d on the bus with node label &i2c18 (datasheet)")
hc2 = r.ai_proposal["final_combined_text"]
assert "4. AI-ADDED AND VERIFIED" in hc2 and 'compatible = "microchip,usb2514";' in hc2.split("4. AI-ADDED")[1]
assert 'compatible = "microchip,usb2514"' not in hc2.split("4. AI-ADDED")[0], "it is NOT mixed into Python's sections"
# no change -> None, not a copy of the Python view
configure(); mock.reply = replying(lambda v, b: v)
r = run("q15c", combined=f"{tmp}/q15c_cmb.dts", ai_propose=True)
assert r.ai_proposal["final_combined_text"] is None and r.ai_proposal["combined_changes"] == 0
print("OK")

print("=== 16. NO reference file: hybrid mode completes the output with the ACCEPTED-CHANGE template (your three screenshots) ===")
SHOTS = {                       # whitespace/comments stripped; exactly what the three screenshots show
 "usb0 regulator": 'usb0_vbus_regulator:usb0_vbus_regulator{compatible="regulator-fixed";regulator-name="usb0_vbus_regulator";regulator-min-microvolt=<5000000>;regulator-max-microvolt=<5000000>;gpio=<&tlmm56GPIO_ACTIVE_LOW>;};',
 "usb1 regulator": 'usb1_vbus_regulator:usb1_vbus_regulator{compatible="regulator-fixed";regulator-name="usb1_vbus_regulator";regulator-min-microvolt=<5000000>;regulator-max-microvolt=<5000000>;enable-active-high;gpio=<&tlmm55GPIO_ACTIVE_HIGH>;};',
 "usbv2_0_power_enable": 'usbv2_0_power_enable:usbv2_0_power_enable{mux{pins="gpio56";function="gpio";};config{pins="gpio56";drive-strength=<8>;output-high;bias-disable;};};',
 "usbv2_1_power_enable": 'usbv2_1_power_enable:usbv2_1_power_enable{mux{pins="gpio55";function="gpio";};config{pins="gpio55";drive-strength=<8>;output-low;bias-disable;};};',
 "&usb0": '&usb0{status="okay";vbus_dwc3-supply=<&usb0_vbus_regulator>;pinctrl-names="default";pinctrl-0=<&usbv2_0_power_enable>;qcom,default-mode-host;};',
 "&usb1": '&usb1{status="okay";vbus_dwc3-supply=<&usb1_vbus_regulator>;pinctrl-names="default";pinctrl-0=<&usbv2_1_power_enable>;',
}
nrm = lambda x: re.sub(r"\s+", "", re.sub(r"/\*.*?\*/", "", x, flags=re.S))
configure(); mock.reply = replying(lambda v, b: v)
auto = dict(excel=HONDA_XL, project="Suzuki_DA3_9444h02", pattern="auto")
py_only = run("t0", **auto)
assert "regulator-fixed" not in py_only.out_text and "&usb0 {" not in py_only.out_text, "Python-only stays evidence-based: no assumption"
r = run("t16", combined=f"{tmp}/t16_cmb.dts", ai_propose=True, **auto)
ap = r.ai_proposal
fin = nrm(ap["final_text"])
for name, exp in SHOTS.items():
    assert exp in fin, f"{name}: the template-completed output differs from the screenshot"
assert ap["template_assumed"] is True and "PATTERN ASSUMED" in open(f"{tmp}/t16.md").read()
assert ap["status"] == "clean" and ap["verification"]["wrong"] == 0, (ap["status"], ap["open_findings"])
assert r.out_text == py_only.out_text, "the Python-only output is untouched"
hc = ap["final_combined_text"]
assert hc and "3. USB VBUS FIXED REGULATORS" in hc and "vbus_dwc3-supply = <&usb0_vbus_regulator>;" in hc and "usb1_vbus_regulator" in hc
assert T.run_dtc(ap["final_text"])[0] in (True, None), "the template-completed final file compiles"
items = " | ".join(ap["open_items"])
assert "INFERRED" in items and "hub IC" in items and "&usb1" in items, items
print("OK")

print("=== 17. AI unavailable: Python's template completion is STILL delivered, and the report says the AI was not used ===")
for k in [k for k in os.environ if k.startswith("LLM_")]:
    del os.environ[k]
mock.requests.clear()
r = run("t17", ai_propose=True, **auto)
ap = r.ai_proposal
assert not mock.requests and ap["status"] == "clean" and ap["ai_note"] and "no LLM is configured" in ap["ai_note"]
assert all(exp in nrm(ap["final_text"]) for exp in SHOTS.values()) and os.path.exists(f"{tmp}/t17.ai.dtsi")
assert "The AI wrote none of this output" in open(f"{tmp}/t17.md").read()
print("OK")

print("=== 18. CCS20 (SA8155P): the style of ITS accepted example (controller) is used; the usb1 VBUS gap is LISTED with pin, net and action ===")
configure(); mock.reply = replying(lambda v, b: v)
r = run("t18", ai_propose=True, pattern="auto")
ap = r.ai_proposal
items = ap["open_items"]
usb1 = next(i for i in items if i.startswith("&usb1"))
assert "no VBUS-enable pin" in usb1 and "GPIO_92" in usb1 and "USB_HUB_I2C_EN" in usb1 and "--vbus-pin" in usb1, usb1
fin18 = ap["final_text"]
assert ap["template_pattern"] == "controller" and "CCS20" in ap["template_source"] and ap["template_same_soc"] is True
assert "compatible = \"regulator-fixed\"" not in fin18, "SA8155P's accepted example is controller style, not regulators"
usb0_18 = fin18.split("&usb0 {")[1].split("\n};")[0]
assert "oc-gpio = <&tlmm 81 GPIO_ACTIVE_LOW>;" in usb0_18 and "vbus-out-gpio = <&tlmm 82 GPIO_ACTIVE_HIGH>;" in usb0_18 and "dwc3@a600000" in usb0_18
usb1_18 = fin18.split("&usb1 {")[1].split("\n};")[0]
assert "oc-gpio = <&tlmm 102 GPIO_ACTIVE_LOW>;" in usb1_18 and "vbus-out-gpio" not in usb1_18, "usb1 is wired for its over-current pin only: no guessed VBUS pin"
assert "dwc3@a600000" not in usb1_18, "the SA8155P unit address is confirmed for usb0 only"
# NOTHING the Python-only output has may be missing from the template-completed hybrid: every pinctrl state carries over
py_states = set(re.findall(r"^\t(\w+): \1 \{", base_run.out_text.replace("\r", ""), re.M))
hy_states = set(re.findall(r"^\t(\w+): \1 \{", ap["final_text"], re.M))
assert py_states and py_states <= hy_states, f"states lost by the template completion: {py_states - hy_states}"
assert {"usbv3_fault_detect", "usbv2_fault_detect"} <= hy_states, "the over-current states must be present"
alltxt = " | ".join(items)
assert "Over-current pin(s)" not in alltxt, "in controller style the over-current pins ARE wired (oc-gpio): nothing to decide there"
assert "Shared I2C bus pin(s) GPIO_23, GPIO_24" in alltxt and "Boot-strap pin(s) GPIO_136" in alltxt, alltxt
assert "GPIO_38" in alltxt and "GPIO_113" in alltxt, "generated-but-unreferenced states are listed"
print("OK")

print("=== 18b. With GPIO_92 decided by the engineer, usb1 gets its vbus-out-gpio and the hybrid passes ===")
configure(); mock.reply = replying(lambda v, b: v)
r = run("t18b", ai_propose=True, pattern="auto", vbus_pin=["GPIO_92"])
ap = r.ai_proposal
assert "vbus-out-gpio = <&tlmm 92 GPIO_ACTIVE_HIGH>;" in ap["final_text"].split("&usb1 {")[1] and "regulator-fixed" not in ap["final_text"]
assert ap["status"] == "clean" and ap["verification"]["ambiguous"] == 0, (ap["status"], ap["open_findings"])
assert not any(i.startswith("&usb1") and "no VBUS-enable pin" in i for i in ap["open_items"]), "the decided gap is no longer listed"
print("OK")

print("=== 18c. Pins that matched 'usb' only through a boot-strap signal are LISTED (with their real function) and the regulator block is indented ===")
configure(); mock.reply = replying(lambda v, b: v)
rh = run("t18c", excel=HONDA_XL, project="Honda_GenID_B0", ai_propose=True, pattern="auto")
items_h = " | ".join(rh.ai_proposal["open_items"])
assert "GPIO_101 matched 'usb' only through a boot-strap signal" in items_h and "CPU_WLAN_PCIE_CTRL_1V8:NRESET_B" in items_h, items_h
reg_lines = [l for l in rh.ai_proposal["final_text"].split("/ {\n")[1].splitlines() if l.strip() and l.strip() != "};"]
assert reg_lines and all(l.startswith("\t") for l in reg_lines), "regulator nodes inside '/ { }' are indented"
print("OK")

print("=== 18d. Net / remark evidence is shown for each level decision - facts only, never a verdict on the driven level ===")
from app import pin_hints
# the exact trace text of a real active-low VBUS enable (pull-up to a rail) and of a pull-down to GND
tr_up = "D5700(GPIO_56) CPU_USB20_0_CTRL_1V8:VBUS_PWR_EN_B, >> R5750(2.2k) PWR:U18S_IO_SOCPMIC, >> ...,  MP8102=(1) ENDPNT,  V8100=undefined(B) ENDPNT"
assert pin_hints.pulls(tr_up) == [{"ref": "R5750", "value": "2.2k", "kind": "pull-up", "target": "PWR:U18S_IO_SOCPMIC"}]
assert pin_hints.pulls("S50000(GPIO_52) x,_x000D_ R5529(10k) GND, >> y R8754(10k) GND") == [
    {"ref": "R5529", "value": "10k", "kind": "pull-down", "target": "GND"}, {"ref": "R8754", "value": "10k", "kind": "pull-down", "target": "GND"}]
assert pin_hints.pulls("R8113=10k(2) z04.z05.SIGN150, R8108=10k(1) z04.z05.SIGN00178") == [], "a resistor to an internal net is not a pull"
e_low = pin_hints.level_evidence(tr_up, "PD during reset", active_low=True)
assert "2.2k pull-up (R5750) to PWR:U18S_IO_SOCPMIC" in e_low and 'Excel remark "PD during reset"' in e_low and "idles high = the enable is disabled" in e_low
e_high = pin_hints.level_evidence("R5753(4.7k) PWR:U18S_IO_SOCPMIC", "", active_low=False)
assert "idles high = the enable is enabled" in e_high, "an active-high enable with a pull-up is ENABLED while undriven - stated, not judged"
assert "idles" not in pin_hints.level_evidence(tr_up + " R9(1k) GND", "", active_low=True), "mixed pull-up and pull-down: no conclusion"
assert "idles" not in pin_hints.level_evidence(tr_up, "", active_low=None) and "supply" not in pin_hints.level_evidence(tr_up, "", active_low=None), "not an enable: facts only"
assert "output-high = supply ON, output-low = supply OFF" in pin_hints.level_evidence("", "", active_low=False, reset_value="0: low"), "active-high: high turns the supply ON"
assert pin_hints.level_evidence("", "", True) is None
configure(); mock.reply = replying(lambda v, b: v)
rh2 = run("t18d", excel=HONDA_XL, project="Honda_GenID_B0", ai_propose=True, pattern="auto")
ev = next(i for i in rh2.ai_proposal["open_items"] if i.startswith("Evidence in the workbook for those levels"))
assert re.search(r"gpio56: Excel remark \"PD during reset\" - Excel ResetValue \"0: low\" - 2\.2k pull-up \(R5750\) to PWR:U18S_IO_SOCPMIC - undriven, the line idles high = the enable is disabled - driven at boot: output-high = supply OFF, output-low = supply ON", ev), ev
rv_item = next(i for i in rh2.ai_proposal["open_items"] if i.startswith("About the Excel's ResetValue column"))
assert re.search(r"says '0: low' on \d+ of \d+ pins", rv_item) and "register's reset default" in rv_item, rv_item
assert not any("has no level column" in i for i in rh2.ai_proposal["open_items"]), "the inaccurate wording is gone"
assert re.search(r"gpio55: .*4\.7k pull-up \(R5753\).*idles high = the enable is enabled", ev), ev
assert "consistent" not in ev and "conflict" not in ev, "no verdict on the driven level"
print("OK")

print("=== 19. A pattern the user chose (or evidence) is NEVER overridden by the template ===")
configure(); mock.reply = replying(lambda v, b: v)
r = run("t19", ai_propose=True, pattern="controller")
assert r.ai_proposal["template_assumed"] is False and "PATTERN ASSUMED" not in open(f"{tmp}/t19.md").read()
assert "oc-gpio" in r.ai_proposal["final_text"] and "regulator-fixed" not in r.ai_proposal["final_text"]
print("OK")

print("=== 20. The AI is called only when it has something to complete; with nothing it is skipped and the report says why ===")
configure(); mock.reply = replying(lambda v, b: v)
r = run("t20a", ai_propose=True, pattern="auto", excel=HONDA_XL, project="Honda_GenID_B0")
assert not mock.requests, "no facts, documents or draft: no AI call (no tokens, no risk of invented lines)"
ap = r.ai_proposal
assert ap["status"] == "clean" and "nothing for the AI to complete" in ap["ai_note"] and ap["final_text"]
assert "The AI wrote none of this output" in (_t := open(f"{tmp}/t20a.md").read()) and "nothing for the AI to complete" in _t
r = run("t20b", ai_propose=True, pattern="auto", excel=HONDA_XL, project="Honda_GenID_B0", facts="context: this board has two USB ports")
assert mock.requests and r.ai_proposal["ai_note"] is None, "a fact gives the AI a job"
print("OK")

print("=== 21. The wiring style per SoC is DATA (reference_templates.json): same SoC -> its example, unknown SoC -> default, flagged weaker ===")
assert T._reference_template("SA8155P") == {"pattern": "controller", "source": "the accepted CCS20 example (SA8155P)", "same_soc": True}
assert T._reference_template("sa6155p")["pattern"] == "regulator" and T._reference_template("SA6155P")["same_soc"] is True
unk = T._reference_template("SA7255P")
assert unk["pattern"] == "regulator" and unk["same_soc"] is False
# an SoC with no entry falls back to the default and the report says the evidence is weaker (driven through a real run)
nomatch = os.path.join(tmp, "tmpl_none.json")
json.dump({"templates": [], "default": {"pattern": "regulator", "source": "some example on another SoC"}}, open(nomatch, "w"))
os.environ["USB_REFERENCE_TEMPLATES"] = nomatch
try:
    configure(); mock.reply = replying(lambda v, b: v)
    rn = run("t21n", ai_propose=True, pattern="auto", excel=HONDA_XL, project="Honda_GenID_B0")
finally:
    del os.environ["USB_REFERENCE_TEMPLATES"]
assert rn.ai_proposal["template_same_soc"] is False
assert "DIFFERENT SoC than the example, so the evidence is weaker" in open(f"{tmp}/t21n.md").read()
assert "DIFFERENT SoC" not in open(f"{tmp}/t16.md").read(), "an SoC with its own example gets no weakness note"
# editing the file changes the behaviour with no code change
custom = os.path.join(tmp, "tmpl.json")
json.dump({"templates": [{"soc": "SA6155P", "pattern": "controller", "source": "a custom example"}], "default": {"pattern": "regulator", "source": "d"}}, open(custom, "w"))
os.environ["USB_REFERENCE_TEMPLATES"] = custom
try:
    configure(); mock.reply = replying(lambda v, b: v)
    rc = run("t21", ai_propose=True, pattern="auto", excel=HONDA_XL, project="Honda_GenID_B0")
finally:
    del os.environ["USB_REFERENCE_TEMPLATES"]
assert rc.ai_proposal["template_pattern"] == "controller" and "a custom example" in rc.ai_proposal["template_source"]
assert "oc-gpio" in rc.ai_proposal["final_text"] and "regulator-fixed" not in rc.ai_proposal["final_text"]
assert "style of a custom example" in open(f"{tmp}/t21.md").read()
print("OK")

print("=== 11. Web API: the proposal travels beside the verified output and cannot change it; a draft can be uploaded ===")
from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)
configure(); mock.reply = replying(lambda v, b: v)
wb = client.post("/api/workbook/upload", files={"file": ("w.xlsx", open(XL, "rb"))}).json()["workbook_id"]
assert client.post(f"/api/workbook/{wb}/build").status_code == 200
url = f"/api/workbook/{wb}/usb-tool/update-or-generate"
paths = {r.path for r in app.routes}
assert not any("usb-consumer" in p for p in paths), "the old USB consumer endpoint is gone (the USB engine replaces it)"
assert url.replace(wb, "{workbook_id}") in paths and any(p.endswith("/usb-tool/verify") for p in paths) and any(p.endswith("/usb-tool/projects") for p in paths)
plain = client.post(url, data={"project": "CCS20", "pattern": "controller"}).json()
assert plain["ai_proposal"] is None and not mock.requests, "opt-in: nothing is sent unless asked"
j = client.post(url, data={"project": "CCS20", "pattern": "controller", "ai_propose": "true",
                           "facts": "usbv3_power_enable (gpio82) boots output-low according to the schematic"}).json()
assert j["out_text"] == plain["out_text"] and j["summary"] == plain["summary"], "the verified output must be identical"
ap = j["ai_proposal"]
assert ap["status"] == "clean" and "output-low;" in ap["text"] and "path" not in ap and KEY not in json.dumps(j)
assert "python_only_text" not in ap and ap["facts_report"][0]["status"] == "applied by Python"
assert ap["template_assumed"] is False and isinstance(ap["open_items"], list) and ap["open_items"]
assert ap["final_combined_text"] and "HYBRID: updated from your facts" in ap["final_combined_text"] and ap["combined_changes"] == 1
assert "## Hybrid output" in j["report_text"] and ap["final_text"]
# THE WEB DEFAULT for where an output level comes from is "convention" - a request that does not say gets no silent Excel levels
dflt = client.post(url, data={"project": "CCS20", "pattern": "regulator", "vbus_pin": ["GPIO_92"]}).json()["out_text"]
assert "level inferred as 'regulator OFF at boot'" in dflt and "source: Excel ResetValue" not in dflt, "web default must be convention"
exl = client.post(url, data={"project": "CCS20", "pattern": "regulator", "vbus_pin": ["GPIO_92"], "level_source": "excel"}).json()["out_text"]
assert "source: Excel ResetValue" in exl and "level inferred" not in exl, "excel only when it is asked for"
mock.requests.clear(); mock.reply = replying(lambda v, b: v)
j2 = client.post(url, data={"project": "CCS20", "pattern": "controller"},
                 files={"ai_draft": ("copilot_draft.dts", open(STALE_DRAFT, "rb"))}).json()
assert j2["ai_proposal"]["rounds"][0]["source"] == "your draft" and j2["ai_proposal"]["rounds"][0]["problems"] >= 8
assert j2["out_text"] == plain["out_text"]
mock.requests.clear(); mock.reply = doc_reply
j4 = client.post(url, data={"project": "CCS20", "pattern": "controller"},
                 files=[("docs", ("hub_notes.md", open(DOC, "rb")))]).json()
assert len(j4["ai_proposal"]["facts_extracted"]) == 1 and j4["ai_proposal"]["facts_rejected"] == 2 and j4["out_text"] == plain["out_text"]
for k in [k for k in os.environ if k.startswith("LLM_")]:
    del os.environ[k]
j3 = client.post(url, data={"project": "CCS20", "pattern": "controller", "ai_propose": "true"}).json()
assert j3["ai_proposal"]["status"] == "unavailable" and j3["out_text"] == plain["out_text"]
print("OK")

mock.stop()
print("\nUSB AI PROPOSER TEST PASSED")
