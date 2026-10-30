"""
Tests the AI SUGGESTION layer (app/usb_ai_assist.py): role suggestions and the completeness audit.

What matters most: the AI can only SUGGEST. Python decides when to ask, validates every answer (pin, verbatim quote, workbook direction,
free slot), and nothing is applied until a person confirms it - the generated DTS is identical with and without the suggestions.
Iveco-specific checks run when IVECO_XL points at that workbook (customer data, not shipped); the validators are tested without it.
"""
import json, os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, ROOT); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(ROOT, "tool"))
import sys as _s, os as _o; _s.path.insert(0, _o.path.join(_o.path.dirname(_o.path.abspath(__file__)), '..', 'scripts')); import _samples; _samples.need(__file__)
from _mock_llm import MockLLM
from app import usb_ai_assist as S
import usb_dts_tool as T

XL = os.path.join(ROOT, "samples", "Nissan_CCS20_SA8155PSIP_PinMux.xlsx")
HONDA = os.path.join(ROOT, "samples", "Honda_database_v2.xlsx")
IV = os.environ.get("IVECO_XL")
IV = IV if IV and os.path.exists(IV) else None
mock = MockLLM(); base = mock.start()
tmp = tempfile.mkdtemp()
KEY = "SECRET-KEY-123"
OLD_VOCAB = os.path.join(tmp, "old_vocab.json")
json.dump({"oc": ["(^|_)OC(_|$)"], "vbus": ["PWR_EN", "VBUS"]}, open(OLD_VOCAB, "w"))      # the words Python knew BEFORE FAULT / EXTERNAL_EN were added


def configure(on=True):
    for k in [k for k in os.environ if k.startswith("LLM_")]:
        del os.environ[k]
    mock.requests.clear(); mock.script.clear(); mock.reply = None
    if on:
        os.environ.update(LLM_BASE_URL=base, LLM_API_KEY=KEY, LLM_DEPLOYMENT="gpt-4o-mini-test", LLM_TIMEOUT="5")


class NS:
    def __init__(s, **k):
        d = dict(project=None, dts=None, out=None, report=None, pattern="auto", overlay=None, insert_after=None, vbus_pin=[], role=[],
                 include_pin=[], exclude_pin=[], inventory=None, combined=None, no_cache=False, no_update=False, keyword="usb", match="both",
                 list_projects=False, ai_advice=False, ai_max_pins=12, excel=None, ai_propose=False, ai_suggest=False, facts="",
                 ai_draft=None, ai_proposal_out=None, docs=[], level_source="convention")
        d.update(k); s.__dict__.update(d)


def run(tag, **k):
    return T.run(NS(out=f"{tmp}/{tag}.dtsi", report=f"{tmp}/{tag}.md", **k))


def cand(**o):
    c = {"pin": "GPIO_70", "signal": "CPU_USB31_EDL_CTRL_1V8:USB_FAULT_B", "tail": "USB_FAULT_B", "instance": "USB31",
         "free_roles": ["oc", "vbus"], "direction": "0: IN", "interface_group": "USB31_EDL", "remark": ""}
    c.update(o)
    return c


print("=== 1. a role suggestion is accepted only if it survives every Python check ===")
by = {"GPIO_70": cand(), "GPIO_75": cand(pin="GPIO_75", signal="CPU_USB31_EDL_CTRL_1V8:5V0S_USB_EXTERNAL_EN", tail="5V0S_USB_EXTERNAL_EN", direction="1: OUT"),
      "GPIO_9": cand(pin="GPIO_9", direction=""), "GPIO_8": cand(pin="GPIO_8", free_roles=["vbus"])}
