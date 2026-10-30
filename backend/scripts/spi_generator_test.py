"""
Tests the SPI engine (app/spi_generator.py, app/dts_style.build_bundle(peripheral="spi")) on synthetic pins - no customer file needed.
Guarantees: pins are chosen by the SELECTED function (SPI-M_QUPn / SPI-S_QUPn), never by the `interfaces` capability list; the function name comes from
the SoC pin table (qupN, or the qup_l* the workbook's mux value points at); a disagreeing mux value is flagged; master / slave filters work; one &spiN per
QUP with the right pins; board-wired SPI control lines (GPIO + Gen5 says SPI) are GPIO pin states tied to the controller of the same Gen5 group and mode;
a non-QUP SoC (GPSB) gives a note, not a pin; the independent verifier catches tampering; flat format changes only the layout.
"""
import os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from app import spi_generator as S, dts_style as D
from app.models import NormalizedRecord

FAILS = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)))
    if not cond:
        FAILS.append(name)


def rec(g, sel, pinmuxed, sig, slot=None, mux=None, direction="0: IN", drv="3: DRV_8_MA", pull="1: PULL_DOWN", interfaces="GPIO, QUP0, SPI"):
    f = {"sa8155sip_ball_name": f"GPIO_{g}", "selected_interface": sel, "pinmuxed_name": pinmuxed, "gen5_signal_name": sig, "interface_group": "X",
         "io_gpio_pull": pull, "io_drv_strength": drv, "direction": direction, "interfaces": interfaces}
    if slot is not None:
        f["pinmux_setting"] = f"Q{slot}"
        for k in (1, 2, 3):
            f[f"regsettingqup{k}"] = f"TLMM_GPIO_CFG{g}(5:2)={mux}"
    return NormalizedRecord(project="P", soc="SA8155P", row_index=g, fields=f)


M0 = [rec(0, "SPI-M_QUP0, QUP0", "SPI-M_MISO", "CPU_GYRO_SPI_1V8:SPI_MISO", 3, 1),
      rec(1, "SPI-M_QUP0, QUP0", "SPI-M_MOSI", "CPU_GYRO_SPI_1V8:SPI_MOSI", 3, 1, direction="1: OUT"),
      rec(2, "SPI-M_QUP0, QUP0", "SPI-M_SCLK", "CPU_GYRO_SPI_1V8:SPI_MCLK", 2, 1, direction="0: IN"),
      rec(3, "SPI-M_QUP0, QUP0", "SPI-M_CS_0", "CPU_GYRO_CTRL_1V8:GYRO_CS_B", 2, 1, direction="1: OUT"),
      rec(59, "SPI-M_QUP0, QUP0", "SPI-M_CS_1", "CPU_GYRO_CTRL_1V8:ACC_CS_B", 2, 2, direction="1: OUT")]      # qup_l4 at value 2
S15 = [rec(27, "SPI-S_QUP15, QUP15", "SPI-S_MOSI", "CPU_INC_SPI_1V8:SPI_S_MOSI", 4, 1),
       rec(28, "SPI-S_QUP15, QUP15", "SPI-S_MISO", "CPU_INC_SPI_1V8:SPI_S_MISO", 4, 1, direction="1: OUT")]
W = [rec(130, "GPIO", "GPIO_130", "CPU_INC_SPI_1V8:SPI_S_REQ"),
     rec(148, "GPIO", "GPIO_148", "CPU_INC_SPI_1V8:SPI_M_REQ")]
CAP = rec(10, "GPIO", "GPIO_10", "CPU_X:LED", interfaces="GPIO, QUP0, SPI-M")                      # lists SPI only as a capability
NOT = rec(11, "I2C-M_QUP1", "I2C_SDA", "CPU_X_I2C:SDA")
ALL = M0 + S15 + W + [CAP, NOT]

