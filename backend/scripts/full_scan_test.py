"""
Reproduces the exact bug reported: on the old code, ProjectIndex.search()
always truncated to `k` results even AFTER keyword filtering had already
narrowed the candidates down - so a broad query on the real 2,358-row
workbook could silently drop real matching rows. This builds a project
with MORE matching rows than the old k=15/k=50 caps used to allow, and
confirms every single one is now found.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.models import NormalizedRecord
from app.rag.index import rag_store
from app.generators.dts_generator import generate_dts

WORKBOOK_ID = "full-scan-test"
PROJECT = "CCS20"

# 80 GPIO records - more than both the old chat cap (k=15) and the old
# DTS-generation cap (k=50).
records = [
    NormalizedRecord(project=PROJECT, soc="SA8155P", row_index=i, fields={
        "ball_id": f"BALL{i}",
        "ball_name": f"GPIO{i}",
        "pinmuxed_name": f"GPIO{i}",
        "selected_interface": "GPIO",
        "interface_group": "Misc",
        "direction": "in",
    })
    for i in range(80)
]

rag_store.index_records(WORKBOOK_ID, PROJECT, records)

print("=== Chat-style query (old code capped this at k=15) ===")
chat_results = rag_store.search(WORKBOOK_ID, PROJECT, "show gpio pins",
                                 k=15, keyword_filter=["gpio"], limit=None)
print(f"found: {len(chat_results)} (expected 80)")
assert len(chat_results) == 80, f"BUG: only found {len(chat_results)}, expected all 80"

print("\n=== DTS-generation-style query (old code capped this at k=50) ===")
dts_results = rag_store.search(WORKBOOK_ID, PROJECT, "gpio",
                                k=50, keyword_filter=["gpio"], limit=None)
print(f"found: {len(dts_results)} (expected 80)")
assert len(dts_results) == 80, f"BUG: only found {len(dts_results)}, expected all 80"

print("\n=== Confirm generated DTS actually contains all 80 pins ===")
dts_text, _amb, _review = generate_dts(PROJECT, "SA8155P", dts_results)
missing = [f"gpio{i}" for i in range(80) if f'"gpio{i}"' not in dts_text]
print(f"missing pins: {len(missing)} (expected 0)")
assert not missing, f"BUG: DTS output is missing pins: {missing[:5]}..."

print("\n=== Confirm display cap still works (chat UI truncation, not data loss) ===")
display_capped = rag_store.search(WORKBOOK_ID, PROJECT, "show gpio pins",
                                   k=15, keyword_filter=["gpio"], limit=10)
print(f"found with limit=10: {len(display_capped)} (expected 10)")
assert len(display_capped) == 10

print("\nFULL-SCAN TEST PASSED")
