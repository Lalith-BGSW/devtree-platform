"""Runs every option of the USB tool on the fabricated demo workbook and checks the result against answers known in advance.
Needs no real data and no network (the kernel scenarios use GitHub when reachable and the built-in table otherwise).
Usage (from the folder containing backend/ and test_kit/):   python test_kit/run_checks.py
Prints one PASS/FAIL line per check and writes everything it generated to test_kit/_out/ so you can open the files."""
import os, re, subprocess, sys, shutil
HERE = os.path.dirname(os.path.abspath(__file__)); BACK = os.path.join(HERE, "..", "backend")
OUT = os.path.join(HERE, "_out"); XL = os.path.join(HERE, "demo_workbook.xlsx")
TOOL = os.path.join(BACK, "tool", "usb_dts_tool.py")
sys.path.insert(0, BACK)
if not os.path.exists(XL):
    subprocess.run([sys.executable, os.path.join(HERE, "make_demo_workbook.py")], check=True)
shutil.rmtree(OUT, ignore_errors=True); os.makedirs(OUT)
FAILS = []


def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "   <- " + str(extra)[:300]))
    if not cond: FAILS.append(name)


def run(tag, *args, dts=None):
    d = os.path.join(OUT, tag); os.makedirs(d)
    cmd = [sys.executable, TOOL, "--excel", XL, "--project", "CCS20", "--out", os.path.join(d, "result.dtsi"),
           "--report", os.path.join(d, "report.md"), "--out-dir", os.path.join(d, "files"), "--no-cache"] + list(args)
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=BACK)
    open(os.path.join(d, "log.txt"), "w").write(p.stdout + p.stderr)
    def rd(name):
        for root, _d, fs in os.walk(d):
            for f in fs:
                if name in f: return open(os.path.join(root, f), encoding="utf-8").read()
        return ""
    return dict(code=p.returncode, log=p.stdout + p.stderr, report=rd("report.md"), pinmux=rd("pinmux.dtsi"), overlay=rd("overlay.dts"),
                result=rd("result.dtsi"), dir=d)


def nodes(text):
    """{label: (pins, functions, properties)} whatever the layout - used to prove two outputs carry the same VALUES."""
    out = {}
    for m in re.finditer(r"(\w+):\s*[\w\-]+\s*\{", text):
        i, depth, j = m.end(), 1, m.end()
        while depth and j < len(text):
            depth += (text[j] == "{") - (text[j] == "}"); j += 1
        body = text[i:j - 1]
        pins = tuple(sorted({q for line in re.findall(r"pins = ([^;]+);", body) for q in re.findall(r'"([^"]+)"', line)}))
        if pins:
            out[m.group(1)] = (pins, tuple(sorted(set(re.findall(r'function = "([^"]+)"', body)))),
                               tuple(sorted(re.findall(r"^\s*((?:drive-strength|bias-[\w-]+|input-enable|output-(?:high|low))[^;]*);", body, re.M))))
    return out


def verifier(r):
    m = re.search(r"independent verifier read the files again: (\d+) wrong \| (\d+) ambiguous \| (\d+) unverifiable \| (\d+) confirmed OK", r["report"])
    return tuple(int(x) for x in m.groups()) if m else None


print("\n== 1. Baseline: no options (regulator style)")
base = run("01_baseline", "--pattern", "regulator")
check("tool ran", base["code"] == 0, base["log"][-300:])
bn = nodes(base["pinmux"])
check("the 5 expected USB pins are generated (gpio10..14)", {p for v in bn.values() for p in v[0]} == {f"gpio{n}" for n in range(10, 15)}, bn.keys())
check("boot-strap pin gpio15 (FORCE_USB) is NOT generated", "gpio15" not in base["pinmux"])
check("UART / I2C pins are not part of a USB run", not re.search(r"gpio2[0-3]", base["pinmux"]))
check("gpio10 = input, pull-up, 4 mA (the workbook's values)", bn.get("usbv2_0_fault_detect") == (("gpio10",), ("gpio",), ("bias-pull-up", "drive-strength = <4>", "input-enable")), bn.get("usbv2_0_fault_detect"))
check("gpio11 (VBUS enable) has an output level and it is flagged NEEDS REVIEW", "output-low;" in base["pinmux"] and "NEEDS REVIEW: level inferred" in base["pinmux"])
check("gpio14 (hub reset) has NO invented level, only a NEEDS REVIEW", re.search(r'gpio14";\s*drive-strength = <8>;\s*/\* NEEDS REVIEW: output pin', base["pinmux"]) is not None)
check("two regulators in the overlay, polarity from the workbook", base["overlay"].count('compatible = "regulator-fixed"') == 2 and "&usb0" in base["overlay"] and "&usb1" in base["overlay"])
check("independent verifier: 0 wrong, 0 ambiguous", (verifier(base) or (9, 9))[:2] == (0, 0), verifier(base))
check("default format = tabs + nested mux/config", "\t\tmux {" in base["pinmux"] and "Format source: **built-in default" in base["report"])

