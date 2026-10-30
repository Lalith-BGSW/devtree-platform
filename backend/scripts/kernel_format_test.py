"""
Tests --kernel (app/kernel_format.py): the pin-state format follows the Linux kernel version.
Reference files come from a LOCAL directory through KERNEL_RAW_BASE=file://..., so the test needs no internet.
Guarantees: the format is learned from the fetched file (not hard-coded); values never change (verifier + signature equality);
the kernel file's copyright header is never copied; a template wins; unreachable network -> the measured table; outside the
measured range -> default format + a stated reason; nothing guessed.
"""
import os, pathlib, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, ROOT); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(ROOT, "tool"))
import sys as _s, os as _o; _s.path.insert(0, _o.path.join(_o.path.dirname(_o.path.abspath(__file__)), '..', 'scripts')); import _samples; _samples.need(__file__)
from app import dts_style as D, kernel_format as K
import usb_dts_tool as T

XL = os.path.join(ROOT, "samples", "Nissan_CCS20_SA8155PSIP_PinMux.xlsx")
tmp = tempfile.mkdtemp()
FAILS = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)))
    if not cond:
        FAILS.append(name)


class NS:
    def __init__(s, **k):
        d = dict(project="CCS20", dts=None, out=None, report=None, pattern="controller", overlay=None, insert_after=None, vbus_pin=[], role=[],
                 include_pin=[], exclude_pin=[], inventory=None, combined=None, no_cache=False, no_update=False, keyword="usb", match="both",
                 list_projects=False, ai_advice=False, ai_max_pins=12, excel=XL, ai_propose=False, ai_suggest=False, facts="",
                 ai_draft=None, ai_proposal_out=None, docs=[], level_source="convention", template=[], out_dir=None, kernel=None,
                 kernel_reference=None, kernel_base=None)
        d.update(k); s.__dict__.update(d)


def run(tag, **k):
    return T.run(NS(out=f"{tmp}/{tag}.dtsi", report=f"{tmp}/{tag}.md", out_dir=f"{tmp}/{tag}_files", **k))


def pinmux(res):
    return next(f for f in res.files if f["role"] == "pinmux")["text"]


def nodes_of(text):
    out = {}
    for b in D.top_level_blocks(D._norm(text)):
        if b["header"].startswith("&tlmm"):
            out.update(D.signature(D.parse_pin_nodes(D._inner(b["text"]))[0]))
    return out


# ---- fixtures: tiny "kernel trees" with their OWN habits (4 spaces in the nested one -> proves the format is learned, not hard-coded)
HDR = "/*\n * SPDX-License-Identifier: BSD-3-Clause\n * Copyright (c) 2099, Example Corp - kernel header that must never be copied\n */\n"
NESTED = HDR + """/dts-v1/;
#include "sm8150.dtsi"
&tlmm {
    a_default: a_default {
        mux {
            pins = "gpio1";
            function = "gpio";
        };
        config {
            pins = "gpio1";
            bias-disable;
        };
    };

    b_default: b_default {
        mux {
            pins = "gpio2";
            function = "gpio";
        };
        config {
            pins = "gpio2";
            bias-disable;
        };
    };
};
"""
FLAT = HDR + """/dts-v1/;
&tlmm {
\ta_default: a-default-state {
\t\tpins = "gpio1";
\t\tfunction = "gpio";
\t\tbias-disable;
\t};

\tb_default: b-default-state {
\t\tpins = "gpio2";
\t\tfunction = "gpio";
\t\tbias-disable;
\t};
};
"""
NOSTATE = HDR + "/dts-v1/;\n&tlmm {\n\tgpio-reserved-ranges = <0 4>;\n};\n"
REF = "arch/arm64/boot/dts/qcom/sa8155p-adp.dts"


def tree(root, tag, text):
    p = os.path.join(root, tag, REF)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w", encoding="utf-8").write(text)


root = tempfile.mkdtemp()
tree(root, "v6.1", NESTED); tree(root, "v6.6", FLAT); tree(root, "v6.3", NOSTATE)
os.environ["KERNEL_RAW_BASE"] = pathlib.Path(root).as_uri()

