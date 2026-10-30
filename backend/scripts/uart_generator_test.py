"""
Tests the UART engine (app/uart_generator.py, app/dts_style.build_bundle(peripheral="uart")) on synthetic pins - no customer file needed.
Guarantees: pins are chosen by the SELECTED function (UART_QUPn / HSUART_QUPn), never by the `interfaces` capability list; the role (tx / rx / cts / rts)
comes from the pin's own name and NOT from the Gen5 net (nets are named from the peer's side); the function name comes from the SoC pin table (the
SPI profile's table is read, not copied); a disagreeing mux value is stated as decided with the table used; one &uartN per QUP with the right pins and
the QUP wrapper; SSC / SAIL QUPs and other SoC families (UART_08) are left out, never guessed; board-wired UART control lines are GPIO states tied to the
UART of the same Gen5 group; a controller label the SoC's dtsi does not define is marked as a guess; the independent verifier catches tampering.
"""
import os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from app import uart_generator as U, dts_style as D
from app.models import NormalizedRecord

FAILS = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)))
    if not cond:
        FAILS.append(name)


def rec(g, sel, pinmuxed, sig, slot=None, mux=None, direction="0: IN", drv="3: DRV_8_MA", pull="1: PULL_DOWN", interfaces="GPIO, QUP0, UART"):
    f = {"sa8155sip_ball_name": f"GPIO_{g}", "selected_interface": sel, "pinmuxed_name": pinmuxed, "gen5_signal_name": sig, "interface_group": "X",
         "io_gpio_pull": pull, "io_drv_strength": drv, "direction": direction, "interfaces": interfaces}
    if slot is not None:
        f["pinmux_setting"] = f"Q{slot}"
        for k in (1, 2, 3):
            f[f"regsettingqup{k}"] = f"TLMM_GPIO_CFG{g}(5:2)={mux}"
    return NormalizedRecord(project="P", soc="SA8155P", row_index=g, fields=f)


# real pins of the Nissan CCS20 sheet: BT HSUART on QUP13 (4-wire), GNSS UART on QUP10, debug on QUP12.  Note the peer-side net names (TX pin on a net called ..._RXD)
BT = [rec(43, "HSUART_QUP13, QUP13", "HSUART_CTS", "CPU_BT_UART_1V8:UART_RTS_B", 1, 1, direction="0: IN"),
      rec(44, "HSUART_QUP13, QUP13", "HSUART_RFR", "CPU_BT_UART_1V8:UART_CTS_B", 1, 1, direction="1: OUT"),
      rec(45, "HSUART_QUP13, QUP13", "HSUART_Tx", "CPU_BT_UART_1V8:UART_RXD", 1, 1, direction="1: OUT"),
      rec(46, "HSUART_QUP13, QUP13", "HSUART_Rx", "CPU_BT_UART_1V8:UART_TXD", 1, 1, direction="0: IN")]
GN = [rec(11, "UART_QUP10, QUP10", "UART_Tx", "CPU_GNSS_UART_1V8:UART_RXD", 1, 1, direction="1: OUT"),
      rec(12, "UART_QUP10, QUP10", "UART_Rx", "CPU_GNSS_UART_1V8:UART_TXD", 1, 1)]
ALL = BT + GN

check("selected: HSUART_QUPn -> hsuart family, qup kind", U.selected({"selected_interface": "HSUART_QUP13, QUP13"}) == {"mode": "hsuart", "inst": "qup13", "n": "13", "se": None, "kind": "qup", "dom": ""})
check("selected: UART_QUPn -> uart family", U.selected({"selected_interface": "UART_QUP10, QUP10"})["mode"] == "uart")
check("selected: SSC and SAIL QUPs are their own kinds", U.selected({"selected_interface": "UART_SSC_QUP2, SSC_QUP2"})["kind"] == "ssc" and U.selected({"selected_interface": "UART_SAIL_QUP0_SE1, SAIL_QUP0_SE1"})["kind"] == "sail")
check("selected: QUPn_SEk (newer SoCs) keeps the engine number", (lambda s: s["n"] == "0" and s["se"] == "3")(U.selected({"selected_interface": "HSUART_QUP0_SE3, QUP0_SE3"})))
check("selected: UART_08 (another SoC family) is kind 'other'", U.selected({"selected_interface": "UART_08"})["kind"] == "other")
check("a plain GPIO or a capability list is not UART", U.selected({"selected_interface": "GPIO"}) is None and U.selected({"selected_interface": "GPIO", "interfaces": "GPIO,UART,SPI"}) is None)
check("a supply name is not UART", U.selected({"selected_interface": "PWR:UART_VDD"}) is None)
check("roles come from the pin's own name", [U.role_of({"pinmuxed_name": n}) for n in ("HSUART_CTS", "HSUART_RFR", "HSUART_Tx", "UART_Rx", "UT_TXD08", "UT_RTS20")] == ["cts", "rts", "tx", "rx", "tx", "rts"])
check("the Gen5 net is NOT used for the role (peer-side names)", U.role_of({"pinmuxed_name": "UART_Tx", "gen5_signal_name": "CPU_GNSS_UART_1V8:UART_RXD"}) == "tx")

