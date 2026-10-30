"""
Converts a ParsedWorkbook into NormalizedRecord objects: one per
(project, worksheet row).

Each record merges:
  - SoC fields for that row (e.g. ball_id, ball_name, power_domain)
  - SoC sub-group fields folded in (PinMux01..08 -> port1..port8 etc.)
  - The project's own fields for that row (e.g. selected_interface, pinmux_setting)

Entity assignment defaults to the hardcoded schema (app/schema.py) but can
still be overridden by callers/tests that need a different grouping.

Also filters out embedded legend/lookup rows (marked via the _PARA_ROW
column - see app/schema.py for what its values mean) that aren't real
per-pin data and would otherwise become spurious records.
"""

from __future__ import annotations
from app.models import ColumnMeta, NormalizedRecord
from app.parsers.workbook_parser import ParsedWorkbook
from app.schema import SOC_ENTITY, PROJECT_ENTITIES, SUBGROUP_ENTITIES

# _PARA_ROW values seen in the real workbook that mark a row as an embedded
# legend/lookup table entry (decoding register values), NOT real pin data.
# "_END_ROW" is deliberately excluded - that row IS still real pin data,
# just also tagged as the last one.
_LEGEND_ROW_MARKERS = {"DRV_STRENGTH", "GPIO_PULL", "GPIO_OE", "GPIO_VALUE", "NA"}


def _cell(row_vals: list, col: ColumnMeta):
    v = row_vals[col.index]
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip()
        return v if v != "" else None
    return v


def normalize(pw: ParsedWorkbook, soc_entity: str = SOC_ENTITY,
              project_entities: list[str] = None,
              soc_subgroup_entities: list[str] = None) -> list[NormalizedRecord]:
    if project_entities is None:
        project_entities = PROJECT_ENTITIES
    if soc_subgroup_entities is None:
        soc_subgroup_entities = SUBGROUP_ENTITIES

    soc_cols = [c for c in pw.columns if c.entity == soc_entity and c.slug]
    subgroup_cols = [c for c in pw.columns if c.entity in soc_subgroup_entities and c.slug]
    project_cols_by_entity = {
        proj: [c for c in pw.columns if c.entity == proj and c.slug]
        for proj in project_entities
    }
    para_row_col = next((c for c in pw.columns if c.slug == "para_row"), None)

    records: list[NormalizedRecord] = []
    for row_idx, row_vals in enumerate(pw.raw_rows):
        if para_row_col is not None:
            marker = _cell(row_vals, para_row_col)
            if marker is not None and str(marker).strip() in _LEGEND_ROW_MARKERS:
                continue  # embedded legend/lookup row, not real pin data

        soc_fields: dict = {}
        for c in soc_cols + subgroup_cols:
            val = _cell(row_vals, c)
            if val is not None:
                # If the same slug appears twice (e.g. a subgroup reusing a
                # base field name), keep the first non-null and stash extras
                # under a suffixed key rather than silently overwriting.
                key = c.slug
                if key in soc_fields:
                    key = f"{c.entity.lower()}_{c.slug}"
                soc_fields[key] = val

        if not soc_fields:
            continue  # row has no SoC-identifying data at all - skip

        for project, cols in project_cols_by_entity.items():
            proj_fields: dict = {}
            for c in cols:
                val = _cell(row_vals, c)
                if val is not None:
                    proj_fields[c.slug] = val
            if not proj_fields:
                continue  # this project has nothing configured on this row
            merged = {**soc_fields, **proj_fields}
            records.append(NormalizedRecord(
                project=project,
                soc=soc_entity,
                row_index=row_idx,
                fields=merged,
            ))
    return records


# Fields worth extracting real vocabulary from - the ones a natural-language
# query is most likely to reference (interface type, functional grouping,
# signal name). Kept small deliberately: this becomes part of every LLM
# prompt, so it should stay a short, high-value list.
VOCABULARY_FIELDS = ("interface_group", "selected_interface")
_MAX_VALUES_PER_FIELD = 150  # safety cap - real files stay well under this


def extract_vocabulary(records: list[NormalizedRecord]
                        ) -> dict[str, list[str]]:
    """Returns {field_slug: sorted distinct real values} for the fields in
    VOCABULARY_FIELDS - an aggregate list only (not full rows), meant to be
    fed into LLM intent parsing so it knows the file's actual abbreviations
    and naming conventions (e.g. "BTWLAN" for Bluetooth/WLAN) instead of
    guessing from natural-language terms alone."""
    vocab: dict[str, set[str]] = {f: set() for f in VOCABULARY_FIELDS}
    for rec in records:
        for field_slug in VOCABULARY_FIELDS:
            val = rec.fields.get(field_slug)
            if val not in (None, ""):
                vocab[field_slug].add(str(val).strip())
    return {
        field_slug: sorted(values)[:_MAX_VALUES_PER_FIELD]
        for field_slug, values in vocab.items()
    }