print("== 1. version parsing")
for txt, tag in [("6.6", "v6.6"), ("v6.1.20", "v6.1"), ("linux-5.15", "v5.15"), ("5.10.y", "v5.10"), ("6.7-rc2", "v6.7-rc2")]:
    v = K.parse_version(txt)
    check(f"parse {txt!r} -> {tag}", v and v["tag"] == tag, v)
for bad in ["", "six", "6", "6.x", "abc.1", None]:
    check(f"parse {bad!r} rejected", K.parse_version(bad) is None)
check("a point release is explained", "v6.1 tag" in K.parse_version("6.1.20")["note"])

print("== 2. fetched file -> format learned from it")
r = K.resolve("6.6", "SA8155P")
check("v6.6 fetched, flat + hyphen-state", r["kind"] == "fetched" and r["style"].layout == "flat" and r["style"].name_style == "hyphen_state", r)
check("source names the tag and file", "v6.6" in r["source"] and "sa8155p-adp.dts" in r["source"], r["source"])
r1 = K.resolve("6.1", "SA8155P")
check("v6.1 fetched, nested, 4-space indent LEARNED from the file", r1["style"].layout == "mux_config" and r1["style"].indent == "    ", r1["style"])
check("kernel file header / includes are not carried", r["style"].header == "" and r1["style"].skipped_includes == [] and r1["style"].preamble == [])
check("a tree you pointed at (env/--kernel-base) is labelled as not mainline", "not mainline" in r["source"])

print("== 3. end to end: values identical, verifier clean, header not copied")
base = run("default", pattern="controller")
flat = run("k66", kernel="6.6")
nest = run("k61", kernel="6.1")
for nm, res in (("6.6", flat), ("6.1", nest)):
    t = pinmux(res)
    check(f"{nm}: same pin values as the default-format output", nodes_of(t) == nodes_of(pinmux(base)), (len(nodes_of(t)), len(nodes_of(pinmux(base)))))
    check(f"{nm}: independent verifier finds nothing wrong", not res.bundle_verification["wrong"], res.bundle_verification["wrong"])
    check(f"{nm}: kernel file copyright not copied", "Example Corp" not in t and "SPDX" not in t)
check("6.6 output is flat '-state' nodes", "-state {" in pinmux(flat) and "mux {" not in pinmux(flat))
check("6.1 output is nested with the reference file's 4-space indent (learned)", "mux {" in pinmux(nest) and "\n    usb" in pinmux(nest) and "\n\tusb" not in pinmux(nest), pinmux(nest)[:400])
check("report states the kernel source", "Linux v6.6" in open(f"{tmp}/k66.md", encoding="utf-8").read())
check("kernel_resolution is returned", flat.kernel_resolution and flat.kernel_resolution["kind"] == "fetched")

print("== 4. a file without pin states is not 'learned' from")
r = K.resolve("6.3", "SA8155P")
check("v6.3 reference has no pin states -> table (flat), reason given", r["kind"] == "table" and r["style"].layout == "flat"
      and any("no pin-state nodes" in n for n in r["notes"]), r)

print("== 4b. a fetched but unfamiliar layout never falls back to an older measured format")
UNKNOWN = '&tlmm {\n    usb_default: usb_default {\n        pin_group {\n            groups = "gpio1";\n        };\n    };\n};\n'
unknown = K.resolve("6.6", "SA8155P", fetch=lambda _url: (200, UNKNOWN), base="https://vendor.example/raw")
check("unknown fetched layout is explicit and has no guessed style", unknown["kind"] == "unrecognized" and unknown["style"] is None
      and any("no older measured format was substituted" in n for n in unknown["notes"]), unknown)

_saved = os.environ.pop("KERNEL_RAW_BASE", None)
main_unknown = K.resolve("6.6", "SA8155P", fetch=lambda _url: (200, UNKNOWN))
if _saved is not None:
    os.environ["KERNEL_RAW_BASE"] = _saved
check("mainline reference not parsable -> the measured table for that same file, honestly labelled",
      main_unknown["kind"] == "table" and main_unknown["style"].layout == "flat" and "not fully parsable" in main_unknown["style"].source, main_unknown)

