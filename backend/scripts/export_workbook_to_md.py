"""
Converts the PinMux workbook's Database sheet into a Markdown file per
project, with exactly the columns requested:

  1. Ball name
  2. Gen5 Signal name
  3. Operating Systems/domain (defaults to "Linux" if the workbook has
     no value for that row)
  4. Direction
  5. Reset Value
  6. IO_GPIO Pull
  7. IO_Drive Strength
  8. Pinmux Setting (e.g. "Q1")
  9. QUPort / Instance for that Pinmux Setting - resolved dynamically:
     "Q1" -> qupport1/instancequp1, "Q4" -> qupport4/instancequp4, etc.
     Left blank if the row's Pinmux Setting isn't a "Q<number>" value
     (nothing else to resolve in that case).

Reuses the already-verified parsing/normalization pipeline
(app.parsers.workbook_parser, app.parsers.normalizer) rather than
re-reading the Excel file independently - this is a formatting layer
over data that's already been extracted and schema-verified, not a
second, separate parsing path that could drift from the real one.

Includes EVERY row for each project, non-signal (ground/power/no-connect)
rows included - this is a full reference dump of the Database sheet's
content, not a DTS-ready filtered view (same principle as the JSON
export feature: raw extracted data, not post-classification content).

Usage:
    python export_workbook_to_md.py <path-to-workbook.xlsx-or-.xlsm> [output_dir]
"""
from __future__ import annotations
import sys
import os
import re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.parsers.workbook_parser import parse_workbook, build_analysis, WorkbookParseError
from app.schema_registry import confirm_new_schema, match_known_schema
from app.schema import _slug
from app.parsers.normalizer import normalize
from app.models import NormalizedRecord

_DEFAULT_OS_DOMAIN = "Linux"

# Every field is matched by KEYWORDS against the real column base_field
# name (case-insensitive, whitespace/punctuation-insensitive), not a
# fixed slug - this is what makes the script tolerate minor naming
# drift between workbook revisions ("Reset Value" vs "ResetValue",
# "Operating System/Domains" vs "OperatingSystem Domains", a chip-
# specific prefix on the ball-name column) without needing a new
# hardcoded candidate added by hand every time a workbook is worded
# slightly differently. All keywords in a rule must appear (as
# substrings, after stripping spaces/underscores/punctuation) for a
# column to match; the first matching column found is used. This does
# NOT attempt to handle a genuinely different KIND of field (e.g. no
# ball-name-like column at all) - that's a structural difference, not a
# naming variation, and still needs a human to look at the real column
# list.
_FIELD_KEYWORD_RULES: dict[str, list[str]] = {
    "ball_name": ["ball", "name"],
    "pinmuxed_name": ["pinmuxed", "name"],
    "gen5_signal_name": ["gen5", "signal"],
    "os_domain": ["operating", "domain"],
    "direction": ["direction"],
    "reset_value": ["reset", "value"],
    "io_gpio_pull": ["gpio", "pull"],
    "io_drv_strength": ["drv", "strength"],
    "pinmux_setting": ["pinmux", "setting"],
}


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def resolve_field_slugs(project_slugs: set[str], slug_to_base_field: dict) -> dict[str, str]:
    """Resolves each concept in _FIELD_KEYWORD_RULES to the one matching
    slug. Matched against the COLUMN SCHEMA (which slugs exist for this
    project) - NOT against whether some sample row happens to have a
    non-blank value, since a genuinely-empty-on-every-row column (a real
    case: Iveco's Operating System/Domain column) would otherwise look
    indistinguishable from a column that doesn't exist at all. Register-
    address reference fields (e.g. "REG_IO_GPIO_PULL", confirmed real
    case on the SA7255P SoC entity) are explicitly excluded - they'd
    otherwise match the same keywords as the real value field they're
    named after and silently win by column order, producing a
    register-address string where a real pull/drive-strength value was
    expected.

    For "ball_name" specifically: some workbooks have MULTIPLE ball-name
    columns (confirmed real case: SA8155P has five - SIP, SoC, two SIP
    package revisions, and a pre-release variant), while others have
    only one (confirmed real case: SA7255P/Iveco). Picking "whichever
    matches first" would work by ACCIDENT of column order alone, not by
    any real rule - fragile if a future workbook happens to list a
    different variant first. Instead, "soc" is explicitly preferred when
    present - the primary chip identifier rather than a package/
    revision-specific one - falling back to whatever ball-name-like
    column exists when there's no SoC variant to choose from (correctly
    resolves Iveco's single column either way).

    Returns concept -> slug; a concept with no match is simply absent
    from the result (caller treats that as "no data for this column")."""
    resolved: dict[str, str] = {}
    for concept, keywords in _FIELD_KEYWORD_RULES.items():
        candidates = []
        for slug, base_field in slug_to_base_field.items():
            if slug not in project_slugs:
                continue
            normalized = _normalize(base_field)
            if normalized.startswith("reg") or normalized.startswith("str"):
                continue  # register-address/display-string duplicate, not the real value field
            if all(kw in normalized for kw in keywords):
                candidates.append((slug, normalized))

        if not candidates:
            continue

        if concept == "ball_name":
            soc_candidates = [slug for slug, norm in candidates if "soc" in norm]
            resolved[concept] = soc_candidates[0] if soc_candidates else candidates[0][0]
        else:
            resolved[concept] = candidates[0][0]
    return resolved