g = S.generate(ALL, "SA8155P", "P", board_wiring=False)
gp = {p["gpio"]: p for p in g["pins"]}
check("selected SPI pins are generated", {"0", "1", "2", "3", "59", "27", "28"} == set(gp), set(gp))
check("a pin that only LISTS SPI as capability / an I2C pin is not generated", "10" not in gp and "11" not in gp)
check("function = qup<N> from the pin table", gp["0"]["function"] == "qup0" and gp["27"]["function"] == "qup15", {k: v["function"] for k, v in gp.items()})
check("CS on a pin without qup<N> takes the qup_l function the workbook's mux points at, flagged", gp["59"]["function"] == "qup_l4" and "confirm" in g["text"], gp["59"])
check("roles and master / slave are read", gp["0"]["role"] == "miso" and gp["3"]["role"] == "cs0" and gp["59"]["role"] == "cs1" and gp["27"]["family"] == "slave" and gp["0"]["family"] == "master")
check("one controller per QUP with its own pins", "&spi0 {" in g["text"] and "&spi15 {" in g["text"] and re.search(r"&spi0 \{[^}]*spi0_miso[^}]*spi0_cs1", g["text"]) and "spi15_mosi" not in re.search(r"&spi0 \{.*?\n\};", g["text"], re.S).group(0))
check("slave controller gets spi-slave with a review mark; master does not", re.search(r"&spi15 \{[^}]*spi-slave", g["text"]) and not re.search(r"&spi0 \{[^}]*spi-slave", g["text"]))
check("QUP wrapper enabled from the dtsi map", "&qupv3_id_0 {" in g["text"] and "&qupv3_id_2 {" in g["text"])
check("SPI devices are never invented", not re.search(r"^\s*compatible\s*=", g["text"], re.M) and "spi-max-frequency" in g["text"] and "NEEDS REVIEW" in g["text"])
check("Direction that contradicts the role is reported, not used", any("Direction" in i["text"] and "gpio2" in i["text"] for i in g["items"]) and "gpio2" not in " ".join(x["pin"] for x in g["left_out"]))

v = S.verify_text(g["text"], ALL, "SA8155P", board_wiring=False)
check("independent verifier agrees", not v["wrong"] and v["ok"] == len(g["pins"]), v)
bad = g["text"].replace('function = "qup0"', 'function = "qup4"', 1)
check("verifier catches a function not on the pin", any("not a QUP function" in w for w in S.verify_text(bad, ALL, "SA8155P", board_wiring=False)["wrong"]))
bad = re.sub(r"\tspi0_mosi: spi0_mosi \{.*?\n\t\};\n", "", g["text"], count=1, flags=re.S)
check("verifier catches a missing pin", any("no pin state" in w or "not defined" in w for w in S.verify_text(bad, ALL, "SA8155P", board_wiring=False)["wrong"]))
bad = g["text"].replace("spi0_miso &spi0_mosi", "spi0_miso &spi15_mosi", 1)
check("verifier catches a pin of another engine in a controller", any("several SPI engines" in w or "does not match" in w for w in S.verify_text(bad, ALL, "SA8155P", board_wiring=False)["wrong"]), S.verify_text(bad, ALL, "SA8155P", board_wiring=False))

# filters
m = S.generate(ALL, "SA8155P", "P", enabled={"master"}, board_wiring=False)
check("master-only filter drops the slave", {p["family"] for p in m["pins"]} == {"master"} and any("switched off" in x["reason"] for x in m["left_out"]))
s_ = S.generate(ALL, "SA8155P", "P", enabled={"slave"}, board_wiring=False)
check("slave-only filter keeps the slave", {p["gpio"] for p in s_["pins"]} == {"27", "28"})
check("verifier honours the filter", not S.verify_text(s_["text"], ALL, "SA8155P", enabled={"slave"}, board_wiring=False)["wrong"])

