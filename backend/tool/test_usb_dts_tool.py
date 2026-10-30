#!/usr/bin/env python3
"""
End-to-end tests: runs the real CLI against real sample workbooks/DTS files
(in ../samples) and checks the OUTPUT TEXT. Ground truth used:
  * CCS20: the project's real existing labels (usbv3_fault_detect etc.),
    GPIO_81/82/102, "4: DRV_10_MA" -> 10.
  * Suzuki: the accepted Gerrit change (change 1200001) - two regulator-fixed
    nodes, gpio 56 ACTIVE_LOW without enable-active-high, gpio 55 ACTIVE_HIGH
    with enable-active-high, 5 V.
Run:  python tool/test_usb_dts_tool.py
"""
import json, os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(ROOT, 'scripts')); import _samples; _samples.need(__file__)
S = os.path.join(ROOT, "samples")
TOOL = os.path.join(HERE, "usb_dts_tool.py")
CCS20_XL = os.path.join(S, "Nissan_CCS20_SA8155PSIP_PinMux.xlsx")
HONDA_XL = os.path.join(S, "Honda_database_v2.xlsx")
CCS20_DTS = os.path.join(S, "ccs20-pinmux.dtsi")
SUZ_PIN = os.path.join(S, "suzuki-pinmux.dtsi")
SUZ_OVL = os.path.join(S, "suzuki-overlay.dts")


def run(args, expect_ok=True):
    p = subprocess.run([sys.executable, TOOL] + args, capture_output=True, text=True, cwd=ROOT)
    if expect_ok and p.returncode != 0:
        raise AssertionError(f"tool failed ({p.returncode}):\n{p.stdout}\n{p.stderr}")
    return p


def has_level(node):
    """True only for a REAL output-level directive line (not the words in a comment)."""
    return bool(re.search(r"^\s*output-(high|low)\s*;", node, re.MULTILINE))


def blk(text, ref):
    """The whole `&usbN { ... };` block, wherever it sits in the file."""
    m = re.search(rf"^{re.escape(ref)} \{{.*?\n\}};", text, re.DOTALL | re.MULTILINE)
    assert m, f"{ref} block not found"
    return m.group(0)


def node_text(text, label):
    m = re.search(rf"^[ \t]*{re.escape(label)}\s*:\s*{re.escape(label)}\s*\{{", text, re.MULTILINE)
    assert m, f"node {label} not found"
    i, depth = text.index("{", m.start()), 0
    for k in range(i, len(text)):
        depth += (text[k] == "{") - (text[k] == "}")
        if depth == 0:
            return text[m.start():k + 1]


tmp = tempfile.mkdtemp()
o = lambda n: os.path.join(tmp, n)

# ------------------------------------------------------------------ 1
print("=== 1. CCS20 update case: minimal patch, existing labels reused, no duplicates ===")
run(["--excel", CCS20_XL, "--project", "CCS20", "--dts", CCS20_DTS, "--out", o("c.dtsi"), "--report", o("c.md")])
orig, out = open(CCS20_DTS).read(), open(o("c.dtsi")).read()
for lbl, pin in (("usbv3_power_enable", "gpio82"), ("usbv3_fault_detect", "gpio81"), ("usbv2_fault_detect", "gpio102")):
    assert "drive-strength = <2>;" in node_text(orig, lbl)
    assert "drive-strength = <10>;" in node_text(out, lbl), f"{lbl} not updated to Excel's 10"
    assert "drive-strength = <2>;" not in node_text(out, lbl)
# property ORDER / comments untouched -> patched node differs from original ONLY in the value
for lbl in ("usbv3_power_enable", "usbv3_fault_detect", "usbv2_fault_detect"):
    assert node_text(orig, lbl).replace("<2>", "<10>") == node_text(out, lbl), f"{lbl}: more than the value changed"
assert "&usb0 {" in out and "&usb1 {" in out
assert "pinctrl-0 = <&usbv3_fault_detect &usbv3_power_enable>;" in out, "must reference the EXISTING labels"
assert "pinctrl-0 = <&usbv2_fault_detect>;" in out
assert "oc-gpio = <&tlmm 81 GPIO_ACTIVE_LOW>;" in out and "vbus-out-gpio = <&tlmm 82 GPIO_ACTIVE_HIGH>;" in out
assert "oc-gpio = <&tlmm 102 GPIO_ACTIVE_LOW>;" in out
usb1 = blk(out, "&usb1")
assert "vbus-out-gpio =" not in usb1, "GPIO_92 is shared - must not be fabricated as usb1's VBUS"
assert "dwc3@" not in usb1.split("NEEDS REVIEW")[0], "dwc3 child must not be emitted for an unconfirmed instance"
assert "None" not in out and out.count("{") == out.count("}")
labels = re.findall(r"^\s*([A-Za-z_]\w*)\s*:\s*[\w@,.\-]+\s*\{", out, re.MULTILINE)
assert len(labels) == len(set(labels)), "duplicate labels introduced"
assert "usb0_oc_active" not in out, "must NOT create duplicate pinctrl states for pins that already have nodes"
for hub in ("microchip", "usb2514", "usb-phy-port-select"):
    assert hub not in out, f"invented content: {hub}"
print("OK")

# ------------------------------------------------------------------ 2
print("=== 2. Idempotence: running on its own output changes nothing ===")
run(["--excel", CCS20_XL, "--project", "CCS20", "--dts", o("c.dtsi"), "--out", o("c2.dtsi"), "--report", o("c2.md")])
assert open(o("c.dtsi")).read() == open(o("c2.dtsi")).read()
assert "already exists - left untouched" in open(o("c2.md")).read()
print("OK")

# ------------------------------------------------------------------ 3
print("=== 3. Missing nodes are GENERATED (no duplicates, function gpio, no guessed output level) ===")
stripped = orig
for lbl in ("usbv3_power_enable", "usbv3_fault_detect", "usbv2_fault_detect"):
    stripped = stripped.replace(node_text(stripped, lbl), "")
open(o("stripped.dtsi"), "w").write(stripped)
run(["--excel", CCS20_XL, "--project", "CCS20", "--dts", o("stripped.dtsi"), "--pattern", "controller",
     "--out", o("g.dtsi"), "--report", o("g.md")])
g = open(o("g.dtsi")).read()
for lbl in ("usbv3_power_enable", "usbv3_fault_detect", "usbv2_fault_detect"):
    assert re.search(rf"^\s*{lbl}\s*:\s*{lbl}\s*\{{", g, re.MULTILINE), \
        f"{lbl}: generated names must follow the project's own convention (they equal the original labels)"
assert "pinctrl-0 = <&usbv3_fault_detect &usbv3_power_enable>;" in g
assert len(re.findall(r"^&tlmm\s*\{", g, re.MULTILINE)) == len(re.findall(r"^&tlmm\s*\{", stripped, re.MULTILINE)), \
    "new states must go INSIDE the existing &tlmm block, not in a new one"
vb = node_text(g, "usbv3_power_enable")
assert 'function = "gpio"' in vb and "drive-strength = <10>;" in vb
assert not has_level(vb) and "NEEDS REVIEW" in vb, "controller pattern: output level must not be guessed"
assert "input-enable;" in node_text(g, "usbv3_fault_detect")
print("OK")

# ------------------------------------------------------------------ 4
print("=== 4. No evidence + no --pattern => still generates real electrical config, just not the consumer wiring ===")
r4 = run(["--excel", HONDA_XL, "--project", "Suzuki_DA3_9444h02", "--dts", SUZ_PIN,
         "--out", o("s0.dtsi"), "--report", o("s0.md")])
assert "Pattern: unconfirmed" in r4.stdout
assert "Updated values: 2" in r4.stdout, "only the two USB-runtime OC pins should be patched"
assert "new pinctrl states: 3" in r4.stdout
s0 = open(o("s0.dtsi")).read()
assert "usbv2_0_power_enable" in s0 and "usbv2_1_power_enable" in s0, "real vbus pin states must still be generated"
assert "&usb0 {" not in s0 and "&usb1 {" not in s0, "no consumer block may be fabricated without a pattern decision"
m4 = open(o("s0.md")).read()
assert "NOT wired into any consumer - pattern unconfirmed" in m4
assert "GPIO_101" in m4 and "USB match came only from a boot-strap signal" in m4
print("OK")

# ------------------------------------------------------------------ 4b
print("=== 4b. GENUINELY CONFLICTING evidence (both patterns found) still stops hard ===")
conflict_dts = o("conflict.dtsi")
open(conflict_dts, "w").write(
    "&usb0 { oc-gpio = <&tlmm 1 GPIO_ACTIVE_LOW>; };\n"
    "usb0_vbus_regulator: usb0_vbus_regulator { compatible = \"regulator-fixed\"; "
    "gpio = <&tlmm 2 GPIO_ACTIVE_HIGH>; };\n")