ok = S.validate_role({"pin": "GPIO_70", "role": "oc", "quote": "USB_FAULT_B", "reason": "fault"}, by)
assert ok["status"] == "accepted" and ok["role_arg"] == "USB_FAULT_B=oc", ok
assert S.validate_role({"pin": "GPIO_70", "role": "oc", "quote": "usb_fault_b"}, by)["status"] == "accepted", "quote check ignores case"
assert S.validate_role({"pin": "GPIO_70", "role": "oc", "quote": "OVERCURRENT_PROTECT"}, by)["why"] == "the quote is not in the signal name"
assert S.validate_role({"pin": "GPIO_70", "role": "oc", "quote": ""}, by)["status"] == "rejected", "no quote, no acceptance"
assert S.validate_role({"pin": "GPIO_999", "role": "oc", "quote": "x"}, by)["why"] == "not one of the pins that were sent"
cf = S.validate_role({"pin": "GPIO_70", "role": "vbus", "quote": "USB_FAULT_B"}, by)
assert cf["status"] == "conflict" and "INPUT" in cf["why"] and cf["role_arg"] == "USB_FAULT_B=vbus", cf        # offered to a person, never auto-applied
cf2 = S.validate_role({"pin": "GPIO_75", "role": "oc", "quote": "USB_EXTERNAL_EN"}, by)
assert cf2["status"] == "conflict" and "OUTPUT" in cf2["why"] and cf2["role_arg"].endswith("=oc"), cf2
assert S.validate_role({"pin": "GPIO_70", "role": "vbus", "quote": "NOT_IN_THE_NAME"}, by)["status"] == "rejected", "a conflict still needs a real quote"
assert S.validate_role({"pin": "GPIO_8", "role": "oc", "quote": "USB_FAULT_B"}, by)["why"] == "USB31 already has a oc pin", "the slot is taken"
assert S.validate_role({"pin": "GPIO_70", "role": "banana", "quote": "USB_FAULT_B"}, by)["status"] == "rejected"
assert S.validate_role({"pin": "GPIO_70", "role": "other", "quote": ""}, by)["status"] == "other"
nd = S.validate_role({"pin": "GPIO_9", "role": "oc", "quote": "USB_FAULT_B"}, by)
assert nd["status"] == "accepted" and "direction is not stated" in nd["why"], "unknown direction: accepted but flagged"
print("OK")

print("=== 2. audit verdicts: a quote must literally be in the text Python sent ===")
byp = {"GPIO_77": {"pin": "GPIO_77", "text": "CPU_DMS_LED_DRV_CTRL_1V8:IRLED_FAULT_B | LED_DRV | net: USBHUB_DFP1:USBH_PPON"}}
assert S.validate_verdict({"pin": "GPIO_77", "verdict": "usb_gpio", "quote": "USBH_PPON"}, byp)["status"] == "include"
assert S.validate_verdict({"pin": "GPIO_77", "verdict": "usb_gpio", "quote": "USB3_VBUS_EN"}, byp)["why"].startswith("the quote is not in")
assert S.validate_verdict({"pin": "GPIO_77", "verdict": "usb_gpio", "quote": ""}, byp)["status"] == "rejected"
assert S.validate_verdict({"pin": "GPIO_77", "verdict": "not_usb", "quote": ""}, byp)["status"] == "not_usb"
assert S.validate_verdict({"pin": "GPIO_77", "verdict": "maybe", "quote": ""}, byp)["status"] == "unclear"
assert S.validate_verdict({"pin": "GPIO_1", "verdict": "usb_gpio", "quote": "x"}, byp)["why"] == "not one of the pins that were sent"
print("OK")

print("=== 3. nothing to ask -> only the data check is sent (Honda and CCS20 already have every role; no unmatched GPIO mentions USB) ===")
configure(); mock.reply = lambda b: "{}"
for xl, proj in ((HONDA, "Honda_GenID_B0"), (HONDA, "Suzuki_DA3_9444h02")):
    r = run("n_" + proj, excel=xl, project=proj, ai_suggest=True)
    assert r.ai_suggest["roles"]["status"] == "none", (proj, r.ai_suggest["roles"])
    assert r.ai_suggest["audit"]["status"] == "none" and r.ai_suggest["audit"]["pads"], "pads are still listed, deterministically"
