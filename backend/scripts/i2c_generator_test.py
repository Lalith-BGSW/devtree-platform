"""
Tests the I2C engine (app/i2c_generator.py, app/dts_style.build_bundle(peripheral="i2c")) on synthetic pins - no customer file needed.
Guarantees: pins are chosen by the SELECTED function (I2C_QUPn / I2C_QUPn_SEk), never by the `interfaces` capability list; the role (sda / scl) comes from
the pin's own name; the function name comes from the SoC pin table (the SPI profile's table is read, not copied); one &i2cN per QUP with the right pins and
the QUP wrapper; several Gen5 nets on one pin (a shared bus) are listed as device groups on the controller, never picked from; CCI, SSC, SAIL and other
SoC families (Telechips I2C_03) are left out, never guessed; a missing internal pull is flagged; the independent verifier catches tampering.
"""
import os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from app import i2c_generator as I, dts_style as D
from app.models import NormalizedRecord

FAILS = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)))
    if not cond:
        FAILS.append(name)


def rec(g, sel, pinmuxed, sig, slot=None, mux=None, direction="0: IO", drv="4: DRV_10_MA", pull="3: PULL_UP", interfaces="GPIO, QUP0, I2C"):
    f = {"sa8155sip_ball_name": f"GPIO_{g}", "selected_interface": sel, "pinmuxed_name": pinmuxed, "gen5_signal_name": sig, "interface_group": "X",
         "io_gpio_pull": pull, "io_drv_strength": drv, "direction": direction, "interfaces": interfaces}
    if slot is not None:
        f["pinmux_setting"] = f"Q{slot}"
        for k in (1, 2, 3):
            f[f"regsettingqup{k}"] = f"TLMM_GPIO_CFG{g}(5:2)={mux}"
    return NormalizedRecord(project="P", soc="SA8155P", row_index=g, fields=f)


# real pins of the Nissan CCS20 sheet: QUP9 (one device), QUP18 (a shared bus: four nets on the same two pins), QUP11 (pull disabled)
Q9 = [rec(39, "I2C_QUP9, QUP9", "I2C_SDA", "CPU_APPLE_I2C_1V8:I2C_SDA", 2, 1), rec(40, "I2C_QUP9, QUP9", "I2C_SCL", "CPU_APPLE_I2C_1V8:I2C_SCL", 2, 1)]
Q18 = [rec(23, "I2C_QUP18, QUP18", "I2C_SDA", "CPU_SUBPCB_I2C_1V8:I2C_SDA, \nCPU_A2B_I2C_1V8:I2C_SDA, \nCPU_VO_RSE_I2C_1V8:I2C_SDA", 2, 2),
       rec(24, "I2C_QUP18, QUP18", "I2C_SCL", "CPU_SUBPCB_I2C_1V8:I2C_SCL, \nCPU_A2B_I2C_1V8:I2C_SCL, \nCPU_VO_RSE_I2C_1V8:I2C_SCL", 2, 3)]
Q11 = [rec(94, "I2C_QUP11, QUP11", "I2C_SDA", "CPU_DARC_I2C_1V8:I2C_SDA", 2, 2, pull="0: NO_PULL"), rec(95, "I2C_QUP11, QUP11", "I2C_SCL", "CPU_DARC_I2C_1V8:I2C_SCL", 3, 2, pull="0: NO_PULL")]
ALL = Q9 + Q18 + Q11

check("selected: I2C_QUPn is the qup kind", I.selected({"selected_interface": "I2C_QUP9, QUP9"}) == {"mode": "i2c", "inst": "qup9", "n": "9", "se": None, "kind": "qup", "dom": ""})
check("selected: SSC and SAIL QUPs are their own kinds", I.selected({"selected_interface": "I2C_SSC_QUP0, SSC_QUP0"})["kind"] == "ssc" and I.selected({"selected_interface": "I2C_SAIL_QUP0_SE1, SAIL_QUP0_SE1"})["kind"] == "sail")
check("selected: QUPn_SEk (newer SoCs) keeps the engine number", (lambda s: s["n"] == "1" and s["se"] == "7")(I.selected({"selected_interface": "I2C_QUP1_SE7, QUP1_SE7"})))
check("selected: CCI is the camera control interface, not an I2C QUP", I.selected({"selected_interface": "CCI"})["kind"] == "cci")
check("selected: I2C_03 (another SoC family) is kind 'other'", I.selected({"selected_interface": "I2C_03"})["kind"] == "other")
check("a plain GPIO or a capability list is not I2C", I.selected({"selected_interface": "GPIO"}) is None and I.selected({"selected_interface": "GPIO", "interfaces": "GPIO,I2C,SPI"}) is None)
check("a UART / SPI function is not I2C", I.selected({"selected_interface": "UART_QUP10, QUP10"}) is None and I.selected({"selected_interface": "SPI-M_QUP8, QUP8"}) is None)
check("roles come from the pin's own name", [I.role_of({"pinmuxed_name": n}) for n in ("I2C_SDA", "I2C_SCL", "CCI_I2C_SDA0", "I2C_SCL03")] == ["sda", "scl", "sda", "scl"])
check("no board-wired lines for I2C (they belong to their own engine)", not I.wired({"selected_interface": "GPIO", "gen5_signal_name": "CPU_X_I2C_1V8:EN"}))