def _resolve_pinmux_group_ports(fields: dict, pinmux_setting: str | None) -> str:
    """Given this row's own Pinmux Setting value, pulls the matching
    Port/Instance fields from the SAME row (all subgroup columns are
    already merged onto every record by normalize() - no separate
    lookup into a different row/table needed). Two real shapes exist in
    the workbook, both handled the same way conceptually:
      - "Q<N>" (e.g. "Q3")  -> qupport{N} / instancequp{N}  (PinmuxQN group)
      - a bare number (e.g. "3") -> port{N} / instance{N}   (PinMux0N group)
    Returns a combined "Port / Instance" string, or "" if the setting
    doesn't match either shape."""
    if not pinmux_setting:
        return ""
    setting = str(pinmux_setting).strip()

    q_match = re.match(r"^Q(\d+)$", setting, re.IGNORECASE)
    if q_match:
        n = q_match.group(1)
        port = fields.get(f"qupport{n}")
        instance = fields.get(f"instancequp{n}")
    elif setting.isdigit():
        n = setting
        port = fields.get(f"port{n}")
        instance = fields.get(f"instance{n}")
    else:
        return ""

    port = str(port).strip() if port is not None else ""
    instance = str(instance).strip() if instance is not None else ""
    if not port and not instance:
        return ""
    return f"{port} / {instance}"