p4b = run(["--excel", HONDA_XL, "--project", "Suzuki_DA3_9444h02", "--dts", conflict_dts,
          "--out", o("s0b.dtsi"), "--report", o("s0b.md")], expect_ok=False)
assert p4b.returncode != 0 and "ambiguous" in (p4b.stdout + p4b.stderr).lower()
print("OK")

# ------------------------------------------------------------------ 5
print("=== 5. Suzuki: reproduces the accepted Gerrit change (regulator-fixed, per-instance) ===")
run(["--excel", HONDA_XL, "--project", "Suzuki_DA3_9444h02", "--pattern", "regulator",
     "--dts", SUZ_PIN, "--overlay", SUZ_OVL, "--insert-after", "emrst",
     "--out", o("s.dtsi"), "--report", o("s.md")])
ovl = open(SUZ_OVL + ".updated").read()
os.remove(SUZ_OVL + ".updated")
r0 = re.search(r"usb0_vbus_regulator:.*?\n\t\};", ovl, re.DOTALL).group(0)
r1 = re.search(r"usb1_vbus_regulator:.*?\n\t\};", ovl, re.DOTALL).group(0)
assert 'compatible = "regulator-fixed";' in r0 and 'compatible = "regulator-fixed";' in r1
assert "regulator-min-microvolt = <5000000>;" in r0 and "regulator-max-microvolt = <5000000>;" in r1
assert "gpio = <&tlmm 56 GPIO_ACTIVE_LOW>;" in r0 and "enable-active-high" not in r0
assert "gpio = <&tlmm 55 GPIO_ACTIVE_HIGH>;" in r1 and "enable-active-high;" in r1
assert ovl.index("emrst") < ovl.index("usb0_vbus_regulator"), "inserted after emrst"
assert "None" not in ovl
spin = open(o("s.dtsi")).read()
for lbl, pin, lvl in (("usbv2_0_power_enable", "gpio56", "output-high"), ("usbv2_1_power_enable", "gpio55", "output-low")):
    n = node_text(spin, lbl)
    assert f'pins = "{pin}"' in n and 'function = "gpio"' in n and "drive-strength = <8>;" in n
    assert re.search(rf"^\s*{lvl};\s*/\* NEEDS REVIEW", n, re.MULTILINE), f"{lbl}: level must be present AND flagged"
print("OK")

# ------------------------------------------------------------------ 6
print("=== 6. Sleep-state pinctrl nodes are never patched ===")
sp0 = open(SUZ_PIN).read()
for sleep in ("cpu_usb_oc_b_usb20_0_sleep", "cpu_usb_oc_b_usb20_1_sleep"):
    a = re.search(rf"{sleep}\s*:\s*{sleep}\s*\{{", sp0)
    assert a, sleep
    assert node_text(sp0, sleep) == node_text(spin, sleep), f"{sleep} was modified"
print("OK")

# ------------------------------------------------------------------ 7
print("=== 7. --no-update leaves every existing node untouched ===")
run(["--excel", HONDA_XL, "--project", "Suzuki_DA3_9444h02", "--pattern", "regulator", "--no-update",
     "--dts", SUZ_PIN, "--out", o("sn.dtsi"), "--report", o("sn.md")])
sn = open(o("sn.dtsi")).read()
assert sn.startswith(sp0.rstrip("\n")[:2000]) and node_text(sn, "cpu_usb_oc_b_usb20_0") == node_text(sp0, "cpu_usb_oc_b_usb20_0")
print("OK")

# ------------------------------------------------------------------ 8
print("=== 8. Distinct controller instances are never merged (USB20_0 vs USB20_1) ===")
rep = open(o("s.md")).read()
assert "USB20_0" in rep and "USB20_1" in rep
print("OK")

# ------------------------------------------------------------------ 9
print("=== 9. Verifier: an AI-written draft is checked against Excel (WRONG / AMBIGUOUS / UNVERIFIABLE kept apart) ===")
VERIFY = os.path.join(HERE, "verify_dts.py")
AI = os.path.join(S, "ai_draft_usb.dts")
p = subprocess.run([sys.executable, VERIFY, "--excel", CCS20_XL, "--project", "CCS20", "--dts", AI,
                    "--report", o("v_ai.md")], capture_output=True, text=True, cwd=ROOT)
assert p.returncode == 2, "an AI draft with wrong values must fail verification"
v = open(o("v_ai.md")).read()
wrong_sec = v.split("## AMBIGUOUS")[0]
amb_sec = v.split("## AMBIGUOUS")[1].split("## UNVERIFIABLE")[0]
unv_sec = v.split("## UNVERIFIABLE")[1].split("## Confirmed OK")[0]
ok_sec = v.split("## Confirmed OK")[1]
for pin in ("gpio81", "gpio82", "gpio92", "gpio102", "gpio113", "gpio23", "gpio24", "gpio38"):
    assert re.search(rf"\| {pin} \| drive-strength 2 \(DTS\) vs 10 \(Excel\)", wrong_sec), f"{pin}: stale 2mA not caught"
assert re.search(r"gpio81 \|[^|]*bias-pull-up \(DTS\) vs bias-disable \(Excel\)", wrong_sec), "gpio81 bias not caught"
assert re.search(r"gpio113 \|[^|]*bias-pull-up \(DTS\) vs bias-disable \(Excel\)", wrong_sec), "gpio113 bias not caught"
assert "usb_0: usb@a600000" in wrong_sec and "usb_1: usb@a800000" in wrong_sec, "fabricated top-level nodes not flagged"
assert "gpio92" in amb_sec and "USB_HUB_I2C_EN" in amb_sec and "USB_PWR_EN" in amb_sec, "shared pin not flagged"
assert "microchip,usb2514" in unv_sec and "usb-phy-port-select-gpio" in unv_sec and "reg = <0x2d>" in unv_sec
assert "maximum-speed" in unv_sec, "board facts must be listed as unverifiable, not silently passed"
assert ok_sec.count("polarity matches") == 3, "the 3 correct oc/vbus polarities should be confirmed"
print("OK")

# ------------------------------------------------------------------ 10
print("=== 10. Verifier and tool agree: the tool fixes exactly the pins it owns ===")
def wrong_pins(dts):
    pr = subprocess.run([sys.executable, VERIFY, "--excel", CCS20_XL, "--project", "CCS20", "--dts", dts,
                         "--report", o("vx.md")], capture_output=True, text=True, cwd=ROOT)
    return set(re.findall(r"\| (gpio\d+) \| (?:drive|bias)", open(o("vx.md")).read().split("## AMBIGUOUS")[0]))
before, after = wrong_pins(CCS20_DTS), wrong_pins(o("c.dtsi"))
assert {"gpio81", "gpio82", "gpio102", "gpio113", "gpio38"} <= before, "the original file's stale USB nodes must be flagged"
assert not ({"gpio81", "gpio82", "gpio102", "gpio113", "gpio38"} & after), \
    "tool output must verify clean for every pin it owns, including 'other_gpio' pins now corrected too"
assert after == {"gpio92"}, f"the only remaining finding must be the genuinely SHARED pin, got {after}"
print("OK")

# ------------------------------------------------------------------ 11
print("=== 11. Parsed-workbook cache: used when unchanged, discarded when the workbook changes ===")
import shutil
wb = o("cache_wb.xlsx")
shutil.copy(CCS20_XL, wb)
common = ["--project", "CCS20", "--dts", CCS20_DTS, "--report", o("k.md")]
r1 = run(["--excel", wb, "--out", o("k1.dtsi")] + common)
r2 = run(["--excel", wb, "--out", o("k2.dtsi")] + common)
assert "Reading workbook" in r1.stderr and "loaded from cache" in r2.stderr, (r1.stderr, r2.stderr)
assert open(o("k1.dtsi")).read() == open(o("k2.dtsi")).read(), "cached run must equal a fresh run"
st = os.stat(wb)
os.utime(wb, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))      # workbook 'edited'
r3 = run(["--excel", wb, "--out", o("k3.dtsi")] + common)
assert "Reading workbook" in r3.stderr, "a changed workbook must never be served from cache"
r4 = run(["--excel", wb, "--no-cache", "--out", o("k4.dtsi")] + common)
assert "Reading workbook" in r4.stderr and open(o("k4.dtsi")).read() == open(o("k1.dtsi")).read()
print("OK")

