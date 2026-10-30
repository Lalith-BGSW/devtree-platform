"""
Tests the USB consumer-node generation feature, including the two
fixes made after review of real accepted code:

  1. Output SPLIT into pinmux_dtsi_text (the &tlmm pinctrl states)
     and consumer_node_text (the &usbN block ONLY, referencing labels
     by name, NEVER repeating mux/config inline) - matches the real
     confirmed shape exactly, including debounce-delay-ms,
     qcom,default-mode-host, and a dwc3@ child node.

  2. MULTI-INSTANCE generation: a workbook can describe more than one
     physical USB controller (confirmed real case: CPU_USB30_CTRL_1V8
     and CPU_USB20_CTRL_1V8 are genuinely separate). Merging both
     instances' oc_gpio/vbus_gpio candidates into one node meant a
     second instance's oc-gpio candidate was rendered as a pinctrl
     state but never actually wired to any oc-gpio property.
     generate_usb_consumer_dts_multi_instance() detects each instance
     from the Gen5 Signal Name and generates one FULLY WIRED consumer
     node per instance instead. The confirmed real instance (USB30)
     maps to the confirmed real alias (usb0); every other detected
     instance is grouped under the next sequential usb1/usb2/... alias
     per direction (not a non-numeric placeholder), generalizing to
     however many distinct instances a given project has.

Also covers RegSetting-based authoritative TLMM detection, the _B/_N
polarity rule, multi-purpose-pin exclusion, differential/PHY pad
exclusion, and the three-way existing-node detection.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import sys as _s, os as _o; _s.path.insert(0, _o.path.join(_o.path.dirname(_o.path.abspath(__file__)), '..', 'scripts')); import _samples; _samples.need(__file__)

from app.parsers.workbook_parser import parse_workbook, build_analysis
from app.parsers.normalizer import normalize
from app.peripheral_extractor import resolve_gen5_signal_slug, filter_records_by_gen5_substring
from app.md_exporter import resolve_field_slugs
from app.usb_consumer_generator import (
    categorize_usb_records, generate_usb_consumer_dts_multi_instance, detect_existing_usb_node,
    resolve_regsetting_slug, pin_is_tlmm_controlled, detect_usb_instance_token, resolve_instance_labels,
)

WORKBOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "samples", "Nissan_CCS20_SA8155PSIP_PinMux.xlsx")
SOC = "SA8155P"


def _build_context(project):
    parsed = parse_workbook(WORKBOOK)
    build_analysis(parsed)
    records = normalize(parsed)
    proj_records = [r for r in records if r.project == project]
    gen5_slug = resolve_gen5_signal_slug(project, SOC, parsed.columns)
    slug_to_base_field = {c.slug: c.base_field for c in parsed.columns if c.slug and c.base_field}
    project_slugs = {c.slug for c in parsed.columns if c.slug and c.entity == project} | \
        {c.slug for c in parsed.columns if c.slug and c.entity == SOC} | \
        {c.slug for c in parsed.columns if c.slug and c.category == "pinmux_register"}
    field_slugs = resolve_field_slugs(project_slugs, slug_to_base_field, SOC)
    pinmux_setting_slug = field_slugs.get("pinmux_setting")
    return parsed, proj_records, gen5_slug, pinmux_setting_slug, project_slugs


print("=== 1. RegSetting-based TLMM check is authoritative ===")
parsed, ccs20, gen5_slug, pinmux_setting_slug, project_slugs = _build_context("CCS20")
by_ball = {r.fields.get("sa8155sip_ball_name"): r for r in ccs20}
assert pin_is_tlmm_controlled(by_ball["GPIO_81"], pinmux_setting_slug, parsed.columns, project_slugs) is True
assert pin_is_tlmm_controlled(by_ball["DP0_LN3_M"], pinmux_setting_slug, parsed.columns, project_slugs) is False
print("OK")

print("\n=== 2. Categorization matches confirmed real breakdown ===")
matched = filter_records_by_gen5_substring(ccs20, "usb", gen5_slug)
assert len(matched) == 17
cat, fallback_counts = categorize_usb_records(matched, gen5_slug, pinmux_setting_slug, parsed.columns, project_slugs)
assert fallback_counts == {"instance_fallback": 0, "ball_name_fallback": 0}
assert len(cat["differential"]) == 8
assert len(cat["oc_gpio"]) == 2
assert len(cat["vbus_gpio"]) == 1
# needs_review now holds only genuinely ambiguous/unusable pins (shared, boot-strap, no signal);
# single-purpose pins whose ROLE we do not recognize (e.g. hub reset, PHY port select) moved to
# the new "other_gpio" bucket - they are not ambiguous, we just do not know their consumer class.
assert len(cat["needs_review"]) == 4
assert len(cat["other_gpio"]) == 2
assert [len(e["shared"]) for e in cat["needs_review"] if e.get("shared")] == [2], "GPIO_92 (hub I2C enable + USB power enable) must carry its two nets as `shared`"
print("OK")

print("\n=== 3. Instance token detection matches confirmed real signals ===")
assert detect_usb_instance_token("CPU_USB30_CTRL_1V8:USB_OC_B") == "USB30"
assert detect_usb_instance_token("CPU_USB20_CTRL_1V8:USB_OC_B") == "USB20"
assert detect_usb_instance_token("CPU_USB30_CTRL_1V8:USB_OC_B") == "USB30"
assert detect_usb_instance_token("CPU_USB20_CTRL_1V8:USB_OC_B") == "USB20"
labels = resolve_instance_labels(["USB20", "USB30"])
assert labels["USB30"] == ("usb0", True)
assert labels["USB20"] == ("usb1", False), \
    "per direction: group ALL detected instances under sequential usb0/usb1/... - not a non-numeric placeholder"
# Generalizes to other counts: a workbook with only one instance still gets usb0
single = resolve_instance_labels(["USB20"])
assert single["USB20"] == ("usb0", False)
# A third instance gets the next number in deterministic (sorted) order
triple = resolve_instance_labels(["USB10", "USB20", "USB30"])
assert triple == {"USB30": ("usb0", True), "USB10": ("usb1", False), "USB20": ("usb2", False)}
print("OK: USB30 -> usb0 (confirmed), USB20 -> usb1 (sequential, per direction); "
      "1-instance workbook -> usb0; 3-instance workbook -> usb0/usb1/usb2 in sorted order")

print("\n=== 4. Multi-instance generation: TWO separate, fully-wired nodes ===")
result = generate_usb_consumer_dts_multi_instance(
    "CCS20", SOC, matched, gen5_slug, pinmux_setting_slug, parsed.columns, project_slugs,
)
assert len(result["instances"]) == 2, f"expected 2 detected instances, got {len(result['instances'])}"
by_token = {inst["instance_token"]: inst for inst in result["instances"]}
assert set(by_token.keys()) == {"USB30", "USB20"}

usb30 = by_token["USB30"]
assert usb30["consumer_node_label"] == "usb0"
assert usb30["is_confirmed_instance"] is True
usb30_text = usb30["consumer_node_text"]
assert "oc-gpio = <&tlmm 81 GPIO_ACTIVE_LOW>;" in usb30_text
assert "vbus-out-gpio = <&tlmm 82 GPIO_ACTIVE_HIGH>;" in usb30_text
assert "pinctrl-0 = <&usb0_oc_active &usb0_vbus_active>;" in usb30_text
print("OK: USB30 -> &usb0, BOTH oc-gpio (81) and vbus-out-gpio (82) correctly wired")

usb20 = by_token["USB20"]
assert usb20["consumer_node_label"] == "usb1", \
    "per direction: second detected instance groups under usb1, not a non-numeric placeholder"
assert usb20["is_confirmed_instance"] is False
usb20_text = usb20["consumer_node_text"]
assert "oc-gpio = <&tlmm 102 GPIO_ACTIVE_LOW>;" in usb20_text, \
    "USB20's own oc-gpio candidate (GPIO_102) must be wired to ITS OWN node, not lost inside usb0's"
assert "vbus-out-gpio =" not in usb20_text, \
    "USB20 has no safe vbus candidate (GPIO_92 excluded as multi-purpose) - must not fabricate one"
print(f"OK: USB20 -> &{usb20['consumer_node_label']}, its own oc-gpio (102) correctly wired to "
      f"ITS OWN node (previously this pin was generated as a pinctrl state but never wired to "
      f"any oc-gpio property at all)")

# Confirm no cross-contamination: usb0's pinctrl-0 must not reference usb1's states, and vice versa
assert "usb1_oc_active" not in usb30_text
assert "usb0_oc_active" not in usb20_text
print("OK: no cross-contamination between the two instances' pinctrl-0 references")

print("\n=== 5. Both instances' consumer nodes have zero mux/config content ===")
for inst in result["instances"]:
    assert "mux {" not in inst["consumer_node_text"]
    assert "config {" not in inst["consumer_node_text"]
    assert inst["consumer_node_text"].count("{") == inst["consumer_node_text"].count("}")
    assert "mux {" in inst["pinmux_dtsi_text"]
    assert inst["pinmux_dtsi_text"].count("{") == inst["pinmux_dtsi_text"].count("}")
print("OK: consumer nodes never repeat pinctrl mux/config inline; pinmux_dtsi_text has it, braces balanced")

print("\n=== 6. Confirmed real shape properties present on the confirmed instance ===")
assert "debounce-delay-ms = <20>;" in usb30_text
assert 'pinctrl-names = "default";' in usb30_text
assert "qcom,default-mode-host;" in usb30_text
assert "dwc3@a600000 {" in usb30_text
assert 'usb-phy = <&usb2_phy0>, <&usb_nop_phy>;' in usb30_text
assert 'maximum-speed = "high-speed";' in usb30_text
print("OK")

print("\n=== 7. Differential/PHY pads still excluded, needs_review still global ===")
assert len(result["excluded_non_tlmm"]) == 8
assert len(result["needs_review"]) == 6
for pin_name in ["dp0ln3m", "dp0ln3p", "usbahsdm", "usb2hsdm"]:
    assert f'"{pin_name}"' not in usb30_text and f'"{pin_name}"' not in usb20_text
print("OK")

print("\n=== 8. detect_existing_usb_node still correctly distinguishes all three shapes ===")
assert detect_existing_usb_node("")["kind"] == "none"
assert detect_existing_usb_node('&usb0 { oc-gpio = <&tlmm 81 GPIO_ACTIVE_LOW>; };')["kind"] == "reference"
monaco_path = "/mnt/user-data/uploads/monaco-vm-usb.dtsi"
if os.path.exists(monaco_path):
    assert detect_existing_usb_node(open(monaco_path).read())["kind"] == "full_definition"
    print("OK: including real monaco-vm-usb.dtsi -> full_definition")
else:
    print("OK (monaco-vm-usb.dtsi not present in this environment, skipped that part)")

print("\nUSB CONSUMER NODE TEST PASSED")

# ------------------------------------------------------------------ 9
print("\n=== 9. RegSetting unavailable for a whole project: GPIO-like pins are flagged, never filed as PHY, never emitted as TLMM ===")
# Regression for a real, confirmed case: a different SoC vendor's workbook (TCC8050) where EVERY RegSettingN
# cell is empty for EVERY row, with alphanumeric BGA-style ball names ("GPIO_A15"). Nothing in such a
# workbook says the pins are Qualcomm TLMM, so they must not be emitted into &tlmm - but filing real
# OC/VBUS/hub-reset GPIOs as "PHY pads" silently hid them, which was the original bug.
from app.models import NormalizedRecord, ColumnMeta
from app.usb_consumer_generator import _ball_has_gpio_capable_instance

def rec(ball, gen5, **extra):
    fields = {"ball_name": ball, "gen5_signal_name": gen5, "pinmux_setting": 0}
    fields.update(extra)
    return NormalizedRecord(project="A0_sample", soc="TCC8050", row_index=0, fields=fields)

synth = [
    rec("GPIO_A15", "CPU_USB20_0_CTRL_3V3:USB_OC_B", instance0="GPIO_A"),
    rec("GPIO_A11", "CPU_USB20_0_CTRL_3V3:VBUS_PWR_EN_B", instance3="GPIO_A"),
    rec("USB20DH_DM", "CPU_USB20_0_CTRL_3V3:USB_DM"),                  # genuine PHY pad: no GPIO instance
    rec("GPIO_ZZ99", "CPU_USB20_0_CTRL_3V3:SOME_SIGNAL"),              # starts with gpio, no instance data
]
cat9, fb9 = categorize_usb_records(synth, "gen5_signal_name", "pinmux_setting", [], set())
assert fb9 == {"instance_fallback": 2, "ball_name_fallback": 2}
assert len(cat9["unconfirmed_tlmm"]) == 3, "all three GPIO-like pins must be flagged, not dropped"
assert len(cat9["differential"]) == 1, "the genuine PHY pad must still be excluded"
assert not cat9["oc_gpio"] and not cat9["vbus_gpio"] and not cat9["other_gpio"], \
    "nothing may be treated as TLMM when no data identifies the pin controller"
assert _ball_has_gpio_capable_instance(synth[0]) and not _ball_has_gpio_capable_instance(synth[2])
# a plain numeric ball name keeps the explicitly reported fallback (Qualcomm-style GPIO_N)
cat9b, fb9b = categorize_usb_records(
    [rec("GPIO_81", "CPU_USB30_CTRL_1V8:USB_OC_B")], "gen5_signal_name", "pinmux_setting", [], set())
assert len(cat9b["oc_gpio"]) == 1 and fb9b["ball_name_fallback"] == 1
print("OK")

# ------------------------------------------------------------------ 10
print("=== 10. resolve_regsetting_slug: a legitimate setting of 0 is not treated as \"no value\" ===")
from app.usb_consumer_generator import resolve_regsetting_slug
cols10 = [ColumnMeta(index=0, excel_letter="A", field_label="RegSetting0 (PinMux00)", entity="PinMux00",
                     base_field="RegSetting0", slug="regsetting0", category="pinmux_register", known=True)]
assert resolve_regsetting_slug(0, cols10, {"regsetting0"}) == "regsetting0"
assert resolve_regsetting_slug(None, cols10, {"regsetting0"}) is None
assert resolve_regsetting_slug("", cols10, {"regsetting0"}) is None
print("OK")

# ------------------------------------------------------------------ 11
print("=== 11. An always-empty RegSetting field is uninformative; once the project has real data, empty means PHY again ===")
cols11 = cols10
def row(i, reg):
    return NormalizedRecord(project="A0_sample", soc="TCC8050", row_index=i,
                            fields={"ball_name": f"GPIO_A{i}", "gen5_signal_name": "CPU_USB20_0_CTRL_3V3:USB_OC_B",
                                    "pinmux_setting": 0, "regsetting0": reg, "instance0": "GPIO_A"})
all_empty = [row(i, None) for i in range(3)]
cat11, _ = categorize_usb_records(all_empty, "gen5_signal_name", "pinmux_setting", cols11, {"regsetting0"})
assert len(cat11["differential"]) == 0 and len(cat11["unconfirmed_tlmm"]) == 3
mixed = all_empty + [row(99, "TLMM_GPIO_CFG99(5:2)=0")]
cat11b, _ = categorize_usb_records(mixed, "gen5_signal_name", "pinmux_setting", cols11, {"regsetting0"})
assert len(cat11b["differential"]) == 3, "real RegSetting data exists in this project, so an empty value is informative again"
assert len(cat11b["oc_gpio"]) == 1, "the row with a TLMM_GPIO_CFG value is a confirmed TLMM pin"

# a non-TLMM controller register (SAIL_CFG) is an unsupported controller, never TLMM, never PHY
sail = [row(0, "SAIL_CFG24(5:2)=0")]
cat11c, _ = categorize_usb_records(sail, "gen5_signal_name", "pinmux_setting", cols11, {"regsetting0"})
assert len(cat11c["unsupported_controller"]) == 1 and not cat11c["oc_gpio"] and not cat11c["differential"]
print("OK")

# ------------------------------------------------------------------ 12
print("=== 12. A pin that matched 'usb' only through a boot-strap segment is reviewed, not generated ===")
wlan = NormalizedRecord(project="P", soc="S", row_index=1, fields={
    "ball_name": "GPIO_101", "pinmux_setting": 3, "interface_group": "BTWLAN, SA6150_CTRL",
    "gen5_signal_name": "CPU_WLAN_PCIE_CTRL_1V8:NRESET_B,\nSCC_CPU_BOOT_1V8:CPU_FORCE_USB,\nSA6150_BOOTCFG_1V8:FORCE_USB"})
cat12, _ = categorize_usb_records([wlan], "gen5_signal_name", "pinmux_setting", [], set())
assert len(cat12["needs_review"]) == 1 and not cat12["other_gpio"]
assert "boot-strap" in cat12["needs_review"][0]["reason"]
print("OK")

print("=== 13. role_map: the built-in keywords can be overridden per project; polarity still follows the _B/_N suffix ===")
def rec13(ball, sig):
    return NormalizedRecord(project="P", soc="S", row_index=0, fields={
        "ball_name": ball, "gen5_signal_name": sig, "pinmux_setting": 1, "regsetting1": "TLMM_GPIO_CFG70(5:2)=0", "interface_group": "USB"})
cols13 = [ColumnMeta(index=0, excel_letter="A", field_label="RegSetting1 (PinMux01)", entity="PinMux01", base_field="RegSetting1",
                     slug="regsetting1", category="pinmux_register", known=True)]
recs13 = [rec13("GPIO_70", "CPU_USB31_EDL_CTRL_1V8:OVERLOAD_STATUS_B"), rec13("GPIO_75", "CPU_USB31_EDL_CTRL_1V8:SUPPLY_ENABLE_X")]
no_map, _ = categorize_usb_records(recs13, "gen5_signal_name", "pinmux_setting", cols13, {"regsetting1"})
assert len(no_map["other_gpio"]) == 2 and not no_map["oc_gpio"] and not no_map["vbus_gpio"], "unknown naming: kept as other GPIOs, never dropped"
with_map, _ = categorize_usb_records(recs13, "gen5_signal_name", "pinmux_setting", cols13, {"regsetting1"},
                                     role_map={"OVERLOAD_STATUS_B": "oc", "SUPPLY_ENABLE_X": "vbus"})
assert [e["polarity"] for e in with_map["oc_gpio"]] == ["GPIO_ACTIVE_LOW"] and [e["polarity"] for e in with_map["vbus_gpio"]] == ["GPIO_ACTIVE_HIGH"]
assert with_map["oc_gpio"][0]["instance_token"] == "USB31" and not with_map["other_gpio"]
# the vocabulary (app/usb_role_keywords.json) is data: names it knows are classified without any role_map
from app.usb_consumer_generator import signal_role_from_name as _role
for name, want in (("USB_OC_B", "oc"), ("USB_FAULT_B", "oc"), ("OVER_CURRENT", "oc"), ("VBUS_PWR_EN_B", "vbus"), ("USB_PWR_EN", "vbus"),
                   ("5V0S_USB_EXTERNAL_EN", "vbus"), ("DEVICE_DET_B", None), ("USB_OTG_EN", None), ("HUB_RST", None), ("DOCK_OCCUPIED", None)):
    assert _role(name) == want, (name, _role(name), want)
import json as _json, tempfile as _tf
_vf = os.path.join(_tf.mkdtemp(), "v.json"); _json.dump({"oc": ["OVERLOAD"], "vbus": ["SUPPLY_ENABLE"]}, open(_vf, "w"))
os.environ["USB_ROLE_KEYWORDS"] = _vf
try:
    assert _role("OVERLOAD_STATUS_B") == "oc" and _role("SUPPLY_ENABLE_X") == "vbus" and _role("USB_FAULT_B") is None, "editing the file changes the behaviour"
    by_file, _ = categorize_usb_records(recs13, "gen5_signal_name", "pinmux_setting", cols13, {"regsetting1"})
    assert len(by_file["oc_gpio"]) == 1 and len(by_file["vbus_gpio"]) == 1 and not by_file["other_gpio"], "no code change, no role_map: the vocabulary file did it"
finally:
    del os.environ["USB_ROLE_KEYWORDS"]
assert _role("USB_FAULT_B") == "oc", "back to the shipped vocabulary"
os.environ["USB_ROLE_KEYWORDS"] = "/nonexistent/vocab.json"
try:
    assert _role("USB_OC_B") == "oc" and _role("VBUS_PWR_EN_B") == "vbus", "a missing file falls back to the minimal built-in words, never to nothing"
finally:
    del os.environ["USB_ROLE_KEYWORDS"]
print("OK")

print("\nUSB CONSUMER NODE TEST PASSED (extended)")
