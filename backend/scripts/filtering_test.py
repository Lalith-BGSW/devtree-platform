"""
Reproduces the real workbook's noise pattern discovered from header+
value-distribution analysis (with explicit permission, aggregate stats
only - no real row data used here): many balls share "AGND"/"DNC"/"NC" as
their PinMuxed Name / Selected Interface by design, which should NOT be
flagged as duplicate-pin errors, and should NOT appear as pinctrl nodes in
generated DTS. Also confirms a genuine duplicate (same ball_id used twice)
STILL gets caught - the fix must not throw out real error detection along
with the false positives.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.models import NormalizedRecord
from app.validators.rules import run_validation
from app.generators.dts_generator import generate_dts

records = [
    # Real signal pin - UART TX
    NormalizedRecord(project="CCS20", soc="SA8155P", row_index=0, fields={
        "sa8155sip_ball_id": "AF12", "sa8155sip_ball_name": "GPIO_43",
        "pinmuxed_name": "GPIO43", "selected_interface": "HSUART_QUP13",
        "gen5_signal_name": "CPU_BT_UART_TX", "direction": "out",
    }),
    NormalizedRecord(project="CCS20", soc="SA8155P", row_index=1, fields={
        "sa8155sip_ball_id": "AF13", "sa8155sip_ball_name": "GPIO_44",
        "pinmuxed_name": "GPIO44", "selected_interface": "HSUART_QUP13",
        "gen5_signal_name": "CPU_BT_UART_RX", "direction": "in",
    }),
    # 5 different physical grounds, all sharing "AGND" as PinMuxed Name -
    # matches the real workbook's pattern (460 such rows there).
    *[
        NormalizedRecord(project="CCS20", soc="SA8155P", row_index=10 + i, fields={
            "sa8155sip_ball_id": f"BALL{i}", "sa8155sip_ball_name": "AGND",
            "pinmuxed_name": "AGND", "selected_interface": "Power",
        })
        for i in range(5)
    ],
    # 3 no-connects, all sharing "DNC"
    *[
        NormalizedRecord(project="CCS20", soc="SA8155P", row_index=20 + i, fields={
            "sa8155sip_ball_id": f"NC{i}", "sa8155sip_ball_name": "NC",
            "pinmuxed_name": "DNC", "selected_interface": "DNC",
        })
        for i in range(3)
    ],
    # A GENUINE duplicate - same ball_id "AF12" reused by mistake on a
    # different row. Must still be caught.
    NormalizedRecord(project="CCS20", soc="SA8155P", row_index=99, fields={
        "sa8155sip_ball_id": "AF12", "sa8155sip_ball_name": "GPIO_43",
        "pinmuxed_name": "GPIO43_DUP", "selected_interface": "I2C_QUP5",
        "gen5_signal_name": "SOME_OTHER_SIGNAL",
    }),
]

print("=== Validation ===")
issues = run_validation(records)
for i in issues:
    print(f"  [{i.severity}] {i.rule}: {i.message}")

dup_issues = [i for i in issues if i.rule == "duplicate_pin_assignment"]
assert len(dup_issues) == 1, f"expected exactly 1 real duplicate flagged, got {len(dup_issues)}"
assert "AF12" in dup_issues[0].message, "the flagged duplicate should be the real AF12 collision"
print(f"\nOK: exactly 1 duplicate flagged (the real AF12 collision), "
      f"AGND x5 and DNC x3 correctly NOT flagged as duplicates.")

print("\n=== DTS generation (full project) ===")
dts, _amb, _review = generate_dts("CCS20", "SA8155P", records)
print(dts)
assert "AGND" not in dts, "ground pins should not appear in generated DTS"
assert "agnd" not in dts.lower(), "ground pins should not appear in generated DTS (case-insensitive)"
assert "DNC" not in dts, "no-connect pins should not appear in generated DTS"
assert "gpio43" in dts and "gpio44" in dts, "real signal pins must still be present"
assert "skipped_non_signal" not in dts, "template variable name leaking into output"
assert "excluded" in dts, "the excluded-count comment should be present"
print("\nOK: AGND/DNC excluded from DTS, real UART signal pins present, "
      "exclusion count noted in header comment.")

print("\nFILTERING TEST PASSED")