# ------------------------------------------------------------------ 12
print("=== 12. Line endings are preserved byte-for-byte (LF stays LF, CRLF stays CRLF) ===")
import difflib
raw = open(CCS20_DTS, "rb").read()
assert b"\r\n" not in raw, "sample expected to be LF"
variants = {"LF": raw, "CRLF": raw.replace(b"\n", b"\r\n")}
for name, blob in variants.items():
    src = o(f"le_{name}.dtsi")
    open(src, "wb").write(blob)
    run(["--excel", CCS20_XL, "--project", "CCS20", "--dts", src, "--out", o(f"le_{name}_out.dtsi"),
         "--report", o("le.md")])
    b = open(o(f"le_{name}_out.dtsi"), "rb").read()
    crlf, lf = b.count(b"\r\n"), b.count(b"\n") - b.count(b"\r\n")
    if name == "LF":
        assert crlf == 0, f"LF input must give LF output, got {crlf} CRLF"
    else:
        assert lf == 0, f"CRLF input must give CRLF output, got {lf} bare LF"
    # the diff against the original is ONLY the real value corrections (5 drive-strength lines -
    # GPIO_81/82/102/113/38 - plus GPIO_38's stale bias) + appended nodes, no line-ending churn
    a_lines = blob.decode().splitlines()
    b_lines = b.decode().splitlines()
    changed = [l for l in difflib.unified_diff(a_lines, b_lines, lineterm="", n=0) if l.startswith("-") and not l.startswith("---")]
    assert len(changed) == 6 and sum("drive-strength = <2>;" in l for l in changed) == 5 \
        and sum("bias-disable;" in l for l in changed) == 1, \
        f"{name}: expected exactly 5 drive-strength + 1 bias line removed, got {len(changed)}: {changed}"
print("OK")

# ------------------------------------------------------------------ 13
print("=== 13. Full accepted Gerrit change reproduced: states + regulators + &usbN consumers ===")
def norm(t):
    return re.sub(r"\s+", "", re.sub(r"/\*.*?\*/", "", t, flags=re.DOTALL))
run(["--excel", HONDA_XL, "--project", "Suzuki_DA3_9444h02", "--pattern", "regulator",
     "--dts", SUZ_PIN, "--overlay", SUZ_OVL, "--insert-after", "emrst", "--out", o("g13.dtsi"), "--report", o("g13.md")])
ovl13 = open(SUZ_OVL + ".updated").read(); os.remove(SUZ_OVL + ".updated")
pin13 = open(o("g13.dtsi")).read()
# screenshot 3 - pinctrl states (pinmux file)
S3 = {"usbv2_0_power_enable": """usbv2_0_power_enable:usbv2_0_power_enable {
    mux { pins = "gpio56"; function = "gpio"; };
    config { pins = "gpio56"; drive-strength = <8>; output-high; bias-disable; };
}""", "usbv2_1_power_enable": """usbv2_1_power_enable:usbv2_1_power_enable {
    mux { pins = "gpio55"; function = "gpio"; };
    config { pins = "gpio55"; drive-strength = <8>; output-low; bias-disable; };
}"""}
for lbl, exp in S3.items():
    assert norm(node_text(pin13, lbl)) == norm(exp), f"{lbl} differs from the accepted change"
# screenshot 1 - regulators (overlay)
R0 = """usb0_vbus_regulator: usb0_vbus_regulator {
    compatible = "regulator-fixed"; regulator-name = "usb0_vbus_regulator";
    regulator-min-microvolt = <5000000>; regulator-max-microvolt = <5000000>;
    gpio = <&tlmm 56 GPIO_ACTIVE_LOW>; }"""
R1 = """usb1_vbus_regulator: usb1_vbus_regulator {
    compatible = "regulator-fixed"; regulator-name = "usb1_vbus_regulator";
    regulator-min-microvolt = <5000000>; regulator-max-microvolt = <5000000>;
    enable-active-high; gpio = <&tlmm 55 GPIO_ACTIVE_HIGH>; }"""
for lbl, exp in (("usb0_vbus_regulator", R0), ("usb1_vbus_regulator", R1)):
    assert norm(node_text(ovl13, lbl)) == norm(exp), f"{lbl} differs from the accepted change"
# screenshot 2 - consumers (overlay): usb0 has qcom,default-mode-host, usb1 does not
C0 = """&usb0 { status = "okay"; vbus_dwc3-supply = <&usb0_vbus_regulator>; pinctrl-names = "default";
    pinctrl-0 = <&usbv2_0_power_enable>; qcom,default-mode-host; };"""
C1 = """&usb1 { status = "okay"; vbus_dwc3-supply = <&usb1_vbus_regulator>; pinctrl-names = "default";
    pinctrl-0 = <&usbv2_1_power_enable>; };"""
for lbl, exp in (("&usb0", C0), ("&usb1", C1)):
    m = re.search(rf"^{re.escape(lbl)} \{{.*?\n\}};", ovl13, re.DOTALL | re.MULTILINE)
    assert m and norm(m.group(0)) == norm(exp), f"{lbl} consumer differs from the accepted change"
assert ovl13.index("emrst") < ovl13.index("usb0_vbus_regulator") < ovl13.index("&usb0 {"), "placement order"
print("OK")

# ------------------------------------------------------------------ 14
print("=== 14. Verifier catches structural problems an AI draft can hide behind correct electrical values ===")
AI2 = os.path.join(S, "ai_draft_regulator_ccs20.dts")
p = subprocess.run([sys.executable, VERIFY, "--excel", CCS20_XL, "--project", "CCS20", "--dts", AI2,
                    "--existing", CCS20_DTS, "--report", o("v_ai2.md")], capture_output=True, text=True, cwd=ROOT)
assert p.returncode == 2
v2 = open(o("v_ai2.md")).read()
w2 = v2.split("## AMBIGUOUS")[0]
a2 = v2.split("## AMBIGUOUS")[1].split("## UNVERIFIABLE")[0]
ok2 = v2.split("## Confirmed OK")[1]
for pin, owner in (("gpio82", "usbv3_power_enable"), ("gpio92", "usbhub_i2c_enable"),
                   ("gpio113", "usbhub_rst_b"), ("gpio38", "sa8155_usb_cfg_usb_phy_port_select")):
    assert f"{pin} is ALREADY configured by '{owner}'" in w2, f"{pin}: duplicate definition not caught"
assert "this project's naming would be 'usbv3_power_enable'" in w2, "mislabelled state (usbv2 on a USB30 pin) not caught"
assert w2.count("node defined at TOP LEVEL") == 2, "both top-level regulator nodes must be flagged"
assert "regulator-fixed usb0_vbus_regulator | no `gpio` property" in w2, "regulator without gpio not caught"
assert "gpio92" in a2 and "USB_HUB_I2C_EN" in a2, "shared pin behind a numeric polarity flag (0) must still be caught"
assert ok2.count("electrical config matches Excel") == 4, "correct electrical values should still be confirmed"
# without --existing the duplicate check cannot run, and must say nothing rather than guess
p = subprocess.run([sys.executable, VERIFY, "--excel", CCS20_XL, "--project", "CCS20", "--dts", AI2,
                    "--report", o("v_ai2b.md")], capture_output=True, text=True, cwd=ROOT)
assert "ALREADY configured" not in open(o("v_ai2b.md")).read()
print("OK")

# ------------------------------------------------------------------ 15
print("=== 15. Excel remarks are surfaced; inventory is written; projects can be listed ===")
r = run(["--excel", CCS20_XL, "--list-projects"])
assert "CCS20" in r.stdout and "POC_NESSY2" in r.stdout, r.stdout
run(["--excel", CCS20_XL, "--project", "CCS20", "--dts", CCS20_DTS, "--out", o("i.dtsi"), "--report", o("i.md"),
     "--inventory", o("inv.md")])
i_out = open(o("i.dtsi")).read()
assert "/* Excel remark (GPIO_102): mounting option only for EU/US subPCB */" in i_out
assert i_out.index("Excel remark (GPIO_102)") < i_out.index("oc-gpio = <&tlmm 102"), "remark must sit next to its property"
inv = open(o("inv.md")).read()
assert "mounting option only for Japan subPCB" in inv and "gpio113" in inv
assert re.search(r"gpio82 \|[^\n]*VBUS-enable pin - used", inv) and re.search(r"gpio81 \|[^\n]*over-current pin - used", inv)
assert re.search(r"gpio92 \|[^\n]*NEEDS REVIEW - Multi-purpose", inv) and "PHY pad" in inv
assert "mounting option only for Japan subPCB" in open(o("i.md")).read(), "remark must appear in the report's review table"
print("OK")