g = U.generate(ALL, "SA8155P", "P")
gp = {p["gpio"]: p for p in g["pins"]}
check("6 UART pins generated", len(g["pins"]) == 6, [p["pin"] for p in g["pins"]])
check("function qupN from the SoC pin table (read from the SPI profile)", gp["45"]["function"] == "qup13" and gp["11"]["function"] == "qup10", gp)
check("labels are uartN_role", {p["label"] for p in g["pins"]} == {"uart13_cts", "uart13_rts", "uart13_tx", "uart13_rx", "uart10_tx", "uart10_rx"}, g["labels"])
check("one &uartN per QUP with its own pins only", re.search(r"&uart13 \{[^}]*uart13_cts[^}]*uart13_rx[^}]*\}", g["text"]) and "uart13_tx" not in re.search(r"&uart10 \{[^}]*\}", g["text"]).group(0), g["text"])
check("the QUP wrapper to enable comes with it", "&qupv3_id_" in g["text"])
check("4-wire and 2-wire are stated from the roles", "4-wire (TX, RX, CTS, RTS)" in g["text"] and "2-wire (TX, RX)" in g["text"])
check("a controller label the SoC dtsi does not define is a marked guess", "the controller label \"uart13\" is a guess" in g["text"])
check("the uart2 / uart9 labels the mainline dtsi defines are known", U.controller_label(U.soc_profile("SA8155P")[1], {"inst": "qup2", "n": "2", "se": None}) == ("uart2", True) and U.controller_label(U.soc_profile("SA8155P")[1], {"inst": "qup13", "n": "13", "se": None})[1] is False)
check("device / console / speed stay open (never invented)", "NEEDS REVIEW: what is connected to this UART" in g["text"] and "compatible" not in re.sub(r"/\*.*?\*/", "", g["text"], flags=re.S))
v = U.verify_text(g["text"], ALL, "SA8155P")
check("the independent verifier accepts the output", not v["wrong"] and v["ok"] == 6, v)

# family filter
gh = U.generate(ALL, "SA8155P", "P", enabled=["hsuart"])
check("hsuart only: the 2-wire UART is left out as 'switched off'", {p["gpio"] for p in gh["pins"]} == {"43", "44", "45", "46"} and any("switched off" in x["reason"] for x in gh["left_out"]))

# direction: the role rule decides, the cell is listed
bad = [rec(11, "UART_QUP10, QUP10", "UART_Tx", "CPU_GNSS_UART_1V8:UART_RXD", 1, 1, direction="0: IN"), GN[1]]
gb = U.generate(bad, "SA8155P", "P")
check("a Direction cell that contradicts the role is decided by the role", "decided: workbook Direction says IN, but TX of a UART is an OUTPUT" in gb["text"] and any("contradicts their UART role" in i["text"] for i in gb["items"]), gb["text"])

# mux disagreeing with the pin table
mm = [rec(11, "UART_QUP10, QUP10", "UART_Tx", "CPU_GNSS_UART_1V8:UART_RXD", 1, 3, direction="1: OUT")]
gm = U.generate(mm, "SA8155P", "P")
check("a workbook mux value that disagrees with the table: the table is used, stated as decided", "decided:" in gm["text"] and "fix the workbook cell" in gm["text"] and gm["pins"][0]["function"] == "qup10", gm["text"])