# board-wired control lines: tied to the controller of the same Gen5 group AND the mode in the signal name
M7 = [rec(98, "SPI-M_QUP7, QUP7", "SPI-M_MISO", "CPU_INC_SPI_1V8:SPI_M_MISO", 2, 1)]
gw = S.generate(M7 + S15 + W, "SA8155P", "P")
gws = {p["gpio"]: p for p in gw["pins"]}
check("wired REQ lines are GPIO pin states marked wired", gws["130"]["function"] == "gpio" and gws["130"]["status"] == "wired" and gws["148"]["status"] == "wired", gws.keys())
check("S_REQ goes to the slave controller, M_REQ to the master one", re.search(r"&spi15 \{[^}]*spi15_s_req", gw["text"]) and re.search(r"&spi7 \{[^}]*spi7_m_req", gw["text"]), gw["text"])
check("verifier accepts wired pins", not S.verify_text(gw["text"], M7 + S15 + W, "SA8155P")["wrong"], S.verify_text(gw["text"], M7 + S15 + W, "SA8155P"))
nz = S.generate(M7 + S15 + W, "SA8155P", "P", board_wiring=False)
check("--no-board-wiring leaves them out", "130" not in {p["gpio"] for p in nz["pins"]})
sw = rec(5, "GPIO", "GPIO_5", "CPU_X_I2C:SDA")
check("a GPIO pin whose signal does not say SPI is not wired", not S.wired(sw.fields))
pc = rec(6, "PCIE1", "PCIE1_WAKE", "CPU_SPI_CTRL:PCIE_WAKE_B")
check("a pin set to another function is never turned into a GPIO", not S.wired(pc.fields))
un = S.generate([rec(92, "WUP_GPIO,PDC", "GPIO_92", "CPU_Z_SPI_CS_1V8:SPI_CS_B")] + M7, "SA8155P", "P")
check("a wired line with no matching group stays unassigned and says so", any("could not be tied" in i["text"] for i in un["items"]) and "no SPI controller known" in un["text"])

# bus-name stem fallback (Honda: CS of a second device on the SBR bus has its own Gen5 group)
B4 = [rec(20, "SPI-M_QUP4, QUP4", "SPI-M_MISO", "CPU_SBR_SPI_1V8:SPI_MISO", 1, 1)]
cs92 = rec(92, "WUP_GPIO,PDC", "GPIO_92", "CPU_SBR_STA710_1_SPI_CS_1V8:SPI_CS_B")
gst = S.generate(B4 + [cs92] + S15, "SA8155P", "P")
check("wired CS with its own group is tied to the engine of the same bus stem, with a review note", re.search(r"&spi4 \{[^}]*spi4_cs_b", gst["text"]) and "only because its Gen5 group starts with" in gst["text"], gst["text"])
inc_ = rec(93, "GPIO", "GPIO_93", "CPU_INC_SPI_1V8:SPI_S_REQ")
check("a stem that matches two engines is not guessed", any("could not be tied" in i["text"] for i in S.generate(B4 + [rec(21, "SPI-M_QUP5, QUP5", "SPI-M_MISO", "CPU_SBR_X_1V8:SPI_MISO", 1, 1), cs92], "XYZ", "P")["items"]))

# unknown SoC / non-QUP SoC
gu = S.generate(M0, "XYZ123", "P")
check("unknown SoC: workbook QUP name as function, review, placeholder controller", {p["function"] for p in gu["pins"]} == {"qup0"} and all(p["status"] == "review" for p in gu["pins"]) and "guess" in gu["text"])
check("unknown SoC: verifier says 'cannot be checked', not wrong", not S.verify_text(gu["text"], M0, "XYZ123")["wrong"] and S.verify_text(gu["text"], M0, "XYZ123")["ambiguous"])
se = rec(10, "SPI-M_QUP2_SE0, QUP2_SE0", "SPI-M_MISO", "CPU_T_SPI:SPI_MISO", 4, 1)
gs = S.generate([se], "SA7255P", "P")
check("QUP_SE naming is kept (qup2_se0) and the controller label is a placeholder", gs["pins"][0]["function"] == "qup2_se0" and "&spi2_se0" in gs["text"])
gp_ = rec(7, "GPSB_06", "GSCK06", "CPU_A_SPI_3V3:SPI_CLK"); gp_.fields["sa8155sip_ball_name"] = "GSCK06"
gg = S.generate([gp_], "TCC8050", "P")
check("a non-QUP SPI block (GPSB) with no GPIO number gives a note, no pin", not gg["pins"] and any("no TLMM GPIO number" in i["text"] for i in gg["items"]), gg["items"])

