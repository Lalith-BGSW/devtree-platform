"""
Tests the AI-assisted parsing fallback:
  1. The strict parser now surfaces unrecognized node-shaped blocks as
     candidates instead of silently discarding them (a pure parsing
     change, fully testable without any LLM).
  2. Without GROQ_API_KEY, use_ai_fallback=True is a safe no-op - same
     output as use_ai_fallback=False. This is the most important
     safety property: the feature must never behave differently/worse
     just because a key happens to be missing.
  3. With extraction mocked (simulating what a real Groq response would
     produce), an unrecognized node gets an advisory comment inserted
     but its own text is NEVER rewritten - verifying the core safety
     boundary of this whole feature.
  4. A low-quality/failed extraction (mocked to return None) results in
     the node being left exactly as untouched as if AI fallback were
     off entirely - no partial/guessed output.
"""
import sys, os
from unittest.mock import patch
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.parsers.dts_parser import parse_dts_nodes, parse_dts_nodes_with_candidates
from app.parsers.ai_node_extractor import ExtractedNodeData
from app.generators.dts_generator import build_pin_index
from app.generators.dts_updater import update_dts_file
from app.models import NormalizedRecord

SAMPLE_DTS = """&tlmm {
\tnormal_node: normal_node {
\t\tmux {
\t\t\tpins = "gpio43";
\t\t\tfunction = "gpio";
\t\t};

\t\tconfig {
\t\t\tpins = "gpio43";
\t\t\tdrive-strength = <2>;
\t\t\tbias-disable;
\t\t};
\t};

\tweird_format_node: weird_format_node {
\t\tqcom,pins = <&gpio 99 0>;
\t\tqcom,function = "gpio";
\t\tqcom,drive-strength = <8>;
\t};
};
"""

records = [
    NormalizedRecord(project="CCS20", soc="SA8155P", row_index=0, fields={
        "sa8155sip_ball_name": "GPIO_43", "pinmuxed_name": "GPIO43",
        "selected_interface": "HSUART_QUP13", "io_drv_strength": "4: DRV_10_MA",
        "io_gpio_pull": "3: PULL_UP",
    }),
    NormalizedRecord(project="CCS20", soc="SA8155P", row_index=1, fields={
        "sa8155sip_ball_name": "GPIO_99", "pinmuxed_name": "GPIO99",
        "selected_interface": "SPI_QUP5", "io_drv_strength": "4: DRV_10_MA",
        "io_gpio_pull": "0: NO_PULL",
    }),
]
pin_index = build_pin_index(records)

print("=== 1. Parser surfaces unrecognized nodes as candidates ===")
nodes, candidates = parse_dts_nodes_with_candidates(SAMPLE_DTS)
print(f"recognized: {[n.label for n in nodes]}")
print(f"candidates: {[c.label for c in candidates]}")
assert len(nodes) == 1 and nodes[0].label == "normal_node"
assert len(candidates) == 1 and candidates[0].label == "weird_format_node"

print("\n=== 2. use_ai_fallback=True with NO key is a safe no-op ===")
result_off = update_dts_file(SAMPLE_DTS, pin_index, use_ai_fallback=False)
result_on_no_key = update_dts_file(SAMPLE_DTS, pin_index, use_ai_fallback=True)
assert result_off.updated_text == result_on_no_key.updated_text, \
    "BUG: enabling AI fallback changed output even with no API key available"
assert len(result_off.node_results) == len(result_on_no_key.node_results)
print("OK: identical output with fallback on vs off when no key is configured")

print("\n=== 3. Mocked successful extraction: advisory inserted, node text UNCHANGED ===")
fake_extraction = ExtractedNodeData(
    pins=["gpio99"], function="gpio", drive_strength="8",
    bias_directive=None, direction_directive=None, confidence="high",
)
with patch("app.generators.dts_updater.extract_node_data", return_value=fake_extraction):
    result = update_dts_file(SAMPLE_DTS, pin_index, use_ai_fallback=True)

print(result.updated_text)
assert "qcom,pins = <&gpio 99 0>;" in result.updated_text, \
    "BUG: AI-assisted path must never rewrite the original node text"
assert "AI-ASSISTED PARSE" in result.updated_text
assert "drive-strength" in result.updated_text and "'8'" in result.updated_text and "'10'" in result.updated_text
ai_results = [r for r in result.node_results if r.ai_assisted]
assert len(ai_results) == 1
assert ai_results[0].changed is False, "AI-assisted results must never claim changed=True"
print("OK: original text preserved exactly, advisory correctly reports the Excel discrepancy")

print("\n=== 4. Mocked failed extraction (None): identical to fallback being off ===")
with patch("app.generators.dts_updater.extract_node_data", return_value=None):
    result_failed = update_dts_file(SAMPLE_DTS, pin_index, use_ai_fallback=True)
assert result_failed.updated_text == result_off.updated_text
print("OK: failed extraction produces no output changes, no guessing")

print("\n=== 5. Coverage tracking includes AI-extracted pins ===")
with patch("app.generators.dts_updater.extract_node_data", return_value=fake_extraction):
    result = update_dts_file(SAMPLE_DTS, pin_index, use_ai_fallback=True)
assert "gpio99" in result.covered_pins
assert "gpio43" in result.covered_pins
print(f"covered_pins: {sorted(result.covered_pins)}")

print("\nAI FALLBACK TEST PASSED")