print("\n== 2. Template DTS files (format only)")
ta = run("02_templateA", "--pattern", "regulator", "--template", os.path.join(HERE, "templates", "template_A_spaces_flat.dtsi"))
check("template A: flat '-state' nodes, 4-space indent", "-state {" in ta["pinmux"] and "\n    usbv2_0" in ta["pinmux"] and "\t" not in ta["pinmux"].split("&tlmm")[1], ta["pinmux"][-400:])
check("template A: none of its own pins/labels are copied", "gpio90" not in ta["pinmux"] and "sample_alpha" not in ta["pinmux"])
check("template A: SAME values as the baseline (only the layout differs)", sorted(v[:1] + v[1:] for v in nodes(ta["pinmux"]).values()) == sorted(v for v in bn.values()), "")
check("template A: report says the format came from the template", "your template" in ta["report"])
tb = run("03_templateB", "--pattern", "regulator", "--template", os.path.join(HERE, "templates", "template_B_tabs_nested.dtsi"))
check("template B: tabs + nested mux/config, its pins not copied", "\t\tmux {" in tb["pinmux"] and "gpio95" not in tb["pinmux"] and "sample_gamma" not in tb["pinmux"])
check("template B: SAME values as the baseline", sorted(nodes(tb["pinmux"]).values()) == sorted(bn.values()))
check("both templates: verifier 0 wrong", (verifier(ta) or (9,))[0] == 0 and (verifier(tb) or (9,))[0] == 0)

print("\n== 3. Linux kernel version (no template)")
k66 = run("04_kernel66", "--pattern", "regulator", "--kernel", "6.6")
k515 = run("05_kernel515", "--pattern", "regulator", "--kernel", "5.15")
check("kernel 6.6 -> flat '-state' nodes", "-state {" in k66["pinmux"] and "mux {" not in k66["pinmux"], k66["report"][-500:])
check("kernel 5.15 -> nested mux/config", "mux {" in k515["pinmux"] and "-state {" not in k515["pinmux"])
check("kernel runs carry the same values as the baseline", sorted(nodes(k66["pinmux"]).values()) == sorted(bn.values()) == sorted(nodes(k515["pinmux"]).values()))
check("the report states where the format came from and the vendor-BSP caveat", "kernel reference" in k66["report"] and "vendor BSP" in k66["report"])
kt = run("06_kernel_plus_template", "--pattern", "regulator", "--kernel", "6.6", "--template", os.path.join(HERE, "templates", "template_B_tabs_nested.dtsi"))
check("a template always wins over --kernel", "mux {" in kt["pinmux"] and "-state {" not in kt["pinmux"])
kb = run("07_kernel_bad", "--pattern", "regulator", "--kernel", "banana")
check("a bad kernel version is refused clearly", kb["code"] != 0 and "6.6" in kb["log"], kb["log"][-200:])

print("\n== 4. Your decisions")
nl = run("08_levels_none", "--pattern", "regulator", "--level-source", "none")
check("--level-source none: no output level is written anywhere", not re.search(r"output-(high|low);", nl["pinmux"]))
ex = run("09_levels_excel", "--pattern", "regulator", "--level-source", "excel")
check("--level-source excel: runs, verifier 0 wrong", ex["code"] == 0 and (verifier(ex) or (9,))[0] == 0)
xp = run("10_exclude_pin", "--pattern", "regulator", "--exclude-pin", "GPIO_14")
check("--exclude-pin GPIO_14: listed in the report; it only silences sheet conflicts / AI suggestions, so a pin that IS in this project's sheet is still generated",
      "Pins you excluded" in xp["report"] and "GPIO_14" in xp["report"].split("Pins you excluded")[1][:200] and nodes(xp["pinmux"]) == bn)
ip = run("11_include_pin", "--pattern", "regulator", "--include-pin", "GPIO_15")
check("--include-pin GPIO_15 (a boot-strap pin you decide needs a runtime state): generated with its workbook values, input + pull-down + 2 mA",
      nodes(ip["pinmux"]).get("sa6150_bootcfg_force_usb") == (("gpio15",), ("gpio",), ("bias-pull-down", "drive-strength = <2>", "input-enable")), ip["log"][-300:])
