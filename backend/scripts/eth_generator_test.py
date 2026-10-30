"""
Tests the Ethernet engine (app/eth_generator.py, app/dts_style.build_bundle(peripheral="ethernet")) on synthetic pins - no customer file needed.
Guarantees: pins are chosen by the SELECTED function (never the `interfaces` capability list); family filters work; a name-only pin is left out
unless the person includes it; function names come from the SoC table and a workbook mux value that disagrees is flagged; SGMII yields a note, not a
pin; the independent verifier catches tampering; the kernel format (flat vs nested) changes only the layout, never a value.
"""
import os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, ROOT)
from app import eth_generator as E, dts_style as D
from app.models import NormalizedRecord

FAILS = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)))
    if not cond:
        FAILS.append(name)


def rec(g, sel, sig, mux=None, pinmuxed=None, drv="3: DRV_8_MA", pull="0: NO_PULL", direction="0: IN", interfaces="GPIO, RGMII, EMAC", ig="ETH"):
    f = {"sa8155sip_ball_name": f"GPIO_{g}", "selected_interface": sel, "pinmuxed_name": pinmuxed or sel, "gen5_signal_name": sig,
         "interface_group": ig, "io_gpio_pull": pull, "io_drv_strength": drv, "direction": direction, "interfaces": interfaces}
    if mux is not None:                       # the SELECTED slot: 'PinMux Setting' = slot number, regsettingN = its mux value
        slot = 1 if mux == 0 else 3
        f["pinmux_setting"] = slot
        f["regsetting1"] = f"TLMM_GPIO_CFG{g}(5:2)=0"
        f[f"regsetting{slot}"] = f"TLMM_GPIO_CFG{g}(5:2)={mux}"
        f["port3"] = "RGMII_X/RMII_X"
    return NormalizedRecord(project="P", soc="SA8155P", row_index=g, fields=f)


RGMII = [rec(g, "RGMII", f"SOC_ETH:RGMII_{n}", mux=2) for g, n in ((114, "TXD3"), (115, "TXD2"), (116, "TXD1"), (117, "TXD0"))]
RGMII += [rec(118, "RGMII", "SOC_ETH:RGMII_TX_CTL", mux=1)]
ALL = RGMII + [
    rec(7, "GPIO", "SOC_ETH:ETH_MDC", mux=0, ig="ETH", interfaces="GPIO, RGMII, MDC"),             # name says Ethernet, selected is GPIO
    rec(59, "RGMII", "SOC_ETH:MDIO", mux=1),                                                         # MDIO selected as RGMII function
    rec(124, "EMAC_PHY", "SOC_ETH:PHY_INT_B", mux=1, pinmuxed="EMAC_PHY_INTR"),
    rec(10, "GPIO", "CPU_USB20_0_CTRL_1V8:USB0_OC_B", mux=0, ig="USB20_0", interfaces="GPIO, EMAC"),  # lists EMAC only as a capability
    rec(11, "SGMII", "SOC_SGMII:SGMII_TX_P", ig="SGMII"),
]
ALL[-1].fields["sa8155sip_ball_name"] = "SGMII_TX_P"

g = E.generate(ALL, "SA8155P", "P", board_wiring=False)      # Selected-Interface-only view (the board-wired rule is tested below)
gp = {p["gpio"] for p in g["pins"]}
check("selected RGMII/EMAC pins are generated", {"114", "115", "116", "117", "118", "59", "124"} <= gp, gp)
check("a pin that only LISTS Ethernet in interfaces is not pulled in", "10" not in gp and "7" not in gp, gp)
check("a pin only NAMED like Ethernet (selected GPIO) is not generated and not suggested", "7" not in gp and not any(x["pin"] == "gpio7" for x in g["left_out"]), g["left_out"])
fn = {p["gpio"]: p["function"] for p in g["pins"]}
check("functions come from the SoC table", fn["114"] == "rgmii" and fn["124"] == "emac_phy" and fn["59"] == "rgmii", fn)
check("SGMII gives a note and no pin", "11" not in gp and any("SerDes" in i["text"] for i in g["items"]), g["items"])
check("consumer node with pinctrl-0 and phy-mode review", "&ethernet {" in g["text"] and "pinctrl-0 = <" in g["text"] and "phy-mode" in g["text"])
check("RGMII phy-mode is a flagged assumed default, never silent", any("phy-mode" in l and "NEEDS REVIEW (assumed default)" in l for l in g["text"].split("\n")))

v = E.verify_text(g["text"], ALL, "SA8155P", board_wiring=False)
check("independent verifier agrees with a clean output", not v["wrong"] and v["ok"] == len(g["pins"]), v)