# other subsystems / SoC families: left out, never guessed
SSC = [rec(134, "UART_SSC_QUP2, SSC_QUP2", "UART_Tx", "CPU_GNSS_UART_1V8:UART_RXD", 8, 8, direction="1: OUT")]
gs = U.generate(SSC, "SA6155P", "P")
check("an SSC QUP is left out with its reason, never guessed", not gs["pins"] and any("sensors subsystem" in x["reason"] for x in gs["left_out"]), gs["left_out"])
TCC = [rec(19, "UART_08", "UT_TXD08", "CPU_DVR_UART_3V3:UART_RXD", 7, 7, direction="1: OUT")]
gt = U.generate(TCC, "TCC8050", "P")
check("another SoC family (UART_08) is left out, never guessed", not gt["pins"] and any("not a Qualcomm QUP" in x["reason"] for x in gt["left_out"]), gt["left_out"])
check("the independent verifier reports a selected-but-missing pin as ambiguous (function name unknown), not wrong", not U.verify_text(gt["text"], TCC, "TCC8050")["wrong"])

# an SoC without a table (newer QUPn_SEk naming)
IV = [rec(36, "HSUART_QUP0_SE2, QUP0_SE2", "HSUART_Tx", "CPU_BT_UART_1V8:RX", 2, 2, direction="1: OUT"),
      rec(37, "HSUART_QUP0_SE2, QUP0_SE2", "HSUART_Rx", "CPU_BT_UART_1V8:TX", 2, 2)]
gi = U.generate(IV, "SA7255P", "P")
check("no pin table: the workbook's QUP name is the function, marked for review", gi["pins"][0]["function"] == "qup0_se2" and "has no verified pin table" in gi["text"] and gi["pins"][0]["status"] == "review", gi["text"])
check("QUPn_SEk gives uartN_seK labels", {p["label"] for p in gi["pins"]} == {"uart0_se2_tx", "uart0_se2_rx"}, gi["labels"])
check("no pin table: the verifier cannot confirm the function, so says ambiguous, not OK-by-guess", U.verify_text(gi["text"], IV, "SA7255P")["ambiguous"])

# board-wired control lines
W = [rec(106, "GPIO", "GPIO_105", "CPU_DBG_GNSS_CTRL_1V8:UART_SEL", direction="1: OUT"),
     rec(110, "GPIO", "GPIO_110", "CPU_BT_UART_1V8:BT_REG_ON", direction="1: OUT")]
W[0].fields["resetvalue"] = "0: low"
DBG = [rec(85, "HSUART_QUP12, QUP12", "HSUART_Tx", "CPU_DBG_UART_1V8:UART_TXD", 1, 1, direction="1: OUT"), rec(86, "HSUART_QUP12, QUP12", "HSUART_Rx", "CPU_DBG_UART_1V8:UART_RXD", 1, 1)]
gw = U.generate(DBG + BT + W, "SA8155P", "P")
gws = {p["gpio"]: p for p in gw["pins"]}
check("a GPIO whose Gen5 signal says UART is a wired control line (gpio state)", "106" in gws and gws["106"]["function"] == "gpio" and gws["106"]["status"] == "wired", gws.keys())
check("a wired line is tied to the UART of the same name stem, with a note to confirm", gws["106"]["instance"] == "qup12" and "only because its Gen5 group starts with" in gw["text"], gw["text"])
check("the wired line takes the ResetValue level, flagged", 'output-low;\t/* NEEDS REVIEW (evidence: Excel ResetValue "0: low")' in gw["text"], gw["text"])
check("verifier accepts wired pins", not U.verify_text(gw["text"], DBG + BT + W, "SA8155P")["wrong"], U.verify_text(gw["text"], DBG + BT + W, "SA8155P"))
gn = U.generate(DBG + BT + W, "SA8155P", "P", board_wiring=False)
check("board_wiring off: no wired pins", not any(p["status"] == "wired" for p in gn["pins"]))

# Gen5 cross-check
x = [rec(11, "UART_QUP10, QUP10", "UART_Tx", "CPU_GYRO_SPI_1V8:SPI_MOSI", 1, 1, direction="1: OUT")]
gx = U.generate(x, "SA8155P", "P")
check("Selected says UART but Gen5 names SPI: followed, flagged", "names SPI" in gx["text"] and any("another bus" in i["text"] for i in gx["items"]), gx["text"])
y = [rec(20, "I2C_QUP3, QUP3", "I2C_SDA", "CPU_X_UART_1V8:UART_TXD")]
gy = U.generate(y, "SA8155P", "P")
check("Gen5 says UART but the workbook selects I2C: never generated, listed", not gy["pins"] and any("Gen5 signal says UART" in z["reason"] for z in gy["left_out"]), gy["left_out"])

