"""
The generic generator must write every pin's OWN values. Regression tests for three real defects found on the Honda workbook:
  1. a node took drive strength / bias / direction from its FIRST pin and applied them to all pins of the group;
  2. every output GPIO got `output-high` although the workbook only says OUT (the boot level is a hardware fact);
  3. pads that are not TLMM pins (DSI / CSI / USB PHY / UFS / JTAG ...) were written into &tlmm with invented functions.
Synthetic records first (no customer data), then the real Honda workbook when it is available.
"""
import os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.join(HERE, "..")
sys.path.insert(0, ROOT); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(ROOT, "tool"))
from app.models import NormalizedRecord
from app.generators import dts_generator as G
from app.generators.dts_updater import update_dts_file

FAILS = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)))
    if not cond:
        FAILS.append(name)


def gpio(n, sig, direction, pull, drv, row):
    return NormalizedRecord(project="P", soc="SA6155P", row_index=row, fields={
        "soc_ball_name": f"GPIO_{n}", "pinmuxed_name": f"GPIO_{n}", "selected_interface": "gpio", "interface_group": "G",
        "gen5_signal_name": sig, "direction": direction, "io_gpio_pull": pull, "io_drv_strength": drv,
        "regsetting1": f"TLMM_GPIO_CFG{n}(5:2)=0"})


def pad(name, row):
    return NormalizedRecord(project="P", soc="SA6155P", row_index=row, fields={
        "soc_ball_name": name, "pinmuxed_name": name, "selected_interface": "DSI0", "interface_group": "Display"})


recs = [
    gpio(28, "BUS_A_1V8:IN_ONE", "0: IN", "1: PULL_DOWN", "0: DRV_2_MA", 1),
    gpio(29, "BUS_A_1V8:IN_TWO", "0: IN", "3: PULL_UP", "3: DRV_8_MA", 2),
    gpio(30, "BUS_B_3V3:RST_B, SA6150_BOOTCFG_1V8:MD01", "1: OUT", "0: NO_PULL", "3: DRV_8_MA", 3),
    gpio(31, "BUS_B_3V3:EN", "1: OUT", "0: NO_PULL", "4: DRV_10_MA", 4),
    pad("DSI0CLKN", 5), pad("USB0_HS_DM", 6),
]
txt, amb, rev = G.generate_dts("P", "SA6155P", recs)
nodes = {m.group(1): m.group(0) for m in re.finditer(r"\n\t(\w+): \1 \{.*?\n\t\};", txt, re.S)}


def node_of(pin):
    return next((b for b in nodes.values() if f'pins = "{pin}"' in b), "")


print("== 1. each pin has its own values")
check("gpio28 pull-down 2 mA", "drive-strength = <2>;" in node_of("gpio28") and "bias-pull-down;" in node_of("gpio28"), node_of("gpio28"))
check("gpio29 pull-up 8 mA (not the first pin's)", "drive-strength = <8>;" in node_of("gpio29") and "bias-pull-up;" in node_of("gpio29"), node_of("gpio29"))
check("gpio31 keeps its own 10 mA", "drive-strength = <10>;" in node_of("gpio31"), node_of("gpio31"))
check("pins with different settings are never in one node", all(len(re.findall(r'"gpio\d+"', b)) == 2 for b in nodes.values()))
check("a plain GPIO node is named after its signal, not after a boot-strap name", "bus_b_rst_b" in nodes and not any("bootcfg" in k for k in nodes), list(nodes))

print("== 2. no guessed output level")
check("no output-high / output-low line is invented", not re.search(r"^\s*output-(high|low);", txt, re.M))
check("each output pin carries a NEEDS REVIEW marker", "NEEDS REVIEW: output pin" in node_of("gpio30") and "NEEDS REVIEW: output pin" in node_of("gpio31"))
check("input pins keep input-enable and no marker", "input-enable;" in node_of("gpio28") and "NEEDS REVIEW" not in node_of("gpio28"))
check("the review list names the missing output level", any("output level" in " ".join(e["missing_fields"]) for e in rev), rev)

