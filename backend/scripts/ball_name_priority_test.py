"""
Reproduces a real bug found by auditing a "remainder" DTS output that
contained a node with pins = "dnc" - the literal non-signal marker
appearing as an actual pin identifier. Root cause: _first_pin_identifier
scanned ball_name-containing fields in raw dict/column order and returned
the first non-empty one, without checking if that value was itself a
marker. The real workbook has 5 ball-name variant columns (one per chip
package revision - SA8155SIP/SA8195SIP/SA8155SoC/SiPV1/PRE-SiPV2) that
can genuinely disagree about a given physical row: confirmed real case
where sa8155sip_ball_name='DNC' but sa8155soc_ball_name='REFGEN_REXT0'
for the SAME row, a real signal pin correctly classified as "signal" by
classify_pin() (which checks PinMuxed Name, not ball_name). The renderer
then picked the wrong (DNC) ball-name variant instead of the real one.

Now fixed in both dts_generator.py (used for DTS rendering) and
validators/rules.py (used for validation display / duplicate-pin
checks) to skip marker values when scanning ball_name/ball_id variants.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.generators.dts_generator import _first_pin_identifier as gen_first_pin_id
from app.validators.rules import _first_pin_identifier as val_first_pin_id
from app.models import NormalizedRecord
from app.generators.dts_generator import generate_dts

# The exact real fields from the real workbook row that exposed this (row
# 259 of the real file) - reproduced here as literal data, not re-read
# from the real workbook.
REAL_CASE_FIELDS = {
    "id": 260,
    "sa8155sip_ball_name": "DNC",
    "sa8155soc_ball_name": "REFGEN_REXT0",
    "sa8155soc_ball_id": "AG3",
    "pre_sipv2_ball_name": "DNC",
    "pre_sipv2_ball_id": "U20",
    "pinmux_exists": "NO",
    "interfaces": "CPU,,,,,,,,,,,,,",
    "pinmuxed_name": "REFGEN_REXT0",
    "selected_interface": "CPU",
}

print("=== dts_generator.py's _first_pin_identifier (DTS rendering) ===")
result = gen_first_pin_id(REAL_CASE_FIELDS)
print(f"result: {result!r}")
assert result == "REFGEN_REXT0", f"BUG: got {result!r}, a real signal pin must never render as its marker ball-name variant"

print("\n=== validators/rules.py's _first_pin_identifier (validation display) ===")
result = val_first_pin_id(REAL_CASE_FIELDS)
print(f"result: {result!r}")
assert result == "REFGEN_REXT0"

print("\n=== Sanity: a genuinely all-DNC row still returns SOMETHING, not silently None ===")
all_dnc_fields = {
    "sa8155sip_ball_name": "DNC", "sa8195sip_ball_name": "DNC",
    "sa8155soc_ball_name": "DNC", "pre_sipv2_ball_name": "DNC",
}
result = gen_first_pin_id(all_dnc_fields)
print(f"result: {result!r}")
assert result == "DNC", "when EVERY variant is a marker, returning the marker is correct (nothing better exists)"

print("\n=== End-to-end: 'dnc' must never appear as a pins=\"...\" value in generated DTS ===")
records = [
    NormalizedRecord(project="CCS20", soc="SA8155P", row_index=0, fields=REAL_CASE_FIELDS),
    # a normal real pin too, to confirm nothing else broke
    NormalizedRecord(project="CCS20", soc="SA8155P", row_index=1, fields={
        "sa8155sip_ball_name": "GPIO_43", "pinmuxed_name": "GPIO43",
        "selected_interface": "HSUART_QUP13", "direction": "0: OUT",
    }),
]
dts_text, ambiguous, _review = generate_dts("CCS20", "SA8155P", records)
print(dts_text)
assert 'pins = "dnc"' not in dts_text.lower()
assert "refgenrext0" in dts_text.lower()
assert "gpio43" in dts_text.lower()

print("\nBALL-NAME-VARIANT-PRIORITY TEST PASSED")