bad = g["text"].replace('function = "emac_phy"', 'function = "rgmii"')
check("verifier catches a wrong function", any("pin table" in w for w in E.verify_text(bad, ALL, "SA8155P")["wrong"]))
bad = re.sub(r"\tconfig \{\n(\t\t\tpins = \"gpio114\";\n)\t\t\tdrive-strength = <8>;", lambda m: "\tconfig {\n" + m.group(1) + "\t\t\tdrive-strength = <2>;", g["text"], count=1)
check("verifier catches a wrong drive strength", any("drive-strength" in w for w in E.verify_text(bad, ALL, "SA8155P")["wrong"]))
bad = re.sub(r"\teth_[a-z0-9_]*rgmii_txd0[^\n]*\{.*?\n\t\};\n", "", g["text"], count=1, flags=re.S)
check("verifier catches a missing pin", any("no pin state" in w or "not defined" in w for w in E.verify_text(bad, ALL, "SA8155P")["wrong"]), E.verify_text(bad, ALL, "SA8155P"))

# filters
only = E.generate(ALL, "SA8155P", "P", enabled={"mdio"}, board_wiring=False)
check("MDIO-only filter keeps exactly the MDIO pin", [p["gpio"] for p in only["pins"]] == ["59"], only["pins"])
nor = E.generate(ALL, "SA8155P", "P", enabled={"emac"})
check("EMAC-only filter keeps only the EMAC pin", [p["gpio"] for p in nor["pins"]] == ["124"], nor["pins"])
check("a switched-off family is stated", any("switched off" in x["reason"] for x in nor["left_out"]))

# include / exclude
inc = E.generate(ALL, "SA8155P", "P", include_pins=["GPIO_7"])
p7 = [p for p in inc["pins"] if p["gpio"] == "7"]
check("include_pins adds the pin as a GPIO with a review mark", p7 and p7[0]["function"] == "gpio" and "added by you" in inc["text"])
check("verifier accepts an included pin", not E.verify_text(inc["text"], ALL, "SA8155P", include_pins=["GPIO_7"])["wrong"], E.verify_text(inc["text"], ALL, "SA8155P", include_pins=["GPIO_7"]))
exc = E.generate(ALL, "SA8155P", "P", exclude_pins=["GPIO_114"])
check("exclude_pins drops the pin", "114" not in {p["gpio"] for p in exc["pins"]})

# mux disagreement
mm = [rec(114, "RGMII", "SOC_ETH:RGMII_TXD3", mux=3)]
gm = E.generate(mm, "SA8155P", "P")
check("workbook mux that disagrees with the table is flagged", "disagrees" in " ".join(i["text"] for i in gm["items"]) and "confirm which is right" in gm["text"], gm["items"])

# unknown SoC
gu = E.generate(ALL, "XYZ123", "P")
check("unknown SoC: RGMII still generated with review, EMAC not guessed", {"114"} <= {p["gpio"] for p in gu["pins"]} and "124" not in {p["gpio"] for p in gu["pins"]}, gu["pins"])
check("unknown SoC: controller label is a stated placeholder", "placeholder" in gu["text"])

# formats: kernel >= 6.2 flat, <= 6.1 nested; values identical
flat = D.Style(role="pinmux", layout="flat", name_style="hyphen_state")
b_def = D.build_bundle(g["text"], "P", {}, peripheral="ethernet")
b_flat = D.build_bundle(g["text"], "P", {"pinmux": flat}, peripheral="ethernet")
names = [f["name"] for f in b_def["files"]]
check("files are named for Ethernet", names == ["p_ethernet_pinmux.dtsi", "p_ethernet_overlay.dts"], names)
vf = E.verify_text("\n".join(f["text"] for f in b_flat["files"]), ALL, "SA8155P")
check("flat-format files verify with the same values", not vf["wrong"] and vf["ok"] == len(g["pins"]), vf)
check("flat format has no mux{} / config{}", "mux {" not in b_flat["files"][0]["text"])

