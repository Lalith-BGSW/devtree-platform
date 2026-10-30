"""
Tests the TEMPLATE ENGINE (app/dts_style.py + the tool's output files).

The guarantees under test: a template gives FORMAT, never content; restyling cannot change a value (and is refused if it would);
output is separate files by role; the independent verifier reads the written files again; a missing / odd template degrades to the
default format with a stated reason.
"""
import base64, io, os, re, sys, tempfile, zipfile
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, ROOT); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(ROOT, "tool"))
import sys as _s, os as _o; _s.path.insert(0, _o.path.join(_o.path.dirname(_o.path.abspath(__file__)), '..', 'scripts')); import _samples; _samples.need(__file__)
from _mock_llm import MockLLM
from app import dts_style as D
import usb_dts_tool as T

S = os.path.join(ROOT, "samples")
XL = os.path.join(S, "Nissan_CCS20_SA8155PSIP_PinMux.xlsx")
HONDA = os.path.join(S, "Honda_database_v2.xlsx")
tmp = tempfile.mkdtemp()
mock = MockLLM(); base = mock.start()


class NS:
    def __init__(s, **k):
        d = dict(project="CCS20", dts=None, out=None, report=None, pattern="controller", overlay=None, insert_after=None, vbus_pin=[], role=[],
                 include_pin=[], exclude_pin=[], inventory=None, combined=None, no_cache=False, no_update=False, keyword="usb", match="both",
                 list_projects=False, ai_advice=False, ai_max_pins=12, excel=XL, ai_propose=False, ai_suggest=False, facts="",
                 ai_draft=None, ai_proposal_out=None, docs=[], level_source="convention", template=[], out_dir=None)
        d.update(k); s.__dict__.update(d)


def run(tag, **k):
    return T.run(NS(out=f"{tmp}/{tag}.dtsi", report=f"{tmp}/{tag}.md", out_dir=f"{tmp}/{tag}_files", **k))


def nodes_of(text):
    out = {}
    for b in D.top_level_blocks(D._norm(text)):
        if b["header"].startswith("&tlmm"):
            out.update(D.signature(D.parse_pin_nodes(D._inner(b["text"]))[0]))
    return out


def read(p):
    return open(p, encoding="utf-8", newline="").read()


# ---- templates written for the test (the real samples are used too)
FLAT = '''// SPDX-License-Identifier: GPL-2.0
/*
 * Mainline-style pinmux of ANOTHER board
 */

&tlmm {
    other_vbus_en:other-vbus-en-state {
        pins = "gpio50";
        function = "gpio";
        drive-strength = <8>;
        bias-disable;
        output-low;
    };
    other_oc:other-oc-state {
        pins = "gpio51";
        function = "gpio";
        drive-strength = <2>;
        bias-pull-up;
        input-enable;
    };
};
'''
flat_path = os.path.join(tmp, "flat.dtsi"); open(flat_path, "w", newline="").write(FLAT.replace("\n", "\r\n"))
OVL4 = '''/dts-v1/;
/plugin/;
#include "other-board-pinmux.dtsi"
#include <dt-bindings/gpio/gpio.h>

/ {
    model = "other board";
    other_reg: other_reg {
        compatible = "regulator-fixed";
    };
};
'''
ovl4_path = os.path.join(tmp, "ovl4.dts"); open(ovl4_path, "w").write(OVL4)