# mux
check("mux value comes from the Q<k> slot's RegSettingQUPk", S._qup_mux(M0[0].fields, "0") == 1 and S._qup_mux({"pinmux_setting": "9", "regsettingqup1": "TLMM_GPIO_CFG0(5:2)=1"}, "0") is None and S._qup_mux({"pinmux_setting": "Q1", "regsettingqup1": "TLMM_GPIO_CFG7(5:2)=1"}, "0") is None)
mm = [rec(0, "SPI-M_QUP0, QUP0", "SPI-M_MISO", "CPU_GYRO_SPI_1V8:SPI_MISO", 3, 3)]
gm = S.generate(mm, "SA8155P", "P")
check("a workbook mux value that disagrees with the table: the table is used, stated as decided, workbook cell listed", "disagrees" in " ".join(i["text"] for i in gm["items"]) and "decided:" in gm["text"] and "fix the workbook cell" in gm["text"], gm["items"])
# evidence: ResetValue gives the output level (flagged), irq column flags an interrupt line, role rule decides direction
wl = rec(130, "GPIO", "GPIO_130", "CPU_INC_SPI_1V8:SPI_S_REQ", direction="1: OUT"); wl.fields["resetvalue"] = "0: low"
wn = rec(131, "GPIO", "GPIO_131", "CPU_INC_SPI_1V8:SPI_S_ACK", direction="1: OUT")
wi = rec(148, "GPIO", "GPIO_148", "CPU_INC_SPI_1V8:SPI_M_REQ"); wi.fields["irq"] = "INT"
ge = S.generate(M0 + S15 + [wl, wn, wi], "SA8155P", "P")["text"]
check("ResetValue gives the output level, flagged with its source", 'output-low;\t/* NEEDS REVIEW (evidence: Excel ResetValue "0: low")' in ge, ge)
check("no ResetValue -> the plain 'level not derivable' placeholder", "level is not derivable from Excel" in ge)
check("an irq = INT pin says it is an interrupt line", "irq = INT" in ge and "interrupts-extended" in ge)
check("a Direction cell that contradicts the SPI role is decided by the role rule, not left open", "decided: workbook Direction says IN, but SCLK of an SPI master is an OUTPUT" in ge)
check("include_pins adds a pin as GPIO", any(p["gpio"] == "10" and p["function"] == "gpio" for p in S.generate(ALL, "SA8155P", "P", include_pins=["GPIO_10"], board_wiring=False)["pins"]))
check("exclude_pins drops a pin", "0" not in {p["gpio"] for p in S.generate(ALL, "SA8155P", "P", exclude_pins=["GPIO_0"], board_wiring=False)["pins"]})

# formats
flat = D.Style(role="pinmux", layout="flat", name_style="hyphen_state")
bd = D.build_bundle(g["text"], "P", {}, peripheral="spi")
bf = D.build_bundle(g["text"], "P", {"pinmux": flat}, peripheral="spi")
names = [f["name"] for f in bd["files"]]
check("files are named for SPI", names == ["p_spi_pinmux.dtsi", "p_spi_overlay.dts"], names)
vf = S.verify_text("\n".join(f["text"] for f in bf["files"]), ALL, "SA8155P", board_wiring=False)
check("flat-format files verify with the same values", not vf["wrong"] and vf["ok"] == len(g["pins"]), vf)