ct = run("12_controller", "--pattern", "controller")
check("controller style: oc-gpio / vbus-out-gpio on &usbN, no regulators", "oc-gpio" in (ct["overlay"] + ct["result"]) and "regulator-fixed" not in (ct["overlay"] + ct["result"]), ct["log"][-300:])

print("\n== 5. Update / check an existing DTS")
up = run("13_update_existing", "--pattern", "regulator", "--dts", os.path.join(HERE, "inputs", "existing_with_errors.dts"))
check("update: 2 wrong values found (gpio10 drive 16 -> 4, bias pull-down -> pull-up)", "| usbv2_0_fault_detect | gpio10 | drive-strength | 16 | 4 |" in up["report"] and "bias-pull-down | bias-pull-up" in up["report"], up["report"][:900])
check("update: the correct node (gpio12) is left unchanged", "usbv2_1_fault_detect | gpio12" not in up["report"].split("## Consumer")[0])
upd_text = open(os.path.join(up["dir"], "result.dtsi"), encoding="utf-8").read() if os.path.exists(os.path.join(up["dir"], "result.dtsi")) else ""
check("update: corrected file has the right values", "drive-strength = <16>" not in upd_text and "bias-pull-down" not in upd_text)
d2 = os.path.join(OUT, "13b_rerun_on_updated"); os.makedirs(d2)
shutil.copy(os.path.join(up["dir"], "result.dtsi"), os.path.join(d2, "in.dts"))
p = subprocess.run([sys.executable, TOOL, "--excel", XL, "--project", "CCS20", "--pattern", "regulator", "--dts", os.path.join(d2, "in.dts"),
                    "--out", os.path.join(d2, "out.dts"), "--report", os.path.join(d2, "r.md"), "--no-cache"], capture_output=True, text=True, cwd=BACK)
check("update is idempotent: running again on its own output changes 0 values", "Updated values: 0" in p.stdout, p.stdout[-300:])
cho = run("14_check_only", "--pattern", "regulator", "--dts", os.path.join(HERE, "inputs", "existing_with_errors.dts"), "--no-update")
check("check only: reports the differences but the file is not rewritten with fixes", "drive-strength = <16>" in (open(os.path.join(cho["dir"], "result.dtsi")).read() if os.path.exists(os.path.join(cho["dir"], "result.dtsi")) else "drive-strength = <16>"), cho["log"][-300:])

print("\n== 6. Generic generator (every peripheral, not only USB)")
from app.generators import dts_generator as G
sys.path.insert(0, os.path.join(BACK, "tool"))
import usb_dts_tool as T
parsed, recs, soc = T.load_records(XL, "CCS20")
txt, amb, rev = G.generate_dts("CCS20", soc, recs)
gn = nodes(txt)
check("UART TX (gpio20) and RX (gpio21) get SEPARATE nodes because their settings differ (no pull vs pull-up)",
      any(v[0] == ("gpio20",) and v[1] == ("qup13",) for v in gn.values()) and any(v[0] == ("gpio21",) and v[1] == ("qup13",) for v in gn.values()))
check("the I2C pair (identical settings) shares ONE qup2 node", any(v[0] == ("gpio22", "gpio23") and v[1] == ("qup2",) for v in gn.values()))
check("every one of the 10 pins appears in exactly one node", sorted(p for v in gn.values() for p in v[0]) == sorted(f"gpio{n}" for n in (10, 11, 12, 13, 14, 15, 20, 21, 22, 23)))
check("each pin keeps ITS OWN values: gpio21 pull-up 2 mA, gpio14 8 mA, gpio22/23 4 mA",
      any(v[0] == ("gpio21",) and "bias-pull-up" in v[2] and "drive-strength = <2>" in v[2] for v in gn.values())
      and any(v[0] == ("gpio14",) and "drive-strength = <8>" in v[2] for v in gn.values())
      and any(v[0] == ("gpio22", "gpio23") and "drive-strength = <4>" in v[2] for v in gn.values()))
check("the 3 output pins (gpio11/13/14) carry NEEDS REVIEW and no invented output level", txt.count("NEEDS REVIEW: output pin") == 3 and not re.search(r"output-(high|low);", txt))

print("\n" + ("ALL CHECKS PASSED" if not FAILS else f"{len(FAILS)} CHECK(S) FAILED: " + "; ".join(FAILS)))
print(f"Files you can open and inspect: {OUT}")
sys.exit(1 if FAILS else 0)
