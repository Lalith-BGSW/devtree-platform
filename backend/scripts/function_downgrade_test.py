"""
Reproduces a real bug found by auditing an actual update run: the CCI
(Camera Control Interface) pins in the real reference file had
function="cci_i2c" (correct, specific), but Excel's Selected Interface
for those rows is just "CCI" (coarser - the "_i2c" sub-mode detail lives
in PinMuxed Name, not Selected Interface). The updater derived
function="cci" from Excel and overwrote the correct, more-specific
existing value with a strictly worse one. Confirmed real regression, now
fixed: function-name updates are skipped (and flagged for review) when
the derived value is a less-specific prefix of what's already there.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.models import NormalizedRecord
from app.generators.dts_generator import build_pin_index
from app.generators.dts_updater import update_dts_file
from app.review_report import build_update_review_report

SAMPLE_DTS = """&tlmm {
\tcci0_active_ccs2: cci0_active_ccs2 {
\t\tmux {
\t\t\tpins = "gpio17", "gpio18";
\t\t\tfunction = "cci_i2c";
\t\t};

\t\tconfig {
\t\t\tpins = "gpio17", "gpio18";
\t\t\tdrive-strength = <2>;
\t\t\tbias-disable;
\t\t};
\t};

\tnormal_upgrade_ccs2: normal_upgrade_ccs2 {
\t\tmux {
\t\t\tpins = "gpio99";
\t\t\tfunction = "gpio";
\t\t};

\t\tconfig {
\t\t\tpins = "gpio99";
\t\t\tdrive-strength = <2>;
\t\t\tbias-disable;
\t\t};
\t};
};
"""

records = [
    # Real case: Excel's Selected Interface is coarser ("CCI") than the
    # existing, correct function name ("cci_i2c").
    NormalizedRecord(project="CCS20", soc="SA8155P", row_index=0, fields={
        "sa8155sip_ball_name": "GPIO_17", "pinmuxed_name": "CCI_I2C_SDA0",
        "selected_interface": "CCI", "io_drv_strength": "4: DRV_10_MA",
        "io_gpio_pull": "3: PULL_UP",
    }),
    NormalizedRecord(project="CCS20", soc="SA8155P", row_index=1, fields={
        "sa8155sip_ball_name": "GPIO_18", "pinmuxed_name": "CCI_I2C_SCL0",
        "selected_interface": "CCI", "io_drv_strength": "4: DRV_10_MA",
        "io_gpio_pull": "3: PULL_UP",
    }),
    # Normal case: a genuine, unrelated function change should still go
    # through fine (not everything gets blocked).
    NormalizedRecord(project="CCS20", soc="SA8155P", row_index=2, fields={
        "sa8155sip_ball_name": "GPIO_99", "pinmuxed_name": "GPIO99",
        "selected_interface": "SPI_QUP5", "io_drv_strength": "4: DRV_10_MA",
        "io_gpio_pull": "0: NO_PULL",
    }),
]

pin_index = build_pin_index(records)
result = update_dts_file(SAMPLE_DTS, pin_index)
by_label = {nr.label: nr for nr in result.node_results}

print("=== CCI case: function must NOT be downgraded ===")
cci = by_label["cci0_active_ccs2"]
print(f"changed={cci.changed} changes={[(c.field, c.old_value, c.new_value) for c in cci.changes]}")
function_changes = [c for c in cci.changes if c.field == "function"]
assert not function_changes, f"BUG: function was changed, should have been protected: {function_changes}"
assert 'function = "cci_i2c"' in result.updated_text, "the correct, specific function name must survive"
assert 'function = "cci";' not in result.updated_text, "must NOT have been downgraded to the less-specific value"
assert "REVIEW:" in result.updated_text and "manual confirmation" in result.updated_text, \
    "the skipped downgrade must be flagged for manual review, not silently ignored"
# drive-strength/bias should still update normally even though function was protected
drv_changes = [c for c in cci.changes if c.field == "drive-strength"]
assert drv_changes, "drive-strength should still update normally"
print("OK: function preserved, flagged for review, other fields still updated normally")

print("\n=== Unrelated node: normal function change still works ===")
normal = by_label["normal_upgrade_ccs2"]
print(f"changed={normal.changed} changes={[(c.field, c.old_value, c.new_value) for c in normal.changes]}")
function_changes = [c for c in normal.changes if c.field == "function"]
assert function_changes, "a genuine, unrelated function change must still go through"
assert 'function = "qup5"' in result.updated_text
print("OK: genuine function changes are not blanket-blocked")

print("\nFUNCTION-DOWNGRADE-PROTECTION TEST PASSED")

print("\n=== Generic interface label is not treated as a confirmed mux function ===")
dp_record = NormalizedRecord(project="CCS20", soc="SA8155P", row_index=3, fields={
    "sa8155sip_ball_name": "GPIO_10",
    "pinmuxed_name": "DP_HOT_PLUG_DETECT",
    "selected_interface": "DP",
    "pinmux_setting": 4,
    "direction": "0: IN",
    "io_drv_strength": "4: DRV_10_MA",
    "io_gpio_pull": "1: PULL_DOWN",
})
dp_dts = '''&tlmm {
	dp_hot_plug_detect: dp_hot_plug_detect {
		mux {
			pins = "gpio10";
			function = "gpio";
		};
		config {
			pins = "gpio10";
			drive-strength = <2>;
			bias-pull-down;
		};
	};
};
'''
dp_result = update_dts_file(dp_dts, build_pin_index([dp_record]))
dp_node = dp_result.node_results[0]
assert 'function = "gpio";' in dp_result.updated_text
assert 'function = "dp";' not in dp_result.updated_text
assert 'drive-strength = <10>;' in dp_result.updated_text
assert any("unverified function candidate 'dp'" in line for line in dp_result.updated_text.splitlines())
assert dp_node.review_note and "manual confirmation" in dp_node.review_note
review_html = build_update_review_report("CCS20", [{
    "output_filename": "ccs20-updated.dtsi",
    "node_results": [{
        "label": dp_node.label,
        "changed": dp_node.changed,
        "changes": [vars(change) for change in dp_node.changes],
        "unmatched_pins": dp_node.unmatched_pins,
        "structural_issue": dp_node.structural_issue,
        "review_note": dp_node.review_note,
        "ai_assisted": False,
        "ai_advisory": None,
    }],
}])
assert "Manual review notes (values left unchanged)" in review_html
assert "Selected Interface &#x27;DP&#x27;" in review_html and "unverified function candidate &#x27;dp&#x27;" in review_html
print("OK: kept gpio, applied the workbook's drive-strength, and flagged the derived function for review")
