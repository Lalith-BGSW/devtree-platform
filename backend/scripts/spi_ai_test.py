"""
The SPI AI (app/spi_ai.py): judges candidate pins and quotes board facts; the tool wires it in behind --ai-check. The AI may only SUGGEST;
Python verifies every quote and nothing in the generated files changes. Mock gateway, synthetic pins - no customer data.
"""
import json, os, sys, argparse
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.join(HERE, "..")
sys.path.insert(0, ROOT); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(ROOT, "tool"))
from _mock_llm import MockLLM
from app import spi_generator as S, spi_ai as A
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
    f = {"sa8155sip_ball_name": f"GPIO_{g}", "selected_interface": sel, "pinmuxed_name": f"GPIO_{g}", "gen5_signal_name": sig, "interface_group": grp}
    f.update(extra)
    return NormalizedRecord(project="P", soc="SA8155P", row_index=g, fields=f)


a = rec(60, "GPIO", "", "MISC", remarks="chip select of the IMU on the SPI bus, SPI mode 3, 10 MHz")
b = rec(61, "GPIO", "", "MISC", remarks="SPI CS not in use on this board")
c = rec(62, "GPIO", "", "MISC", customer_note="SPI spare strap")
d = rec(63, "GPIO", "CPU_X_UART_1V8:UART_RX", "MISC", qti_ref="SPI0_MOSI")
e = rec(64, "GPIO", "CPU_LED:LED", "MISC", interfaces="GPIO, SPI-M", port3="SPI_MOSI")         # capability columns only: never a candidate
recs = [a, b, c, d, e]
g = S.generate(recs, "SA8155P", "P")
cl = {x["gpio"]: x for x in g["left_out"] if x.get("gpio")}
check("setup: four candidates, capability-only pin is not one, nothing generated", set(cl) == {"60", "61", "62", "63"} and not g["pins"], cl.keys())
cap = rec(65, "GPIO", "CPU_V_CTRL:INT_B", "MISC", remarks="Could be used as SPI (master)")
check("'could be used as SPI' is an option, not evidence", not any(x.get("gpio") == "65" for x in S.generate([cap], "SA8155P", "P")["left_out"]))
gx = rec(66, "GPIO", "CPU_V_CTRL:RST_B", "MISC", imported_schematic_signal_name="CPU_HSM_SPI_1V8:SPI_MCLK", old_x="CPU_HSM_SPI_1V8:SPI_CS_B")
check("SPI wording only in reference columns, Gen5 names something else -> possible, never likely", [x["tier"] for x in S.generate([gx], "SA8155P", "P")["left_out"]] == ["possible"])
gy = rec(67, "GPIO", "", "CPU_Z_SPI_1V8", remarks="IMU chip select")
check("Interface group says SPI and no Gen5 -> likely", [x["tier"] for x in S.generate([gy], "SA8155P", "P")["left_out"]] == ["likely"], S.generate([gy], "SA8155P", "P")["left_out"])
check("tiers: negative note and Gen5 conflict are only 'possible'", cl["61"]["tier"] == "possible" and cl["63"]["tier"] == "possible" and cl["63"]["conflict"], cl)

configure(on=False)
off = A.judge(recs, g["left_out"], set())
check("no gateway -> unavailable, no crash", off["status"] == "unavailable" and not off["verdicts"], off)

configure()
mock.reply = lambda body: json.dumps({"verdicts": [
    {"pin": "gpio60", "verdict": "spi_line", "role": "cs", "quote": "chip select of the IMU", "reason": "says chip select"},
    {"pin": "gpio61", "verdict": "spi_line", "role": "cs", "quote": "SPI CS not in use", "reason": "x"},
    {"pin": "gpio62", "verdict": "spi_line", "role": "cs", "quote": "an invented quote", "reason": "x"},
    {"pin": "gpio63", "verdict": "spi_line", "role": "mosi", "quote": "SPI0_MOSI", "reason": "reference name"},
    {"pin": "gpio99", "verdict": "spi_line", "role": "cs", "quote": "x", "reason": "x"}],
    "facts": [{"pin": "gpio60", "topic": "spi_mode", "value": "mode 3", "quote": "SPI mode 3, 10 MHz"},
              {"pin": "gpio60", "topic": "max_frequency", "value": "10 MHz", "quote": "SPI mode 3, 10 MHz"},
              {"pin": "gpio60", "topic": "device", "value": "BMI160", "quote": "chip select of the IMU"},          # value not in quote -> dropped
              {"pin": "gpio60", "topic": "word_size", "value": "8", "quote": "invented 8 bit"}]})                 # quote not in cells -> dropped
