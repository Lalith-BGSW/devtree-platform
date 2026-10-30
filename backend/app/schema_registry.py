"""
Schema registry: supports multiple KNOWN, human-confirmed workbook
schemas (not just the one original hardcoded SA8155P schema), plus fully
general, pattern-based sub-group detection that works regardless of how
many PinMux/QUP-style register sub-tables a workbook has or how they're
numbered/named.

Design principle, unchanged from the original hardcoded-schema decision:
NOTHING is trusted without either (a) an exact match to a schema a human
has already confirmed, or (b) fresh explicit human confirmation for a
genuinely new structure. The difference from the original dynamic
detection removed earlier in this project: that version silently
miscounted because it had no ground truth to check itself against. This
version never silently proceeds on a guess - every entity's role in an
unmatched file is a SUGGESTION surfaced for confirmation, never applied
until confirmed, and once confirmed it's saved so the SAME structure is
recognized automatically next time (no re-confirmation needed).

Known limitation: the registry is in-memory only (matches the rest of
this app's session store), so confirmed schemas from a previous run are
lost on backend restart. Fine for a single working session; would need
disk/DB persistence to survive restarts across days - not built yet,
flagged here rather than silently losing confirmed work.
"""

from __future__ import annotations
import json
import os
from dataclasses import dataclass, asdict

from app.schema import (
    KNOWN_COLUMNS as _INITIAL_KNOWN_COLUMNS,
    SOC_ENTITY as _INITIAL_SOC_ENTITY,
    PROJECT_ENTITIES as _INITIAL_PROJECT_ENTITIES,
    SUBGROUP_ENTITIES as _INITIAL_SUBGROUP_ENTITIES,
    ColumnDef, _slug as slugify,
)

# Where user-confirmed schemas (everything beyond the original seeded
# SA8155P one) are persisted to disk, so they survive a backend restart -
# without this, every newly confirmed schema would need re-classifying
# every time the server restarts, defeating the point of "confirm once".
_PERSIST_PATH = os.environ.get(
    "SCHEMA_REGISTRY_PATH",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "confirmed_schemas.json"),
)


@dataclass
class KnownSchema:
    """One confirmed workbook schema. The original hardcoded SA8155P one
    is the first entry, seeded at import time from app/schema.py exactly
    as before - zero behavior change for that file. Additional schemas
    get added when a user confirms classification for a genuinely new
    workbook structure (see confirm_new_schema())."""
    name: str
    soc_entity: str
    project_entities: list[str]
    subgroup_entities: list[str]
    known_columns: dict[str, ColumnDef]
    # AI-assisted value-format decoding (see app/ai_value_decoder.py) -
    # None means "not configured", which falls back to NEEDS REVIEW for
    # that concept, same as if this schema had no decode maps at all.
    # Populated by /classify-schema when use_ai_value_decoding=True, and
    # persisted to disk alongside everything else so it's a one-time cost.
    bias_field_slug: str | None = None
    bias_value_map: dict[str, str | None] | None = None
    drive_strength_field_slug: str | None = None
    drive_strength_value_map: dict[str, str | None] | None = None
    direction_field_slug: str | None = None
    direction_value_map: dict[str, str | None] | None = None


_REGISTRY: list[KnownSchema] = [
    KnownSchema(
        name="SA8155P (Nissan CCS20/POC_NESSY2)",
        soc_entity=_INITIAL_SOC_ENTITY,
        project_entities=list(_INITIAL_PROJECT_ENTITIES),
        subgroup_entities=list(_INITIAL_SUBGROUP_ENTITIES),
        known_columns=dict(_INITIAL_KNOWN_COLUMNS),
    ),
]


