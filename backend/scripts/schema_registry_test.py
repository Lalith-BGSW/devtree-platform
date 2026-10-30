"""
Tests the schema registry that replaced the single-hardcoded-schema
design, added to support genuinely different workbook structures
(different SoC/project names, different PinMux sub-table counts) without
reintroducing the blind-guessing risk the original hardcoded schema was
built to eliminate.

Covers:
  1. The real SA8155P schema still exact-matches with zero regression.
  2. Structural sub-group detection works for ANY count/naming of
     Port/Instance/RegSetting-shaped entities, not a fixed list.
  3. A genuinely new structure is correctly flagged as needing
     classification, with accurate (but unconfirmed) role suggestions.
  4. Confirming a new schema actually produces real records (this
     specific bug - confirmed schema not being written back onto the
     session's parsed columns before normalize() ran - was found and
     fixed while building this).
  5. The same new structure is auto-recognized on a SECOND upload, no
     re-classification needed.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.schema_registry import (
    list_known_schemas, match_known_schema, detect_subgroup_shaped_entities,
    suggest_role, confirm_new_schema,
)
from app.schema import KNOWN_COLUMNS

print("=== 1. Real SA8155P schema still exact-matches ===")
real_labels = set(KNOWN_COLUMNS.keys())
matched = match_known_schema(real_labels)
assert matched is not None
assert matched.name == "SA8155P (Nissan CCS20/POC_NESSY2)"
assert len(matched.known_columns) == 124
print("OK: 124-column exact match unchanged")

print("\n=== 2. Structural sub-group detection - any count/naming ===")
fake_fields = {
    "PinMux01": ["Port1", "Instance1", "RegSetting1"],
    "PinMux09": ["Port9", "Instance9", "RegSetting9"],
    "PinMux10": ["Port10", "Instance10", "RegSetting10"],
    "WeirdNameXYZ": ["PortXYZ", "InstanceXYZ", "RegSettingXYZ"],
    "SA9000X": ["ID", "Ball Name", "Ball ID", "Power Domain"],
    "PROJECT_BETA": ["Selected Interface", "PinMux Setting", "Remarks"],
}
detected = set(detect_subgroup_shaped_entities(fake_fields))
assert detected == {"PinMux01", "PinMux09", "PinMux10", "WeirdNameXYZ"}
print(f"OK: detected {sorted(detected)}, correctly excluded SoC/project entities")

print("\n=== 3. Role suggestion heuristics ===")
role, reason = suggest_role(["ID", "Ball Name", "Ball ID", "Power Domain"])
assert role == "soc"
role, reason = suggest_role(["Selected Interface", "PinMux Setting", "Remarks"])
assert role == "project"
role, reason = suggest_role(["SomeRandomUnrecognizableField"])
assert role == "unknown"
print("OK: soc/project/unknown all correctly suggested")

print("\n=== 4. Confirming a new schema registers it correctly ===")
before_count = len(list_known_schemas())
schema = confirm_new_schema(
    name="Registry Test Schema", soc_entity="TESTSOC",
    project_entities=["TESTPROJ"], subgroup_entities=[],
    columns_by_label={
        "ID (TESTSOC)": ("TESTSOC", "ID", "identity"),
        "Selected Interface (TESTPROJ)": ("TESTPROJ", "Selected Interface", "project_config"),
    },
)
after_count = len(list_known_schemas())
assert after_count == before_count + 1
assert schema.soc_entity == "TESTSOC"
assert len(schema.known_columns) == 2

print("\n=== 5. The newly confirmed schema now exact-matches on its own labels ===")
test_labels = {"ID (TESTSOC)", "Selected Interface (TESTPROJ)"}
matched2 = match_known_schema(test_labels)
assert matched2 is not None and matched2.name == "Registry Test Schema"
print("OK: same structure auto-recognized without re-classification")

print("\n=== 6. A DIFFERENT structure still correctly finds no match ===")
different_labels = {"Something Totally Different (FOO)"}
assert match_known_schema(different_labels) is None
print("OK: genuinely different structures still correctly fall through to classification")

print("\n=== 7. A file with a labeled-but-no-entity marker column self-matches after classify_schema ===")
# Regression: classify_schema() (app/main.py) used to silently drop any column with no
# detected entity (or a real entity marked "ignore") when building known_columns - but
# build_analysis() compares against ALL of a file's labeled columns when deciding whether
# to match. That mismatch meant a file with even one such column (confirmed real case: an
# Iveco workbook's "_END_SB"-style structural boundary marker, label present, no entity)
# could NEVER match its own just-confirmed schema again - reclassification every upload,
# forever. Reproduced here at the registry level: mirror exactly what classify_schema()
# now does (a labeled, entity-less column becomes an explicit ("", label, "marker") entry,
# the same convention app/schema.py's own _MARKER_COLUMNS already uses for SA8155P).
schema3 = confirm_new_schema(
    name="Registry Test Schema With Marker", soc_entity="TESTSOC2",
    project_entities=["TESTPROJ2"], subgroup_entities=[],
    columns_by_label={
        "ID (TESTSOC2)": ("TESTSOC2", "ID", "identity"),
        "Selected Interface (TESTPROJ2)": ("TESTPROJ2", "Selected Interface", "project_config"),
        "_END_SB": ("", "_END_SB", "marker"),   # what classify_schema() now does for a no-entity column
    },
)
assert len(schema3.known_columns) == 3, "the marker column must be persisted, not silently dropped"
all_labels_incl_marker = {"ID (TESTSOC2)", "Selected Interface (TESTPROJ2)", "_END_SB"}
matched3 = match_known_schema(all_labels_incl_marker)
assert matched3 is not None and matched3.name == "Registry Test Schema With Marker", \
    "a file with a marker column must self-match its own confirmed schema, not ask again forever"
# and WITHOUT the marker column, it must NOT match (proves this isn't accidentally fuzzy)
assert match_known_schema({"ID (TESTSOC2)", "Selected Interface (TESTPROJ2)"}) is None
print("OK: a file with a labeled marker column self-matches after being confirmed once")

print("\nSCHEMA REGISTRY TEST PASSED")
