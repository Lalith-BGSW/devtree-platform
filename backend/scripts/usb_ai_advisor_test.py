"""
Tests the AI advisory layer (app/usb_ai_advisor.py) and its integration with the USB tool, against a local mock
of the gateway. The invariant that matters most: the AI can only EXPLAIN - the generated DTS must be
byte-identical with and without it, and anything the AI states that is not in the workbook evidence is removed.
"""
import json, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, ROOT); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(ROOT, "tool"))
import sys as _s, os as _o; _s.path.insert(0, _o.path.join(_o.path.dirname(_o.path.abspath(__file__)), '..', 'scripts')); import _samples; _samples.need(__file__)
from _mock_llm import MockLLM
from app import llm_client as L, usb_ai_advisor as A
import usb_dts_tool as T

KEY = "SECRET-KEY-123"
mock = MockLLM(); base = mock.start()

def configure(**kw):
    for k in [k for k in os.environ if k.startswith("LLM_")]:
        del os.environ[k]
    os.environ.update(LLM_BASE_URL=base, LLM_API_KEY=KEY, LLM_DEPLOYMENT="gpt-4o-mini-test", LLM_TIMEOUT="3", **kw)
    mock.requests.clear(); mock.script.clear(); mock.reply = None

EV = {"pin": "GPIO_23", "why_the_tool_flagged_it": "Shared I2C bus pin - 4 devices on one bus",
      "signal_names": ["CPU_SUBPCB_I2C_1V8:I2C_SDA", "CPU_USBHUB_SMI2C_1V8:SMB_DATA"],
      "selected_interface": "I2C_QUP18, QUP18", "schematic_net_trace": "D5700(AJ20) CPU_SUBPCB_I2C_1V8:I2C_SDA,MP8207=MP(1) ENDPNT,X8190=245656060009829+(54) ENDPNT",
      "excel_remark": "mounting option only for EU/US subPCB"}

print("=== 1. guard: anything not in the evidence is removed ===")
g = A.guard({"situation": "shared_i2c_bus", "summary": "The hub sits at address 0x2d on this bus.", "evidence_used": [], "open_questions": []}, EV)
assert g["summary"] == A._REMOVED and g["removed"] == 1, g                      # invented I2C address
g = A.guard({"situation": "shared_i2c_bus", "summary": "Several devices share one I2C bus.", "evidence_used": [],
             "open_questions": ["Is the hub a microchip,usb2514?", "Which devices are populated on this variant?"]}, EV)
assert g["open_questions"] == ["Which devices are populated on this variant?"] and g["removed"] == 1   # invented vendor,model
g = A.guard({"situation": "shared_gpio_net", "summary": "Drive it output-high at boot.", "evidence_used": [], "open_questions": []}, EV)
assert g["summary"] == A._REMOVED                                                # output level not in the evidence
ev_lvl = dict(EV, excel_remark="check output-high at reset")
assert A.guard({"situation": "unclear", "summary": "Remark mentions output-high.", "evidence_used": [], "open_questions": []}, ev_lvl)["removed"] == 0   # but fine when the evidence says it
g = A.guard({"situation": "shared_i2c_bus", "summary": "ok", "evidence_used": ["X8190=245656060009829+(54)", "a quote nobody wrote"],
             "open_questions": []}, EV)
assert g["evidence_used"] == ["X8190=245656060009829+(54)"] and g["removed"] == 1      # invented quote dropped, real one kept
assert A.guard({"situation": "made_up_value", "summary": "x", "evidence_used": [], "open_questions": []}, EV)["situation"] == "unclear"
print("OK")

print("=== 2. parsing tolerates fences and chatter, rejects garbage ===")
assert A._parse('```json\n{"a": 1}\n```') == {"a": 1}
assert A._parse('Sure! {"a": 2} hope that helps') == {"a": 2}
assert A._parse("no json here") is None and A._parse("[1,2]") is None
print("OK")