# ------------------------------------------------------------------ 16
print("=== 16. A USB sheet that disagrees with the Database sheet is reported and warned on the used pin ===")
run(["--excel", CCS20_XL, "--project", "CCS20", "--dts", CCS20_DTS, "--out", o("x.dtsi"), "--report", o("x.md")])
xr = open(o("x.md")).read()
sec = xr.split("## Cross-sheet conflicts")[1].split("## PHY")[0]
assert re.search(r"USBHUB \| CPU_USBHUB_CTRL_1V8:RST_B \| GPIO_102 \| Database sheet assigns it to: CPU_USB20_CTRL_1V8:USB_OC_B", sec)
assert re.search(r"USBHUB \| CPU_USBHUB_CTRL_1V8:USB_PWR_EN \| GPIO_113 \| Database sheet assigns it to: CPU_USBHUB_CTRL_1V8:RST", sec)
assert re.search(r"USBHUB \| CPU_USBHUB_CTRL_1V8:USB_OC_B \| GPIO_123 \|", sec)
xo = open(o("x.dtsi")).read()
usb1x = blk(xo, "&usb1")
assert "sheet 'USBHUB' puts CPU_USBHUB_CTRL_1V8:RST_B on GPIO_102" in usb1x, "warning must sit on the used pin"
usb0x = blk(xo, "&usb0")
assert "sheet 'USBHUB'" not in usb0x, "pins the sheets agree on must not be warned"
# it never changes the generated values - only warns
assert "oc-gpio = <&tlmm 102 GPIO_ACTIVE_LOW>;" in usb1x
print("OK")

# ------------------------------------------------------------------ 17
print("=== 17. Regulator style on CCS20: usb0 automatic, usb1 only after an explicit --vbus-pin decision ===")
base = ["--excel", CCS20_XL, "--project", "CCS20", "--pattern", "regulator", "--dts", CCS20_DTS]
os.makedirs(o("ra"), exist_ok=True); os.makedirs(o("rb"), exist_ok=True)
run(base + ["--out", o("ra/o.dtsi"), "--report", o("ra/r.md")])
snip_a = open(o("ra/usb_vbus_regulators.dts")).read()
assert "usb0_vbus_regulator" in snip_a and "usb1_vbus_regulator" not in snip_a, "shared GPIO_92 must NOT be decided automatically"
assert "gpio = <&tlmm 82 GPIO_ACTIVE_HIGH>;" in snip_a and "enable-active-high;" in snip_a
run(base + ["--vbus-pin", "GPIO_92", "--out", o("rb/o.dtsi"), "--report", o("rb/r.md")])
snip_b = open(o("rb/usb_vbus_regulators.dts")).read()
r1 = re.search(r"usb1_vbus_regulator: usb1_vbus_regulator \{.*?\n\};", snip_b, re.DOTALL).group(0)
assert "gpio = <&tlmm 92 GPIO_ACTIVE_HIGH>;" in r1 and "enable-active-high;" in r1
assert "NEEDS REVIEW: GPIO_92 is a shared pin" in r1 and "explicit --vbus-pin decision" in r1, "the human decision must be visible on the node"
c1 = blk(snip_b, "&usb1")
assert "vbus_dwc3-supply = <&usb1_vbus_regulator>;" in c1 and "pinctrl-0 = <&usbhub_i2c_enable>;" in c1, "must reuse the EXISTING label"
assert "qcom,default-mode-host;" not in c1 and "NEEDS REVIEW" in c1, "default-mode-host is only confirmed for usb0"
assert "qcom,default-mode-host;" in blk(snip_b, "&usb0")
assert snip_b.index("&usb0 {") < snip_b.index("&usb1 {"), "&usb0 must come before &usb1"
assert "## Human decisions applied" in open(o("rb/r.md")).read()
for bad in (["--vbus-pin", "999"], ["--vbus-pin", "GPIO_82"]):        # unknown pin / already handled automatically
    pr = run(base + bad + ["--out", o("rb/x.dtsi"), "--report", o("rb/x.md")], expect_ok=False)
    assert pr.returncode != 0 and "--vbus-pin" in (pr.stdout + pr.stderr)
print("OK")

# ------------------------------------------------------------------ 18
print("=== 18. Regulator output into folders that do not exist yet (no crash) ===")
deep = o("brand/new/nested")
assert not os.path.exists(deep)
run(["--excel", CCS20_XL, "--project", "CCS20", "--pattern", "regulator", "--vbus-pin", "GPIO_92",
     "--dts", CCS20_DTS, "--out", os.path.join(deep, "o.dtsi"), "--report", os.path.join(deep, "r.md"),
     "--inventory", os.path.join(deep, "inv.md")])
for name in ("o.dtsi", "r.md", "inv.md", "usb_vbus_regulators.dts"):
    assert os.path.exists(os.path.join(deep, name)), f"{name} was not written"
print("OK")

# ------------------------------------------------------------------ 19
print("=== 19. --combined writes every USB piece in ONE file, in order, with well-formed nodes ===")
for name, extra in (("regulator", ["--pattern", "regulator", "--vbus-pin", "GPIO_92"]), ("controller", [])):
    run(["--excel", CCS20_XL, "--project", "CCS20", "--dts", CCS20_DTS, "--out", o(f"cb_{name}.dtsi"),
         "--report", o(f"cb_{name}.md"), "--combined", o(f"cb_{name}_all.dts")] + extra)
    cb = open(o(f"cb_{name}_all.dts")).read()
    assert cb.count("{") == cb.count("}"), f"{name}: unbalanced braces"
    assert cb.index("1. USB CONTROLLER BLOCKS") < cb.index("2. USB PINCTRL") , f"{name}: section order"
    sec2 = cb.split("2. USB PINCTRL")[1].split("/* 3.")[0]
    nodes_in_sec2 = re.findall(r"^\t\w+: \w+ \{", sec2, re.MULTILINE)
    assert nodes_in_sec2, f"{name}: no pinctrl nodes in section 2"
    # every node closes with '};' - a bare '}' is invalid DTS
    assert not re.search(r"^\t\}\s*$", sec2, re.MULTILINE), f"{name}: a pinctrl node ends with '}}' not '}};'"
    assert len(re.findall(r"^\t\};", sec2, re.MULTILINE)) == len(nodes_in_sec2)
    assert "&usb0 {" in cb and "&usb1 {" in cb
    if name == "regulator":
        assert cb.index("2. USB PINCTRL") < cb.index("3. USB VBUS FIXED REGULATORS")
        assert "usb0_vbus_regulator: usb0_vbus_regulator {" in cb and "usb1_vbus_regulator: usb1_vbus_regulator {" in cb
        assert "vbus_dwc3-supply = <&usb0_vbus_regulator>;" in cb
        assert "usbv3_power_enable: usbv3_power_enable {" in sec2 and "usbhub_i2c_enable: usbhub_i2c_enable {" in sec2
    else:
        assert "3. USB VBUS FIXED REGULATORS" not in cb, "controller style has no regulator section"
        assert "usbv3_fault_detect: usbv3_fault_detect {" in sec2 and "usbv2_fault_detect: usbv2_fault_detect {" in sec2
        assert "oc-gpio = <&tlmm 81 GPIO_ACTIVE_LOW>;" in cb
print("OK")

# ------------------------------------------------------------------ 20
print("=== 20. Calling run(args) directly (no subprocess) gives byte-identical output to the CLI ===")
sys.path.insert(0, HERE)
import sys as _s, os as _o; _s.path.insert(0, _o.path.join(_o.path.dirname(_o.path.abspath(__file__)), '..', 'scripts')); import _samples; _samples.need(__file__)
import usb_dts_tool as udt

class NS:
    def __init__(self, **kw):
        defaults = dict(project=None, dts=None, out=None, report=None, pattern="auto", overlay=None,
                        insert_after=None, vbus_pin=[], resolve=[], inventory=None, combined=None,
                        no_cache=False, no_update=False, keyword="usb", match="both", list_projects=False)
        defaults.update(kw)
        self.__dict__.update(defaults)

run(["--excel", CCS20_XL, "--project", "CCS20", "--dts", CCS20_DTS, "--out", o("d20_cli.dtsi"), "--report", o("d20_cli.md")])
udt.USE_CACHE = True
res = udt.run(NS(excel=CCS20_XL, project="CCS20", dts=CCS20_DTS, out=o("d20_api.dtsi"), report=o("d20_api.md")))
assert open(o("d20_cli.dtsi")).read() == open(o("d20_api.dtsi")).read(), "direct run() must match the CLI byte-for-byte"
assert res.summary == {"updated": 6, "consumer_nodes": 2, "regulators": 0, "new_states": 0,
                       "other_gpio": 2, "unsupported_controller": 0, "unconfirmed_controller": 0,
                       "needs_review": 4}