print("== 5. network unreachable -> measured table, honestly labelled")
os.environ["KERNEL_RAW_BASE"] = pathlib.Path(root, "nowhere").as_uri()
for ver, lay, ns_ in (("6.6", "flat", "hyphen_state"), ("6.2", "flat", "hyphen_state"), ("6.1", "mux_config", "same"), ("5.15", "mux_config", "same")):
    r = K.resolve(ver, "SA8155P")
    check(f"{ver}: table -> {lay}", r["kind"] == "table" and r["style"].layout == lay and r["style"].name_style == ns_, r["kind"])
check("the table path always carries the vendor-BSP caveat", any("vendor BSP" in n for n in r["notes"]))
check("table source says 'not fetched' and names the evidence", "not fetched" in r["source"] and "measured on SA8155P" in r["source"])
t = run("tbl", kernel="6.6")
check("table output is flat and verified", "-state {" in pinmux(t) and not t.bundle_verification["wrong"])
r = K.resolve("6.6", "SA6155P")
check("an unmeasured SoC is flagged", any("was not measured" in n for n in r["notes"]), r["notes"])

print("== 6. outside the measured range: nothing guessed")
for ver in ("5.10", "4.19", "6.13", "7.0"):
    r = K.resolve(ver, "SA8155P")
    check(f"{ver}: no style, default kept, reason given", r["style"] is None and r["kind"] == "none" and any("outside the measured range" in n for n in r["notes"]), r)
res = run("old", kernel="5.10")
check("5.10 run: default format, note present", "mux {" in pinmux(res) and any("outside the measured range" in n for n in res.bundle_notes), res.bundle_notes)

print("== 7. a template always wins")
os.environ["KERNEL_RAW_BASE"] = pathlib.Path(root).as_uri()
tpl = os.path.join(tmp, "tpl.dtsi"); open(tpl, "w").write(NESTED.replace("    ", "\t"))
res = run("win", kernel="6.6", template=[tpl])
check("template layout used, not the kernel's", "mux {" in pinmux(res) and "-state {" not in pinmux(res))
check("note says the template wins", any("was not used for the pinmux file" in n for n in res.bundle_notes), res.bundle_notes)

print("== 7b. provenance in the report; vendor tree via --kernel-base; template vs kernel comparison")
rep = lambda tag: open(f"{tmp}/{tag}.md", encoding="utf-8").read()
check("kernel run: report names the format source", "Format source: **kernel reference - fetched**" in rep("k66") and "NOT confirmed to match your BSP" not in rep("k66"))
check("no template/kernel: report says NOT confirmed + asks for --template", "NOT confirmed to match your BSP" in rep("nok") if os.path.exists(f"{tmp}/nok.md") else True)
check("template run: report says it is the user's template", "Format source: **your template" in rep("win"))
check("template differs from kernel format -> stated, template followed", any("differs from the one" in n and "vendor BSP" in n for n in res.bundle_notes), res.bundle_notes)
vroot = tempfile.mkdtemp(); VEND = NESTED.replace("a_default", "v_default")
tree(vroot, "v6.6", VEND)       # a 'vendor' tree that is still nested at 6.6 (mainline is flat there)
rv = K.resolve("6.6", "SA8155P", base=pathlib.Path(vroot).as_uri())
check("--kernel-base: vendor tree wins over mainline, labelled as not mainline",
      rv["kind"] == "fetched" and rv["style"].layout == "mux_config" and "not mainline" in rv["source"], rv["source"])
check("no mainline caveat when the tree is the person's own", not any("vendor BSP kernel" in n for n in rv["notes"]))
vres = run("vend", kernel="6.6", kernel_base=pathlib.Path(vroot).as_uri())
check("vendor-tree run: nested output at 6.6 and verified", "mux {" in pinmux(vres) and not vres.bundle_verification["wrong"])

print("== 8. bad input / no kernel")
try:
    run("bad", kernel="six"); check("bad version refused", False)
except SystemExit as e:
    check("bad version refused with a clear message", "expected a version" in str(e), e)
none = run("nok")
check("without --kernel nothing changes (default format)", "mux {" in pinmux(none) and none.kernel_resolution is None)

print("\n%s: %d failure(s)" % ("FAILED" if FAILS else "ALL PASSED", len(FAILS)))
sys.exit(1 if FAILS else 0)
