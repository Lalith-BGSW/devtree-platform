"""
Extracts the subset of a project's records whose Gen5 Signal Name
matches a given keyword (e.g. "usb") - per mentor guidance, matching
is a substring match against Gen5 Signal Name specifically, not
Interface Group or Selected Interface.

This is the shared filter both stages of the two-stage UI use: the
Markdown summary stage and the scoped-DTS-generation stage call the
SAME function with the SAME keyword, so the two stages are always
looking at exactly the same set of pins - no risk of the DTS stage
silently covering a different set than what the MD summary showed.
"""

from __future__ import annotations
import re

from app.models import ColumnMeta, NormalizedRecord

_GEN5_KEYWORD_RULE = ["gen5", "signal"]


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def resolve_gen5_signal_slug(project: str, soc_entity: str | None, columns: list[ColumnMeta]) -> str | None:
    """Finds the Gen5 Signal Name column's slug for this project, the
    same keyword-matching approach used throughout this project's other
    field resolution (tolerant of minor wording drift), excluding
    register-address/display-string duplicate columns."""
    candidates = []
    for c in columns:
        if not c.slug or not c.base_field:
            continue
        if not (c.entity == project or c.entity == soc_entity):
            continue
        normalized = _normalize_name(c.base_field)
        if normalized.startswith("reg") or normalized.startswith("str"):
            continue
        if all(kw in normalized for kw in _GEN5_KEYWORD_RULE):
            candidates.append(c.slug)
    return candidates[0] if candidates else None


def related_group_mates(all_records, matched, ig_slug, limit: int = 40):
    """Rows that share an Interface Group with a matched row but were NOT matched by the keyword - e.g. an IMU's chip-select
    (`IMU_CS_B`) when searching "spi". A SUGGESTION list for a person to confirm, never auto-included: in the same group there
    are also unrelated buses (a tuner's I2S pins next to its SPI)."""
    if not ig_slug:
        return []
    norm = lambda v: re.sub(r"\s+", " ", str(v or "")).strip()
    ids = {id(r) for r in matched}
    groups = {norm(r.fields.get(ig_slug)) for r in matched} - {""}
    return [r for r in all_records if id(r) not in ids and norm(r.fields.get(ig_slug)) in groups][:limit]


def build_related_markdown(mates, gen5_slug, ig_slug, keyword: str) -> str:
    from app.generators.dts_generator import _first_pin_identifier
    rows = ["| Pin | Gen5 Signal Name | Interface Group |", "|---|---|---|"]
    for r in mates:
        sig = re.sub(r"\s+", " ", str(r.fields.get(gen5_slug) or "-")).replace("|", "/")
        grp = re.sub(r"\s+", " ", str(r.fields.get(ig_slug) or "-")).replace("|", "/")
        rows.append(f"| {_first_pin_identifier(r.fields) or '-'} | {sig} | {grp} |")
    return (f"### Related pins - same Interface Group, NOT matched by `{keyword}` (not in the DTS)\n\n"
            f"These share an Interface Group with the matched pins but their signal names do not contain the keyword. Some may belong "
            f"to the peripheral (a chip-select, an interrupt of the same device); others are a different bus of the same device. "
            f"If one belongs, search again with a word from its signal name.\n\n" + "\n".join(rows) + "\n")


def filter_records_by_gen5_substring(
    records: list[NormalizedRecord], keyword: str, gen5_slug: str,
) -> list[NormalizedRecord]:
    """Case-insensitive substring match of `keyword` against each
    record's Gen5 Signal Name value. A record with no value for that
    field, or whose value doesn't contain the keyword, is excluded -
    never guessed into the match set."""
    needle = keyword.strip().lower()
    matched = []
    for r in records:
        val = r.fields.get(gen5_slug)
        if val is not None and needle in str(val).lower():
            matched.append(r)
    return matched


def _resolve_field_slugs_for_summary(project: str, soc_entity: str | None, columns: list[ColumnMeta]) -> dict[str, str]:
    """Resolves the summary table's columns the same keyword-matching
    way as the Gen5 slug above - tolerant of workbook-to-workbook
    wording drift, excludes REG_/STR_ duplicate columns."""
    rules = {
        "ball_name": ["ball", "name"],
        "pinmuxed_name": ["pinmuxed", "name"],
        "interface_group": ["interface", "group"],
        "direction": ["direction"],
        "io_gpio_pull": ["gpio", "pull"],
        "io_drv_strength": ["drv", "strength"],
    }
    resolved: dict[str, str] = {}
    for concept, keywords in rules.items():
        candidates = []
        for c in columns:
            if not c.slug or not c.base_field:
                continue
            if not (c.entity == project or c.entity == soc_entity):
                continue
            normalized = _normalize_name(c.base_field)
            if normalized.startswith("reg") or normalized.startswith("str"):
                continue
            if all(kw in normalized for kw in keywords):
                candidates.append(c.slug)
        if candidates:
            resolved[concept] = candidates[0]
    return resolved


def build_peripheral_summary_markdown(
    keyword: str, project: str, matched: list[NormalizedRecord],
    gen5_slug: str, columns: list[ColumnMeta], soc_entity: str | None,
) -> str:
    """Renders the matched pins as a Markdown table - the first-stage
    output shown before any DTS is generated. Columns: Ball Name,
    PinMuxed Name, Gen5 Signal Name, Interface Group, Direction,
    IO_GPIO_PULL, IO_DRV_STRENGTH."""
    field_slugs = _resolve_field_slugs_for_summary(project, soc_entity, columns)

    lines = [
        f"# {project} - \"{keyword}\" pins (matched on Gen5 Signal Name)",
        "",
        f"{len(matched)} pin(s) matched.",
        "",
        "| Ball Name | PinMuxed Name | Gen5 Signal Name | Interface Group | Direction | IO_GPIO_PULL | IO_DRV_STRENGTH |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in matched:
        f = r.fields

        def get(concept):
            slug = field_slugs.get(concept)
            val = f.get(slug) if slug else None
            return str(val).strip() if val is not None and str(val).strip() else ""

        gen5 = str(f.get(gen5_slug, "")).strip()
        row = [get("ball_name"), get("pinmuxed_name"), gen5, get("interface_group"),
               get("direction"), get("io_gpio_pull"), get("io_drv_strength")]
        escaped = [c.replace("|", "\\|").replace("\n", " ") for c in row]
        lines.append("| " + " | ".join(escaped) + " |")
    return "\n".join(lines)