assert res.exit_code == 0 and res.pattern == "controller" and res.out_text == open(o("d20_api.dtsi")).read()
res2 = udt.run(NS(excel=CCS20_XL, list_projects=True))
assert "CCS20" in res2.projects and "POC_NESSY2" in res2.projects
try:
    udt.run(NS(excel=CCS20_XL))
    assert False, "missing --project must raise"
except SystemExit:
    pass
print("OK")

# ------------------------------------------------------------------ 21
print("=== 21. verify_dts.run(args) directly matches its own CLI byte-for-byte ===")
import verify_dts as vd

class VNS:
    def __init__(self, **kw):
        d = dict(existing=[], no_cache=False, all_pins=False)
        d.update(kw)
        self.__dict__.update(d)

AI = os.path.join(S, "ai_draft_usb.dts")
p = subprocess.run([sys.executable, VERIFY, "--excel", CCS20_XL, "--project", "CCS20", "--dts", AI,
                    "--report", o("v21_cli.md")], capture_output=True, text=True, cwd=ROOT)
assert p.returncode == 2
vd.T.USE_CACHE = True
res = vd.run(VNS(excel=CCS20_XL, project="CCS20", dts=AI, report=o("v21_api.md")))
assert open(o("v21_cli.md")).read() == open(o("v21_api.md")).read() == res.report_text
assert res.exit_code == 2 and len(res.wrong) == 10 and len(res.ambiguous) == 1 and len(res.unverifiable) == 14
print("OK")

# ------------------------------------------------------------------ 22
print("=== 22. A SECOND run against a workbook that now self-matches produces the SAME correct result ===")
# Regression for two real, related bugs, both confirmed live before this fix:
#   (a) _read_workbook()'s "known schema matched" branch called normalize(parsed) with NO explicit
#       entity args, silently defaulting to the hardcoded SA8155P/CCS20/POC_NESSY2 module-level
#       defaults in app/schema.py regardless of which schema actually matched - confirmed to turn
#       761 real Suzuki records into 0 the moment a workbook ever DID self-match.
#   (b) the "unknown -> classify" branch's own columns_by_label construction silently dropped any
#       labeled column with no entity (or a real entity with no confirmed role) instead of keeping
#       it as a marker - so a real workbook could never self-match its own just-confirmed schema in
#       the first place, which is exactly why (a) went unexercised by every earlier test run here.
# Together: run the CLI twice on the SAME workbook with the schema cache genuinely NOT wiped between
# runs (every other test in this file wipes it first) - run 2 must hit the "known" branch and must
# still produce the correct, non-empty, IDENTICAL result.
run(["--excel", HONDA_XL, "--project", "Suzuki_DA3_9444h02", "--pattern", "regulator",
     "--dts", SUZ_PIN, "--overlay", SUZ_OVL, "--insert-after", "emrst",
     "--out", o("r22a.dtsi"), "--report", o("r22a.md")])
run(["--excel", HONDA_XL, "--project", "Suzuki_DA3_9444h02", "--pattern", "regulator",
     "--dts", SUZ_PIN, "--overlay", SUZ_OVL, "--insert-after", "emrst",
     "--out", o("r22b.dtsi"), "--report", o("r22b.md")])
if os.path.exists(SUZ_OVL + ".updated"):
    os.remove(SUZ_OVL + ".updated")        # the tool writes <overlay>.updated next to its input - never leave it in samples/
assert open(o("r22a.dtsi")).read() == open(o("r22b.dtsi")).read(), \
    "second run (workbook now self-matches) must give the IDENTICAL result to the first"
r22b = open(o("r22b.dtsi")).read()
assert "usbv2_0_power_enable" in r22b and "drive-strength = <8>;" in r22b, \
    "second run must still contain real Suzuki data, not an empty/defaulted result"
sys.path.insert(0, HERE)
import usb_dts_tool as udt
parsed_chk = udt.__dict__  # just to ensure module loaded; real check below
from app.parsers.workbook_parser import parse_workbook
from app.schema_registry import match_known_schema
p_chk = parse_workbook(HONDA_XL)
found_chk = {c.field_label for c in p_chk.columns if c.field_label}
assert match_known_schema(found_chk) is not None, \
    "the workbook must actually self-match by the second run (confirms branch (b) is fixed too)"
print("OK")

# ------------------------------------------------------------------ 23
print("=== 23. 'other_gpio': single-purpose unrecognized-role pins are never dropped ===")
# CCS20: GPIO_113/GPIO_38 already have real nodes in the sample file - must be REUSED and their
# stale values corrected, not duplicated, and needs_review must shrink to only the genuinely
# ambiguous/boot-strap pins (GPIO_92 shared, GPIO_23/24 shared 4-way, GPIO_136 boot-strap).
r23 = run(["--excel", CCS20_XL, "--project", "CCS20", "--dts", CCS20_DTS, "--out", o("o23.dtsi"), "--report", o("o23.md")])
assert "other GPIOs included: 2" in r23.stdout
m23 = open(o("o23.md")).read()
sec23 = m23.split("## Other USB-related GPIOs")[1].split("## NEEDS REVIEW")[0]
assert "GPIO_113 | usbhub_rst_b | existing node reused/updated" in sec23
assert "GPIO_38 | sa8155_usb_cfg_usb_phy_port_select | existing node reused/updated" in sec23
o23 = open(o("o23.dtsi")).read()
assert o23.count("usbhub_rst_b:") == 1 and o23.count("sa8155_usb_cfg_usb_phy_port_select:") == 1, \
    "existing nodes must be reused, never duplicated"
n23 = m23.split("## NEEDS REVIEW")[1].split("## Cross-sheet")[0]
assert "GPIO_113" not in n23 and "GPIO_38" not in n23, "these two must have moved OUT of needs_review"
assert "GPIO_92" in n23 and "GPIO_136" in n23, "genuinely ambiguous/boot-strap pins must still be there"

# Second half: the SAME class of pin, but with NO existing node to reuse - must get a genuinely
# NEW node instead (real drive-strength/direction/bias from Excel, a label derived from the pin's
# own signal - never a guessed role name), using only the already-bundled CCS20 sample so this has
# permanent, always-running coverage (no dependency on an external/proprietary workbook).
stripped23 = orig
for lbl in ("usbhub_rst_b", "sa8155_usb_cfg_usb_phy_port_select"):
    stripped23 = stripped23.replace(node_text(stripped23, lbl), "")
open(o("stripped23.dtsi"), "w").write(stripped23)
r23c = run(["--excel", CCS20_XL, "--project", "CCS20", "--dts", o("stripped23.dtsi"),
            "--out", o("gen23.dtsi"), "--report", o("gen23.md")])
assert "other GPIOs included: 2" in r23c.stdout
g23 = open(o("gen23.dtsi")).read()
for pin, drv in (("gpio113", "10"), ("gpio38", "10")):
    assert f'pins = "{pin}"' in g23 and f"drive-strength = <{drv}>;" in g23[g23.index(f'pins = "{pin}"'):g23.index(f'pins = "{pin}"') + 300]
m23c = open(o("gen23.md")).read()
sec23c = m23c.split("## Other USB-related GPIOs")[1].split("## NEEDS REVIEW")[0]
assert "new node generated" in sec23c and sec23c.count("new node generated") == 2
assert "mounting option only for Japan subPCB" in sec23c, "the Excel remark must still be shown for a newly generated other_gpio node"
labels23 = re.findall(r"^\s*([A-Za-z_]\w*)\s*:\s*[\w@,.\-]+\s*\{", g23, re.MULTILINE)
assert len(labels23) == len(set(labels23)), "no duplicate labels between generated other_gpio nodes and the rest of the file"

# If a local Iveco workbook is explicitly supplied, ensure SAIL_CFG rows are never emitted as
# TLMM GPIO states. SAIL controller syntax is not established by this tool; fail closed on B1
# while retaining confirmed TLMM GPIO generation for B2.
IVECO_XL = os.environ.get("IVECO_XL")
if IVECO_XL and os.path.exists(IVECO_XL):
    r23b1 = run(["--excel", IVECO_XL, "--project", "Iveco CDC B1", "--out", o("iv23b1.dtsi"),
                 "--report", o("iv23b1.md"), "--combined", o("iv23b1_all.dts")], expect_ok=False)
    assert "validation problems:" in r23b1.stdout.lower()
    assert "non-tlmm pin-controller" in r23b1.stdout.lower()
    iv23b1 = open(o("iv23b1.dtsi")).read().lower()
    assert not any(f'sailio{n}' in iv23b1 for n in (24, 25, 26, 27, 28)), \
        "SAIL_CFG pins must never be emitted as gpio pin states inside &tlmm"
    assert "SAIL_CFG24" in open(o("iv23b1.md")).read()

    r23b2 = run(["--excel", IVECO_XL, "--project", "Iveco CDC B2", "--pattern", "controller",
                 "--out", o("iv23b2.dtsi"), "--report", o("iv23b2.md")])
    iv23b2 = open(o("iv23b2.dtsi")).read().lower()
    assert 'pins = "gpio70"' in iv23b2 and 'pins = "gpio75"' in iv23b2
    assert not any(f'sailio{n}' in iv23b2 for n in (24, 25, 26, 27, 28, 58))