# roles / audit send nothing when Python has nothing to ask; the DATA CHECK is the one call that always runs (one per run)
assert len(mock.requests) == 2 and all("workbook_cells" in q["body"]["messages"][1]["content"] for q in mock.requests), "only the data check is sent"
print("OK")

print("=== 4. CCS20: the USBHUB sheet's pin that the Database sheet leaves unmatched is a candidate; the AI is asked once ===")
configure(False)
r0 = run("c0", excel=XL, project="CCS20", ai_suggest=True)
srcs = {c["pin"]: c for c in r0.ai_suggest["audit"]["candidates"]}
assert "GPIO_123" in srcs and "USB-named sheet" in srcs["GPIO_123"]["text"], list(srcs)
assert r0.ai_suggest["audit"]["status"] == "unavailable" and "no LLM is configured" in r0.ai_suggest["audit"]["note"]
print("OK")

if IV:
    os.environ["USB_ROLE_KEYWORDS"] = OLD_VOCAB          # names the vocabulary does not know: the AI's job
    print("=== 5. IVECO: Python narrows to 3 signals; the AI proposes; only answers that fit the workbook are accepted; nothing is applied ===")
    configure(False)
    plain = run("iv_plain", excel=IV, project="Iveco CDC B2", pattern="controller")
    off = run("iv_off", excel=IV, project="Iveco CDC B2", pattern="controller", ai_suggest=True)
    assert off.out_text == plain.out_text, "suggestions never change the output"
    rc = off.ai_suggest["roles"]["candidates"]
    assert [c["tail"] for c in rc] == ["USB_FAULT_B", "5V0S_USB_EXTERNAL_EN", "DEVICE_DET_B"] and all(c["free_roles"] == ["oc", "vbus"] for c in rc), rc
    assert {"USB1_HS_DM", "USB1_HS_DP"} <= {p["pin"] for p in off.ai_suggest["audit"]["pads"]}, "USB pads with no signal name are listed"
    assert [c["pin"] for c in off.ai_suggest["audit"]["candidates"]] == ["GPIO_77"]

    def good(body):
        sysm = body["messages"][0]["content"]
        if "classify USB-related GPIO signals" in sysm:
            return json.dumps({"suggestions": [
                {"pin": "GPIO_70", "role": "oc", "quote": "USB_FAULT_B", "reason": "fault of the port power switch"},
                {"pin": "GPIO_75", "role": "vbus", "quote": "5V0S_USB_EXTERNAL_EN", "reason": "enables the external 5V supply"},
                {"pin": "GPIO_86", "role": "other", "quote": "DEVICE_DET_B", "reason": "device detect"}]})
        return json.dumps({"verdicts": [{"pin": "GPIO_77", "verdict": "not_usb", "quote": "", "reason": "an IR LED driver fault"}]})
    configure(); mock.reply = good
    on = run("iv_on", excel=IV, project="Iveco CDC B2", pattern="controller", ai_suggest=True)
    assert len(mock.requests) == 2, "one call for roles, one for the audit"
    assert on.out_text == plain.out_text, "the Python output is byte-identical with the AI on"
    rows = {r["pin"]: r for r in on.ai_suggest["roles"]["rows"]}
    assert rows["GPIO_70"]["status"] == "accepted" and rows["GPIO_70"]["role_arg"] == "USB_FAULT_B=oc"
    # the AI reads GPIO_75 as a VBUS enable (right, by its name); the workbook says INPUT: a real contradiction, put to the person
    assert rows["GPIO_75"]["status"] == "conflict" and "INPUT" in rows["GPIO_75"]["why"] and rows["GPIO_75"]["role_arg"] == "5V0S_USB_EXTERNAL_EN=vbus", rows["GPIO_75"]
    assert rows["GPIO_86"]["status"] == "other"
    assert on.ai_suggest["audit"]["rows"][0]["status"] == "not_usb"
    rep = open(f"{tmp}/iv_on.md").read()
    assert "## AI suggestions to confirm - nothing here changed the output" in rep and "`--role USB_FAULT_B=oc`" in rep
    # confirming = the same as typing the roles: Python then wires them from the Excel
    applied = run("iv_applied", excel=IV, project="Iveco CDC B2", pattern="controller", role=[rows["GPIO_70"]["role_arg"]])
    u = applied.out_text.split("&usb0 {")[1]
    assert "oc-gpio = <&tlmm 70 GPIO_ACTIVE_LOW>;" in u and "vbus-out-gpio" not in u and not applied.role_warnings
    print("OK")

    print("=== 6. IVECO: a misbehaving AI is contained (wrong quote, wrong direction, duplicate claims, invented pin, garbage) ===")
    def bad(body):
        if "classify USB-related GPIO signals" in body["messages"][0]["content"]:
            return json.dumps({"suggestions": [
                {"pin": "GPIO_70", "role": "oc", "quote": "OVERCURRENT", "reason": "x"},                  # quote not in the signal
                {"pin": "GPIO_75", "role": "vbus", "quote": "5V0S_USB_EXTERNAL_EN", "reason": "x"},       # the workbook says INPUT
                {"pin": "GPIO_86", "role": "oc", "quote": "DEVICE_DET_B", "reason": "x"},                  # the workbook says OUTPUT
                {"pin": "GPIO_999", "role": "vbus", "quote": "x", "reason": "x"}]})                        # invented pin
        return "this is not json"
    configure(); mock.reply = bad
    b = run("iv_bad", excel=IV, project="Iveco CDC B2", pattern="controller", ai_suggest=True)
    st = {r["pin"]: (r["status"], r["why"]) for r in b.ai_suggest["roles"]["rows"]}
    assert st["GPIO_70"][0] == "rejected" and "quote is not in the signal name" in st["GPIO_70"][1]
    assert st["GPIO_75"][0] == "conflict" and "INPUT" in st["GPIO_75"][1]
    assert st["GPIO_86"][0] == "conflict" and "OUTPUT" in st["GPIO_86"][1] and st["GPIO_999"][0] == "rejected"
    assert not any(r["status"] == "accepted" for r in b.ai_suggest["roles"]["rows"]), "nothing the AI got wrong is auto-accepted: only conflicts, for a person"
    assert b.ai_suggest["audit"]["status"] == "error" and "usable JSON" in b.ai_suggest["audit"]["note"]
    assert b.out_text == plain.out_text
    def dup(body):
        if "classify USB-related GPIO signals" in body["messages"][0]["content"]:
            return json.dumps({"suggestions": [{"pin": "GPIO_70", "role": "oc", "quote": "USB_FAULT_B", "reason": "x"},
                                               {"pin": "GPIO_75", "role": "oc", "quote": "5V0S_USB_EXTERNAL_EN", "reason": "x"}]})        # both INPUTS, both quotes valid
        return json.dumps({"verdicts": []})
    configure(); mock.reply = dup
    d = run("iv_dup", excel=IV, project="Iveco CDC B2", pattern="controller", ai_suggest=True)
    assert all(r["status"] == "rejected" and "ambiguous" in r["why"] for r in d.ai_suggest["roles"]["rows"]), d.ai_suggest["roles"]["rows"]
    assert not any(r["role_arg"] for r in d.ai_suggest["roles"]["rows"]), "two claimants for one slot: neither is offered"
    print("OK")

    print("=== 6b. a role the PERSON sets is checked against the workbook too (warning, never a block) ===")
    configure(False)
    w = run("iv_warn", excel=IV, project="Iveco CDC B2", pattern="controller", role=["5V0S_USB_EXTERNAL_EN=vbus"])
    assert len(w.role_warnings) == 1 and "GPIO_75" in w.role_warnings[0] and "a VBUS enable should be an output" in w.role_warnings[0], w.role_warnings
    assert "vbus-out-gpio = <&tlmm 75" in w.out_text, "still generated: it is the engineer's decision, but it is stated"
    assert "**WARNING - role vs workbook:**" in open(f"{tmp}/iv_warn.md").read()
    for xl, proj in ((XL, "CCS20"), (HONDA, "Honda_GenID_B0"), (HONDA, "Suzuki_DA3_9444h02")):
        assert run("nw_" + proj, excel=xl, project=proj).role_warnings == [], proj + ": normal naming-based roles all fit the workbook"
    print("OK")

    print("=== 7. IVECO: --include-pin brings a confirmed pin in, with values from the Excel and no assumed role ===")
    configure(False)
    inc = run("iv_inc", excel=IV, project="Iveco CDC B2", pattern="controller", include_pin=["GPIO_77"])
    assert inc.included_pins == ["GPIO_77"] and 'pins = "gpio77"' in inc.out_text, inc.included_pins
    assert "Pins you chose to include (--include-pin): GPIO_77" in open(f"{tmp}/iv_inc.md").read()
    assert 'pins = "gpio77"' not in plain.out_text
    try:
        run("iv_inc_bad", excel=IV, project="Iveco CDC B2", include_pin=["GPIO_9999"])
        raise AssertionError("an unknown pin must stop with a message")
    except SystemExit as e:
        assert "no such pin" in str(e)
    print("OK")
    del os.environ["USB_ROLE_KEYWORDS"]

    print("=== 5b. IVECO with the DEFAULT vocabulary: Python itself knows FAULT_B = over-current and EXTERNAL_EN = VBUS enable ===")
    configure(); mock.reply = lambda b: "{}"
    dv = run("iv_default", excel=IV, project="Iveco CDC B2", pattern="controller", ai_suggest=True)
    assert "oc/vbus: 2  |  other GPIOs: 1" in open(f"{tmp}/iv_default.md").read(), "70 = oc, 75 = vbus, 86 stays unclassified"
    u = dv.out_text.split("&usb0 {")[1]
    assert "oc-gpio = <&tlmm 70 GPIO_ACTIVE_LOW>;" in u and "vbus-out-gpio = <&tlmm 75 GPIO_ACTIVE_HIGH>;" in u, u
    assert len(dv.role_warnings) == 1 and "GPIO_75" in dv.role_warnings[0], "the workbook's INPUT is still stated"
    n75 = dv.out_text.split('pins = "gpio75";')[2].split("};")[0]          # [1] is the mux block, [2] the config block
    assert "NEEDS REVIEW: treated as a VBUS enable (an output) but the workbook says this pin's direction is INPUT" in n75, n75
    assert dv.ai_suggest["roles"]["status"] == "none", "both slots are filled by name"
    assert not any("classify USB-related GPIO signals" in r["body"]["messages"][0]["content"] for r in mock.requests), "so the AI is not asked about roles"
    print("OK")