def _load_persisted_schemas() -> None:
    """Loads user-confirmed schemas saved by a previous run, if any.
    Never raises - a missing/corrupt file just means starting with only
    the seeded schema, same as before persistence existed. Called once
    at import time."""
    if not os.path.exists(_PERSIST_PATH):
        return
    try:
        with open(_PERSIST_PATH, "r") as f:
            saved = json.load(f)
        for entry in saved:
            known_columns = {
                label: ColumnDef(**col) for label, col in entry["known_columns"].items()
            }
            _REGISTRY.append(KnownSchema(
                name=entry["name"], soc_entity=entry["soc_entity"],
                project_entities=entry["project_entities"],
                subgroup_entities=entry["subgroup_entities"],
                known_columns=known_columns,
                bias_field_slug=entry.get("bias_field_slug"),
                bias_value_map=entry.get("bias_value_map"),
                drive_strength_field_slug=entry.get("drive_strength_field_slug"),
                drive_strength_value_map=entry.get("drive_strength_value_map"),
                direction_field_slug=entry.get("direction_field_slug"),
                direction_value_map=entry.get("direction_value_map"),
            ))
    except Exception:
        pass  # corrupt/unreadable file - proceed with just the seeded schema


def _save_persisted_schemas() -> None:
    """Writes every CONFIRMED (non-seed) schema to disk. Called after
    each confirm_new_schema() or update_value_decode_maps(). Never
    raises - a failed write means the change still works for the rest of
    THIS session (it's already in the in-memory _REGISTRY), it just
    won't survive a restart; that's a degraded-but-working state, not a
    reason to fail the request itself."""
    try:
        os.makedirs(os.path.dirname(_PERSIST_PATH), exist_ok=True)
        # index 0 is the original seeded schema, already in app/schema.py -
        # only persist what a human has actually confirmed beyond that.
        to_save = [
            {
                "name": s.name, "soc_entity": s.soc_entity,
                "project_entities": s.project_entities,
                "subgroup_entities": s.subgroup_entities,
                "known_columns": {label: asdict(col) for label, col in s.known_columns.items()},
                "bias_field_slug": s.bias_field_slug,
                "bias_value_map": s.bias_value_map,
                "drive_strength_field_slug": s.drive_strength_field_slug,
                "drive_strength_value_map": s.drive_strength_value_map,
                "direction_field_slug": s.direction_field_slug,
                "direction_value_map": s.direction_value_map,
            }
            for s in _REGISTRY[1:]
        ]
        with open(_PERSIST_PATH, "w") as f:
            json.dump(to_save, f, indent=2)
    except Exception:
        pass  # best-effort - see docstring


_load_persisted_schemas()


def find_schema_by_soc_entity(soc_entity: str) -> KnownSchema | None:
    """Looks up a schema by its soc_entity name - used by
    derive_pin_config() to find a record's owning schema (via
    NormalizedRecord.soc) so it can consult that schema's AI-assisted
    value decode maps, if any, as a fallback when the standard SA8155P-
    specific field lookups don't find anything."""
    for schema in _REGISTRY:
        if schema.soc_entity == soc_entity:
            return schema
    return None


def update_value_decode_map(soc_entity: str, concept: str, field_slug: str,
                             value_map: dict[str, str | None]) -> None:
    """Attaches an AI-assisted value decode map to a schema (identified
    by its soc_entity) and persists it. concept is "bias" |
    "drive_strength" | "direction". Overwrites any existing map for that
    concept - callers should only call this once per (schema, concept),
    typically right after classification."""
    schema = find_schema_by_soc_entity(soc_entity)
    if schema is None:
        return
    if concept == "bias":
        schema.bias_field_slug, schema.bias_value_map = field_slug, value_map
    elif concept == "drive_strength":
        schema.drive_strength_field_slug, schema.drive_strength_value_map = field_slug, value_map
    elif concept == "direction":
        schema.direction_field_slug, schema.direction_value_map = field_slug, value_map
    _save_persisted_schemas()


def list_known_schemas() -> list[KnownSchema]:
    return list(_REGISTRY)


def match_known_schema(found_labels: set[str]) -> KnownSchema | None:
    """Returns the schema whose column set exactly matches found_labels,
    or None if nothing matches. Exact match only - no partial/fuzzy
    matching here, since that would reintroduce silent-guessing risk. A
    close-but-not-exact match falls through to the guided classification
    flow instead, same as a totally new structure would."""
    for schema in _REGISTRY:
        if set(schema.known_columns.keys()) == found_labels:
            return schema
    return None