print("=== 1. what is LEARNED from the three real templates ===")
ccs = D.learn_style(read(os.path.join(S, "ccs20-pinmux.dtsi")), "ccs20-pinmux.dtsi")
suz = D.learn_style(read(os.path.join(S, "suzuki-pinmux.dtsi")), "suzuki-pinmux.dtsi")
ovl = D.learn_style(read(os.path.join(S, "suzuki-overlay.dts")), "suzuki-overlay.dts")
assert (ccs.role, suz.role, ovl.role) == ("pinmux", "pinmux", "overlay")
assert ccs.indent == "\t" and ccs.layout == "mux_config" and ccs.label_sep == ": " and ccs.header.startswith("/* Copyright")
assert ccs.blank_in_node is True and any("blank line inside a node" in m for m in ccs.mixed), ccs.mixed
assert suz.blank_in_node is False and not suz.mixed and suz.header == "", "Suzuki: 7% -> no; consistent; no header"
assert ovl.preamble == ["/dts-v1/;", "/plugin/;"] and ovl.header.startswith("/* Copyright")
assert any("sa6150p-oem-suz-da3-C1-pinmux.dtsi" in i for i in ovl.skipped_includes) and any("<dt-bindings" in i for i in ovl.skipped_includes), "every include is listed, none is reused"
fl = D.learn_style(read(flat_path), "flat.dtsi")
assert (fl.indent, fl.label_sep, fl.layout, fl.name_style, fl.blank_between_nodes, fl.eol) == ("    ", ":", "flat", "hyphen_state", False, "\r\n")
NESTED_GROUPS = '''&tlmm {
	bus_pins: bus_pins {
		i2c_sda: i2c_sda {
            mux {
                pins = "gpio17";
                function = "qup0_se0_l0";
            };
            config {
                pins = "gpio17";
                bias-pull-up;
            };
		};
		i2c_scl: i2c_scl {
            mux {
                pins = "gpio18";
                function = "qup0_se0_l1";
            };
            config {
                pins = "gpio18";
                bias-pull-up;
            };
		};
	};
};
'''
nested_style = D.learn_style(NESTED_GROUPS, "nested-groups.dtsi")
assert nested_style.role == "pinmux" and nested_style.layout == "mux_config"
assert nested_style.recognized_pin_states == 2 and nested_style.unrecognized_pin_states == 0
SOC_NESTED = '''&soc {
	tlmm: pinctrl@500000 {
		group: group {
			state: state {
                mux {
                    pins = "gpio1";
                    function = "gpio";
                };
                config {
                    pins = "gpio1";
                    bias-disable;
                };
			};
		};
	};
};
'''
assert D.learn_style(SOC_NESTED, "soc-nested.dtsi").recognized_pin_states == 1
PARTIAL = NESTED_GROUPS.replace("\t};\n};\n", "\t\tunknown_state: unknown_state {\n\t\t\tpinconf {\n\t\t\t\tpins = \"gpio19\";\n\t\t\t\tfunction = \"gpio\";\n\t\t\t};\n\t\t};\n\t};\n};\n", 1)
partial_style = D.learn_style(PARTIAL, "partial.dtsi")
assert partial_style.recognized_pin_states == 2 and partial_style.unrecognized_pin_states == 1
partial_path = os.path.join(tmp, "partial.dtsi"); open(partial_path, "w", encoding="utf-8").write(PARTIAL)
partial_run = run("t_partial", pattern="controller", template=[partial_path])
assert partial_run.files[0]["style_source"].startswith("default format")
assert any("ignored so its format is not learned from incomplete evidence" in note for note in partial_run.bundle_notes)
PINMUX_PINCONF = '''&tlmm {
	usb1_default: usb1_default {
		pinmux {
			pins = "gpio31";
			function = "gpio";
		};
		pinconf {
			pins = "gpio31";
			bias-pull-up;
			drive-strength = <8>;
		};
	};
};
'''
pinconf_style = D.learn_style(PINMUX_PINCONF, "pinmux-pinconf.dtsi")
pinconf_block = D.top_level_blocks(D._norm(PINMUX_PINCONF))[0]
pinconf_nodes, pinconf_unparsed = D.parse_pin_nodes(D._inner(pinconf_block["text"]))
assert pinconf_style.layout == "pinmux_pinconf" and len(pinconf_nodes) == 1 and not pinconf_unparsed
assert "pinmux{}/pinconf{}" in pinconf_style.summary()
pinconf_restyled, pinconf_note = D.restyle_tlmm(pinconf_block["text"], pinconf_style)
roundtrip_nodes, roundtrip_unparsed = D.parse_pin_nodes(D._inner(pinconf_restyled))
assert not pinconf_note and not roundtrip_unparsed and D.signature(roundtrip_nodes) == D.signature(pinconf_nodes)
assert "pinmux {" in pinconf_restyled and "pinconf {" in pinconf_restyled
assert D.learn_style("this is not a dts", "junk.txt").role == "unknown"
print("OK")