else:
    print("(Iveco checks 5-7 skipped: set IVECO_XL to run them)")

print("=== 7b. An EMPTY result is never reported as 'passed': nothing generated = nothing verified ===")
from app import usb_ai_proposer as P
configure(False)
stub = lambda text: {"wrong": [], "ambiguous": [], "unverifiable": [], "ok": 0, "dtc": (None, "dtc not installed"), "report_text": ""}
res_e = P.propose("", [], "", "unconfirmed", stub, allow_no_ai=True, skip_ai=True)
assert res_e["status"] == "empty", res_e["status"]
assert "NOTHING GENERATED" in "\n".join(P.render_report(dict(res_e, open_items=[]), None))
res_ok = P.propose("&tlmm {\n};\n", [], "", "unconfirmed", stub, allow_no_ai=True, skip_ai=True)
assert res_ok["status"] == "clean", "a result that has content and no findings is still 'clean'"
if IV:
    configure(); mock.reply = lambda b: "{}"
    b1 = run("iv_b1", excel=IV, project="Iveco CDC B1", ai_propose=True, ai_suggest=True)
    assert b1.ai_proposal["status"] == "empty" and b1.ai_proposal["path"] is None, b1.ai_proposal["status"]
    assert not os.path.exists(f"{tmp}/iv_b1.hybrid.dtsi"), "no empty file is presented as a result"
    it = " | ".join(b1.ai_proposal["open_items"])
    assert "SAIL_CFG" in it and "SAIL_IO_" in it and "stay a manual item" in it, it
    assert b1.ai_suggest["roles"]["note"] == "there are no unclassified USB GPIO signals to classify", b1.ai_suggest["roles"]["note"]
    assert "Status: NOTHING GENERATED" in open(f"{tmp}/iv_b1.md").read()