# Ethernet written only in some other column (a remark, a custom column), or only as a name: never a pin state, never suggested
oth = rec(30, "GPIO", "CPU_X:SPARE_30", mux=0, interfaces="GPIO", ig="MISC")
oth.fields["customer_note"] = "RGMII RXC to the PHY"
pps = rec(124, "GPIO", "CPU_GNSS_CTRL_1V8:GNSS_1PPS", mux=0, interfaces="GPIO, EMAC", ig="GNSS")
go = E.generate([oth, pps] + RGMII, "SA8155P", "P")
check("Ethernet in another column never makes a pin state", "30" not in {p["gpio"] for p in go["pins"]})
check("...but it is SUGGESTED (possible), with the column and value as evidence", any(x.get("gpio") == "30" and x["tier"] == "possible" and x["evidence"][0]["column"] == "customer_note" for x in go["left_out"]), go["left_out"])
check("GNSS 1PPS is not mistaken for an Ethernet PPS pin", "124" not in {p["gpio"] for p in go["pins"]})
pw = rec(40, "SGMII", "CPU_ETH_SGMII:TX_P", ig="SGMII"); pw.fields["sa8155sip_ball_name"] = "SGMII_TX_P"
gl = E.generate([pw], "SA8155P", "P")
check("an SGMII lane gives a note, no pin state and no table row", not gl["pins"] and not gl["left_out"] and any("SGMII lane" in i["text"] for i in gl["items"]), gl["items"])
# the real-workbook shape: slots 1..3 present, the selected one named by pinmux_setting - NOT the first regsetting
real = rec(4, "RGMII", "CPU_ETH_RGMII_1V8:RD1", mux=2)
check("mux value comes from the selected slot, not the first regsetting", E._mux_value(real.fields, "4") == 2, E._mux_value(real.fields, "4"))
check("no slot number -> no mux value (never guessed)", E._mux_value({"regsetting1": "TLMM_GPIO_CFG4(5:2)=0"}, "4") is None)
check("a correct pin is NOT flagged as a mux mismatch", "disagrees" not in " ".join(i["text"] for i in E.generate([real], "SA8155P", "P")["items"]))
sup = rec(9, "PWR", "PWR_SOC:L4A_VDD_A", ig="PWR"); sup.fields["pinmuxed_name"] = "VDD_A_SGMII_0_0P9"
check("a supply pin named VDD_A_SGMII is not an Ethernet pin", not E.generate([sup], "SA8155P", "P")["pins"] and not E.classify([sup], set(E.ORDER))["selected"])
check("Selected Interface alone decides: a named pin whose selected function is GPIO is not generated", "7" not in {p["gpio"] for p in E.generate(ALL, "SA8155P", "P", board_wiring=False)["pins"]})
check("with the board-wired rule on, the named ETH_MDC pin (group ETH + ETH signal) IS generated, as a GPIO", {p["gpio"]: p["status"] for p in E.generate(ALL, "SA8155P", "P")["pins"]}.get("7") == "wired")
cap = rec(81, "WUP_GPIO,PDC", "CPU_X:GPIO_LOCK", ig="VI0123"); cap.fields["port3"] = "RGMII_RXD2"; cap.fields["instance3"] = "RGMII"
gc = E.generate([cap], "SA8155P", "P")
check("alternate-function-only pins give no pin and no 'added' offer, but a count", not gc["pins"] and not gc["left_out"] and any("alternate function" in i["text"] for i in gc["items"]), (gc["left_out"], gc["items"]))
# board-wired control lines: Selected Interface is GPIO / WUP_GPIO, but Interface Group AND Gen5 signal say ETH
w1 = rec(59, "GPIO", "CPU_ETH_CTRL_1V8:MDIO", mux=0, ig="CPU_ETH", interfaces="GPIO, RGMII")
w2 = rec(79, "GPIO", "CPU_ETH_CTRL_1V8:RST_B", mux=0, ig="CPU_ETH", direction="1: OUT")
w3 = rec(124, "WUP_GPIO,PDC", "CPU_ETH_CTRL_1V8:INT_B", mux=0, ig="CPU_ETH")
oth_g = rec(38, "WUP_GPIO,PDC", "SA8155_USB_CFG:USB_PHY_PORT_SELECT", mux=0, ig="USB_DP_PORT_SEL")      # 'PHY' alone is not Ethernet
grp_only = rec(41, "GPIO", "CPU_X_CTRL:SPARE", mux=0, ig="CPU_ETH")                                         # group without an ETH signal
sig_only = rec(42, "GPIO", "CPU_ETH_CTRL_1V8:RST_B", mux=0, ig="MISC")                                       # Gen5 says ETH, group does not: still wired
pcie_eth = rec(43, "PCIE1", "CPU_ETH_PCIE_CTRL_1V8:PCIE_WAKE_B", mux=2, ig="MISC")                          # ETH in the name but set to PCIe: never turned into a GPIO
gw = E.generate([w1, w2, w3, oth_g, grp_only, sig_only, pcie_eth] + RGMII, "SA8155P", "P")
gws = {p["gpio"]: p for p in gw["pins"]}
check("board-wired MDIO / reset / interrupt are generated as GPIO pin states", {"59", "79", "124"} <= set(gws) and all(gws[k]["function"] == "gpio" for k in ("59", "79", "124")), gws.keys())
check("wired pins are marked 'wired' and keep their family (MDIO -> mdio)", gws["59"]["status"] == "wired" and gws["59"]["family"] == "mdio" and gws["79"]["family"] == "emac")
check("USB 'PHY' pin and a group-only pin are NOT wired Ethernet", "38" not in gws and "41" not in gws and "43" not in gws, gws.keys())
check("Gen5 signal alone (group not ETH) makes a GPIO-selected pin wired", "42" in gws and gws["42"]["function"] == "gpio", gws.keys())
check("independent verifier accepts wired pins", not E.verify_text(gw["text"], [w1, w2, w3, oth_g, grp_only, sig_only, pcie_eth] + RGMII, "SA8155P")["wrong"], E.verify_text(gw["text"], [w1, w2, w3, oth_g, grp_only, sig_only, pcie_eth] + RGMII, "SA8155P"))
off = E.generate([w1, w2, w3] + RGMII, "SA8155P", "P", board_wiring=False)
check("board_wiring=False restores Selected-Interface-only", not {"59", "79", "124"} & {p["gpio"] for p in off["pins"]})
mo = E.generate([w1, w2, w3] + RGMII, "SA8155P", "P", enabled={"rgmii"})
check("a switched-off family keeps wired pins of that family out", "59" not in {p["gpio"] for p in mo["pins"]} and "79" not in {p["gpio"] for p in mo["pins"]})
# candidates: tiers, noise filters
lk = rec(50, "GPIO", "", mux=0, ig="ETH_CPU"); lk.fields["remarks"] = "MDIO to the switch"
neg = rec(51, "GPIO", "", mux=0, ig="MISC"); neg.fields["remarks"] = "MDIO not in use on this board"
rail = rec(52, "SPI", "MCU_CPU_INC_SPI:SPI_MISO", mux=2, ig="MCU_CPU"); rail.fields["imported_external_termination"] = "SPI_MISO R52186(10k) PWR:3V3S_MCU_ETH"
old = rec(53, "GPIO", "", mux=0, ig="MISC"); old.fields["old_import_schematic_signal_name"] = "SAIL_ETH_RGMII_1V8:RGMII_TXCLK"
sail = rec(54, "SAIL", "", ig="MISC"); sail.fields["sa8155sip_ball_name"] = "SAILIO24"; sail.fields["qti_ref_aasignal"] = "SAIL_RGMII_TXC"
gc2 = E.generate([lk, neg, rail, old, sail] + RGMII, "SA8155P", "P")
lo = {x["gpio"]: x for x in gc2["left_out"] if x.get("gpio")}
check("group ETH + wording in a live column -> likely", lo.get("50", {}).get("tier") == "likely", lo.keys())
check("a note saying 'not in use' keeps it at possible", lo.get("51", {}).get("tier") == "possible", lo.get("51"))
check("a power-rail name (PWR:..._ETH) is not Ethernet wording", "52" not in lo)
check("wording only in an old-import column is never likely", lo.get("53", {}).get("tier") == "possible", lo.get("53"))
check("candidates are never generated", not ({"50", "51", "52", "53"} & {p["gpio"] for p in gc2["pins"]}))
check("a non-TLMM pin with Ethernet wording gives an info note, no pin", any("without a TLMM GPIO number" in i["text"] for i in gc2["items"]), gc2["items"])
inc2 = E.generate([lk, neg] + RGMII, "SA8155P", "P", include_pins=["gpio50"])
check("adding a suggested pin generates it as a GPIO and the verifier accepts it", "50" in {p["gpio"] for p in inc2["pins"]} and not E.verify_text(inc2["text"], [lk, neg] + RGMII, "SA8155P", include_pins=["gpio50"])["wrong"])
# SGMII only (no TLMM pins): the controller node is still generated, phy-mode taken from the workbook
sg0 = rec(7, "SGMII", "ETH:SGMII_RX_P", pinmuxed="SGMII0_RX_P")
sg0.fields.pop("sa8155sip_ball_name", None)              # an SerDes lane has no TLMM GPIO number
gsg = E.generate([sg0], "SA8155P", "P")
check("SGMII only: no pin state but an &ethernet node with phy-mode = sgmii decided from the workbook", not gsg["pins"] and "&ethernet {" in gsg["text"] and 'phy-mode = "sgmii";\t/* decided:' in gsg["text"] and 'status = "okay"' in gsg["text"], gsg["text"])
check("SGMII only: no &tlmm block is written", "&tlmm" not in gsg["text"])
gout = rec(9, "GPIO", "ETH:PHY_RST", pinmuxed="GPIO_9", direction="1: OUT"); gout.fields["resetvalue"] = "1: high"
gx = E.generate(RGMII + [gout], "SA8155P", "P", include_pins=["gpio9"])["text"]
check("ResetValue gives an Ethernet GPIO output level, flagged with its source", 'output-high;\t/* NEEDS REVIEW (evidence: Excel ResetValue "1: high")' in gx, gx)
print("\nFAILED: %d" % len(FAILS) if FAILS else "\nAll Ethernet tests passed")
sys.exit(1 if FAILS else 0)