else:
    print("(Set IVECO_XL to a local Iveco workbook path to run the SAIL/TLMM safety regression)")
print("OK")

# ------------------------------------------------------------------ 23a
print("=== 23a. USB boot-strap match must not emit an unrelated runtime pin ===")
r23a = run(["--excel", HONDA_XL, "--project", "Honda_GenID_B0", "--pattern", "controller",
            "--out", o("honda_usb.dtsi"), "--report", o("honda_usb.md")])
honda_usb_out = open(o("honda_usb.dtsi")).read().lower()
honda_usb_report = open(o("honda_usb.md")).read()
other23a = honda_usb_report.split("## Other USB-related GPIOs")[1].split("## NEEDS REVIEW")[0]
review23a = honda_usb_report.split("## NEEDS REVIEW")[1].split("## Cross-sheet")[0]
assert "gpio101" not in honda_usb_out and "GPIO_101" not in other23a
assert "GPIO_101" in review23a and "boot-strap signal" in review23a
print("OK")

# ------------------------------------------------------------------ 24
print("=== 24. Hub vs non-hub split: report tables and --combined section 2 both split correctly ===")
r24 = run(["--excel", CCS20_XL, "--project", "CCS20", "--dts", CCS20_DTS, "--out", o("h24.dtsi"),
           "--report", o("h24.md"), "--combined", o("h24_all.dts")])
m24 = open(o("h24.md")).read()
other24 = m24.split("## Other USB-related GPIOs")[1].split("## NEEDS REVIEW")[0]
nonhub24 = other24.split("**With hub")[0]
withhub24 = other24.split("**With hub")[1]
assert "GPIO_38" in nonhub24 and "GPIO_38" not in withhub24, "PHY port select is NOT hub-related"
assert "GPIO_113" in withhub24 and "GPIO_113" not in nonhub24, "hub reset IS hub-related"
review24 = m24.split("## NEEDS REVIEW")[1].split("## Cross-sheet")[0]
rnonhub24 = review24.split("**With hub")[0]
rwithhub24 = review24.split("**With hub")[1]
assert "GPIO_136" in rnonhub24, "boot-strap pin is NOT hub-related"
assert all(f"GPIO_{n}" in rwithhub24 for n in ("92", "23", "24")), "hub I2C-enable and hub I2C bus pins ARE hub-related"
c24 = open(o("h24_all.dts")).read()
assert c24.index("2a. Without hub") < c24.index("usbv3_fault_detect:") < c24.index("2b. With hub") < c24.index("usbhub_rst_b:")
sec_a = c24[c24.index("2a. Without hub"):c24.index("2b. With hub")]
sec_b = c24[c24.index("2b. With hub"):]
assert "usbhub_rst_b:" not in sec_a and "usbv3_fault_detect:" not in sec_b, \
    "each pinctrl node must appear in exactly the correct hub/non-hub combined sub-section"
print("OK")

# ------------------------------------------------------------------ 25
print("=== 25. Pattern unconfirmed: BOTH consumer-wiring shapes previewed in --combined, from real data ===")
r25 = run(["--excel", CCS20_XL, "--project", "CCS20", "--out", o("pv25.dtsi"), "--report", o("pv25.md"),
           "--combined", o("pv25_all.dts")])
c25 = open(o("pv25_all.dts")).read()
assert "1-PREVIEW" in c25 and "NEITHER is" in c25
usb0_ctrl = c25[c25.index("usb0 (USB30) - IF controller"):c25.index("usb0 (USB30) - IF regulator")]
usb0_reg = c25[c25.index("usb0 (USB30) - IF regulator"):c25.index("usb1 (USB20) - IF controller")]
assert "oc-gpio = <&tlmm 81 GPIO_ACTIVE_LOW>;" in usb0_ctrl and "vbus-out-gpio = <&tlmm 82 GPIO_ACTIVE_HIGH>;" in usb0_ctrl
assert 'compatible = "regulator-fixed"' in usb0_reg and "gpio = <&tlmm 82 GPIO_ACTIVE_HIGH>;" in usb0_reg
# the regression this test exists for: the regulator preview's pinctrl-0 must reference the VBUS
# pin's OWN label (usbv3_power_enable, gpio82) - NOT the OC pin's label (usbv3_fault_detect, gpio81),
# which an earlier version of this code grabbed by mistake (pin_labels[0], irrespective of which
# pin it actually belonged to)
assert "pinctrl-0 = <&usbv3_power_enable>;" in usb0_reg
assert "usbv3_fault_detect" not in usb0_reg, "regulator preview must not reference the OC pin's label"
assert "over-current (GPIO_81, GPIO_ACTIVE_LOW) has no confirmed wiring" in usb0_reg
usb1_reg = c25[c25.index("usb1 (USB20) - IF regulator"):]
assert "no vbus pin found for this instance" in usb1_reg, "usb1/USB20 has no real vbus pin - must say so, not fabricate one"
# neither preview may appear in the actual generated file - previews are --combined-only
pv25 = open(o("pv25.dtsi")).read()
assert "regulator-fixed" not in pv25 and "&usb0 {" not in pv25 and "&usb1 {" not in pv25
print("OK")

# ------------------------------------------------------------------ 26
print("=== 26. Real dtc compiles every kind of generated output (skipped when dtc is not installed) ===")
# dtc checks SYNTAX and label consistency only (the base tree's labels are stubbed - bindings cannot be
# checked without the real tree). It still catches what a text-only review cannot: a missing ';', an
# unbalanced brace, a duplicated label, a reference to a label nothing defines.
if shutil.which("dtc"):
    assert udt.run_dtc("&tlmm { a: a { };\n")[0] is False, "unbalanced brace must be rejected"
    assert udt.run_dtc('&tlmm { n: n { pins = "gpio1" function = "gpio"; }; };\n')[0] is False, "missing ';' must be rejected"
    assert udt.run_dtc("&tlmm { a: a { }; };\n&tlmm { a: b { }; };\n")[0] is False, "duplicate label must be rejected"
    assert udt.run_dtc('&tlmm { a: a { mux { pins = "gpio1"; function = "gpio"; }; }; };\n')[0] is True
    udt.USE_CACHE = True
    compile_cases = [
        ("CCS20 controller, from scratch", dict(excel=CCS20_XL, project="CCS20", pattern="controller")),
        ("CCS20 patched into the real pinmux file", dict(excel=CCS20_XL, project="CCS20", dts=CCS20_DTS)),
        ("POC_NESSY2 controller", dict(excel=CCS20_XL, project="POC_NESSY2", pattern="controller")),
        ("Honda, pattern unconfirmed", dict(excel=HONDA_XL, project="Honda_GenID_B0")),
        ("Suzuki regulator, patched into the real pinmux file", dict(excel=HONDA_XL, project="Suzuki_DA3_9444h02",
                                                                     pattern="regulator", dts=SUZ_PIN)),
    ]
    for i, (label, kw) in enumerate(compile_cases):
        r26 = udt.run(NS(out=o(f"dtc{i}.dtsi"), report=o(f"dtc{i}.md"), **kw))
        assert r26.dtc_result and r26.dtc_result.startswith("dtc OK"), f"{label}: {r26.dtc_result}"
        assert not any("dtc reported" in x for x in r26.validation_problems), label
    # regulator style as a WHOLE: pinctrl + regulators (inside a parent node) + &usbN consumers
    r26r = udt.run(NS(excel=CCS20_XL, project="CCS20", pattern="regulator", vbus_pin=["GPIO_92"], dts=CCS20_DTS,
                      out=o("dtcr.dtsi"), report=o("dtcr.md")))
    snip26 = open(o("usb_vbus_regulators.dts")).read()
    parts26 = re.split(r"/\* === SECTION \d.*?=== \*/", snip26, flags=re.S)
    whole26 = r26r.out_text + "\n/ {\n" + parts26[1] + "};\n" + parts26[2]
    assert udt.run_dtc(whole26)[0] is True, "regulator-style output must compile as a whole"