assert run("h_note", excel=HONDA, project="Honda_GenID_B0", ai_suggest=True).ai_suggest["roles"]["note"].startswith("every controller instance already has")
print("OK")

print("=== 7c. the audit is honest: a sheet-only pin is a variant question, pads need a clearly USB word and are listed once ===")
sheet_c = {"pin": "GPIO_123", "text": "USB-named sheet: USBHUB: CPU_USBHUB_CTRL_1V8:USB_OC_B", "source": "USB-named sheet"}
v = S.validate_verdict({"pin": "GPIO_123", "verdict": "usb_gpio", "quote": "USB_OC_B"}, {"GPIO_123": sheet_c})
assert v["status"] == "variant", v                       # not "include": including it could not generate a node
assert S.validate_verdict({"pin": "GPIO_123", "verdict": "usb_gpio", "quote": "USB_OC_B"}, {"GPIO_123": dict(sheet_c, source="workbook text")})["status"] == "include"
configure(); mock.reply = lambda b: json.dumps({"verdicts": [{"pin": "GPIO_123", "verdict": "usb_gpio", "quote": "USB_OC_B", "reason": "x"}]})
cc = run("c_var", excel=XL, project="CCS20", ai_suggest=True)
assert [r["status"] for r in cc.ai_suggest["audit"]["rows"] if r["pin"] == "GPIO_123"] == ["variant"]
assert "only a USB-specific sheet says so" in open(f"{tmp}/c_var.md").read()
pad_names = [p["pin"] for p in cc.ai_suggest["audit"]["pads"]]
assert len(pad_names) == len(set(pad_names)), "each pad once: " + str(pad_names)
assert not any("FAULT" in n for n in pad_names), "a power-fault pad is not a USB pad: " + str(pad_names)
assert any(n.startswith("USB") or "USB" in n for n in pad_names), pad_names
print("OK")

