"""
Tests the DTS update engine (app/generators/dts_updater.py) against the
four confirmed policies:
  1. A pin's DTS value disagreeing with Excel gets overwritten, with an
     old->new comment.
  2. A node with only SOME pins matched: matched pins' properties update,
     unmatched pins are left alone with a flag comment.
  3. A pin counts as "covered" by simply appearing in a file, regardless
     of whether its values matched.
  4. A node whose mux and config blocks disagree about which pins they
     describe (e.g. hand-edited inconsistently) is left completely
     untouched and flagged - never silently resolved by trusting one
     side. Reproduces a real case: editing only mux's pin from "gpio10"
     to "gpio100" (leaving config's "gpio10" untouched) must NOT let the
     updater confidently absorb gpio100's unrelated real Excel data.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.models import NormalizedRecord
from app.generators.dts_generator import build_pin_index
from app.generators.dts_updater import update_dts_file
from app.generators import dts_updater as updater_module
from app.parsers.ai_node_extractor import ExtractedNodeData
from app.review_report import build_update_review_report

# Synthetic "uploaded DTS file" text, covering 5 scenarios:
SAMPLE_DTS = """/* Copyright test header */

&tlmm {
	uart_node: uart_node {
		mux {
			pins = "gpio43", "gpio46";
			function = "qup13";
		};

		config {
			pins = "gpio43", "gpio46";
			drive-strength = <2>;
			bias-disable;
		};
	};

	already_correct: already_correct {
		mux {
			pins = "gpio21";
			function = "qup2";
		};

		config {
			pins = "gpio21";
			drive-strength = <10>;
			bias-pull-up;
		};
	};

	partial_match: partial_match {
		mux {
			pins = "gpio99", "gpio100";
			function = "gpio";
		};

		config {
			pins = "gpio99", "gpio100";
			drive-strength = <2>;
			bias-disable;
		};
	};

	no_match_at_all: no_match_at_all {
		mux {
			pins = "gpio200";
			function = "gpio";
		};

		config {
			pins = "gpio200";
			drive-strength = <2>;
			bias-disable;
		};
	};

	mismatched_mux_config: mismatched_mux_config {
		mux {
			pins = "gpio300";
			function = "gpio";
		};

		config {
			pins = "gpio10";
			drive-strength = <2>;
			bias-pull-down;
		};
	};
};
"""

records = [
    # Scenario 1: values DIFFER from DTS (2->10, bias-disable->bias-pull-up)
    NormalizedRecord(project="CCS20", soc="SA8155P", row_index=0, fields={
        "ball_name": "GPIO_43", "ball_id": "AF12",
        "selected_interface": "HSUART_QUP13", "pinmux_setting": "QUP13",
        "io_drv_strength": "4: DRV_10_MA", "io_gpio_pull": "3: PULL_UP",
    }),
    NormalizedRecord(project="CCS20", soc="SA8155P", row_index=1, fields={
        "ball_name": "GPIO_46", "ball_id": "AF15",
        "selected_interface": "HSUART_QUP13", "pinmux_setting": "QUP13",
        "io_drv_strength": "4: DRV_10_MA", "io_gpio_pull": "3: PULL_UP",
    }),
    # Scenario 2: values ALREADY match DTS exactly (10, bias-pull-up)
    NormalizedRecord(project="CCS20", soc="SA8155P", row_index=2, fields={
        "ball_name": "GPIO_21", "ball_id": "AC09",
        "selected_interface": "I2C_QUP2", "pinmux_setting": "QUP2",
        "io_drv_strength": "4: DRV_10_MA", "io_gpio_pull": "3: PULL_UP",
    }),
    # Scenario 3: only gpio99 matches (gpio100 does not exist in Excel)
    NormalizedRecord(project="CCS20", soc="SA8155P", row_index=3, fields={
        "ball_name": "GPIO_99", "ball_id": "BB01",
        "selected_interface": "GPIO", "direction": "0: OUT",
        "io_drv_strength": "4: DRV_10_MA", "io_gpio_pull": "0: NO_PULL",
    }),
    # Scenario 4: gpio300 IS a real, valid Excel pin (unrelated QUP7
    # signal) - proving that even a genuine match must NOT override the
    # mux/config mismatch check. This is the exact real case: a node
    # hand-edited so mux and config disagree about which pin is meant.
    NormalizedRecord(project="CCS20", soc="SA8155P", row_index=4, fields={
        "ball_name": "GPIO_300", "ball_id": "CC01",
        "selected_interface": "SOME_QUP7_SIGNAL", "pinmux_setting": "QUP7",
        "io_drv_strength": "4: DRV_10_MA", "io_gpio_pull": "3: PULL_UP",
    }),
    # NOTE: no record at all for gpio100, gpio200, or gpio10
]

pin_index = build_pin_index(records)
print(f"pin_index keys: {sorted(pin_index.keys())}")
assert set(pin_index.keys()) == {"gpio43", "gpio46", "gpio21", "gpio99", "gpio300"}

result = update_dts_file(SAMPLE_DTS, pin_index)

by_label = {nr.label: nr for nr in result.node_results}

print("\n=== Scenario 1: uart_node (should update drive-strength + bias) ===")
r = by_label["uart_node"]
print(f"changed={r.changed} changes={[(c.field, c.old_value, c.new_value) for c in r.changes]}")
assert r.changed
fields_changed = {c.field for c in r.changes}
assert "drive-strength" in fields_changed
assert "bias" in fields_changed

print("\n=== Scenario 2: already_correct (should NOT change) ===")
r = by_label["already_correct"]
print(f"changed={r.changed} changes={r.changes}")
assert not r.changed
assert not r.changes

print("\n=== Scenario 3: partial_match (gpio99 matched, gpio100 not) ===")
r = by_label["partial_match"]
print(f"changed={r.changed} changes={[(c.field, c.old_value, c.new_value) for c in r.changes]} unmatched={r.unmatched_pins}")
assert r.changed  # gpio99's real values differ from the stale DTS
assert r.unmatched_pins == ["gpio100"]

print("\n=== Scenario 4: no_match_at_all (should be completely untouched) ===")
r = by_label["no_match_at_all"]
print(f"changed={r.changed} unmatched={r.unmatched_pins}")
assert not r.changed
assert r.unmatched_pins == ["gpio200"]
# Verify the raw text for this node is byte-identical in the output
assert 'pins = "gpio200"' in result.updated_text

print("\n=== Scenario 5: mismatched_mux_config (mux says gpio300, config says gpio10) ===")
r = by_label["mismatched_mux_config"]
print(f"changed={r.changed} structural_issue={r.structural_issue!r}")
assert not r.changed, "must NOT update, even though gpio300 is a real, valid Excel pin"
assert r.structural_issue is not None
assert "gpio300" in r.structural_issue and "gpio10" in r.structural_issue
# The node's original (inconsistent) text must survive byte-identical -
# critically, it must NOT have absorbed gpio300's real QUP7 config.
assert 'pins = "gpio300"' in result.updated_text
assert 'pins = "gpio10"' in result.updated_text  # config's original value, untouched
assert "SOME_QUP7_SIGNAL" not in result.updated_text  # never pulled in
assert "qup7" not in result.updated_text.lower()

print("\n=== Coverage: every pin from every node counts as covered ===")
print(f"covered_pins={sorted(result.covered_pins)}")
assert result.covered_pins == {
    "gpio43", "gpio46", "gpio21", "gpio99", "gpio100", "gpio200", "gpio300", "gpio10",
}
# gpio100 and gpio200 are covered (appeared in a file) even though they
# have NO Excel match at all - per the confirmed "appearing is enough" policy.
# gpio300/gpio10 are covered too (mux/config both count) even though the
# node itself was left unmodified due to the structural mismatch.

print("\n=== Verify surgical editing: untouched parts are byte-identical ===")
assert "/* Copyright test header */" in result.updated_text
assert result.updated_text.count("&tlmm {") == 1
# already_correct's node text should be byte-identical to the original
orig_node_text = SAMPLE_DTS[SAMPLE_DTS.index("already_correct:"):SAMPLE_DTS.index("partial_match:")]
assert orig_node_text.strip("\n\t ") in result.updated_text

print("\n=== Verify update comments are present for changed nodes ===")
assert "UPDATED from Excel: drive-strength" in result.updated_text
assert "UPDATED from Excel: bias" in result.updated_text
assert "NOT FOUND IN EXCEL - left unmodified: gpio100" in result.updated_text

print("\n=== Scenario 6: nested Monaco group preserves indentation and sleep state ===")
nested_dts = '''&tlmm {
	group_pins: group_pins {
		default: default {
			mux {
				pins = "gpio1";
				function = "gpio";
			};
			config {
				pins = "gpio1";
				drive-strength = <2>;
				bias-disable;
			};
		};
		active: active {
			mux {
				pins = "gpio1";
				function = "gpio";
			};
			config {
				pins = "gpio1";
				drive-strength = <2>;
				bias-pull-up;
			};
		};
		sleep: sleep {
			mux {
				pins = "gpio1";
				function = "gpio";
			};
			config {
				pins = "gpio1";
				drive-strength = <2>;
				bias-disable;
			};
		};
	};
};
'''
nested_record = NormalizedRecord(project="IVECO_B1", soc="SA7255P", row_index=0, fields={
	"ball_name": "GPIO_1", "selected_interface": "HSUART_QUP0", "pinmux_setting": "QUP0",
	"io_drv_strength": "4: DRV_6_MA", "io_gpio_pull": "0: NO_PULL",
})
nested_index = build_pin_index([nested_record])
sleep_start = nested_dts.index("\t\tsleep: sleep {")
sleep_end = nested_dts.index("\n\t};\n};", sleep_start)
sleep_original = nested_dts[sleep_start:sleep_end]
nested_result = update_dts_file(nested_dts, nested_index)
assert "\n\t\t\tmux {" in nested_result.updated_text
assert "\n\t\t\t\tfunction = \"qup0\";" in nested_result.updated_text
assert sleep_original in nested_result.updated_text
sleep_result = next(n for n in nested_result.node_results if n.label == "sleep")
assert not sleep_result.changed and "no state-specific configuration" in sleep_result.review_note
default_result = next(n for n in nested_result.node_results if n.label == "default")
assert not default_result.changed and "no state-specific configuration" in default_result.review_note
review_html = build_update_review_report("IVECO_B1", [{
	"output_filename": "monaco_auto-pinctrl.updated.dtsi",
	"node_results": [{
		"label": "sleep", "changed": False, "changes": [], "unmatched_pins": [],
		"structural_issue": None, "review_note": sleep_result.review_note,
		"ai_assisted": False, "ai_advisory": None,
	}],
}])
assert "Manual review notes (values left unchanged)" in review_html and "sleep" in review_html

print("\\n=== Scenario 7: AI reads an unknown layout but never rewrites it ===")
unknown_layout = '''&tlmm {
	unknown: unknown {
		pinconf {
			pins = "gpio1";
			function = "qup0";
			drive-strength = <2>;
		};
	};
};
'''
original_extract = updater_module.extract_node_data
updater_module.extract_node_data = lambda _text: ExtractedNodeData(
	pins=["gpio1"], function="qup0", drive_strength="2", bias_directive=None,
	direction_directive=None, confidence="high",
)
try:
	ai_result = update_dts_file(unknown_layout, nested_index, use_ai_fallback=True)
finally:
	updater_module.extract_node_data = original_extract
ai_node = next(n for n in ai_result.node_results if n.label == "unknown")
assert ai_node.ai_assisted and not ai_node.changed and ai_node.ai_advisory
assert ai_result.ai_candidate_count == 1 and ai_result.ai_extraction_failure_count == 0
assert "advisories for 1 unrecognized node" in ai_result.ai_fallback_status
unknown_node_text = '''\tunknown: unknown {
		pinconf {
			pins = "gpio1";
			function = "qup0";
			drive-strength = <2>;
		};
	};'''
assert unknown_node_text in ai_result.updated_text
assert "AI-ASSISTED PARSE" in ai_result.updated_text

off_result = update_dts_file(unknown_layout, nested_index, use_ai_fallback=False)
assert off_result.ai_candidate_count == 1 and not off_result.ai_fallback_requested
assert "AI fallback is off" in off_result.ai_fallback_status

updater_module.extract_node_data = lambda _text: None
previous_llm_environment = {key: os.environ.get(key) for key in ("LLM_API_KEY", "LLM_DEPLOYMENT", "LLM_FEATURES")}
os.environ["LLM_API_KEY"] = "test-only-placeholder"
os.environ["LLM_DEPLOYMENT"] = "test-only-deployment"
os.environ["LLM_FEATURES"] = "usb_advice,usb_propose,usb_suggest"
try:
	unavailable_result = update_dts_file(unknown_layout, nested_index, use_ai_fallback=True)
finally:
	updater_module.extract_node_data = original_extract
	for key, value in previous_llm_environment.items():
		if value is None:
			os.environ.pop(key, None)
		else:
			os.environ[key] = value
assert unavailable_result.ai_candidate_count == 1
assert unavailable_result.ai_extraction_failure_count == 1
assert "node_extractor is not enabled in LLM_FEATURES" in unavailable_result.ai_fallback_status
ai_review_html = build_update_review_report("CCS20", [{
	"output_filename": "nissan_ccs20_ai_format_fallback_demo.dtsi",
	"node_results": [],
	"ai_candidate_count": unavailable_result.ai_candidate_count,
	"ai_extraction_failure_count": unavailable_result.ai_extraction_failure_count,
	"ai_fallback_status": unavailable_result.ai_fallback_status,
}])
assert "1</strong> unrecognized node(s) considered for AI" in ai_review_html
assert "no usable response" in ai_review_html
assert "No changes, unmatched pins, or issues in this file." not in ai_review_html
assert "AI-assisted findings for unrecognized nodes are read-only advisories" in ai_review_html

print("\nDTS UPDATE ENGINE TEST PASSED")