print("=== 3. advise(): table, guard counting, usage line ===")
configure()
def reply(body):
    ev = json.loads(body["messages"][1]["content"].split("EVIDENCE:\n", 1)[1])
    if ev["pin"] == "GPIO_B":
        return json.dumps({"situation": "shared_i2c_bus", "summary": "Device is at 0x2d.", "evidence_used": [], "open_questions": []})
    return json.dumps({"situation": "shared_i2c_bus", "summary": "Several devices share this pin.",
                       "evidence_used": ["I2C_QUP18"], "open_questions": ["Which devices are fitted?"]})
mock.reply = reply
lines = A.advise([dict(EV, pin="GPIO_A"), dict(EV, pin="GPIO_B")])
txt = "\n".join(lines)
assert "## AI advisory - explanation only, NOT used to generate or change anything" in txt
assert "| GPIO_A | shared_i2c_bus | Several devices share this pin." in txt
assert "GPIO_B | shared_i2c_bus | " + A._REMOVED in txt, "invented address must be removed from the report"
assert "statements removed by the guard: 1" in txt and "calls: 2" in txt and "gpt-4o-mini-test" in txt
assert len(mock.requests) == 2 and "NEVER state or guess" in mock.requests[0]["body"]["messages"][0]["content"]
sysmsg = mock.requests[0]["body"]["messages"][0]["content"]
assert "schematic_net_trace" in sysmsg and "Do NOT ask what the tool already states" in sysmsg and "at most 2" in sysmsg
assert KEY not in txt
print("OK")

print("=== 4. failure modes are visible notes, never crashes ===")
configure(); mock.reply = lambda b: "not json at all"
assert "did not return usable JSON" in "\n".join(A.advise([EV]))
configure(); mock.script.append((200, {"choices": [{"message": {"content": json.dumps({"situation": "unclear", "summary": "s", "evidence_used": [], "open_questions": []})}}]}, {}))
mock.script.append((401, {"error": "no"}, {}))
t = "\n".join(A.advise([dict(EV, pin="P1"), dict(EV, pin="P2"), dict(EV, pin="P3")]))
assert "| P1 |" in t and "| P2 |" not in t and "stopped early" in t and "key rejected" in t and KEY not in t
configure(); t = "\n".join(A.advise([dict(EV, pin=f"P{i}") for i in range(5)], max_pins=2))
assert "3 further flagged pin(s) were not sent (limit 2)" in t and len(mock.requests) == 2
for k in [k for k in os.environ if k.startswith("LLM_")]:
    del os.environ[k]
mock.requests.clear()
t = "\n".join(A.advise([EV]))
assert "unavailable" in t and "no LLM is configured" in t and not mock.requests, "no key -> no network call, clear message"
assert "Nothing was flagged" in "\n".join(A.advise([]))
print("OK")

print("=== 5. END TO END on CCS20: the DTS is byte-identical with and without the AI ===")
XL = os.path.join(ROOT, "samples", "Nissan_CCS20_SA8155PSIP_PinMux.xlsx")
import tempfile
tmp = tempfile.mkdtemp()
class NS:
    def __init__(self, **k):
        d = dict(project="CCS20", dts=None, out=None, report=None, pattern="auto", overlay=None, insert_after=None,
                 vbus_pin=[], inventory=None, combined=None, no_cache=False, no_update=False, keyword="usb",
                 match="both", list_projects=False, ai_advice=False, ai_max_pins=12, excel=XL)
        d.update(k); self.__dict__.update(d)
configure()
def reply_e2e(body):
    ev = json.loads(body["messages"][1]["content"].split("EVIDENCE:\n", 1)[1])
    s = "Shared pin." if ev["pin"] != "GPIO_92" else "Probably the hub at 0x2d."          # one answer carries an invented address
    return json.dumps({"situation": "unclear", "summary": s, "evidence_used": [], "open_questions": ["Which variant is fitted?"]})