g = I.generate(ALL, "SA8155P", "P")
gp = {p["gpio"]: p for p in g["pins"]}
check("6 I2C pins generated", len(g["pins"]) == 6, [p["pin"] for p in g["pins"]])
check("function qupN from the SoC pin table", gp["39"]["function"] == "qup9" and gp["23"]["function"] == "qup18" and gp["94"]["function"] == "qup11", gp)
check("labels are i2cN_role", {p["label"] for p in g["pins"]} == {"i2c9_sda", "i2c9_scl", "i2c18_sda", "i2c18_scl", "i2c11_sda", "i2c11_scl"}, g["labels"])
check("one &i2cN per QUP with its own pins only", re.search(r"&i2c9 \{[^}]*i2c9_sda[^}]*i2c9_scl[^}]*\}", g["text"]) and "i2c9_" not in re.search(r"&i2c18 \{[^}]*\}", g["text"]).group(0), g["text"])
check("the QUP wrapper to enable comes with it", "&qupv3_id_" in g["text"])
check("i2c labels the mainline dtsi defines are known (no guess note)", "is a guess" not in g["text"] and I.controller_label(I.soc_profile("SA8155P")[1], {"inst": "qup9", "n": "9", "se": None}) == ("i2c9", True))
check("a shared bus: every device group of the nets is listed on the controller", "4 device groups" not in g["text"] and "3 device groups on this bus: SUBPCB, A2B, VO_RSE" in g["text"], g["text"])
check("a single device group is named too", "device group on this bus (Gen5): APPLE" in g["text"])
check("bus speed, devices and addresses stay open (never invented)", "bus speed (clock-frequency) is not in the pin data" in g["text"] and "clock-frequency =" not in re.sub(r"/\*.*?\*/", "", g["text"], flags=re.S) and "reg =" not in re.sub(r"/\*.*?\*/", "", g["text"], flags=re.S))
check("a missing internal pull is flagged (an I2C line needs a pull-up)", g["text"].count("the workbook sets no internal pull") == 2, g["text"].count("the workbook sets no internal pull"))
check("a pin that has a pull-up in the workbook is not flagged", "bias-pull-up" in g["text"])
v = I.verify_text(g["text"], ALL, "SA8155P")
check("the independent verifier accepts the output", not v["wrong"] and v["ok"] == 6, v)

# left out, never guessed
extra = [rec(17, "CCI", "CCI_I2C_SDA0", "CPU_RVCA_I2C_1V8:I2C_SDA"), rec(124, "I2C_SSC_QUP0, SSC_QUP0", "I2C_SDA", "CPU_VO1_PT1_I2C_1V8:I2C_SDA"), rec(5, "I2C_03", "I2C_SCL03", "CPU_X_I2C_3V3:I2C_SCL")]
ge = I.generate(ALL + extra, "SA8155P", "P")
reasons = " ".join(x["reason"] for x in ge["left_out"])
check("CCI is left out with its reason", "camera control interface" in reasons and any(i["kind"] == "info" and "CCI" in i["text"] for i in ge["items"]))
check("SSC and other-family I2C do not become pin states", len(ge["pins"]) == 6 and ("QUP of the sensors subsystem" in reasons or "no TLMM GPIO number" in " ".join(i["text"] for i in ge["items"]) or "not a Qualcomm QUP" in reasons), (reasons, ge["items"]))
vv = I.verify_text(ge["text"], ALL + extra, "SA8155P")
check("the verifier does not call a deliberately left-out CCI pin 'missing'", not vv["wrong"] and not any("CCI" in a for a in vv["ambiguous"]), vv)

# mux disagreeing with the pin table
mm = [rec(39, "I2C_QUP9, QUP9", "I2C_SDA", "CPU_APPLE_I2C_1V8:I2C_SDA", 2, 3)]
gm = I.generate(mm, "SA8155P", "P")
check("a workbook mux value that disagrees with the table: the table is used, stated as decided", "decided:" in gm["text"] and gm["pins"][0]["function"] == "qup9", gm["text"])

# unknown SoC: the workbook's QUP name, marked
gu = I.generate([rec(5, "I2C_QUP1, QUP1", "I2C_SDA", "CPU_CMN1_CFG_I2C_1V8:I2C_SDA", 2, 1)], "SA6155P", "P")
check("an SoC without a pin table: workbook name, review mark, controller label a guess", gu["pins"][0]["function"] == "qup1" and "no verified pin table" in gu["text"] and "is a guess" in gu["text"] and any("no verified I2C pin table" in i["text"] for i in gu["items"]), gu["text"])

# include / exclude
gi = I.generate(ALL, "SA8155P", "P", exclude_pins=["GPIO_39"])
check("an excluded pin is dropped and listed", "39" not in {p["gpio"] for p in gi["pins"]} and any(x["reason"] == "excluded by you" for x in gi["left_out"]))

# tamper
t = g["text"].replace('function = "qup9"', 'function = "qup4"', 1)
check("the verifier catches a wrong function", bool(I.verify_text(t, ALL, "SA8155P")["wrong"]))
t2 = g["text"].replace("&i2c9 {", "&i2c3 {", 1)
check("the verifier catches a controller whose number does not match its pins", bool(I.verify_text(t2, ALL, "SA8155P")["wrong"]))

# bundle file names
b = D.build_bundle(g["text"], "P", {}, generator="i2c_dts_tool", peripheral="i2c")
check("bundle file names are <project>_i2c_pinmux.dtsi / _overlay.dts", {f["name"] for f in b["files"]} == {"p_i2c_pinmux.dtsi", "p_i2c_overlay.dts"}, [f["name"] for f in b["files"]])

cp = I.check_prompt()
check("the AI data check prompt knows I2C (shared buses and bidirectional lines are normal)", "I2C-specific rules" in cp and "SHARED BUS" in cp and "bidirectional" in cp, cp[-300:])
print("\nFAILED: %d" % len(FAILS) if FAILS else "\nAll I2C tests passed")
sys.exit(1 if FAILS else 0)