print("=== 7d. WHERE THE AI WAS USED: every run lists all six AI features with the gateway's real calls and tokens ===")
configure()
def audit_reply(b):
    s = b["messages"][0]["content"]
    return json.dumps({"verdicts": []}) if "find USB-related GPIO pins" in s else (json.dumps({"suggestions": []}) if "classify USB-related" in s else "{}")
mock.reply = audit_reply
led = run("ledger", excel=XL, project="CCS20", ai_suggest=True, ai_advice=True)
rows = {r["feature"]: r for r in led.ai_ledger}
assert list(rows) == ["usb_advice", "usb_propose", "usb_suggest", "value_decoder", "node_extractor", "usb_check"], list(rows)
assert rows["usb_advice"]["status"] == "ran" and rows["usb_suggest"]["status"] == "ran"
assert rows["usb_propose"]["status"] == "not requested in this run"
assert all(rows[f]["switch"] == "off" and rows[f]["status"].startswith("OFF") for f in ("value_decoder", "node_extractor")), "the two older features are OFF by default - stated"
assert sum(r["calls"] for r in led.ai_ledger) == len(mock.requests) > 0, "the ledger's calls are the gateway's calls"
assert "## Where the AI was used in this run" in open(f"{tmp}/ledger.md").read()
configure(); mock.reply = audit_reply
nf = run("ledger2", excel=XL, project="CCS20", ai_propose=True)
pr = {r["feature"]: r for r in nf.ai_ledger}["usb_propose"]
assert pr["status"].startswith("skipped: nothing for the AI to complete") and pr["calls"] == 0, pr
configure(False)
na = run("ledger3", excel=XL, project="CCS20", ai_advice=True, ai_suggest=True)
assert {r["feature"]: r for r in na.ai_ledger}["usb_advice"]["status"].startswith("not used") and sum(r["calls"] for r in na.ai_ledger) == 0
assert all(r["switch"] == "off" for r in na.ai_ledger), "no gateway configured: every feature is off"
# Hybrid with the template ON (CCS20 completes with its template): the missed-pin audit is asked ONCE, and every call is booked once
for xl_, pj_ in ((XL, "CCS20"), (HONDA, "Honda_GenID_B0")):
    configure(); mock.reply = audit_reply
    both = run("ledger5_" + pj_, excel=xl_, project=pj_, ai_propose=True, ai_suggest=True)
    n_aud = sum(1 for q in mock.requests if "find USB-related GPIO pins" in q["body"]["messages"][0]["content"])
    n_all = len(mock.requests)
    assert n_aud <= 1, f"{pj_}: the audit reached the gateway {n_aud} times (the internal template run must not ask the AI)"
    assert sum(r["calls"] for r in both.ai_ledger) == n_all, f"{pj_}: ledger calls {[(r['feature'], r['calls']) for r in both.ai_ledger]} vs {n_all} real requests"
    assert {r["feature"]: r for r in both.ai_ledger}["usb_propose"]["calls"] == 0, "a skipped completion has no calls"