print("=== 2. restyling cannot change a value: parse-back equals the verified text, in every style ===")
default_text = read(run("base", pattern="controller").files_dir + "/ccs20_usb_pinmux.dtsi")
tl = next(b for b in D.top_level_blocks(D._norm(default_text)) if b["header"].startswith("&tlmm"))
want = nodes_of(default_text)
assert len(want) == 6   # the 5 single-purpose pins + the shared GPIO_92 (its pin state is generated once)
for st in (ccs, suz, fl, D.Style(), D.Style(indent="  ", label_sep=":", layout="flat", name_style="hyphen_state", blank_between_nodes=False)):
    out, note = D.restyle_tlmm(tl["text"], st)
    assert not note, (st.summary(), note)
    assert nodes_of(out) == want, f"meaning changed in style {st.summary()}"
same, _ = D.restyle_tlmm(tl["text"], D.Style())
assert D._norm(same).strip() == D._norm(tl["text"]).strip() or nodes_of(same) == want, "default style is a no-op in meaning"
# a renderer that drops or alters anything is REFUSED, and the file keeps the default format
orig = D.render_pin_nodes
D.render_pin_nodes = lambda nodes, style, wrapper="&tlmm": orig([dict(n, props=n["props"][:-1]) for n in nodes], style, wrapper)
try:
    refused, why = D.restyle_tlmm(tl["text"], fl)
finally:
    D.render_pin_nodes = orig
assert refused == tl["text"] and "refused" in why, why
print("OK")

print("=== 3. NO template: still separate files (pinmux + overlay) in the default format, verified again ===")
r = run("t_none", pattern="controller")
assert [f["name"] for f in r.files] == ["ccs20_usb_pinmux.dtsi", "ccs20_usb_overlay.dts"] and r.files_dir
assert all(f["style_source"] == D.DEFAULT_SOURCE for f in r.files)
assert sorted(os.listdir(r.files_dir)) == ["ccs20_usb_overlay.dts", "ccs20_usb_pinmux.dtsi"]
assert all(read(os.path.join(r.files_dir, f["name"])) == f["text"] for f in r.files), "what is written is what is returned"
bv = r.bundle_verification
assert not bv["wrong"] and not bv["ambiguous"] and bv["ok"] >= 8, bv
assert "&usb0 {" in r.files[1]["text"] and "&tlmm {" in r.files[0]["text"] and "&usb0" not in r.files[0]["text"], "pin states and consumers are separated by role"
print("OK")

print("=== 4. WITH the real templates: the format is theirs, the content is ours, and nothing of theirs is copied ===")
r = run("t_suz", pattern="controller", template=[os.path.join(S, "suzuki-pinmux.dtsi"), os.path.join(S, "suzuki-overlay.dts")])
pm, ov = r.files
assert "learned from the template 'suzuki-pinmux.dtsi'" in pm["style_source"] and "learned from the template 'suzuki-overlay.dts'" in ov["style_source"]
assert ov["text"].startswith("/* Copyright (c) 2018-2019, The Linux Foundation") and "/dts-v1/;\n/plugin/;" in ov["text"]
assert not re.search(r"^#include", ov["text"], re.M), "no #include is ever copied"
suz_text = read(os.path.join(S, "suzuki-pinmux.dtsi")) + read(os.path.join(S, "suzuki-overlay.dts"))
LABEL_RE = r"^\s*([A-Za-z_]\w*)\s*:\s*[\w@,.\-]+\s*\{"
suz_labels = set(re.findall(LABEL_RE, suz_text, re.M))
mine = set(re.findall(LABEL_RE, pm["text"] + ov["text"], re.M))
assert not (mine & suz_labels), f"labels copied from a template: {mine & suz_labels}"
assert "gpio2\"" not in pm["text"] and "qupv3" not in pm["text"] + ov["text"], "no pin or node of the template's project appears"
assert nodes_of(pm["text"]) == nodes_of(default_text), "same values as the default-format file"
rep = read(f"{tmp}/t_suz.md")
assert "## Output files and format" in rep and "Template `#include` lines were NOT copied" in rep and "sa6150p-oem-suz-da3-C1-pinmux.dtsi" in rep
assert "Bosch" not in pm["text"], "the Suzuki pinmux template has no header, so none is invented"
r2 = run("t_ccs", pattern="controller", template=[os.path.join(S, "ccs20-pinmux.dtsi")])
assert r2.files[0]["text"].startswith("/* Copyright (c) 2021-2022, Robert Bosch Car Multimedia GmbH"), "its header is reused"
assert "no overlay template was given: the overlay file uses the default format" in r2.bundle_notes
assert "inconsistent about" in read(f"{tmp}/t_ccs.md") and "a blank line inside a node" in read(f"{tmp}/t_ccs.md"), "a mixed habit is reported, not hidden"
print("OK")

