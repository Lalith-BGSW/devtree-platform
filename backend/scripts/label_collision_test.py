"""
Reproduces two real bugs found by reviewing actual "remainder DTS" output
against the real workbook:

  1. Label collision: two DIFFERENT Excel "Selected Interface" strings
     that slugify to the SAME node label (e.g. "PCIE_C (SA8195)" vs
     "PCIE_C(SA8195)" - confirmed real values, differing only by a space)
     must never produce two top-level DTS nodes sharing one label -
     that's invalid/conflicting devicetree source, not a cosmetic issue.
  2. Non-signal filtering gaps: a literal numeric 0 Selected Interface
     (an unconfigured-pin placeholder, confirmed on 48 real rows),
     voltage-regulator pin names (VREG*/VCOIN/LNBBCLK*), and "Unused X"
     Interface Group values (e.g. "Unused PCIe") must all be excluded
     from DTS generation, same as DNC/NC/AGND already were.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.models import NormalizedRecord
from app.constants import is_non_signal_value
from app.generators.dts_generator import generate_dts

print("=== Non-signal value detection (extended set) ===")
new_cases = {
    "0": True, "0.0": True,
    "VREG_S4A_1P8": True, "VCOIN": True, "LNBBCLK3": True,
    "Unused PCIe": True, "unused": True,
    # sanity: real signal values must NOT be caught
    "HSUART_QUP13": False, "PCIE_C (SA8195)": False, "GPIO43": False,
}
for value, expected in new_cases.items():
    got = is_non_signal_value(value)
    status = "OK" if got == expected else "FAIL"
    print(f"  [{status}] is_non_signal_value({value!r}) = {got} (expected {expected})")
    assert got == expected, f"is_non_signal_value({value!r}) should be {expected}, got {got}"

print("\n=== Label collision: two real interface strings, same slug ===")
records = [
    NormalizedRecord(project="CCS20", soc="SA8155P", row_index=0, fields={
        "sa8155sip_ball_name": "PCIE_C_CLK_REQ", "sa8155sip_ball_id": "AB1",
        "selected_interface": "PCIE_C (SA8195)",  # note the space
        "interface_group": "Unused PCIe",  # would also be filtered once
                                            # the fix above is in place, so
                                            # use a different group here to
                                            # keep this record a real signal
                                            # for this specific test
    }),
]
# Override interface_group to something that ISN'T filtered, so this test
# is specifically about the label-collision fix, not the filtering fix.
records[0].fields["interface_group"] = "PCIe"
records.append(NormalizedRecord(project="CCS20", soc="SA8155P", row_index=1, fields={
    "sa8155sip_ball_name": "PCIE_C_RST", "sa8155sip_ball_id": "AB2",
    "selected_interface": "PCIE_C(SA8195)",  # no space - different string,
    "interface_group": "PCIe",               # same slug as above
}))

dts, _amb, _review = generate_dts("CCS20", "SA8155P", records)
print(dts)

# Both real pins must be present (identifier derived from ball_name, the
# field that actually holds descriptive names on the real workbook)...
assert "pieclkreq" in dts.lower() or "pciecclkreq" in dts.lower()
assert "pciecrst" in dts.lower()
# ...but as two DISTINCT node labels, never duplicated.
import re
labels = re.findall(r'^\t(\S+): \1 \{', dts, re.MULTILINE)
print(f"\nnode labels found: {labels}")
assert len(labels) == len(set(labels)), f"duplicate label(s) in output: {labels}"
assert len(labels) == 2, f"expected 2 distinct nodes for 2 distinct interface strings, got {len(labels)}"

print("\n=== Confirm filtering actually removes these from generated DTS ===")
filtered_records = [
    NormalizedRecord(project="CCS20", soc="SA8155P", row_index=2, fields={
        "sa8155sip_ball_name": "0", "sa8155sip_ball_id": "CC1", "selected_interface": "0",
    }),
    NormalizedRecord(project="CCS20", soc="SA8155P", row_index=3, fields={
        "sa8155sip_ball_name": "VCOIN", "sa8155sip_ball_id": "CC2", "selected_interface": "0",
    }),
    NormalizedRecord(project="CCS20", soc="SA8155P", row_index=4, fields={
        "sa8155sip_ball_name": "PCIE_C_CLK_REQ", "sa8155sip_ball_id": "CC3",
        "selected_interface": "PCIE_C (SA8195)", "interface_group": "Unused PCIe",
    }),
    # one real signal pin that must still come through
    NormalizedRecord(project="CCS20", soc="SA8155P", row_index=5, fields={
        "sa8155sip_ball_name": "GPIO_43", "sa8155sip_ball_id": "DD1",
        "pinmuxed_name": "GPIO43",
        "selected_interface": "HSUART_QUP13", "interface_group": "Bluetooth",
    }),
]
dts2, _amb2, _review2 = generate_dts("CCS20", "SA8155P", filtered_records)
print(dts2)
assert 'function = "0"' not in dts2, "bare numeric-0 interface leaked through as a function name"
assert "vcoin" not in dts2.lower()
assert "pciecclkreq" not in dts2.lower(), "Unused-PCIe-group pin should be filtered"
assert "gpio43" in dts2, "the one real signal pin must still be generated"

print("\nLABEL-COLLISION AND FILTERING TEST PASSED")
