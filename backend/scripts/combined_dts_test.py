"""
The combined DTS (app/combined_dts.py): merges the per-peripheral files into one pin-state file and one consumer file and checks what no single engine
can see - the same GPIO or label claimed by two peripherals, a controller node written twice, a pinctrl reference that points nowhere.
Synthetic text only - no customer data.
"""
import os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from app import combined_dts as C, dts_style as D

FAILS = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)))
    if not cond:
        FAILS.append(name)


def node(label, gpio, fn="gpio"):
    return f"\t{label}: {label} {{\n\t\tmux {{\n\t\t\tpins = \"gpio{gpio}\";\n\t\t\tfunction = \"{fn}\";\n\t\t}};\n\t\tconfig {{\n\t\t\tpins = \"gpio{gpio}\";\n\t\t\tdrive-strength = <8>;\n\t\t\tbias-disable;\n\t\t}};\n\t}};"


def part(per, nodes, consumers):
    text = "&tlmm {\n" + "\n\n".join(nodes) + "\n};" + ("\n\n" + consumers if consumers else "")
    b = D.build_bundle(text, "P", {}, generator="t", peripheral=per)
    return {"peripheral": per, "files": b["files"]}


usb = part("usb", [node("usb_oc", 81), node("usb_en", 82)], "&usb0 {\n\tstatus = \"okay\";\n\tpinctrl-names = \"default\";\n\tpinctrl-0 = <&usb_oc &usb_en>;\n};")
eth = part("ethernet", [node("eth_mdc", 60, "rgmii"), node("eth_mdio", 61, "rgmii")], "&ethernet {\n\tpinctrl-names = \"default\";\n\tpinctrl-0 = <&eth_mdc &eth_mdio>;\n};")
spi = part("spi", [node("spi0_miso", 0, "qup0"), node("spi0_mosi", 1, "qup0")], "&spi0 {\n\tstatus = \"okay\";\n\tpinctrl-names = \"default\";\n\tpinctrl-0 = <&spi0_miso &spi0_mosi>;\n};")

m = C.merge([usb, eth, spi], "P")
names = [f["name"] for f in m["files"]]
check("one pin-state file and one consumer file", names == ["p_all_pinmux.dtsi", "p_all_overlay.dts"], names)
pm, ov = m["files"][0]["text"], m["files"][1]["text"]
check("a single &tlmm block holds every pin state", len(re.findall(r"^&tlmm \{", pm, re.M)) == 1 and m["pin_states"] == 6 and all(x in pm for x in ("usb_oc", "eth_mdc", "spi0_miso")), pm)
check("each peripheral has its own commented section, in order", [pm.index("USB pin states"), pm.index("ETHERNET pin states"), pm.index("SPI pin states")] == sorted([pm.index("USB pin states"), pm.index("ETHERNET pin states"), pm.index("SPI pin states")]))
check("every controller node is in the one consumer file", all(x in ov for x in ("&usb0 {", "&ethernet {", "&spi0 {")) and ov.count("=====") >= 6)
check("no conflicts for clean input", not m["conflicts"], m["conflicts"])
check("the per-peripheral pin counts are reported", {s["peripheral"]: s["pin_states"] for s in m["sections"]} == {"usb": 2, "ethernet": 2, "spi": 2}, m["sections"])
# the merged text is itself valid for the single-peripheral readers
from app import eth_generator, spi_generator
nodes, _un = D.parse_pin_nodes(D._inner(D.top_level_blocks(D._norm(pm))[0]["text"]))
check("the merged &tlmm parses back into all six nodes", len(nodes) == 6, len(nodes))

# conflicts
spi_dup = part("spi", [node("spi0_miso", 81, "qup0")], "&spi0 {\n\tpinctrl-0 = <&spi0_miso>;\n};")
c = C.merge([usb, eth, spi_dup], "P")["conflicts"]
check("the same GPIO in two peripherals is a conflict", any("gpio81" in x and "USB" in x and "SPI" in x for x in c), c)
eth_dup = part("ethernet", [node("usb_oc", 70, "rgmii")], "&ethernet {\n\tpinctrl-0 = <&usb_oc>;\n};")
c = C.merge([usb, eth_dup], "P")["conflicts"]
check("the same node label in two peripherals is a conflict", any("usb_oc" in x and "label" in x for x in c), c)
twice = part("spi", [node("spi0_x", 5, "qup0")], "&usb0 {\n\tpinctrl-0 = <&spi0_x>;\n};")
c = C.merge([usb, twice], "P")["conflicts"]
check("a controller node written by two peripherals is a conflict", any("&usb0" in x and "both" in x for x in c), c)
broken = part("spi", [node("spi0_a", 5, "qup0")], "&spi0 {\n\tpinctrl-0 = <&spi0_a &spi0_missing>;\n};")
c = C.merge([broken], "P")["conflicts"]
check("a pinctrl reference to a state nobody defines is a conflict", any("spi0_missing" in x for x in c), c)

