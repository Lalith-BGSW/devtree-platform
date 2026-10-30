"""
Tests the general classify_pin() mechanism, which replaced ad-hoc
exclude-word guessing with a real three-way decision:
  - "non_signal": matches a confirmed marker
  - "signal": has real data, REGARDLESS of whether the specific interface
    type is recognized - an unfamiliar real value is still real data
  - "ambiguous": no usable data in EITHER field - flagged for review,
    never silently resolved either way

Specifically verifies the three real-workbook values initially flagged as
"ambiguous" (MUST_BE_OPEN, SA8155SIP_HW, CPU_BOOT) resolve correctly
WITHOUT any special-casing - because Selected Interface, not Interface
Group, is what actually decides classification. This is the general
mechanism working as designed, not a hardcoded fix for these 3 values.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.constants import classify_pin
from app.models import NormalizedRecord
from app.generators.dts_generator import generate_dts

print("=== Core classify_pin() behavior ===")
cases = [
    # (interface_value, pin_name, interface_group, expected)
    ("DNC", "GPIO43", None, "non_signal"),                    # confirmed marker wins even with a real-looking pin name
    ("HSUART_QUP13", "GPIO43", "Bluetooth", "signal"),          # normal real case
    ("SomeInterfaceTypeNeverSeenBefore", "GPIO99", None, "signal"),  # unrecognized but real - must still be "signal"
    (None, None, None, "ambiguous"),                            # nothing to go on
    ("", "", None, "ambiguous"),                                # empty strings, same as None
    (None, "GPIO50", None, "signal"),                           # pin_name alone is enough
    ("PCIE_C (SA8195)", "PCIE_C_CLK_REQ", "Unused PCIe", "non_signal"),  # real workbook case: Interface Group's "Unused" marker overrides an otherwise-real-looking Selected Interface
]
for iface, pin, group, expected in cases:
    got = classify_pin(iface, pin, group)
    status = "OK" if got == expected else "FAIL"
    print(f"  [{status}] classify_pin({iface!r}, {pin!r}, {group!r}) = {got!r} (expected {expected!r})")
    assert got == expected

print("\n=== The 3 real values initially flagged as 'ambiguous' - resolved with NO special-casing ===")
# Reproduces the exact real rows found via full-file extraction.
real_cases = [
    # MUST_BE_OPEN group, but Selected Interface is real -> must be "signal"
    ("DP", "DP_HOT_PLUG_DETECT", "MUST_BE_OPEN", "signal"),
    # MUST_BE_OPEN group, Selected Interface is "0" -> must be "non_signal"
    ("0", "0", "MUST_BE_OPEN", "non_signal"),
    # MUST_BE_OPEN group, Selected Interface is DNC -> must be "non_signal"
    ("DNC", "DNC", "MUST_BE_OPEN", "non_signal"),
    # SA8155SIP_HW group, always paired with real GPIO/WUP_GPIO signals -> "signal"
    ("WUP_GPIO,PDC", "WUP_GPIO_60", "SA8155SIP_HW", "signal"),
    ("GPIO", "GPIO_67", "SA8155SIP_HW", "signal"),
    # CPU_BOOT group, always real GPIO signals -> "signal"
    ("GPIO", "GPIO_74", "CPU_BOOT", "signal"),
    ("WUP_GPIO,PDC", "WUP_GPIO_68", "CPU_BOOT, SA8155SIP_HW", "signal"),
]
for iface, pin, group, expected in real_cases:
    got = classify_pin(iface, pin, group)
    status = "OK" if got == expected else "FAIL"
    print(f"  [{status}] group={group!r} selected_if={iface!r} -> {got!r} (expected {expected!r})")
    assert got == expected, f"REGRESSION: {group!r}/{iface!r} should be {expected!r}, got {got!r}"

print("\n=== End-to-end: ambiguous records are surfaced, not silently dropped ===")
records = [
    NormalizedRecord(project="CCS20", soc="SA8155P", row_index=0, fields={
        "sa8155sip_ball_name": "GPIO_43", "pinmuxed_name": "GPIO43",
        "selected_interface": "HSUART_QUP13", "interface_group": "Bluetooth",
    }),
    # Genuinely ambiguous: no selected_interface, no pinmuxed_name at all
    NormalizedRecord(project="CCS20", soc="SA8155P", row_index=1, fields={
        "sa8155sip_ball_name": "GPIO_999",
    }),
]
dts_text, ambiguous, _review = generate_dts("CCS20", "SA8155P", records)
print(dts_text)
assert len(ambiguous) == 1, f"expected 1 ambiguous record, got {len(ambiguous)}"
assert ambiguous[0].row_index == 1
assert "gpio43" in dts_text, "the real signal pin must still be generated"
assert "999" not in dts_text, "the ambiguous record must NOT appear in generated DTS"
assert "could not be classified" in dts_text, "ambiguous count must be visible in the header comment"

print("\nAMBIGUITY CLASSIFICATION TEST PASSED")