ORIG_REPLY = mock.reply
res = A.judge(recs, g["left_out"], set())
v = res["verdicts"]
check("a verified answer is kept", v["gpio60"]["verdict"] == "spi_line" and v["gpio60"]["role"] == "cs", v)
check("a 'not in use' note caps at unclear", v["gpio61"]["verdict"] == "unclear" and "not in use" in v["gpio61"]["note"])
check("an invented quote is not used", v["gpio62"]["verdict"] == "unclear")
check("a reference-column name does not outvote the Gen5 signal", v["gpio63"]["verdict"] == "unclear" and "Gen5" in v["gpio63"]["note"], v["gpio63"])
check("an answer for a pin never sent is dropped", "gpio99" not in v)
check("facts kept only with quote in cells and value in quote", [(f["topic"], f["value"]) for f in res["facts"]] == [("spi_mode", "mode 3"), ("max_frequency", "10 MHz")], res["facts"])
check("files unchanged by the AI", S.generate(recs, "SA8155P", "P")["text"] == g["text"])

# the Iveco case: SPI wording only in an old/imported column, the pin's own Gen5 signal names something else -> AI is capped
old = rec(84, "GPIO", "CPU_V_CTRL:INT_B", "MISC", old_stla_brain_imported_schematic_signal_name="D5700(BG45_GPIO_84) CPU_HSM_SPI_1V8:SPI_MISO", qti_ref_aasignal="SOC_AUDIO_PRI_MISO")
go = S.generate([old], "SA8155P", "P")
mock.reply = lambda body: json.dumps({"verdicts": [{"pin": "gpio84", "verdict": "spi_line", "role": "miso", "quote": "CPU_HSM_SPI_1V8:SPI_MISO", "reason": "x"}], "facts": []})
vo = A.judge([old], go["left_out"], set())["verdicts"]["gpio84"]
check("AI cannot call a pin SPI on old/reference-column wording alone when Gen5 says otherwise", vo["verdict"] == "unclear" and "old / imported" in vo["note"], vo)
mock.reply = lambda body: json.dumps({"verdicts": [{"pin": "gpio60", "verdict": "spi_line", "role": "cs", "quote": "chip select of the IMU", "reason": "x"}], "facts": []})
check("...but remarks (a live column) still count", A.judge(recs, g["left_out"], set())["verdicts"]["gpio60"]["verdict"] == "spi_line")

mock.reply = ORIG_REPLY

# through the tool (workbook reader is bypassed): the ledger row and the report
import spi_dts_tool as T
T_load = T._u.load_records
T._u.load_records = lambda excel, project: (None, recs, "SA8155P")
T._u.validate = lambda text: ([], None)
res_t = T.run(argparse.Namespace(excel="x", project="P", list_projects=False, family=None, include_pin=None, exclude_pin=None, no_board_wiring=False,
                                 template=None, kernel=None, kernel_base=None, out_dir=None, report=None, ai_check=True))
check("tool: AI judge ledger row and facts present", any(r["feature"] == "spi_judge" and r["calls"] >= 1 for r in res_t.ai_ledger) and res_t.ai_facts, res_t.ai_ledger)
check("tool: verdict sits on the left-out row and in the report", any(x.get("ai", {}).get("verdict") == "spi_line" for x in res_t.left_out) and "AI: spi_line" in res_t.report_text and "spi_mode: mode 3" in res_t.report_text)
res_n = T.run(argparse.Namespace(excel="x", project="P", list_projects=False, family=None, include_pin=None, exclude_pin=None, no_board_wiring=False,
                                 template=None, kernel=None, kernel_base=None, out_dir=None, report=None, ai_check=False))
check("tool: AI off -> no AI rows", not res_n.ai_ledger and not res_n.ai_facts)
inc = S.generate(recs, "SA8155P", "P", include_pins=["gpio60"])
check("adding a suggested pin generates it as GPIO and verifies", any(p["gpio"] == "60" and p["function"] == "gpio" for p in inc["pins"]) and not S.verify_text(inc["text"], recs, "SA8155P", include_pins=["gpio60"])["wrong"])
print("\nFAILED: %d" % len(FAILS) if FAILS else "\nAll SPI AI tests passed")
sys.exit(1 if FAILS else 0)