mock.reply = reply_e2e
off = T.run(NS(out=tmp + "/off.dtsi", report=tmp + "/off.md"))
n_before = len(mock.requests)
on = T.run(NS(out=tmp + "/on.dtsi", report=tmp + "/on.md", ai_advice=True))
assert n_before == 0, "without --ai-advice the tool must make NO LLM call"
assert off.out_text == on.out_text and open(tmp + "/off.dtsi").read() == open(tmp + "/on.dtsi").read(), "the AI must not change the DTS"
assert off.summary == on.summary and off.exit_code == on.exit_code
rep_on = open(tmp + "/on.md").read()
assert rep_on.startswith(open(tmp + "/off.md").read().rstrip("\n")[:2000]) or "## AI advisory" in rep_on
sec = rep_on.split("## AI advisory")[1]
for pin in ("GPIO_92", "GPIO_23", "GPIO_24"):
    assert f"| {pin} |" in sec, pin
assert "| GPIO_136 |" not in sec, "boot-strap pins are fully explained by Python and are not sent to the AI"
assert "GPIO_92 | unclear | " + A._REMOVED in sec, "the invented 0x2d must be removed"
assert len(mock.requests) == 3, "one call per pin that needs interpretation (boot-strap pins excluded)"
ev23 = next(json.loads(r["body"]["messages"][1]["content"].split("EVIDENCE:\n", 1)[1]) for r in mock.requests
            if '"GPIO_23"' in r["body"]["messages"][1]["content"])
assert "Shared I2C bus" in ev23["why_the_tool_flagged_it"] and "X8190" in ev23["schematic_net_trace"]
assert any("QUP18" in o.get("instance", "") for o in ev23["mux_options"]), "mux options are sent as evidence"
print("OK")

print("=== 6. Web API: status never leaks the key; the AI option is opt-in and cannot change the DTS ===")
from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)
configure(); mock.reply = reply_e2e
st = client.get("/api/ai/status").json()
assert st["configured"] and st["deployment"] == "gpt-4o-mini-test" and KEY not in json.dumps(st)
wb = client.post("/api/workbook/upload", files={"file": ("w.xlsx", open(XL, "rb"))}).json()["workbook_id"]
assert client.post(f"/api/workbook/{wb}/build").status_code == 200
url = f"/api/workbook/{wb}/usb-tool/update-or-generate"
a = client.post(url, data={"project": "CCS20"}).json()
assert not mock.requests and "## AI advisory" not in a["report_text"], "AI is opt-in: no call unless asked"
b = client.post(url, data={"project": "CCS20", "ai_advice": "true"}).json()
assert len(mock.requests) == 3 and "## AI advisory" in b["report_text"]
assert a["out_text"] == b["out_text"] and a["summary"] == b["summary"], "the AI must not change the DTS"
assert KEY not in json.dumps(b)
for k in [k for k in os.environ if k.startswith("LLM_")]:
    del os.environ[k]
mock.requests.clear()
d = client.post(url, data={"project": "CCS20", "ai_advice": "true"}).json()
assert "unavailable" in d["report_text"] and not mock.requests and d["out_text"] == a["out_text"]
assert client.get("/api/ai/status").json()["configured"] is False
print("OK")

print("=== 7. Pins Python already explains completely are not sent to the AI, but are still listed ===")
HONDA = os.path.join(ROOT, "samples", "Honda_database_v2.xlsx")
configure(); mock.reply = reply_e2e
rh = T.run(NS(excel=HONDA, project="Honda_GenID_B0", ai_advice=True, out=tmp + "/h.dtsi", report=tmp + "/h.md"))
assert not mock.requests, "boot-strap pins and 'matched only through a boot-strap signal' pins are explained by Python alone"
assert "Nothing was flagged" in open(tmp + "/h.md").read()
assert "GPIO_101" in open(tmp + "/h.md").read() and "GPIO_43" in open(tmp + "/h.md").read(), "still in the report's needs-review table"
print("OK")

mock.stop()
print("\nUSB AI ADVISOR TEST PASSED")