else:
    print("(dtc not installed in this environment - compile check skipped)")
print("OK")

# ------------------------------------------------------------------ 27
print("=== 27. The report's 'Schematic net' column shows the real net trace, never a pin-mux group name ===")
run(["--excel", CCS20_XL, "--project", "CCS20", "--out", o("n27.dtsi"), "--report", o("n27.md")])
rows27 = {m.group(1): m.group(0) for m in re.finditer(r"^\| (GPIO_(?:23|24|92|136)) \|.*$", open(o("n27.md")).read().split("## NEEDS REVIEW")[1].split("## Cross-sheet")[0], re.MULTILINE)}
assert set(rows27) == {"GPIO_23", "GPIO_24", "GPIO_92", "GPIO_136"}, rows27.keys()
for pin in ("GPIO_23", "GPIO_92"):
    assert "ENDPNT" in rows27[pin] and "X8190" in rows27[pin], f"{pin}: net trace missing from the report"
for pin, row in rows27.items():
    assert not re.search(r"\| PinMux\d+ \|\s*$", row), f"{pin}: a pin-mux group name is shown as the net trace"
print("OK")

# ------------------------------------------------------------------ 28
print("=== 28. The ACCEPTED CHANGE itself (the three screenshots) passes the verifier - a boot-strap pin used as a GPIO is not 'wrong' ===")
accepted = """
&tlmm {
	usbv2_0_power_enable:usbv2_0_power_enable {
		mux { pins = "gpio56"; function = "gpio"; };
		config { pins = "gpio56"; drive-strength = <8>; output-high; bias-disable; };
	};
	usbv2_1_power_enable:usbv2_1_power_enable {
		mux { pins = "gpio55"; function = "gpio"; };
		config { pins = "gpio55"; drive-strength = <8>; output-low; bias-disable; };
	};
};
/ {
	usb0_vbus_regulator: usb0_vbus_regulator {
		compatible = "regulator-fixed"; regulator-name = "usb0_vbus_regulator";
		regulator-min-microvolt = <5000000>; regulator-max-microvolt = <5000000>;
		gpio = <&tlmm 56 GPIO_ACTIVE_LOW>;
	};
	usb1_vbus_regulator: usb1_vbus_regulator {
		compatible = "regulator-fixed"; regulator-name = "usb1_vbus_regulator";
		regulator-min-microvolt = <5000000>; regulator-max-microvolt = <5000000>;
		enable-active-high;
		gpio = <&tlmm 55 GPIO_ACTIVE_HIGH>;
	};
};
&usb0 { status = "okay"; vbus_dwc3-supply = <&usb0_vbus_regulator>; pinctrl-names = "default"; pinctrl-0 = <&usbv2_0_power_enable>; qcom,default-mode-host; };
&usb1 { status = "okay"; vbus_dwc3-supply = <&usb1_vbus_regulator>; pinctrl-names = "default"; pinctrl-0 = <&usbv2_1_power_enable>; };
"""
acc_path = o("accepted_change.dts")
open(acc_path, "w").write(accepted)
res28 = vd.run(VNS(excel=HONDA_XL, project="Suzuki_DA3_9444h02", dts=acc_path, report=o("v28.md")))
assert res28.wrong == [], res28.wrong
assert len(res28.ok) >= 2
print("OK")

# ------------------------------------------------------------------ 29
print("=== 29. A shared pin the engineer DECIDED (--vbus-pin) is not reported as an unresolved ambiguity ===")
shared = "&usb1 { vbus-out-gpio = <&tlmm 92 GPIO_ACTIVE_HIGH>; };\n"
sp = o("shared92.dts"); open(sp, "w").write(shared)
undecided = vd.run(VNS(excel=CCS20_XL, project="CCS20", dts=sp, report=o("v29a.md")))
assert any("gpio92" in a[1] for a in undecided.ambiguous), "without a decision it is ambiguous"
decided = vd.run(VNS(excel=CCS20_XL, project="CCS20", dts=sp, report=o("v29b.md"), decided=["GPIO_92"]))
assert not decided.ambiguous and any("as you decided" in x[2] for x in decided.ok), (decided.ambiguous, decided.ok)
print("OK")

# ------------------------------------------------------------------ 30
print("=== 30. SoC-specific boilerplate is emitted only for the SoC it was confirmed on (SA8155P), never by instance name alone ===")
r30 = udt.run(NS(excel=CCS20_XL, project="CCS20", pattern="controller", out=o("s30a.dtsi"), report=o("s30a.md")))
assert "dwc3@a600000" in r30.out_text and "usb-phy = <&usb2_phy0>" in r30.out_text, "confirmed SoC: boilerplate emitted"
real_soc = udt.CONFIRMED_SOC
udt.CONFIRMED_SOC = "SOME_OTHER_SOC"            # as if this workbook were a different SoC that also has a USB30 instance
try:
    r30b = udt.run(NS(excel=CCS20_XL, project="CCS20", pattern="controller", out=o("s30b.dtsi"), report=o("s30b.md")))
finally:
    udt.CONFIRMED_SOC = real_soc
usb0_30b = blk(r30b.out_text, "&usb0")
assert "dwc3@a600000" not in r30b.out_text and "usb2_phy0" not in r30b.out_text, "another SoC must not receive SA8155P's unit address / phy labels"
assert "NEEDS REVIEW" in usb0_30b and "oc-gpio = <&tlmm 81 GPIO_ACTIVE_LOW>;" in usb0_30b, "the workbook-derived lines are still generated"
print("OK")

# ------------------------------------------------------------------ 31
print("=== 31. --role teaches a project's own signal names; a bad value stops with a clear message ===")
bad = run(["--excel", CCS20_XL, "--project", "CCS20", "--role", "FOO=bar", "--out", o("r31.dtsi"), "--report", o("r31.md")], expect_ok=False)
assert bad.returncode != 0 and "must be 'oc' or 'vbus'" in (bad.stdout + bad.stderr)
bad2 = run(["--excel", CCS20_XL, "--project", "CCS20", "--role", "NOEQUALS", "--out", o("r31.dtsi"), "--report", o("r31.md")], expect_ok=False)
assert bad2.returncode != 0 and "expected SIGNAL=oc" in (bad2.stdout + bad2.stderr)
# CCS20: force the PHY port-select signal to be treated as a VBUS enable - it moves out of "other GPIOs" and is reported as a user decision
base31 = udt.run(NS(excel=CCS20_XL, project="CCS20", pattern="auto", out=o("r31a.dtsi"), report=o("r31a.md")))
role31 = udt.run(NS(excel=CCS20_XL, project="CCS20", pattern="auto", role=["USB_PHY_PORT_SELECT=vbus"], out=o("r31b.dtsi"), report=o("r31b.md")))
assert base31.summary["other_gpio"] - 1 == role31.summary["other_gpio"], (base31.summary, role31.summary)
assert "Signal roles set by you (--role): USB_PHY_PORT_SELECT = vbus" in role31.report_text
assert "Signal roles set by you" not in base31.report_text
if IVECO_XL and os.path.exists(IVECO_XL):
    # Iveco names its over-current / VBUS-enable signals USB_FAULT_B and 5V0S_USB_EXTERNAL_EN. They are in the SHIPPED vocabulary
    # (app/usb_role_keywords.json); here the vocabulary is swapped for one that does not know them, to test --role on unknown names.
    _vf31 = o("vocab_without.json"); json.dump({"oc": ["(^|_)OC(_|$)"], "vbus": ["PWR_EN", "VBUS"]}, open(_vf31, "w"))
    os.environ["USB_ROLE_KEYWORDS"] = _vf31
    try:
        iv0 = udt.run(NS(excel=IVECO_XL, project="Iveco CDC B2", pattern="controller", out=o("iv31a.dtsi"), report=o("iv31a.md")))
        assert iv0.summary["other_gpio"] == 3 and iv0.summary["consumer_nodes"] == 0, iv0.summary
        iv1 = udt.run(NS(excel=IVECO_XL, project="Iveco CDC B2", pattern="controller", role=["USB_FAULT_B=oc,5V0S_USB_EXTERNAL_EN=vbus"],
                         out=o("iv31b.dtsi"), report=o("iv31b.md")))
        assert iv1.summary["other_gpio"] == 1 and iv1.summary["consumer_nodes"] == 1, iv1.summary
        u = blk(iv1.out_text, "&usb0")
        assert "oc-gpio = <&tlmm 70 GPIO_ACTIVE_LOW>;" in u and "vbus-out-gpio = <&tlmm 75 GPIO_ACTIVE_HIGH>;" in u, u
        assert "dwc3@a600000" not in iv1.out_text, "SA7255P must not receive the SA8155P unit address"
    finally:
        del os.environ["USB_ROLE_KEYWORDS"]
    # with the shipped vocabulary the same result needs no --role at all
    ivd = udt.run(NS(excel=IVECO_XL, project="Iveco CDC B2", pattern="controller", out=o("iv31c.dtsi"), report=o("iv31c.md")))
    assert ivd.summary["other_gpio"] == 1 and ivd.summary["consumer_nodes"] == 1, ivd.summary
    assert blk(ivd.out_text, "&usb0") == blk(iv1.out_text, "&usb0"), "vocabulary and --role give the same wiring"
    # the verifier knows the vocabulary AND the roles a person set. Under a vocabulary that does not know these names, wiring made by
    # --role is "not a ... signal" to the verifier UNLESS it is told the roles were decided.
    os.environ["USB_ROLE_KEYWORDS"] = _vf31
    try:
        v_blind = vd.run(VNS(excel=IVECO_XL, project="Iveco CDC B2", dts=o("iv31b.dtsi"), report=o("v31a.md")))
        v_told = vd.run(VNS(excel=IVECO_XL, project="Iveco CDC B2", dts=o("iv31b.dtsi"), report=o("v31b.md"),
                            decided_roles=["USB_FAULT_B=oc", "5V0S_USB_EXTERNAL_EN=vbus"]))
    finally:
        del os.environ["USB_ROLE_KEYWORDS"]
    blind = [w[2] for w in v_blind.wrong]
    assert any("not a oc signal" in w for w in blind) and any("not a vbus signal" in w for w in blind), blind
    assert not any("signal" in w[2] and "not a" in w[2] for w in v_told.wrong), v_told.wrong