c1 = rec(4, "SPI-M_QUP6, QUP6", "SPI-M_MISO", "CPU_X_UART_1V8:UART_RX", 1, 1)
c2 = rec(21, "UART3", "UART_TX", "CPU_Y_SPI_1V8:SPI_MOSI")
gcx = S.generate([c1, c2], "SA8155P", "P")
check("Selected SPI but Gen5 names another bus: followed, flagged for review", [p["gpio"] for p in gcx["pins"]] == ["4"] and "names UART" in gcx["text"] and any("another bus" in i["text"] for i in gcx["items"]), gcx["items"])
check("Gen5 says SPI but a non-GPIO function is selected: not generated, listed with the reason", any(x["pin"] == "gpio21" and "Gen5 signal says SPI" in x["reason"] for x in gcx["left_out"]), gcx["left_out"])
cp = S.check_prompt()
check("AI data-check prompt is about SPI, not USB", "SPI-related" in cp and "live SPI signal" in cp and "MISO" in cp and "USB" not in cp.split("Direction")[0][:400], cp[:300])

check("data-check prompt tells the AI a bus-vs-device group name is normal", "names the DEVICE" in cp and "DIFFERENT bus numbers" in cp)
check("judge prompt maps MCLK to sclk", "MCLK / CLK / SCK / SCLK = sclk" in __import__("app.spi_ai", fromlist=["x"]).PROMPT)


# ---- GPIO chip select: a master CS on a pin that has no function for the selected QUP and no workbook mux hint is a software chip select
cs8 = [rec(88, "SPI-M_QUP8, QUP8", "SPI-M_MISO", "CPU_SBR_SPI_1V8:SPI_M_MISO", 3, 1),
       rec(96, "SPI-M_QUP8, QUP8", "SPI-M_CS_1", "CPU_SBR_STA710_2_SPI_CS_1V8:SPI_CS_B", 3, 7, direction="1: OUT")]
gc8 = S.generate(cs8, "SA8155P", "P", board_wiring=False)
p96 = {p["gpio"]: p for p in gc8["pins"]}
check("a CS on a pin without the selected QUP function is a GPIO chip select (no 'does not have' mismatch)", p96["96"]["function"] == "gpio" and p96["96"]["gpio_cs"] and "which this pin does not have" not in gc8["text"], p96["96"])
check("the native pin on the same bus keeps its QUP function", p96["88"]["function"] == "qup8" and not p96["88"]["gpio_cs"])
check("the controller lists the software chip select for cs-gpios, active level flagged", "<&tlmm 96 GPIO_ACTIVE_LOW>" in gc8["text"] and "software chip select" in gc8["text"])
check("the GPIO chip select output level is not invented", ("output-high" not in gc8["text"] and "output-low" not in gc8["text"]) or "NEEDS REVIEW" in gc8["text"])
v8 = S.verify_text(gc8["text"], cs8, "SA8155P", board_wiring=False)
check("independent verifier accepts the GPIO chip select", not v8["wrong"] and v8["ok"] == 2 and any("software (GPIO) chip select" in a for a in v8["ambiguous"]), v8)

cs9 = [rec(96, "SPI-M_QUP8, QUP8", "SPI-M_CS_1", "CPU_SBR_STA710_2_SPI_CS_1V8:SPI_CS_B", 3, 2, direction="1: OUT")]
g9 = S.generate(cs9, "SA8155P", "P", board_wiring=False)
check("a workbook mux value that names a table function is still followed (qup_l5), with the software-CS alternative offered", g9["pins"][0]["function"].startswith("qup_l") and not g9["pins"][0]["gpio_cs"] and "cs-gpios" in g9["text"], g9["pins"][0])

print("\n%d failed" % len(FAILS))
sys.exit(1 if FAILS else 0)