print("=== 5. a flat / 4-space / label:node / CRLF template: layout, names, indentation and line endings all follow ===")
r = run("t_flat", pattern="controller", template=[flat_path, ovl4_path])
pm, ov = r.files
raw_pm = open(os.path.join(r.files_dir, pm["name"]), "rb").read()
assert raw_pm.count(b"\r\n") == raw_pm.count(b"\n") and b"\r\n" in raw_pm, "CRLF everywhere"
body = pm["text"].replace("\r\n", "\n")
assert '    usbv3_fault_detect:usbv3-fault-detect-state {\n        pins = "gpio81";\n        function = "gpio";' in body, body[:700]
assert "mux {" not in body and "config {" not in body, "flat layout"
assert nodes_of(body) == nodes_of(default_text), "same values in a different layout"
assert "other_vbus_en" not in body and "gpio50" not in body and "other-oc-state" not in body, "nothing of the template's pins or labels"
assert "other-board-pinmux.dtsi" not in ov["text"] and "other_reg" not in ov["text"] and "other board" not in ov["text"]
assert "\n    oc-gpio = <&tlmm 81 GPIO_ACTIVE_LOW>;" in ov["text"], "overlay indentation follows the overlay template (4 spaces)"
assert not r.bundle_verification["wrong"], r.bundle_verification["wrong"]
print("OK")

print("=== 6. odd templates degrade, stated: unreadable / not DTS / a second one for the same role / only one role given ===")
junk = os.path.join(tmp, "junk.txt"); open(junk, "w").write("hello world, this is not a device tree")
r = run("t_odd", pattern="controller", template=[junk, flat_path, os.path.join(S, "suzuki-pinmux.dtsi")])
assert any("junk.txt" in n and "ignored" in n for n in r.bundle_notes), r.bundle_notes
assert any("second pinmux template" in n and "suzuki-pinmux.dtsi" in n for n in r.bundle_notes), r.bundle_notes
assert "learned from the template 'flat.dtsi'" in r.files[0]["style_source"], "the first usable template wins"
assert "no overlay template was given: the overlay file uses the default format" in r.bundle_notes
try:
    run("t_missing", template=[os.path.join(tmp, "does_not_exist.dtsi")])
    raise AssertionError("a missing template must stop with a message")
except SystemExit as e:
    assert "cannot read it" in str(e)
print("OK")

print("=== 7. an unconfirmed wiring style has no consumers: one file, and it says why ===")
r = run("t_unconf", pattern=None)
assert [f["name"] for f in r.files] == ["ccs20_usb_pinmux.dtsi"] and any("no overlay file" in n for n in r.bundle_notes), r.bundle_notes
print("OK")

print("=== 8. regulator style: the overlay carries `/ { regulators }` in the template's indentation ===")
r = run("t_reg", excel=HONDA, project="Honda_GenID_B0", pattern="regulator", template=[ovl4_path])
ov = next(f for f in r.files if f["role"] == "overlay")["text"]
normalized_overlay = ov.replace("\r\n", "\n")
assert '/ {\n    usb0_vbus_regulator: usb0_vbus_regulator {\n        compatible = "regulator-fixed";' in normalized_overlay, normalized_overlay[normalized_overlay.index("/ {"):][:260]
assert "&usb0 {" in ov and not r.bundle_verification["wrong"], r.bundle_verification["wrong"]
print("OK")

print("=== 9. Hybrid: the files are made from the FINAL (verified) text, with the same values as Python's own output ===")
for k in [k for k in os.environ if k.startswith("LLM_")]:
    del os.environ[k]
