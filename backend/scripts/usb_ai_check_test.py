"""
The AI DATA CHECK (app/usb_ai_assist.check): the AI reads each generated pin's own workbook cells and reports contradictions in the data
that WAS given. It may only report - Python verifies every quote and nothing in the generated DTS changes. Uses the fabricated demo
workbook in test_kit/ (GPIO_12's remark asks for a pull-down while its pull cell says PULL_UP), so it needs no customer data.
"""
import json, os, re, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.join(HERE, "..")
sys.path.insert(0, ROOT); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(ROOT, "tool"))
from _mock_llm import MockLLM
from app import usb_ai_assist as S
import usb_dts_tool as T

XL = os.path.join(ROOT, "..", "test_kit", "demo_workbook.xlsx")
if not os.path.exists(XL):
    print("SKIPPED usb_ai_check_test.py: test_kit/demo_workbook.xlsx not present"); sys.exit(0)
mock = MockLLM(); base = mock.start(); tmp = tempfile.mkdtemp()


def configure(on=True):
    for k in [k for k in os.environ if k.startswith("LLM_")]:
        del os.environ[k]
    mock.requests.clear(); mock.script.clear(); mock.reply = None
    if on:
        os.environ.update(LLM_BASE_URL=base, LLM_API_KEY="K", LLM_DEPLOYMENT="gpt-test", LLM_TIMEOUT="5")


class NS:
    def __init__(s, **k):
        d = dict(project="CCS20", dts=None, out=None, report=None, pattern="regulator", overlay=None, insert_after=None, vbus_pin=[], role=[],
                 include_pin=[], exclude_pin=[], inventory=None, combined=None, no_cache=True, no_update=False, keyword="usb", match="both",
                 list_projects=False, ai_advice=False, ai_max_pins=12, excel=XL, ai_propose=False, ai_suggest=False, facts="",
                 ai_draft=None, ai_proposal_out=None, docs=[], level_source="convention")
        d.update(k); s.__dict__.update(d)


def run(tag, **k):
    return T.run(NS(out=f"{tmp}/{tag}.dtsi", report=f"{tmp}/{tag}.md", **k))


print("=== 1. validator: every finding must name a sent pin and quote that pin's own cells ===")
by = {"GPIO_12": {"pin": "GPIO_12", "text": "Signal: USB1_OC_B | Pull: 3: PULL_UP | Remark: External pull-down required on this line"}}
good = S.validate_finding({"pin": "GPIO_12", "type": "pull", "quote_a": "3: PULL_UP", "quote_b": "External pull-down required", "issue": "pull-up vs pull-down"}, by)
assert good["status"] == "finding", good
for bad, why in (({"pin": "GPIO_99", "type": "pull", "quote_a": "x", "issue": "i"}, "not one of the pins"),
                 ({"pin": "GPIO_12", "type": "pull", "quote_a": "an invented quote", "issue": "i"}, "not in the workbook cells"),
                 ({"pin": "GPIO_12", "type": "pull", "quote_a": "PULL_UP", "quote_b": "invented", "issue": "i"}, "second quote"),
                 ({"pin": "GPIO_12", "type": "weird", "quote_a": "PULL_UP", "issue": "i"}, "unknown finding type"),
                 ({"pin": "GPIO_12", "type": "pull", "quote_a": "PULL_UP", "issue": ""}, "no description")):
    r = S.validate_finding(bad, by)
    assert r["status"] == "rejected" and why in r["why"], (bad, r)
print("OK")

print("=== 2. a real run: the finding is reported, the DTS is identical with and without the AI ===")
configure(on=False)
plain = run("plain")
configure()


def reply(body):
    user = body["messages"][1]["content"]
    assert "workbook_cells" in user and "GPIO_12" in user, "the pin's cells are sent"
    return json.dumps({"findings": [
        {"pin": "GPIO_12", "type": "pull", "quote_a": "PULL_UP", "quote_b": "External pull-down required", "issue": "the remark asks for a pull-down but the pull cell says PULL_UP"},
        {"pin": "GPIO_10", "type": "direction", "quote_a": "this text is invented", "quote_b": "", "issue": "made up"},
        {"pin": "GPIO_777", "type": "other", "quote_a": "x", "quote_b": "", "issue": "not a pin that was sent"}]})


mock.reply = reply
r = run("ai", ai_suggest=True)
assert r.out_text == plain.out_text, "the AI check must not change the generated DTS"
assert r.ai_check["status"] == "ok" and r.ai_check["checked"] >= 5, r.ai_check
ok = [x for x in r.ai_check["rows"] if x["status"] == "finding"]
assert [x["pin"] for x in ok] == ["GPIO_12"] and ok[0]["type"] == "pull", r.ai_check["rows"]
assert sum(x["status"] == "rejected" for x in r.ai_check["rows"]) == 2
md = open(f"{tmp}/ai.md").read()
assert "## Workbook data check" in md and "External pull-down required" in md and "2 AI finding(s) were dropped" in md, md[-1500:]
led = {x["feature"]: x for x in r.ai_ledger}
assert led["usb_check"]["calls"] == 1 and "1 verified finding(s), 2 dropped" in led["usb_check"]["effect"], led["usb_check"]
print("OK")

print("=== 3. clean data -> an empty answer is a good answer ===")
configure(); mock.reply = lambda body: json.dumps({"findings": []})
r = run("clean", ai_suggest=True)
assert r.ai_check["status"] == "ok" and not r.ai_check["rows"]
assert "No contradictions found" in open(f"{tmp}/clean.md").read()
print("OK")

print("=== 4. no LLM configured: Python result unchanged, the check says so ===")
configure(on=False)
r = run("off", ai_suggest=True)
assert r.out_text == plain.out_text and r.ai_check["status"] == "unavailable"
assert "pin(s) were not cross-checked" in open(f"{tmp}/off.md").read()
print("OK")

print("\nUSB AI DATA CHECK TEST PASSED")

# regression: consistent direction cells must not be reported (real gateway run produced 5 such false positives)
import app.usb_ai_assist as _S
for _sig, _d, _exp in (("USB20_0_OC_B", "0: IN", "rejected"), ("USB_VBUS_EN", "1: OUT", "rejected"), ("USB_VBUS_EN", "0: IN", "finding")):
    _c = {"pin": "P", "text": f"Signal: {_sig} | Direction: {_d}"}
    assert _S.validate_finding({"pin": "P", "type": "direction", "quote_a": "Direction: " + _d, "quote_b": "Signal: " + _sig, "issue": "i"}, {"P": _c})["status"] == _exp, (_sig, _d)
print("direction plausibility OK")