def _md_escape(value) -> str:
    """Escapes pipe characters so a cell's own content can never break
    the Markdown table's column structure."""
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def build_project_markdown(project: str, records: list[NormalizedRecord],
                            field_slugs: dict[str, str]) -> str:
    lines = [
        f"# {project} - PinMux Database Export",
        "",
        f"{len(records)} row(s) from the Database sheet.",
        "",
        "| Ball Name | PinMuxed Name | Gen5 Signal Name | Operating System/Domain | Direction | "
        "Reset Value | IO_GPIO_PULL | IO_DRV_STRENGTH | Pinmux Setting | "
        "Port / Instance |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in records:
        f = r.fields

        def get(concept):
            slug = field_slugs.get(concept)
            val = f.get(slug) if slug else None
            return str(val).strip() if val is not None and str(val).strip() else None

        ball_name = get("ball_name")
        pinmuxed_name = get("pinmuxed_name")
        gen5_signal = get("gen5_signal_name")
        os_domain = get("os_domain") or _DEFAULT_OS_DOMAIN
        direction = get("direction")
        reset_value = get("reset_value")
        io_pull = get("io_gpio_pull")
        io_drv = get("io_drv_strength")
        pinmux_setting = get("pinmux_setting")
        qup_ports = _resolve_pinmux_group_ports(f, pinmux_setting)

        lines.append(
            "| " + " | ".join(_md_escape(v) for v in [
                ball_name, pinmuxed_name, gen5_signal, os_domain, direction, reset_value,
                io_pull, io_drv, pinmux_setting, qup_ports,
            ]) + " |"
        )
    return "\n".join(lines)


def main():
    if len(sys.argv) < 2:
        print("Usage: python export_workbook_to_md.py <workbook.xlsx-or-.xlsm> [output_dir]")
        sys.exit(1)

    workbook_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else "."
    os.makedirs(output_dir, exist_ok=True)

    try:
        parsed = parse_workbook(workbook_path)
    except WorkbookParseError as e:
        print(f"Failed to parse workbook: {e}")
        sys.exit(1)

    found_labels = {c.field_label for c in parsed.columns if c.field_label}
    matched = match_known_schema(found_labels)

    if matched is not None:
        soc_entity = matched.soc_entity
        project_entities = list(matched.project_entities)
        subgroup_entities = list(matched.subgroup_entities)
        print(f"Matched known schema: {matched.name}")
        # build_analysis() is what actually writes slug/category/known
        # onto each column - parse_workbook() alone leaves those unset,
        # and normalize() silently produces zero records without them
        # (confirmed real gotcha, hit earlier in this same project).
        for col in parsed.columns:
            known_def = matched.known_columns.get(col.field_label) if col.field_label else None
            if known_def:
                col.base_field = known_def.base_field
                col.entity = known_def.entity
                col.slug = known_def.slug
                col.category = known_def.category
                col.known = True
    else:
        # New/unrecognized structure - this script doesn't do interactive
        # classification (that's the app's job); it uses the structural
        # suggestions directly since a standalone script has no UI to
        # confirm them in. Review the printed roles below before trusting
        # the output for a genuinely new workbook.
        print("No known schema matched - using structural suggestions "
              "(review these before trusting the output):")
        analysis = build_analysis(parsed)
        role_by_entity = {e.name: e.role for e in analysis.entities}
        category_by_role = {"soc": "identity", "project": "project_config", "subgroup": "pinmux_register"}
        columns_by_label = {}
        for col in parsed.columns:
            if not col.field_label or not col.entity or not col.base_field:
                continue
            role = role_by_entity.get(col.entity)
            if role is None:
                continue
            columns_by_label[col.field_label] = (col.entity, col.base_field, category_by_role[role])
        soc_entity = [e for e, r in role_by_entity.items() if r == "soc"][0]
        project_entities = [e for e, r in role_by_entity.items() if r == "project"]
        subgroup_entities = [e for e, r in role_by_entity.items() if r == "subgroup"]
        for e in analysis.entities:
            print(f"  {e.name}: {e.role}")
        confirm_new_schema(
            name=f"{os.path.basename(workbook_path)} (script-suggested)",
            soc_entity=soc_entity, project_entities=project_entities,
            subgroup_entities=subgroup_entities, columns_by_label=columns_by_label,
        )
        # Assign slug/category/known directly from columns_by_label (the
        # exact same data confirm_new_schema() just registered) rather
        # than trying to re-fetch via match_known_schema() - that lookup
        # requires an EXACT full-label-set match, but columns_by_label
        # only includes classified entities (columns from any entity
        # left as "unknown" are excluded), so it's always a subset of
        # found_labels and would never match itself back.
        for col in parsed.columns:
            if col.field_label in columns_by_label:
                entity, base_field, category = columns_by_label[col.field_label]
                col.base_field = base_field
                col.entity = entity
                col.slug = _slug(base_field)
                col.category = category
                col.known = True

    records = normalize(parsed, soc_entity=soc_entity, project_entities=project_entities,
                         soc_subgroup_entities=subgroup_entities)

    # Global slug -> base_field map (across every entity - SoC, project,
    # subgroups) so the keyword resolver can find a match regardless of
    # which entity a concept's real column happens to belong to.
    slug_to_base_field = {
        col.slug: col.base_field for col in parsed.columns
        if col.slug and col.base_field
    }

    for project in project_entities:
        proj_records = [r for r in records if r.project == project]
        if not proj_records:
            print(f"Wrote nothing for {project!r} - zero records.")
            continue
        # Which slugs genuinely exist as COLUMNS for this project (SoC +
        # this project + subgroups) - checked against the schema, not
        # against whether a sample row happens to have a non-blank
        # value, since an always-blank real column must still resolve.
        project_slugs = {
            col.slug for col in parsed.columns
            if col.slug and (col.entity == soc_entity or col.entity == project or col.entity in subgroup_entities)
        }
        field_slugs = resolve_field_slugs(project_slugs, slug_to_base_field)
        unresolved = [c for c in _FIELD_KEYWORD_RULES if c not in field_slugs]
        if unresolved:
            print(f"  [{project}] Could not find a matching column for: {', '.join(unresolved)} "
                  f"- these will be blank/defaulted in the output. Check the real column names "
                  f"if this is unexpected.")
        md_text = build_project_markdown(project, proj_records, field_slugs)
        safe_name = re.sub(r"[^a-zA-Z0-9]+", "_", project.lower()).strip("_")
        out_path = os.path.join(output_dir, f"{safe_name}_database_export.md")
        with open(out_path, "w") as f:
            f.write(md_text)
        print(f"Wrote {out_path} ({len(proj_records)} rows)")


if __name__ == "__main__":
    main()