off = run("ledger4", excel=XL, project="CCS20")
assert off.ai_ledger == [] and "Where the AI was used" not in open(f"{tmp}/ledger4.md").read(), "no AI requested: no ledger noise"
print("OK")

print("=== 8. suggestions off by default: no AI call, no section, no change ===")
configure(); mock.reply = lambda b: "{}"
r = run("off", excel=XL, project="CCS20")
assert r.ai_suggest is None and not mock.requests and "AI suggestions to confirm" not in open(f"{tmp}/off.md").read()
print("OK")

print("=== 9. Web API carries the suggestions, accepts include_pins, and echoes what it applied ===")
from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)
configure(); mock.reply = lambda b: json.dumps({"verdicts": [{"pin": "GPIO_123", "verdict": "not_usb", "quote": "", "reason": "x"}]})
wb = client.post("/api/workbook/upload", files={"file": ("w.xlsx", open(XL, "rb"))}).json()["workbook_id"]
assert client.post(f"/api/workbook/{wb}/build").status_code == 200
url = f"/api/workbook/{wb}/usb-tool/update-or-generate"
plain = client.post(url, data={"project": "CCS20", "pattern": "controller"}).json()
assert plain["ai_suggest"] is None and plain["included_pins"] == [] and not mock.requests
j = client.post(url, data={"project": "CCS20", "pattern": "controller", "ai_suggest": "true"}).json()
assert j["out_text"] == plain["out_text"] and j["ai_suggest"]["audit"]["status"] == "ok" and KEY not in json.dumps(j)
j2 = client.post(url, data={"project": "CCS20", "pattern": "controller", "include_pins": ["GPIO_123"]})
assert j2.status_code == 200 and j2.json()["included_pins"] == ["GPIO_123"], j2.text[:200]
mock.stop()
print("OK")

print("\nUSB AI ASSIST TEST PASSED")