os.environ.update(LLM_BASE_URL=base, LLM_API_KEY="k", LLM_DEPLOYMENT="d", LLM_TIMEOUT="5")
def hyb(b):
    m = re.search(r"VERIFIED_DTS:\n```dts\n(.*?)\n```", b["messages"][1]["content"], re.S)
    return "```dts\n" + ((m.group(1) + "\n") if m else "") + "```"
def garbage(b):
    return "I could not do that, sorry."
mock.reply = hyb
h = run("t_hyb", pattern="controller", ai_propose=True, facts="context: this board has two USB ports", template=[flat_path])
assert h.ai_proposal and h.ai_proposal["status"] in ("clean", "needs_attention")
_a, _b = nodes_of(h.files[0]["text"]), nodes_of(h.out_text)
assert _a == _b, ("hybrid files carry the same pin values as the Python output", {k: (_a.get(k), _b.get(k)) for k in set(_a) | set(_b) if _a.get(k) != _b.get(k)}, list(_a), h.files[0]["name"], h.files[0]["text"][:420], h.files[0]["note"])
assert "    usbv3_fault_detect:usbv3-fault-detect-state {" in h.files[0]["text"].replace("\r\n", "\n")
print("OK")

print("=== 9b. a hybrid result that is not clean NEVER becomes the files: they come from Python's verified output, and say so ===")
mock.reply = garbage
g = run("t_garbage", pattern="controller", ai_propose=True, facts="context: this board has two USB ports", template=[flat_path])
assert g.ai_proposal["status"] == "needs_attention", g.ai_proposal["status"]
assert any("needs attention" in n and "Python's verified output" in n for n in g.bundle_notes), g.bundle_notes
assert [f["name"] for f in g.files] == ["ccs20_usb_pinmux.dtsi", "ccs20_usb_overlay.dts"]
assert nodes_of(g.files[0]["text"]) == nodes_of(g.out_text) and "I could not do that" not in "".join(f["text"] for f in g.files)
mock.reply = hyb
print("OK")

print("=== 9c. a template that is also an output file is refused BEFORE anything is written (it would be overwritten unread) ===")
tp = os.path.join(tmp, "same.dtsi"); open(tp, "w").write(FLAT)
try:
    T.run(NS(out=tp, report=f"{tmp}/same.md", out_dir=f"{tmp}/same_files", template=[tp]))
    raise AssertionError("must refuse")
except SystemExit as e:
    assert "overwritten before it is read" in str(e)
assert open(tp).read() == FLAT, "the template is untouched"
print("OK")

print("=== 10. an existing DTS is its own template: no separate files, said plainly ===")
ex = run("t_exist", pattern="controller", dts=os.path.join(S, "ccs20-pinmux.dtsi"))
assert ex.files == [] and any("existing DTS was given" in n for n in ex.bundle_notes)
print("OK")

print("=== 11. Web API: templates upload -> files + a zip with the same files ===")
from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)
wb = client.post("/api/workbook/upload", files={"file": ("w.xlsx", open(XL, "rb"))}).json()["workbook_id"]
assert client.post(f"/api/workbook/{wb}/build").status_code == 200
url = f"/api/workbook/{wb}/usb-tool/update-or-generate"
j = client.post(url, data={"project": "CCS20", "pattern": "controller"},
                files=[("templates", ("flat.dtsi", open(flat_path, "rb"), "text/plain")), ("templates", ("suzuki-overlay.dts", open(os.path.join(S, "suzuki-overlay.dts"), "rb"), "text/plain"))]).json()
assert [f["role"] for f in j["files"]] == ["pinmux", "overlay"] and "learned from the template 'flat.dtsi'" in j["files"][0]["style_source"]
z = zipfile.ZipFile(io.BytesIO(base64.b64decode(j["bundle_zip_b64"])))
assert sorted(z.namelist()) == sorted(f["name"] for f in j["files"]) and all(z.read(f["name"]).decode() == f["text"] for f in j["files"])
j0 = client.post(url, data={"project": "CCS20", "pattern": "controller"}).json()
assert [f["name"] for f in j0["files"]] == ["ccs20_usb_pinmux.dtsi", "ccs20_usb_overlay.dts"] and j0["bundle_zip_b64"]
mock.stop()
print("OK")

print("\nDTS STYLE TEST PASSED")
