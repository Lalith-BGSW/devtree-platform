"""
Tests the fix for a real, confirmed data-corruption bug: some real DTS
files nest several genuine pin nodes inside one outer "label: label { }"
wrapper that has no mux/config of its own (confirmed real case: a
Qualcomm Monaco platform reference file, structurally:

    qupv3_se0_i2c_pins: qupv3_se0_i2c_pins {
        qupv3_se0_i2c_sda_active: qupv3_se0_i2c_sda_active {
            mux { pins = "gpio17"; function = "qup0_se0_l0"; };
            config { pins = "gpio17"; drive-strength = <2>; bias-pull-up; };
        };
        qupv3_se0_i2c_scl_active: qupv3_se0_i2c_scl_active {
            mux { pins = "gpio18"; function = "qup0_se0_l1"; };
            config { pins = "gpio18"; drive-strength = <2>; bias-pull-up; };
        };
    };

Before the fix, _extract_sub_block searched the OUTER label's full span
for the first "mux {"/"config {" it could find - which is actually the
FIRST CHILD's block, not the wrapper's own. The wrapper was then wrongly
treated as a real, updatable node, and the resulting text reconstruction
corrupted the boundary with the next sibling - confirmed real output:
the second child's label "qupv3_se0_i2c_scl_active" got mangled into the
fragment "cl_active" with a missing opening brace/header, breaking the
file's brace balance and producing genuinely invalid DTS syntax.

The fix: detect when a labeled span contains OTHER labeled spans nested
strictly inside it - if so, it's a pure grouping wrapper, skip it
entirely (neither a node nor a candidate) and let the real children be
found and processed independently, each correctly scoped to its own
span.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.parsers.dts_parser import parse_dts_nodes_with_candidates
from app.generators.dts_generator import build_pin_index
from app.generators.dts_updater import update_dts_file
from app.models import NormalizedRecord

NESTED_SAMPLE = """&tlmm {
\tqupv3_se0_i2c_pins: qupv3_se0_i2c_pins {
\t\tqupv3_se0_i2c_sda_active: qupv3_se0_i2c_sda_active {
\t\t\tmux {
\t\t\t\tpins = "gpio17";
\t\t\t\tfunction = "qup0_se0_l0";
\t\t\t};

\t\t\tconfig {
\t\t\t\tpins = "gpio17";
\t\t\t\tdrive-strength = <2>;
\t\t\t\tbias-pull-up;
\t\t\t};
\t\t};

\t\tqupv3_se0_i2c_scl_active: qupv3_se0_i2c_scl_active {
\t\t\tmux {
\t\t\t\tpins = "gpio18";
\t\t\t\tfunction = "qup0_se0_l1";
\t\t\t};

\t\t\tconfig {
\t\t\t\tpins = "gpio18";
\t\t\t\tdrive-strength = <2>;
\t\t\t\tbias-pull-up;
\t\t\t};
\t\t};
\t};
};
"""

print("=== 1. Grouping wrapper correctly excluded, real children correctly found ===")
nodes, candidates = parse_dts_nodes_with_candidates(NESTED_SAMPLE)
labels = [n.label for n in nodes]
assert "qupv3_se0_i2c_pins" not in labels, "wrapper must NOT be treated as a node"
assert "qupv3_se0_i2c_sda_active" in labels
assert "qupv3_se0_i2c_scl_active" in labels
assert len(nodes) == 2
assert len(candidates) == 0
print(f"OK: {len(nodes)} real nodes found, wrapper correctly excluded")

sda = next(n for n in nodes if n.label == "qupv3_se0_i2c_sda_active")
assert sda.mux_pins == ["gpio17"]
assert sda.drive_strength == "2"
print("OK: child node's own data correctly scoped (not stolen from a sibling)")

print("\n=== 2. Full update run produces valid, non-corrupted output ===")
records = [
    NormalizedRecord(project="P", soc="S", row_index=0, fields={
        "sa8155sip_ball_name": "GPIO_17", "pinmuxed_name": "GPIO17",
        "selected_interface": "I2C", "io_gpio_pull": "0: NO_PULL",
        "io_drv_strength": "2: DRV_6_MA",
    }),
    NormalizedRecord(project="P", soc="S", row_index=1, fields={
        "sa8155sip_ball_name": "GPIO_18", "pinmuxed_name": "GPIO18",
        "selected_interface": "I2C", "io_gpio_pull": "0: NO_PULL",
        "io_drv_strength": "2: DRV_6_MA",
    }),
]
pin_index = build_pin_index(records)
result = update_dts_file(NESTED_SAMPLE, pin_index, use_ai_fallback=False)

open_b, close_b = result.updated_text.count("{"), result.updated_text.count("}")
assert open_b == close_b, f"BUG STILL PRESENT: brace imbalance {open_b} vs {close_b}"
print(f"OK: brace balance preserved ({open_b} == {close_b})")

assert "qupv3_se0_i2c_scl_active" in result.updated_text, "second child's label must survive intact"
assert "cl_active {" not in result.updated_text.replace("qupv3_se0_i2c_scl_active", ""), \
    "must not reproduce the exact mangled-label corruption"
print("OK: second child's label intact, no mangled fragment")

changed_labels = {nr.label for nr in result.node_results if nr.changed}
assert "qupv3_se0_i2c_pins" not in changed_labels, "wrapper must never be reported as changed"
assert "qupv3_se0_i2c_sda_active" in changed_labels
assert "qupv3_se0_i2c_scl_active" in changed_labels
print("OK: only the real leaf nodes are reported as changed, never the wrapper")

print("\nNESTED GROUPING TEST PASSED")