print("OK")

# ------------------------------------------------------------------ 32
print("=== 32. The inventory never calls a boot-strap pin without a GPIO number a 'PHY pad' ===")
if IVECO_XL and os.path.exists(IVECO_XL):
    udt.run(NS(excel=IVECO_XL, project="Iveco CDC B2", out=o("i32.dtsi"), report=o("i32.md"), inventory=o("i32_inv.md")))
    inv32 = open(o("i32_inv.md")).read()
    sail = next(l for l in inv32.splitlines() if "SAIL_FORCE_USB" in l)
    assert "N/A (no TLMM GPIO number)" in sail and "PHY pad" not in sail and "NEEDS REVIEW" in sail, sail
    assert any("N/A (PHY pad)" in l and "USB20_DM" in l for l in inv32.splitlines()), "real PHY pads keep their label"
print("OK")

# ------------------------------------------------------------------ 33
print("=== 33. Regulator-fixed VBUS enables are VERIFIED (signal role, polarity, enable-active-high) - not left 'unverifiable' ===")
reg_ok = vd.run(VNS(excel=HONDA_XL, project="Suzuki_DA3_9444h02", dts=acc_path, report=o("v33a.md")))
assert reg_ok.wrong == [] and not any("gpio" in u[1] and "regulator" not in u[1] and "->" in u[1] for u in reg_ok.unverifiable), reg_ok.unverifiable
assert sum("regulator gpio" in x[1] and x[2] == "polarity matches" for x in reg_ok.ok) == 2, reg_ok.ok
flip = accepted.replace("gpio = <&tlmm 56 GPIO_ACTIVE_LOW>;", "gpio = <&tlmm 56 GPIO_ACTIVE_HIGH>;")
p33b = o("flip33.dts"); open(p33b, "w").write(flip)
r33b = vd.run(VNS(excel=HONDA_XL, project="Suzuki_DA3_9444h02", dts=p33b, report=o("v33b.md")))
assert any("GPIO_ACTIVE_HIGH (DTS) vs GPIO_ACTIVE_LOW" in w[2] for w in r33b.wrong), r33b.wrong
assert any("enable-active-high must be present" in w[2] for w in r33b.wrong), "ACTIVE_HIGH without enable-active-high is wrong"
noeah = accepted.replace("\t\tenable-active-high;\n", "")
p33c = o("noeah33.dts"); open(p33c, "w").write(noeah)
r33c = vd.run(VNS(excel=HONDA_XL, project="Suzuki_DA3_9444h02", dts=p33c, report=o("v33c.md")))
assert any("enable-active-high must be present" in w[2] for w in r33c.wrong), r33c.wrong
notvbus = accepted.replace("&tlmm 56 GPIO_ACTIVE_LOW", "&tlmm 58 GPIO_ACTIVE_LOW")      # gpio58 is an over-current pin in this workbook
p33d = o("notvbus33.dts"); open(p33d, "w").write(notvbus)
r33d = vd.run(VNS(excel=HONDA_XL, project="Suzuki_DA3_9444h02", dts=p33d, report=o("v33d.md")))
assert any("not a VBUS enable" in w[2] for w in r33d.wrong), r33d.wrong
print("OK")

# ------------------------------------------------------------------ 34
print("=== 34. Output levels: convention is the default; Excel ResetValue and none are explicit, tagged, and never silent ===")
def node34(text, pin):
    m = re.search(r"^\t\w+: \w+ \{(?:(?!^\t\};).)*?pins = \"" + pin + r"\";.*?^\t\};", text, re.S | re.M)
    return m.group(0) if m else ""
base34 = dict(excel=HONDA_XL, project="Honda_GenID_B0", pattern="regulator", report=o("l34.md"))
conv = udt.run(NS(out=o("l34c.dtsi"), **base34))
assert "output-high;  /* NEEDS REVIEW: level inferred as 'regulator OFF at boot'" in node34(conv.out_text, "gpio56"), "default = convention (gpio56: active-low enable -> high = off)"
assert "NEEDS REVIEW: output pin" in node34(conv.out_text, "gpio87"), "an output pin that is not a VBUS enable stays a decision"
xl34 = udt.run(NS(out=o("l34x.dtsi"), level_source="excel", **base34))
n56 = node34(xl34.out_text, "gpio56")
assert 'output-low;  /* source: Excel ResetValue "0: low" - the accepted-change convention would be output-high */' in n56, n56
assert "supply is ON at boot (Excel level)" in n56, "an active-low enable driven low is ON at boot - stated in the file"
assert "supply is ON" not in node34(xl34.out_text, "gpio55"), "active-high enable + low = off: no warning"
assert 'output-low;  /* source: Excel ResetValue' in node34(xl34.out_text, "gpio87")
non34 = udt.run(NS(out=o("l34n.dtsi"), level_source="none", **base34))
assert "NEEDS REVIEW: output pin" in node34(non34.out_text, "gpio56") and "level inferred" not in non34.out_text
assert udt.LEVEL_SOURCE == "none"
print("OK")

# ------------------------------------------------------------------ 35
print("=== 35. The 'existing file BEFORE this run' table appears only when there IS an existing file ===")
no_file = udt.run(NS(excel=CCS20_XL, project="CCS20", out=o("n35a.dtsi"), report=o("n35a.md")))
assert "existing file as it was BEFORE this run" not in no_file.report_text, "a table of '(none in file)' rows is noise"
with_file = udt.run(NS(excel=CCS20_XL, project="CCS20", dts=CCS20_DTS, out=o("n35b.dtsi"), report=o("n35b.md")))
assert "existing file as it was BEFORE this run" in with_file.report_text and "| GPIO_" in with_file.report_text.split("existing file as it was BEFORE this run")[1][:600]
# the other sections that used to print a heading and "None" are silent when they have nothing to say, present when they do
assert "## Updated in existing file" not in no_file.report_text and "No existing file given" not in no_file.report_text
assert "## Updated in existing file" in with_file.report_text
assert "## USB pins on an unsupported" not in no_file.report_text, "no unsupported-controller pins in CCS20: no heading"
assert "## Cross-sheet conflicts" in no_file.report_text, "CCS20 does have conflicts: the section is there"
hon35 = udt.run(NS(excel=HONDA_XL, project="Honda_GenID_B0", out=o("n35h.dtsi"), report=o("n35h.md")))
assert "## Cross-sheet conflicts" not in hon35.report_text and "None found (USB-named" not in hon35.report_text, "Honda has none: no heading"
if IVECO_XL and os.path.exists(IVECO_XL):
    iv35 = udt.run(NS(excel=IVECO_XL, project="Iveco CDC B1", out=o("n35i.dtsi"), report=o("n35i.md")))
    assert "## USB pins on an unsupported pin controller" in iv35.report_text, "SAIL pins exist in B1: the section is there"
print("OK")

print("\nUSB DTS TOOL TEST PASSED")
