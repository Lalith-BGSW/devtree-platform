"""
Converts a workbook's already-parsed/normalized PinMux data into a
Markdown reference table for one project - the same table format
that was previously only produced by a standalone script
(scripts/export_workbook_to_md.py) run outside the app.

Deliberately reuses the app's OWN already-parsed ColumnMeta and
NormalizedRecord objects (from the session, after /build has already
run) rather than re-parsing the Excel file a second time - this is
strictly a formatting layer over data the app has already extracted
and schema-verified, not a second, independent parsing path that
could drift from the real one.

Columns produced: Ball Name, PinMuxed Name, Gen5 Signal Name,
Operating System/Domain, Direction, Reset Value, IO_GPIO_PULL,
IO_DRV_STRENGTH, Pinmux Setting, Port/Instance.
"""

from __future__ import annotations
import re

from app.models import ColumnMeta, NormalizedRecord

_DEFAULT_OS_DOMAIN = "Linux"

# Every field is matched by KEYWORDS against the real column base_field
# name (case-insensitive, whitespace/punctuation-insensitive), not a
# fixed slug - tolerates minor wording drift between workbook revisions
# ("Reset Value" vs "ResetValue", "Operating System/Domains" vs
# "OperatingSystem Domains") without a new hardcoded candidate needed
# every time a workbook is worded slightly differently. Confirmed real
# case both ways on two structurally different real workbooks.
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


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def resolve_field_slugs(project_slugs: set[str], slug_to_base_field: dict[str, str],
                         soc_entity: str | None) -> dict[str, str]:
    """Resolves each concept to the one matching slug. Excludes
    register-address ("REG_...") and display-string ("STR_...")
    duplicate columns, which would otherwise match the same keywords as
    the real value field and silently win by column order (confirmed
    real case: REG_IO_GPIO_PULL held a register address, not a pull
    state). For "ball_name" specifically: if multiple candidates exist
    (confirmed real case: SA8155P has five ball-name columns, one per
    package/revision variant), excludes any that reference a DIFFERENT
    chip's number than this workbook's own SoC, then prefers a
    "soc"-named variant among what's left, falling back to whichever
    was found first."""
    soc_code_m = re.search(r"\d{3,}", soc_entity) if soc_entity else None
    soc_code = soc_code_m.group() if soc_code_m else None

    resolved: dict[str, str] = {}
    for concept, keywords in _FIELD_KEYWORD_RULES.items():
        candidates = []
        for slug, base_field in slug_to_base_field.items():
            if slug not in project_slugs:
                continue
            normalized = _normalize_name(base_field)
            if normalized.startswith("reg") or normalized.startswith("str"):
                continue
            if all(kw in normalized for kw in keywords):
                candidates.append((slug, normalized, base_field))

        if concept == "ball_name" and soc_code:
            filtered = []
            for slug, norm, base_field in candidates:
                other_code_m = re.search(r"\d{3,}", base_field)
                if other_code_m and other_code_m.group() != soc_code:
                    continue
                filtered.append((slug, norm, base_field))
            candidates = filtered

        if not candidates:
            continue

        if concept == "ball_name":
            soc_candidates = [slug for slug, norm, _ in candidates if "soc" in norm]
            resolved[concept] = soc_candidates[0] if soc_candidates else candidates[0][0]
        else:
            resolved[concept] = candidates[0][0]
    return resolved


def resolve_pinmux_group_ports(fields: dict, pinmux_setting) -> str:
    """Given this row's own Pinmux Setting value, pulls the matching
    Port/Instance fields from the SAME row (all subgroup columns are
    already merged onto every record by normalize()). Two real shapes,
    both handled the same way conceptually: "Q<N>" (e.g. "Q3") ->
    qupport{N}/instancequp{N}; a bare number (e.g. "3") -> port{N}/
    instance{N}."""
    if not pinmux_setting:
        return ""
    setting = str(pinmux_setting).strip()
    q_match = re.match(r"^Q(\d+)$", setting, re.IGNORECASE)
    if q_match:
        n = q_match.group(1)
        port, instance = fields.get(f"qupport{n}"), fields.get(f"instancequp{n}")
    elif setting.isdigit():
        n = setting
        port, instance = fields.get(f"port{n}"), fields.get(f"instance{n}")
    else:
        return ""
    port = str(port).strip() if port is not None else ""
    instance = str(instance).strip() if instance is not None else ""
    if not port and not instance:
        return ""
    return f"{port} / {instance}"


def _md_escape(value) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def build_project_markdown(project: str, soc_entity: str | None,
                            records: list[NormalizedRecord],
                            columns: list[ColumnMeta]) -> tuple[str, list[str]]:
    """Returns (markdown_text, unresolved_concepts) - the caller decides
    whether/how to surface unresolved concepts (e.g. as a warning in the
    API response), rather than this function only printing to a
    console the way the standalone script did."""
    slug_to_base_field = {c.slug: c.base_field for c in columns if c.slug and c.base_field}
    project_slugs = {c.slug for c in columns if c.slug and c.entity == project} | \
        {c.slug for c in columns if c.slug and c.entity == soc_entity} | \
        {c.slug for c in columns if c.slug and c.category == "pinmux_register"}

    field_slugs = resolve_field_slugs(project_slugs, slug_to_base_field, soc_entity)
    unresolved = [c for c in _FIELD_KEYWORD_RULES if c not in field_slugs]

    lines = [
        f"# {project} - PinMux Database Export",
        "",
        f"{len(records)} row(s) from the Database sheet.",
        "",
        "| Ball Name | PinMuxed Name | Gen5 Signal Name | Operating System/Domain | Direction | "
        "Reset Value | IO_GPIO_PULL | IO_DRV_STRENGTH | Pinmux Setting | Port / Instance |",
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
        qup_ports = resolve_pinmux_group_ports(f, pinmux_setting)

        lines.append(
            "| " + " | ".join(_md_escape(v) for v in [
                ball_name, pinmuxed_name, gen5_signal, os_domain, direction, reset_value,
                io_pull, io_drv, pinmux_setting, qup_ports,
            ]) + " |"
        )
    return "\n".join(lines), unresolved