# nothing selected for a peripheral -> it is simply absent, the others still merge
mm = C.merge([usb, {"peripheral": "ethernet", "files": []}, spi], "P")
check("a peripheral with no files is skipped; the rest still merge", mm["pin_states"] == 4 and not mm["conflicts"] and {s["peripheral"] for s in mm["sections"]} == {"usb", "spi"}, mm["sections"])
check("nothing at all -> no files", C.merge([{"peripheral": "usb", "files": []}], "P")["files"] == [])
# flat-format input merges too
flat = D.Style(role="pinmux", layout="flat", name_style="hyphen_state")
bf = D.build_bundle("&tlmm {\n" + node("spi0_miso", 0, "qup0") + "\n};", "P", {"pinmux": flat}, generator="t", peripheral="spi")
mf = C.merge([usb, {"peripheral": "spi", "files": bf["files"]}], "P")
check("different layouts still merge without losing a pin", mf["pin_states"] == 3 and not mf["conflicts"], mf["conflicts"])

# ---- left-out footer and report sections (tool level, synthetic result)
sys.path.insert(0, os.path.join(HERE, "..", "tool"))
import combined_dts_tool as CT
class _R: pass
fake = CT.CombinedResult()
fake.peripherals = [{"peripheral": "ethernet", "status": "needs_decisions", "headline": "h", "pin_states": 1, "files": ["a"], "notes": [],
                     "decisions": ["phy address is not in the pin data"],
                     "left_out": [{"pin": "gpio9", "signal": "X", "reason": "function name unknown - not guessed", "tier": None},
                                  {"pin": "gpio7", "signal": "Y", "reason": "possible: wording only", "tier": "possible"}]}]
fake.decisions = [{"peripheral": "ethernet", "text": "phy address is not in the pin data"}]
fake.left_out = [{"peripheral": "ethernet", **x} for x in fake.peripherals[0]["left_out"]]
fake.overview = {"headline": "H"}
ft = CT._footer(fake)
check("footer lists a stated-reason left-out pin and only counts the wording-only ones", "gpio9" in ft and "gpio7" not in ft and "1 weak candidate" in ft, ft)
check("footer is a closed comment", ft.strip().startswith("/*") and ft.strip().endswith("*/") and ft.count("*/") == 1, ft)
class _A: project = "P"
rt = CT.render_report(fake, _A)
check("report has an open-decisions section and a left-out table", "## Open decisions" in rt and "phy address" in rt and "## Left out" in rt and "| ETHERNET | gpio9 |" in rt, rt)

# ---- the same QUP wrapper enabled by two peripherals (SPI and UART) is written once, not a conflict; a differing body still is
def _ovp(per, body):
    return {"peripheral": per, "files": [{"name": per + "_overlay.dts", "role": "overlay", "text": body}]}
w1 = '&qupv3_id_0 {\n\tstatus = "okay";\n};\n\n&spi0 {\n\tstatus = "okay";\n};\n'
w2 = '&qupv3_id_0 {\n\tstatus = "okay";\n};\n\n&uart2 {\n\tstatus = "okay";\n};\n'
mw = C.merge([_ovp("spi", w1), _ovp("uart", w2)], "P")
ovt = [f for f in mw["files"] if f["role"] == "overlay"][0]["text"]
check("identical QUP wrapper in SPI and UART: written once, no conflict", ovt.count("&qupv3_id_0 {") == 1 and not mw["conflicts"] and "&spi0 {" in ovt and "&uart2 {" in ovt, (mw["conflicts"], ovt))
w3 = '&qupv3_id_0 {\n\tstatus = "okay";\n\tfoo;\n};\n'
mw2 = C.merge([_ovp("spi", w1), _ovp("uart", w3)], "P")
check("a wrapper whose bodies differ is still a conflict", any("qupv3_id_0" in c for c in mw2["conflicts"]), mw2["conflicts"])

# a weak candidate of one engine is dropped when ANOTHER engine already wrote that GPIO; stated reasons and own-engine rows stay
pm = {"peripheral": "spi", "files": [{"name": "s.dtsi", "role": "pinmux", "text": '&tlmm {\n\tx: x {\n\t\tmux {\n\t\t\tpins = "gpio88";\n\t\t};\n\t};\n};\n'}]}
pers = [{"peripheral": "spi", "left_out": [{"pin": "gpio7", "reason": "possible: SPI wording", "tier": "possible"}]},
        {"peripheral": "i2c", "left_out": [{"pin": "gpio88", "reason": "possible: I2C wording", "tier": "possible"},
                                         {"pin": "gpio17", "reason": "CCI is the camera control interface", "tier": None},
                                         {"pin": "gpio7", "reason": "possible: I2C wording", "tier": "possible"}]}]
pers[0]["left_out"].append({"pin": "gpio17", "reason": "possible: SPI wording", "tier": "possible"})
CT._drop_claimed_candidates(pers, [pm])
check("a weak candidate for a GPIO another engine wrote is dropped", [x["pin"] for x in pers[1]["left_out"]] == ["gpio17", "gpio7"], pers[1]["left_out"])
check("a weak candidate for a GPIO another engine left out for a stated reason is dropped; own-engine and unclaimed rows stay", [x["pin"] for x in pers[0]["left_out"]] == ["gpio7"], pers[0]["left_out"])
print("\nFAILED: %d" % len(FAILS) if FAILS else "\nAll combined-DTS tests passed")
sys.exit(1 if FAILS else 0)
