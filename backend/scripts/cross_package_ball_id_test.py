"""
Reproduces a real bug found by reviewing actual validation output from a
live run of the app: duplicate_pin_assignment falsely flagged pairs like
"Ball 'E35' is assigned on more than one row" - but the two rows were on
COMPLETELY DIFFERENT chip package variants (SA8155SIP vs SA8155SoC),
whose ball-grid coordinate systems are independent. Two different
physical balls, on two different packages, coincidentally sharing a
coordinate label ("E35") were treated as the same ball.

Confirmed real case (from the actual workbook):
  row 133: SA8155SIP Ball ID = 'E35' (WUP_GPIO_132, a real GPIO pin)
  row 271: SA8155SoC Ball ID = 'E35' (SP_ARI_POWER_ALARM, unrelated)

Fixed by qualifying the uniqueness key with which package/column it came
from, so cross-package coincidences can never collide - while genuine
duplicates WITHIN the same package/column are still correctly caught.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.models import NormalizedRecord
from app.validators.rules import run_validation

print("=== Real case: same coordinate label, different packages - must NOT be flagged ===")
records = [
    NormalizedRecord(project="CCS20", soc="SA8155P", row_index=133, fields={
        "sa8155sip_ball_id": "E35", "pinmuxed_name": "WUP_GPIO_132",
        "selected_interface": "WUP_GPIO,PDC",
    }),
    NormalizedRecord(project="CCS20", soc="SA8155P", row_index=271, fields={
        "sa8155soc_ball_id": "E35", "pinmuxed_name": "SP_ARI_POWER_ALARM",
        "selected_interface": "GPIO",
    }),
]
issues = run_validation(records)
dup_issues = [i for i in issues if i.rule == "duplicate_pin_assignment"]
print(f"duplicate_pin_assignment issues: {len(dup_issues)}")
for i in dup_issues:
    print(f"  {i.message}")
assert len(dup_issues) == 0, \
    f"BUG: cross-package coordinate coincidence falsely flagged as duplicate: {dup_issues}"
print("OK: no false positive")

print("\n=== Genuine duplicate WITHIN the same package - must still be caught ===")
records2 = [
    NormalizedRecord(project="CCS20", soc="SA8155P", row_index=0, fields={
        "sa8155sip_ball_id": "AF12", "pinmuxed_name": "GPIO43",
        "selected_interface": "HSUART_QUP13",
    }),
    NormalizedRecord(project="CCS20", soc="SA8155P", row_index=1, fields={
        "sa8155sip_ball_id": "AF12",  # same package, same coordinate - real duplicate
        "pinmuxed_name": "SOME_OTHER_SIGNAL", "selected_interface": "I2C_QUP5",
    }),
]
issues2 = run_validation(records2)
dup_issues2 = [i for i in issues2 if i.rule == "duplicate_pin_assignment"]
print(f"duplicate_pin_assignment issues: {len(dup_issues2)}")
for i in dup_issues2:
    print(f"  {i.message}")
assert len(dup_issues2) == 1, "a genuine same-package duplicate must still be caught"

print("\n=== All three real confirmed cases from the live app output ===")
real_cases = [
    ({"sa8155sip_ball_id": "B28", "pinmuxed_name": "QLINK_CLK_P", "selected_interface": "QLINK"},
     {"sa8155soc_ball_id": "B28", "pinmuxed_name": "EBI2_CA_CA_5", "selected_interface": "LPDDR4"}),
    ({"sa8155sip_ball_id": "B32", "pinmuxed_name": "QLINK_UDL2_M", "selected_interface": "QLINK"},
     {"sa8155soc_ball_id": "B32", "pinmuxed_name": "EBI2_DQ_10", "selected_interface": "LPDDR4"}),
    ({"sa8155sip_ball_id": "A31", "pinmuxed_name": "QLINK_UDL1_M", "selected_interface": "QLINK"},
     {"sa8155soc_ball_id": "A31", "pinmuxed_name": "EBI2_DQ_11", "selected_interface": "LPDDR4"}),
]
for i, (f1, f2) in enumerate(real_cases):
    recs = [
        NormalizedRecord(project="CCS20", soc="SA8155P", row_index=i * 2, fields=f1),
        NormalizedRecord(project="CCS20", soc="SA8155P", row_index=i * 2 + 1, fields=f2),
    ]
    issues = run_validation(recs)
    dups = [x for x in issues if x.rule == "duplicate_pin_assignment"]
    assert len(dups) == 0, f"case {i}: false positive still present: {dups}"
print(f"OK: all {len(real_cases)} real confirmed false-positive cases now resolved")

print("\nCROSS-PACKAGE BALL ID TEST PASSED")