# candidates are suggestions only
c = rec(60, "GPIO", "GPIO_60", "", interfaces="GPIO"); c.fields["interface_group"] = "SCC_UART"; c.fields["imported_schematic_signal_name"] = "D5700 CPU_X_UART_1V8:UART_TXD"
gc = U.generate(ALL + [c], "SA8155P", "P")
check("a pin with only UART wording elsewhere is a suggestion, never generated", "60" not in {p["gpio"] for p in gc["pins"]} and any(z.get("gpio") == "60" for z in gc["left_out"]), gc["left_out"])
gi2 = U.generate(ALL + [c], "SA8155P", "P", include_pins=["gpio60"])
check("adding a suggested pin generates it as a GPIO and the verifier accepts it", "60" in {p["gpio"] for p in gi2["pins"]} and not U.verify_text(gi2["text"], ALL + [c], "SA8155P", include_pins=["gpio60"])["wrong"])

# tampering is caught
tam = g["text"].replace('function = "qup13"', 'function = "qup12"', 1)
check("the verifier catches a wrong function", U.verify_text(tam, ALL, "SA8155P")["wrong"])
tam2 = g["text"].replace("drive-strength = <10>", "drive-strength = <2>", 1) if "drive-strength = <10>" in g["text"] else g["text"].replace("drive-strength = <8>", "drive-strength = <2>", 1)
check("the verifier catches a wrong drive strength", U.verify_text(tam2, ALL, "SA8155P")["wrong"])
tam3 = g["text"].replace("&uart13_tx", "&uart13_zz", 1)
check("the verifier catches a dangling pinctrl reference", U.verify_text(tam3, ALL, "SA8155P")["wrong"])

# bundle: names + flat layout only changes the layout
b = D.build_bundle(g["text"], "P", {}, generator="uart_dts_tool", peripheral="uart")
check("bundle file names are <project>_uart_pinmux.dtsi / _overlay.dts", {f["name"] for f in b["files"]} == {"p_uart_pinmux.dtsi", "p_uart_overlay.dts"}, [f["name"] for f in b["files"]])

cp = U.check_prompt()
check("the AI data check prompt knows UART (peer-side net names are normal)", "UART-specific rules" in cp and "PEER's side" in cp, cp[-300:])
check("'can be muxed ...' remarks are capability wording, not wiring", not U._evidence({"remarks": "can be muxed QUP1_SE1:UART"}) and bool(U._evidence({"remarks": "UART to the BT chip"})))
from app import usb_ai_assist as _u
_c1 = {"pin": "GPIO_105", "text": "Signal: CPU_DBG_GNSS_CTRL_1V8:UART_SEL | Direction: 0: IN"}
_f1 = {"pin": "GPIO_105", "type": "not_used", "quote_a": "Signal: CPU_DBG_GNSS_CTRL_1V8:UART_SEL", "issue": "Pin is configured as a live UART signal."}
check("AI not_used finding is dropped when no quote says unused (Honda GPIO_105)", _u.validate_finding(_f1, {"GPIO_105": _c1})["status"] == "rejected")
_c2 = {"pin": "GPIO_105", "text": "Signal: X:UART_SEL | Remark: not mounted on this variant"}
check("AI not_used finding is kept when the remark says not mounted", _u.validate_finding(dict(_f1, quote_a="Remark: not mounted on this variant"), {"GPIO_105": _c2})["status"] == "finding")
# shared pins (Nissan gpio83/84: the same QUP16 pins carry a sub-board net and a tuner net)
SH = [rec(83, "HSUART_QUP16, QUP16", "HSUART_Rx", "CPU_SUBPCB_UART_1V8:UART_TXD, \nCPU_SDARS_UART_1V8:UART_TXD", 4, 2, direction="0: IN"),
      rec(84, "HSUART_QUP16, QUP16", "HSUART_Tx", "CPU_SUBPCB_UART_1V8:UART_RXD, \nCPU_SDARS_UART_1V8:UART_RXD", 4, 2, direction="1: OUT")]
gsh = U.generate(SH, "SA8155P", "P")
check("a shared UART pin lists both device groups on the controller", "2 device groups on the pins of this UART: SUBPCB, SDARS" in gsh["text"], gsh["text"])
check("the AI data check prompt says several nets on one pin are normal", "shared pin" in U.check_prompt())
print("\nFAILED: %d" % len(FAILS) if FAILS else "\nAll UART tests passed")
sys.exit(1 if FAILS else 0)

