"""
Full pipeline test against schema_synthetic.xlsx, which is built directly
FROM the hardcoded schema (app/schema.py) - so this validates the new
schema-driven parser/normalizer, including:
  - Exact schema match reporting (should be schema_match=True since the
    file was generated from the schema itself)
  - Correct entity roles (soc/project/subgroup) with no guessing
  - Legend-row filtering (_PARA_ROW="DRV_STRENGTH" row excluded)
  - _END_ROW row correctly KEPT as real data
  - Non-signal (AGND/DNC) filtering in DTS generation
  - Real-duplicate (AF12) still caught by validation
  - UART TX/RX merged into one DTS node
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.parsers.workbook_parser import parse_workbook, build_analysis
from app.parsers.normalizer import normalize
from app.rag.index import rag_store
from app.validators.rules import run_validation
from app.generators.dts_generator import generate_dts
from app.schema import SOC_ENTITY, PROJECT_ENTITIES, SUBGROUP_ENTITIES

WB_PATH = os.path.join(os.path.dirname(__file__), "schema_synthetic.xlsx")

print("=== 1. Parsing + schema validation ===")
parsed = parse_workbook(WB_PATH)
analysis = build_analysis(parsed)
print(f"total_columns={analysis.total_columns} hidden={analysis.hidden_columns} "
      f"schema_match={analysis.schema_match}")
if not analysis.schema_match:
    print("issues:", analysis.schema_issues)
assert analysis.schema_match, "schema_synthetic.xlsx was built FROM the schema - must match exactly"
assert analysis.total_columns == 124

roles = {e.name: e.role for e in analysis.entities}
assert roles[SOC_ENTITY] == "soc"
for p in PROJECT_ENTITIES:
    assert roles[p] == "project", f"{p} should be role=project, got {roles[p]}"
for s in SUBGROUP_ENTITIES:
    assert roles[s] == "subgroup", f"{s} should be role=subgroup, got {roles[s]}"
print("OK: all entity roles correct (soc/project/subgroup), no guessing involved")

print("\n=== 2. Normalizing (with legend-row + _END_ROW filtering) ===")
records = normalize(parsed)  # uses schema defaults now, no params needed
ccs20 = [r for r in records if r.project == "CCS20"]
print(f"CCS20 records: {len(ccs20)}")
# 9 real data rows for CCS20 (TX, RX, I2C, 3x AGND, 2x DNC, dup AF12) +
# the _END_ROW row (GND) = 10. The DRV_STRENGTH legend row must NOT appear.
assert len(ccs20) == 10, f"expected 10 records (legend row excluded), got {len(ccs20)}"
signals = [r.fields.get("gen5_signal_name") for r in ccs20]
assert "0: DRV_2_MA" not in [r.fields.get("id") for r in ccs20], \
    "BUG: legend row's 'ID' value leaked into a record"
print("OK: legend row excluded, _END_ROW row correctly included as real data")

print("\n=== 3. RAG indexing + full-scan search ===")
rag_store.index_records("schema-test", "CCS20", ccs20)
hits = rag_store.search("schema-test", "CCS20", "bluetooth uart", k=5, keyword_filter=["bluetooth"])
assert len(hits) == 2, f"expected 2 bluetooth UART hits, got {len(hits)}"
print(f"OK: found {len(hits)} bluetooth UART records via full scan")

print("\n=== 4. Validation (duplicate detection + non-signal exclusion) ===")
issues = run_validation(ccs20)
for i in issues:
    print(f"  [{i.severity}] {i.rule}: {i.message}")
dup_issues = [i for i in issues if i.rule == "duplicate_pin_assignment"]
assert len(dup_issues) == 1, f"expected exactly 1 duplicate (AF12), got {len(dup_issues)}"
assert "AF12" in dup_issues[0].message
print("OK: exactly 1 real duplicate flagged (AF12), AGND x3 correctly not flagged")

print("\n=== 5. DTS generation (non-signal rows excluded) ===")
dts, _amb, _review = generate_dts("CCS20", SOC_ENTITY, ccs20)
print(dts)
assert "gpio43" in dts and "gpio44" in dts and "gpio21" in dts
assert "AGND" not in dts and "DNC" not in dts
assert "&tlmm {" in dts, "root wrapper should be &tlmm per the real sample format"
assert 'function = "qup13"' in dts, "uart node should use real qup13 function name (see sample: se13 = QUP13 = HSUART Bluetooth)"
assert 'function = "qup2"' in dts, "i2c node should use real qup2 function name"
assert "drive-strength = <2>;" in dts, "IO_DRV_STRENGTH '0: DRV_2_MA' should parse to drive-strength=<2>"
assert "drive-strength = <4>;" in dts, "IO_DRV_STRENGTH '1: DRV_4_MA' should parse to drive-strength=<4>"
assert "bias-pull-up;" in dts, "IO_GPIO_PULL '3: PULL_UP' should parse to bias-pull-up"
assert "bias-disable;" in dts, "IO_GPIO_PULL '0: NO_PULL' should parse to bias-disable"
assert "NEEDS REVIEW" in dts, "the duplicate-ball test row has no drive-strength/bias data - should be flagged"
needs_review_lines = dts.count("NEEDS REVIEW")
assert needs_review_lines == 3, f"expected 3 NEEDS REVIEW mentions (1 header summary + drive-strength/bias inline comments for the one under-specified row), got {needs_review_lines}"
print("OK: real signal pins present, AGND/DNC excluded from generated DTS")

print("\nSCHEMA-DRIVEN PIPELINE TEST PASSED")
