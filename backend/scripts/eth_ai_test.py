"""
The Ethernet AI (app/eth_ai.py): judges candidate pins and quotes board facts. The AI may only SUGGEST; Python verifies every quote and
nothing in the generated files changes. Uses a mock gateway and synthetic pins - no customer data.
"""
import json, os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.join(HERE, "..")
sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
from _mock_llm import MockLLM
from app import eth_generator as E, eth_ai as A
from app.models import NormalizedRecord

mock = MockLLM(); base = mock.start()
FAILS = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)))
    if not cond:
        FAILS.append(name)


def configure(on=True):
    for k in [k for k in os.environ if k.startswith("LLM_")]:
        del os.environ[k]
    mock.requests.clear(); mock.script.clear(); mock.reply = None
    if on:
        os.environ.update(LLM_BASE_URL=base, LLM_API_KEY="K", LLM_DEPLOYMENT="gpt-test", LLM_TIMEOUT="5")


def rec(g, sel, sig, grp, **extra):
    f = {"sa8155sip_ball_name": f"GPIO_{g}", "selected_interface": sel, "pinmuxed_name": f"GPIO_{g}", "gen5_signal_name": sig, "interface_group": grp,
         "pinmux_setting": 1, "regsetting1": f"TLMM_GPIO_CFG{g}(5:2)=0"}
    f.update(extra)
    return NormalizedRecord(project="P", soc="SA8155P", row_index=g, fields=f)


a = rec(60, "GPIO", "", "ETH", remarks="MDIO line to the switch, RGMII-ID with 2 ns delay")
b = rec(61, "GPIO", "", "ETH", remarks="MDC not in use on this board")
c = rec(62, "GPIO", "", "MISC", customer_note="RGMII spare strap")
d = rec(63, "GPIO", "CPU_DNL_CTRL_1V8:DNL_EN", "MISC", qti_ref_aasignal="SGMII0_MDC_MDPX3_1P8")
recs = [a, b, c, d]
g = E.generate(recs, "SA8155P", "P")
check("setup: three candidates, none generated", {x["gpio"] for x in g["left_out"] if x.get("gpio")} == {"60", "61", "62", "63"} and not g["pins"], g["left_out"])

configure(on=False)
off = A.judge(recs, g["left_out"], set())
check("no gateway -> unavailable, no crash, nothing changes", off["status"] == "unavailable" and not off["verdicts"], off)

configure()


def reply(body):
    return json.dumps({"verdicts": [
        {"pin": "gpio60", "verdict": "ethernet_gpio", "role": "mdio", "quote": "MDIO line to the switch", "reason": "says MDIO"},
        {"pin": "gpio61", "verdict": "ethernet_gpio", "role": "mdc", "quote": "MDC not in use", "reason": "says MDC"},      # note says not in use -> capped
        {"pin": "gpio62", "verdict": "ethernet_gpio", "role": "data", "quote": "an invented quote", "reason": "x"},            # quote not in cells -> rejected
        {"pin": "gpio63", "verdict": "ethernet_gpio", "role": "mdc", "quote": "SGMII0_MDC_MDPX3_1P8", "reason": "reference name"},   # Gen5 names another function -> capped
        {"pin": "gpio99", "verdict": "ethernet_gpio", "role": "mdio", "quote": "x", "reason": "x"}],                         # never sent
        "facts": [
        {"pin": "gpio60", "topic": "clock_delay", "value": "2 ns", "quote": "RGMII-ID with 2 ns delay"},
        {"pin": "gpio60", "topic": "phy_address", "value": "0x1c", "quote": "RGMII-ID with 2 ns delay"},                      # value not inside its quote -> dropped
        {"pin": "gpio60", "topic": "speed", "value": "1000", "quote": "invented quote 1000"}]})                              # quote not in cells -> dropped
mock.reply = reply
res = A.judge(recs, g["left_out"], set())
v = res["verdicts"]
check("a verified answer is kept", v["gpio60"]["verdict"] == "ethernet_gpio" and v["gpio60"]["role"] == "mdio", v)
check("a 'not in use' note caps the AI at unclear", v["gpio61"]["verdict"] == "unclear" and "not in use" in v["gpio61"]["note"], v["gpio61"])
check("an AI quote that is not in the cells is not used", v["gpio62"]["verdict"] == "unclear", v["gpio62"])
check("a reference-column name does not outvote the pin's own Gen5 signal", v["gpio63"]["verdict"] == "unclear" and "Gen5" in v["gpio63"]["note"], v["gpio63"])
check("an answer for a pin that was never sent is dropped", "gpio99" not in v)
check("a fact is kept only when its quote is in the cells and its value is in the quote", [(f["topic"], f["value"]) for f in res["facts"]] == [("clock_delay", "2 ns")], res["facts"])
check("the files are unchanged by the AI", E.generate(recs, "SA8155P", "P")["text"] == g["text"])
sent = json.loads(mock.requests[-1]["body"]["messages"][1]["content"].split("PINS:\n", 1)[1])
check("the AI is sent the pin's own cells, not the whole workbook", len(sent) == 4 and "MDIO line to the switch" in sent[0]["workbook_cells"])
print("\nFAILED: %d" % len(FAILS) if FAILS else "\nAll Ethernet AI tests passed")
sys.exit(1 if FAILS else 0)