def confirm_new_schema(name: str, soc_entity: str, project_entities: list[str],
                        subgroup_entities: list[str],
                        columns_by_label: dict[str, tuple[str, str, str]]
                        ) -> KnownSchema:
    """Registers a newly human-confirmed schema.

    columns_by_label maps label -> (entity, base_field, category), built
    from the parsed workbook's actual columns plus the user's confirmed
    role assignment. Descriptions are left generic (no hand-written
    semantic description exists yet for a schema nobody has reviewed in
    this depth) - can be refined later without affecting correctness,
    since descriptions only feed the LLM's grounding context, not any
    parsing/generation logic.
    """
    known_columns = {}
    for label, (entity, base_field, category) in columns_by_label.items():
        known_columns[label] = ColumnDef(
            label=label, entity=entity, base_field=base_field,
            slug=slugify(base_field), category=category,
            description="(user-confirmed field - no detailed description written yet)",
        )
    schema = KnownSchema(
        name=name, soc_entity=soc_entity, project_entities=list(project_entities),
        subgroup_entities=list(subgroup_entities), known_columns=known_columns,
    )
    _REGISTRY.append(schema)
    _save_persisted_schemas()
    return schema


# --- Structural, pattern-based sub-group detection ---
# Works regardless of how many PinMux/QUP-style register sub-tables a
# workbook has, or how they're numbered/named - detects the SHAPE
# (Port*/Instance*/RegSetting* fields present together), not a fixed list
# of names. This is what makes "PinMux01-08 vs PinMux01-10" a non-issue:
# it was never a fixed count to begin with, it's whatever entities
# actually have this shape in a given file.
def detect_subgroup_shaped_entities(entity_to_base_fields: dict[str, list[str]]) -> list[str]:
    detected = []
    for entity, base_fields in entity_to_base_fields.items():
        lowered = [f.lower() for f in base_fields]
        has_instance = any("instance" in f for f in lowered)
        has_regsetting = any("regsetting" in f for f in lowered)
        if has_instance and has_regsetting:
            detected.append(entity)
    return detected


# --- Keyword-based role suggestion for entities not caught by the
# structural subgroup check - SUGGESTION ONLY, never trusted without
# explicit human confirmation. Same precedence logic proven correct
# earlier in this project (project-hint phrases claim a field before the
# generic "soc" hints get a chance to look at it - this is what fixed the
# real IO_GPIO_PULL/"gpio" substring collision found on the real
# workbook, and that fix generalizes to any future file with a similarly
# named field).
_SOC_HINT_WORDS = ("ball", "power domain", "gpio", "id")
_PROJECT_HINT_WORDS = (
    "selected interface", "pinmux setting", "remarks", "sw driver",
    "reset value", "io_gpio_pull", "io_drv_strength", "gen5 signal",
)


def suggest_role(base_fields: list[str]) -> tuple[str, str]:
    """Returns (suggested_role, reason). Role is 'soc' | 'project' |
    'unknown'. This is a SUGGESTION for a human-confirmation UI - it is
    never applied automatically anywhere in the pipeline."""
    lowered = [f.lower() for f in base_fields]
    project_claimed = [f for f in lowered if any(w in f for w in _PROJECT_HINT_WORDS)]
    unclaimed = [f for f in lowered if f not in project_claimed]
    soc_claimed = [f for f in unclaimed if any(w in f for w in _SOC_HINT_WORDS)]

    project_hits = sorted({w for f in project_claimed for w in _PROJECT_HINT_WORDS if w in f})
    soc_hits = sorted({w for f in soc_claimed for w in _SOC_HINT_WORDS if w in f})

    if project_hits and not soc_hits:
        return "project", f"has project-style fields: {', '.join(project_hits)}"
    if soc_hits and not project_hits:
        return "soc", f"has chip-level fields: {', '.join(soc_hits)}"
    if soc_hits and project_hits:
        if len(project_claimed) >= len(soc_claimed):
            return "project", f"has both, leaning project ({len(project_claimed)} vs {len(soc_claimed)} fields)"
        return "soc", f"has both, leaning SoC ({len(soc_claimed)} vs {len(project_claimed)} fields)"
    return "unknown", "no strong signal from field names - please classify manually"
