"""
Tests the AI-assisted value-format decoder for newly confirmed schemas:
  1. Field-identification heuristic (suggest_value_field) - deterministic.
  2. ai_decode_values() with a mocked AI response - proves the mapping
     mechanism and its value validation work correctly.
  3. derive_pin_config() actually USES a configured decode map as a
     fallback when the standard SA8155P-specific fields aren't present -
     the real integration point.
  4. The verified SA8155P schema is COMPLETELY UNAFFECTED by any of
     this - it never needs the fallback since its fields are always
     found via the standard path.
"""
import sys, os
from unittest.mock import patch
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ai_value_decoder import suggest_value_field, ai_decode_values
from app.schema_registry import confirm_new_schema, update_value_decode_map, find_schema_by_soc_entity
from app.models import NormalizedRecord
from app.generators.dts_generator import derive_pin_config

print("=== 1. Field identification heuristic ===")
slug_to_field = {
    "gpio_pull_cfg": "GPIO Pull Config", "drv_str": "Drive Strength mA",
    "sig_dir": "Signal Direction", "unrelated_field": "Something Else",
}
assert suggest_value_field(slug_to_field, "bias") == "gpio_pull_cfg"
assert suggest_value_field(slug_to_field, "drive_strength") == "drv_str"
assert suggest_value_field(slug_to_field, "direction") == "sig_dir"
assert suggest_value_field({"x": "Totally Unrelated"}, "bias") is None
print("OK: correctly identifies likely fields by name, None when nothing matches")

print("\n=== 2. ai_decode_values with mocked response ===")
class FakeChoice:
    def __init__(self, content):
        self.message = type("M", (), {"content": content})()
class FakeResp:
    def __init__(self, content):
        self.choices = [FakeChoice(content)]

fake_client = type("C", (), {})()
fake_client.chat = type("Chat", (), {})()
fake_client.chat.completions = type("Comp", (), {})()
fake_client.chat.completions.create = lambda **kw: FakeResp(
    '{"PULL_UP": "bias-pull-up", "PULL_DOWN": "bias-pull-down", "NONE": "bias-disable", "WEIRD": "bias-invalid-garbage"}'
)

with patch("app.ai_value_decoder.get_client", return_value=fake_client):
    result = ai_decode_values("GPIO Pull Config", ["PULL_UP", "PULL_DOWN", "NONE", "WEIRD"], "bias")

print(result)
assert result["PULL_UP"] == "bias-pull-up"
assert result["PULL_DOWN"] == "bias-pull-down"
assert result["NONE"] == "bias-disable"
assert result["WEIRD"] is None, "an invalid directive the model hallucinated must be dropped, not passed through"
print("OK: valid directives pass through, invalid ones are dropped rather than trusted")

print("\n=== 3. No client available -> None, never guesses ===")
with patch("app.ai_value_decoder.get_client", return_value=None):
    result = ai_decode_values("Anything", ["A", "B"], "bias")
assert result is None
print("OK")

print("\n=== 4. derive_pin_config() uses a configured decode map as fallback ===")
schema = confirm_new_schema(
    name="Value Decoder Test Schema", soc_entity="VDTESTSOC",
    project_entities=["VDTESTPROJ"], subgroup_entities=[],
    columns_by_label={
        "ID (VDTESTSOC)": ("VDTESTSOC", "ID", "identity"),
        "Selected Interface (VDTESTPROJ)": ("VDTESTPROJ", "Selected Interface", "project_config"),
        "GPIO Pull Config (VDTESTPROJ)": ("VDTESTPROJ", "GPIO Pull Config", "project_config"),
    },
)
update_value_decode_map(
    "VDTESTSOC", "bias", "gpio_pull_config",
    {"PULL_UP": "bias-pull-up", "PULL_DOWN": "bias-pull-down", "NONE": "bias-disable"},
)

rec = NormalizedRecord(project="VDTESTPROJ", soc="VDTESTSOC", row_index=0, fields={
    "sa8155sip_ball_name": "GPIO_5",  # falls back to ball_name since no pinmuxed_name
    "selected_interface": "GPIO",
    "gpio_pull_config": "PULL_UP",  # NOT the standard "io_gpio_pull" slug
})
derived = derive_pin_config(rec)
print(f"bias_directive={derived.bias_directive!r} ai_assisted_bias={derived.ai_assisted_bias}")
assert derived.bias_directive == "bias-pull-up"
assert derived.ai_assisted_bias is True
assert derived.bias_needs_review is False
print("OK: AI-assisted decode map correctly used as fallback, clearly tagged")

print("\n=== 5. Real SA8155P schema is completely unaffected ===")
real_rec = NormalizedRecord(project="CCS20", soc="SA8155P", row_index=0, fields={
    "sa8155sip_ball_name": "GPIO_43", "pinmuxed_name": "GPIO43",
    "selected_interface": "HSUART_QUP13", "io_gpio_pull": "3: PULL_UP",
    "io_drv_strength": "4: DRV_10_MA",
})
real_derived = derive_pin_config(real_rec)
assert real_derived.ai_assisted_bias is False
assert real_derived.ai_assisted_drive_strength is False
assert real_derived.bias_directive == "bias-pull-up"  # via the verified standard path, not AI
print("OK: SA8155P records never touch the AI-assisted path - standard fields always found first")

print("\nAI VALUE DECODER TEST PASSED")