print("== 3. non-TLMM pads")
check("pads are not written as pin states", "dsi0clkn" not in txt.split("NOT GENERATED")[0] and "usb0hsdm" not in txt.split("NOT GENERATED")[0])
check("pads are listed as not generated", "NOT GENERATED" in txt and "dsi0clkn" in txt and "usb0hsdm" in txt)
no_regs = [pad("DSI0CLKN", 1)]
no_regs[0].fields["selected_interface"] = "DSI0"
t2, _a, _r = G.generate_dts("P", "SA6155P", no_regs)
check("a workbook without register data drops nothing on this basis", "dsi0clkn" in t2.split("NOT GENERATED")[0], t2)

print("== 4. a node can never mix settings")
try:
    G.render_node("gpio", "in", "P", [recs[0], recs[1]], set())
    check("render_node refuses pins with different settings", False)
except ValueError:
    check("render_node refuses pins with different settings", True)

print("== 5. the updater never replaces an existing output level")
existing = '''&tlmm {
\tmy_rst: my_rst {
\t\tmux {
\t\t\tpins = "gpio30";
\t\t\tfunction = "gpio";
\t\t};
\t\tconfig {
\t\t\tpins = "gpio30";
\t\t\tdrive-strength = <2>;
\t\t\tbias-pull-up;
\t\t\toutput-low;
\t\t};
\t};
};
'''
res = update_dts_file(existing, G.build_pin_index(recs))
check("the node was updated (drive/bias differ)", res.node_results and res.node_results[0].changed)
check("output-low is kept, no output-high appears", "output-low;" in res.updated_text and "output-high" not in res.updated_text, res.updated_text)

print("== 6. real Honda workbook")
import _samples
XL = os.path.join(ROOT, "samples", "Honda_database_v2.xlsx")
if not os.path.exists(XL):
    print("SKIPPED (customer sample not present)")
else:
    import usb_dts_tool as T
    parsed, records, soc = T.load_records(XL, "Honda_GenID_B0")
    txt, amb, rev = G.generate_dts("Honda_GenID_B0", soc, records)
    by = {str(r.fields.get("soc_ball_name")): r for r in records}
    nds = re.findall(r"\n\t(\w+): \1 \{\n\t\tmux \{\n\t\t\tpins = ([^;]*);\n\t\t\tfunction = \"(\w+)\";\n\t\t\};\n\n\t\tconfig \{\n\t\t\tpins = ([^;]*);\n(.*?)\n\t\t\};", txt, re.S)
    bad, seen = 0, {}
    for lab, mp, fn, cp, props in nds:
        for p in re.findall(r'"(\w+)"', mp):
            seen[p] = seen.get(p, 0) + 1
            r = by[p.upper().replace("GPIO", "GPIO_")]
            drv = re.search(r"DRV_(\d+)_MA", r.fields["io_drv_strength"]).group(1)
            pull = {"NO_PULL": "bias-disable", "PULL_UP": "bias-pull-up", "PULL_DOWN": "bias-pull-down"}[r.fields["io_gpio_pull"].split(": ")[1]]
            ok = f"drive-strength = <{drv}>;" in props and pull + ";" in props
            if fn == "gpio":
                d = r.fields["direction"]
                is_in = "IN" in d and "OUT" not in d
                ok = ok and (("input-enable;" in props) if is_in else ("NEEDS REVIEW: output pin" in props))
            bad += 0 if ok else 1
    check("every Honda GPIO has exactly its own workbook values", bad == 0, bad)
    check("115 GPIO pins, each exactly once", len(seen) == 115 and all(c == 1 for c in seen.values()), (len(seen), [p for p, c in seen.items() if c > 1]))
    check("no pad is written into &tlmm", not re.search(r'"(ssc\d+|dsi0\w+|usb0hs\w+|ufsl0\w+|tck|tdo)"', txt.split("NOT GENERATED")[0]))
    check("no invented output level", not re.search(r"^\s*output-(high|low);", txt, re.M))

print("\n%s: %d failure(s)" % ("FAILED" if FAILS else "ALL PASSED", len(FAILS)))
sys.exit(1 if FAILS else 0)
