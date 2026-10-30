"""
Deterministic Device Tree generation, matching the STYLE of the real
reference file (sa8155p-oem-aivi2-ccs20-common-pinmux.dtsi) - not its
content. Per explicit confirmation: the sample is used only to learn
structural/format conventions, never for verbatim reuse or diffing
against workbook data.

Real conventions this replicates (see backend README / conversation
history for the analysis that established these):
  - Root wrapper is `&tlmm { ... }`, not `&soc { pinctrl@0 { ... } }`
  - Each pin group is a flat top-level node: `label: label { mux{}; config{}; };`
  - mux{} has pins+function only; config{} has pins + drive-strength +
    bias-pull-*/bias-disable, plus a direction directive (input-enable/
    output-low/output-high) ONLY for plain-GPIO-function nodes - QUP-muxed
    nodes (uart/i2c/spi) don't carry a direction directive in the sample.
  - Multi-pin interfaces split into separate nodes by DIRECTION (out-going
    signals like RTS+TXD together; in-coming signals like CTS+RXD
    together) rather than one merged node per interface - confirmed
    against the real UART example (se13: two nodes, not one).
  - drive-strength/bias are parsed directly from the workbook's own
    IO_DRV_STRENGTH/IO_GPIO_PULL fields, which already store decoded
    strings like "0: DRV_2_MA" / "3: PULL_UP" - confirmed against real
    query output. When a record lacks these fields or the value doesn't
    parse, the node is marked NEEDS REVIEW rather than guessing.

Known gap (not implemented): the sample shows some pins have separate
_active/_sleep states (same pins, different mux function for low-power).
The workbook has no distinct "sleep configuration" column, so only the
primary/active state is generated - documented, not silently ignored.
"""

from __future__ import annotations
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.models import NormalizedRecord
from app.constants import is_non_signal_value, classify_pin
from app.schema_registry import find_schema_by_soc_entity

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(disabled_extensions=(".j2",)),
    trim_blocks=True,
    lstrip_blocks=True,
)

# Exact-match candidates checked first; substring candidates handle the
# real workbook's chip-revision-prefixed ball-name variants.
_PIN_EXACT_CANDIDATES = ("pinmuxed_name", "gpio", "pin_id", "pin_name")
_PIN_SUBSTRING_CANDIDATES = ("ball_name", "ball_id")

_INTERFACE_CANDIDATES = ("selected_interface", "interface", "interface_group")
_SIGNAL_CANDIDATES = ("gen5_signal_name", "signal", "signal_name", "pinmuxed_name")
_DIRECTION_CANDIDATES = ("direction",)
_DRIVE_STRENGTH_CANDIDATES = ("io_drv_strength",)
_BIAS_CANDIDATES = ("io_gpio_pull",)

_TYPE_KEYWORDS = {
    "uart": ("uart",),
    "i2c": ("i2c", "i²c"),
    "spi": ("spi",),
    "gpio": ("gpio",),
}

# Maps the decoded LABEL portion of an IO_GPIO_PULL value (e.g. "PULL_UP"
# from "3: PULL_UP") to the real devicetree bias directive. Matched on the
# label text, not the leading digit, since digit meaning isn't confirmed
# independent of the label.
_BIAS_LABEL_MAP = {
    "PULL_UP": "bias-pull-up",
    "PULL_DOWN": "bias-pull-down",
    "NO_PULL": "bias-disable",
    "DISABLE": "bias-disable",
}


def _first(fields: dict, candidates: tuple[str, ...]) -> str | None:
    for k in candidates:
        if k in fields and fields[k] not in (None, ""):
            return str(fields[k])
    return None


def _first_pin_identifier_raw(fields: dict) -> str | None:
    """Returns the identifier to use for the DTS `pins = "..."` value -
    NOT a display/label name. Real DTS pin references are the SoC's raw
    ball identity (e.g. 'gpio10'), never the project's friendly label -
    confirmed against the real sample AND against a real mismatch this
    caused: the workbook's PinMuxed Name for GPIO10 is 'DP_HOT_PLUG_DETECT'
    (a human label), but the real DTS still writes `pins = "gpio10"`.

    Checks ball_NAME fields first specifically (not ball_id): a field like
    'sa8155sip_ball_name' holds the GPIO-style identity ('GPIO_43'), while
    'sa8155sip_ball_id' holds a physical grid coordinate ('AF12') - not a
    GPIO name at all.

    Skips any ball_name/ball_id value that is ITSELF a non-signal marker
    before accepting it - the workbook has five ball-name variant columns
    (SA8155SIP/SA8195SIP/SA8155SoC/SiPV1/PRE-SiPV2, one per chip package
    revision) and they can genuinely disagree about a given physical row:
    confirmed real case where sa8155sip_ball_name='DNC' but
    sa8155soc_ball_name='REFGEN_REXT0' for the SAME row - a real,
    classified-as-signal pin. Taking whichever ball_name field happened to
    come first in column order (without checking its value) returned
    'DNC' as the rendered pin name for a real signal pin. Now scans ALL
    ball_name variants for one that isn't itself a marker before falling
    back to PinMuxed Name, and only accepts a marker value as an
    absolute last resort (still better than returning nothing)."""
    for key, val in fields.items():
        if val not in (None, "") and "ball_name" in key and not is_non_signal_value(val):
            return str(val)
    for key, val in fields.items():
        if val not in (None, "") and "ball_id" in key and not is_non_signal_value(val):
            return str(val)
    exact = _first(fields, _PIN_EXACT_CANDIDATES)
    if exact is not None:
        return exact
    # Every ball_name/ball_id variant was itself a non-signal marker (or
    # absent), and there's no PinMuxed Name either - fall back to
    # whatever ball_name/ball_id exists at all, marker or not, since
    # returning something is still better than nothing for display.
    for key, val in fields.items():
        if val not in (None, "") and ("ball_name" in key or "ball_id" in key):
            return str(val)
    return None


def _first_pin_identifier(fields: dict) -> str | None:
    """The pin identity for `pins = "..."`. A row whose ball name is an alias (e.g. DP0_HPD, a dual-bond of GPIO_10) but whose
    workbook register is TLMM_GPIO_CFG10 is TLMM pin gpio10: the register index is the authoritative identity."""
    name = _first_pin_identifier_raw(fields)
    if name and not re.fullmatch(r"GPIO[_\-]?\d+", name.split("/", 1)[0].strip(), re.IGNORECASE):
        for key, value in fields.items():
            if key.startswith("regsetting") and value:
                m = re.match(r"^TLMM_GPIO_CFG(\d+)\b", str(value).strip(), re.IGNORECASE)
                if m:
                    return f"GPIO_{m.group(1)}"
    return name


def _to_dts_pin_name(pin_id: str) -> str:
    """The workbook writes pin names like 'GPIO43'; the real DTS format
    uses lowercase 'gpio43'. Falls back to a slugified form for
    non-GPIO-style identifiers (ball names, DNC markers etc. are filtered
    out before this is called, so this mainly normalizes casing).

    Some workbook pin names list multiple aliases separated by '/' (e.g.
    'PRI_MI2S_DATA0/PCM1_DIN/TDM1_DATA0' - the same physical pin under
    three different peripheral names). Previously these were mashed into
    one unreadable token; now the first alias is used as the pin's
    primary name, matching the real sample's style of a single compact
    lowercase identifier per pin (e.g. 'sdc2cmd')."""
    pin_id = pin_id.strip()
    if "/" in pin_id:
        pin_id = pin_id.split("/", 1)[0].strip()
    m = re.match(r"^GPIO[_\-]?(\d+)$", pin_id, re.IGNORECASE)
    if m:
        return f"gpio{m.group(1)}"
    return re.sub(r"[^a-zA-Z0-9]+", "", pin_id).lower()


def _classify(interface_value: str) -> str:
    v = interface_value.lower()
    for node_type, keywords in _TYPE_KEYWORDS.items():
        if any(kw in v for kw in keywords):
            return node_type
    return "generic"


def _extract_qup_number(fields: dict) -> str | None:
    """Looks for a QUP instance number (e.g. 'HSUART_QUP13' -> '13') across
    the fields most likely to carry it. Used to build the real-style
    'qupv3_seN_...' node name; falls back to a generic slug when absent."""
    for key in ("selected_interface", "pinmux_setting", "interface_group"):
        val = fields.get(key)
        if not val:
            continue
        m = re.search(r"QUP\s*0*(\d+)", str(val), re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def _parse_direction_label(raw: str | None) -> str | None:
    """Real workbook format is 'N: LABEL' (e.g. '0: IN', '1: OUT') - same
    convention as IO_DRV_STRENGTH/IO_GPIO_PULL. Returns 'in'/'out'/None."""
    if not raw:
        return None
    label = raw.split(":", 1)[-1].strip().upper()
    if "IN" in label and "OUT" not in label:
        return "in"
    if "OUT" in label:
        return "out"
    return None


def _direction_bucket(direction: str | None) -> str:
    """Groups by direction the same way the real sample does: RTS/TXD
    (outgoing) share a node, CTS/RXD (incoming) share a separate node.
    Falls back to 'na' for records with no parseable direction, so they
    still form a valid (if singleton) group rather than crashing."""
    parsed = _parse_direction_label(direction)
    return parsed or "na"


def _parse_drive_strength(raw: str | None) -> tuple[str | None, bool]:
    """'0: DRV_2_MA' -> '2'. Returns (value_or_None, needs_review)."""
    if not raw:
        return None, True
    m = re.search(r"DRV[_\s]*(\d+)[_\s]*MA", raw, re.IGNORECASE)
    if m:
        return m.group(1), False
    return None, True


def _parse_bias(raw: str | None) -> tuple[str | None, bool]:
    """'3: PULL_UP' -> 'bias-pull-up'. Returns (directive_or_None, needs_review)."""
    if not raw:
        return None, True
    label = raw.split(":", 1)[-1].strip().upper().replace(" ", "_")
    for key, directive in _BIAS_LABEL_MAP.items():
        if key in label:
            return directive, False
    return None, True


def _node_name(interface_value: str, node_type: str, direction_bucket: str,
               project: str, fields: dict) -> str:
    # Sanitized the same way func_slug already is below - a project name
    # is free-text from the workbook (e.g. "Iveco CDC B1", with spaces),
    # not guaranteed to already be a valid DTS identifier. Confirmed real
    # bug: CCS20/POC_NESSY2 have no spaces, so this was never exercised
    # before a genuinely different project name surfaced it - unsanitized
    # spaces in a node label produce syntactically INVALID devicetree
    # source that would fail to compile in any real dtc toolchain.
    project_suffix = re.sub(r"[^a-z0-9]+", "_", project.lower()).strip("_") or "project"
    qup_num = _extract_qup_number(fields)
    if qup_num and node_type in ("uart", "i2c", "spi"):
        base = f"qupv3_se{qup_num}_{node_type}"
    else:
        func_slug = re.sub(r"[^a-z0-9]+", "_", interface_value.lower()).strip("_") or "gpio"
        base = func_slug
    if direction_bucket in ("in", "out"):
        base = f"{base}_{direction_bucket}"
    return f"{base}_{project_suffix}"


def group_records_by_node(records: list[NormalizedRecord]
                           ) -> tuple[dict[tuple[str, str], list[NormalizedRecord]],
                                      list[NormalizedRecord]]:
    """Groups by (interface_value, direction_bucket) - matching the real
    sample's pattern of splitting one interface into separate out-going
    and in-coming pin groups, rather than one merged node per interface.

    Returns (groups, ambiguous_records). Non-signal records (grounds/
    power/no-connects) are excluded entirely and never returned in either
    value. Ambiguous records (no usable interface/pin data at all - see
    classify_pin()) are NOT included in groups and are NOT silently
    dropped either - they're returned separately so the caller can
    surface them for human review, per the "never silently guess" policy.
    """
    groups: dict[tuple[str, str], list[NormalizedRecord]] = defaultdict(list)
    ambiguous: list[NormalizedRecord] = []
    for rec in records:
        iface = _first(rec.fields, _INTERFACE_CANDIDATES)
        # Classification uses the PROJECT's own PinMuxed Name specifically -
        # NOT _first_pin_identifier()'s full fallback chain, which also
        # falls back to the SoC's physical ball name/ID. A ball name is a
        # chip-level fact that exists for every real pin regardless of
        # whether THIS project configured it - using it as classification
        # evidence would make almost nothing ever count as ambiguous,
        # defeating the point. Ball-name fallback is still correct and
        # used elsewhere for generating a DISPLAY pin name once a record
        # is already confirmed to be a real signal.
        pinmuxed_name = rec.fields.get("pinmuxed_name")
        interface_group = rec.fields.get("interface_group")
        classification = classify_pin(iface, pinmuxed_name, interface_group)

        if classification == "non_signal":
            continue
        if classification == "ambiguous":
            ambiguous.append(rec)
            continue

        direction = _first(rec.fields, _DIRECTION_CANDIDATES)
        bucket = _direction_bucket(direction)
        groups[(iface or "unassigned", bucket)].append(rec)
    return groups, ambiguous


@dataclass
class DerivedPinConfig:
    """The canonical DTS-side properties for ONE Excel record, derived
    exactly once so generation and updating never disagree with each
    other about what a given record's correct config should be."""
    dts_pin_name: str
    interface_value: str
    node_type: str
    function: str
    drive_strength: str | None
    drive_strength_needs_review: bool
    bias_directive: str | None
    bias_needs_review: bool
    direction_directive: str | None  # only meaningful when node_type == "gpio"
    ai_assisted_bias: bool = False  # True if bias came from a schema's AI-assisted decode map, not the verified SA8155P "N: LABEL" parser
    ai_assisted_drive_strength: bool = False
    output_level_unknown: bool = False  # a plain-GPIO OUTPUT: the workbook says OUT but never says high or low at boot


def derive_pin_config(rec: NormalizedRecord) -> DerivedPinConfig | None:
    """Computes the correct function/drive-strength/bias/direction for one
    Excel record. Returns None for records with no usable pin identifier
    or interface (shouldn't happen for records already filtered as
    signal pins, but defensive)."""
    pin_id = _first_pin_identifier(rec.fields)
    interface_value = _first(rec.fields, _INTERFACE_CANDIDATES)
    if pin_id is None or interface_value is None:
        return None

    node_type = _classify(interface_value)
    qup_num = _extract_qup_number(rec.fields)
    if qup_num and node_type in ("uart", "i2c", "spi"):
        function = f"qup{qup_num}"
    elif node_type == "gpio":
        function = "gpio"
    else:
        function = re.sub(r"[^a-z0-9]+", "_", interface_value.lower()).strip("_") or "unassigned"

    raw_drv = _first(rec.fields, _DRIVE_STRENGTH_CANDIDATES)
    drive_strength, drv_review = _parse_drive_strength(raw_drv)

    raw_bias = _first(rec.fields, _BIAS_CANDIDATES)
    bias_directive, bias_review = _parse_bias(raw_bias)

    # Fallback for a schema OTHER than the verified SA8155P one: if the
    # standard io_gpio_pull/io_drv_strength fields weren't found at all
    # (a genuinely different schema's field names, not just a missing
    # value), consult that schema's AI-assisted value decode map, if one
    # was configured (see app/ai_value_decoder.py). Every value produced
    # this way is tagged ai_assisted_* - callers use this to flag it
    # distinctly from the fully-verified SA8155P path, never silently
    # merged with it.
    ai_assisted_bias = False
    ai_assisted_drive_strength = False
    if raw_drv is None or raw_bias is None:
        schema = find_schema_by_soc_entity(rec.soc)
        if schema is not None:
            if raw_bias is None and schema.bias_field_slug and schema.bias_value_map:
                raw_val = rec.fields.get(schema.bias_field_slug)
                if raw_val is not None:
                    mapped = schema.bias_value_map.get(str(raw_val))
                    if mapped is not None:
                        bias_directive, bias_review, ai_assisted_bias = mapped, False, True
            if raw_drv is None and schema.drive_strength_field_slug and schema.drive_strength_value_map:
                raw_val = rec.fields.get(schema.drive_strength_field_slug)
                if raw_val is not None:
                    mapped = schema.drive_strength_value_map.get(str(raw_val))
                    if mapped is not None:
                        drive_strength, drv_review, ai_assisted_drive_strength = mapped, False, True

    direction_directive = None
    output_level_unknown = False
    if node_type == "gpio":
        raw_dir = _first(rec.fields, _DIRECTION_CANDIDATES)
        parsed_dir = _parse_direction_label(raw_dir)
        if parsed_dir == "in":
            direction_directive = "input-enable"
        elif parsed_dir == "out":
            output_level_unknown = True     # the Excel direction is OUT; the boot level is a hardware fact it does not hold
        elif raw_dir is None:
            schema = find_schema_by_soc_entity(rec.soc)
            if schema is not None and schema.direction_field_slug and schema.direction_value_map:
                raw_val = rec.fields.get(schema.direction_field_slug)
                if raw_val is not None:
                    mapped = schema.direction_value_map.get(str(raw_val))
                    if mapped is not None:
                        direction_directive = mapped

    return DerivedPinConfig(
        dts_pin_name=_to_dts_pin_name(pin_id),
        interface_value=interface_value,
        node_type=node_type,
        function=function,
        drive_strength=drive_strength,
        drive_strength_needs_review=drv_review,
        bias_directive=bias_directive,
        bias_needs_review=bias_review,
        direction_directive=direction_directive,
        ai_assisted_bias=ai_assisted_bias,
        ai_assisted_drive_strength=ai_assisted_drive_strength,
        output_level_unknown=output_level_unknown,
    )


def _signature(d: "DerivedPinConfig | None"):
    """What a node's pins must have IN COMMON to be written as ONE node: the same function, drive strength, bias and direction."""
    if d is None:
        return None
    return (d.function, d.drive_strength, d.bias_directive, d.direction_directive, d.output_level_unknown)


def _signal_label(rec: NormalizedRecord) -> str | None:
    """A node label named after the board signal, the way real DTS files do it: 'CPU_USBHUB_CTRL_1V8:RST' -> 'cpu_usbhub_ctrl_rst'.
    The voltage token of the bus name (_1V8, _3V3) is dropped. None when the workbook gives the pin no signal name."""
    raw = rec.fields.get("gen5_signal_name") or rec.fields.get("pinmuxed_name")
    if not raw:
        return None
    names = [x.strip() for x in re.split(r"[,\n]", str(raw)) if x.strip()]
    if not names:
        return None
    # a boot-strap (BOOTCFG) name only describes how the pin is sampled at reset: prefer the signal the pin carries at run time
    first = next((x for x in names if "BOOTCFG" not in x.upper()), names[0])
    bus, _, sig = first.rpartition(":")
    bus = re.sub(r"_?\d+V\d*\b", "", bus, flags=re.IGNORECASE)
    label = re.sub(r"[^a-z0-9]+", "_", f"{bus}_{sig}".lower()).strip("_")
    return label or None


def render_node(interface_value: str, direction_bucket: str, project: str,
                 records: list[NormalizedRecord], used_names: set[str],
                 name_override: str | None = None
                 ) -> tuple[str, bool, dict | None]:
    """Returns (rendered_text, needs_review, review_entry). review_entry
    is None when nothing needs review, else a dict with node_name/pins/
    missing_fields/reason - structured data for building a standalone
    review report (see app/review_report.py), not just the inline
    comments already embedded in the rendered text. `used_names` tracks
    every node name already emitted in this file - if the computed name
    collides with one already used, a numeric suffix is appended before
    rendering. This matters because node naming is a lossy slugify of the
    interface value: two DIFFERENT real Excel strings can slugify to the
    identical label - confirmed on the real workbook, where "PCIE_C
    (SA8195)" (with a space) and "PCIE_C(SA8195)" (without) both reduce to
    "pcie_c_sa8195". Without this check, two distinct top-level DTS nodes
    would silently share one label - invalid/conflicting devicetree
    source, not just a cosmetic issue."""
    node_type = _classify(interface_value)
    pins: list[str] = []
    drive_strength = None
    bias_directive = None
    direction_directive = None
    output_level_unknown = False
    needs_review = False
    unresolvable_pins: list[str] = []

    # One node = one set of values. Every pin in it must have the SAME function / drive / bias / direction; taking the first pin's
    # values for the rest would silently write wrong settings for the others. generate_dts() splits the groups so this holds;
    # this check makes a violation impossible to miss.
    derived_all = [derive_pin_config(rec) for rec in records]
    sigs = {_signature(d) for d in derived_all if d is not None}
    if len(sigs) > 1:
        raise ValueError("internal error: pins with different settings were put in one node: "
                         + ", ".join(sorted(_to_dts_pin_name(_first_pin_identifier(r.fields) or "?") for r in records)))

    for rec, derived in zip(records, derived_all):
        pin_id = _first_pin_identifier(rec.fields) or f"row{rec.row_index}"
        pins.append(_to_dts_pin_name(pin_id))
        if derived is None:
            needs_review = True
            unresolvable_pins.append(_to_dts_pin_name(pin_id))
            continue

        if drive_strength is None:
            drive_strength = derived.drive_strength
            needs_review = needs_review or derived.drive_strength_needs_review

        if bias_directive is None:
            bias_directive = derived.bias_directive
            needs_review = needs_review or derived.bias_needs_review

        if direction_directive is None and node_type == "gpio":
            direction_directive = derived.direction_directive
        output_level_unknown = output_level_unknown or derived.output_level_unknown
    if output_level_unknown:
        needs_review = True

    node_name = name_override or _node_name(interface_value, node_type, direction_bucket, project,
                            records[0].fields)
    if node_name in used_names:
        suffix = 2
        while f"{node_name}_{suffix}" in used_names:
            suffix += 1
        node_name = f"{node_name}_{suffix}"
    used_names.add(node_name)

    qup_num = _extract_qup_number(records[0].fields)
    if qup_num and node_type in ("uart", "i2c", "spi"):
        # Real convention: the mux function is the QUP block itself
        # (e.g. "qup13"), not the protocol name - confirmed against the
        # sample (qupv3_se13_... nodes all use function = "qup13" whether
        # they're UART, I2C, or SPI wired to that QUP instance).
        function = f"qup{qup_num}"
    elif node_type == "gpio":
        # The workbook's own Selected Interface literally says "gpio" for
        # this row - that's a real, correct function value, not a fallback.
        function = "gpio"
    else:
        # Any other interface (PCIe, SD, TDM, audio, etc.) - derive the
        # function name FROM THE WORKBOOK'S OWN interface value, not from
        # whether the sample DTS happened to contain a matching example.
        # The Excel data is the source of truth for content; the sample is
        # only a structural/style reference (mux/config blocks, &tlmm,
        # naming pattern) - it should never gate what content gets
        # generated. Falling back to a bare "gpio" here would be actively
        # wrong (a PCIe pin doesn't have GPIO function), so this is always
        # derived from real data instead.
        function = re.sub(r"[^a-z0-9]+", "_", interface_value.lower()).strip("_") or "unassigned"

    context = {
        "node_name": node_name,
        "function": function,
        "pins": pins,
        "drive_strength": drive_strength,
        "bias_directive": bias_directive,
        "direction_directive": direction_directive,
        "output_level_unknown": output_level_unknown,
    }
    rendered = _env.get_template("pin_node.dts.j2").render(**context)

    review_entry = None
    if needs_review:
        missing_fields = []
        if drive_strength is None:
            missing_fields.append("drive-strength (IO_DRV_STRENGTH not determined)")
        if bias_directive is None:
            missing_fields.append("bias (IO_GPIO_PULL not determined)")
        if output_level_unknown:
            missing_fields.append("output level (the Excel says OUT but not whether the pin boots high or low - a hardware fact)")
        if unresolvable_pins:
            missing_fields.append(
                f"no usable pin identifier/interface for: {', '.join(unresolvable_pins)}"
            )
        review_entry = {
            "node_name": node_name, "pins": pins,
            "interface_value": interface_value, "missing_fields": missing_fields,
        }

    return rendered, needs_review, review_entry


def build_pin_index(records: list[NormalizedRecord]) -> dict[str, NormalizedRecord]:
    """Reverse index: dts-style pin name (e.g. 'gpio43') -> the
    NormalizedRecord it came from. Built with the same _to_dts_pin_name()
    transform the generator itself uses, so a lookup here is guaranteed
    consistent with what generate_dts() would have produced. Used by
    app/generators/dts_updater.py to match pins in an uploaded, existing
    DTS file back to their authoritative Excel row. Non-signal AND
    ambiguous records (see classify_pin()) are excluded - neither was
    ever going to appear as a confidently-generated pinctrl node."""
    index: dict[str, NormalizedRecord] = {}
    for rec in records:
        pin_id = _first_pin_identifier(rec.fields)
        iface = _first(rec.fields, _INTERFACE_CANDIDATES)
        interface_group = rec.fields.get("interface_group")
        # Classification uses PinMuxed Name specifically, not pin_id's
        # ball-name fallback chain - same reasoning as group_records_by_node.
        pinmuxed_name = rec.fields.get("pinmuxed_name")
        if pin_id is None or classify_pin(iface, pinmuxed_name, interface_group) != "signal":
            continue
        dts_name = _to_dts_pin_name(pin_id)
        if dts_name and dts_name not in index:
            index[dts_name] = rec
    return index


def remainder_records(records: list[NormalizedRecord], covered_pins: set[str]
                       ) -> list[NormalizedRecord]:
    """Given the full set of a project's signal-pin records and the union
    of pin names already covered by uploaded DTS files, returns the
    records for pins NOT covered by any of them - the basis for the
    'remainder' DTS file."""
    pin_index = build_pin_index(records)
    return [rec for dts_name, rec in pin_index.items() if dts_name not in covered_pins]


_TLMM_REGISTER = re.compile(r"^TLMM_GPIO_CFG\d+\b", re.IGNORECASE)
_OTHER_CONTROLLER_REGISTER = re.compile(r"^[A-Z][A-Z0-9_]*_CFG\d+", re.IGNORECASE)


def non_tlmm_register(rec: NormalizedRecord) -> str | None:
    """The first RegSetting value that is a register of a pin controller OTHER than TLMM (e.g. SA7255P's SAIL_CFG53), or None.
    A row with any TLMM_GPIO_CFG register is a TLMM pin; a row with no register data at all cannot be judged and is left alone.
    Emitting such a pin into &tlmm (as `sailio53`, function ...) would put it under the wrong pin controller - this tool has no
    confirmed DTS binding for any other controller, so those pins are listed instead of generated."""
    other = None
    for key, value in rec.fields.items():
        if key.startswith("regsetting") and value and str(value).strip():
            v = str(value).strip()
            if _TLMM_REGISTER.match(v):
                return None
            if other is None and _OTHER_CONTROLLER_REGISTER.match(v):
                other = v
    return other


def _is_non_tlmm_pad(rec: NormalizedRecord) -> bool:
    """A real signal that is not a TLMM pin: its name is not gpioN and the workbook gives it no TLMM_GPIO_CFG register."""
    iface = _first(rec.fields, _INTERFACE_CANDIDATES)
    if classify_pin(iface, rec.fields.get("pinmuxed_name"), rec.fields.get("interface_group")) != "signal":
        return False
    pin_id = _first_pin_identifier(rec.fields)
    if pin_id and re.fullmatch(r"gpio\d+", _to_dts_pin_name(pin_id)):
        return False
    for key, value in rec.fields.items():
        if key.startswith("regsetting") and value and _TLMM_REGISTER.match(str(value).strip()):
            return False
    return True


def _tlmm_special_names(soc: str, recs) -> list[tuple[str, str]]:
    import json, os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tlmm_special_pins.json")
    try:
        data = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = {}
    for fam in data.get("families", []):
        if not any(k in (soc or "") for k in fam.get("socs", [])):
            continue
        for r in recs:
            w = _to_dts_pin_name(_first_pin_identifier(r.fields) or "")
            for p in fam.get("pins", []):
                if re.match(p["match"], w):
                    out[w] = p["dts"]
    return sorted(out.items())


def generate_dts(project: str, soc: str, records: list[NormalizedRecord]
                  ) -> tuple[str, list[NormalizedRecord], list[dict]]:
    """Full deterministic pipeline: workbook records -> rendered DTS text
    in the reference file's structural style.

    Returns (dts_text, ambiguous_records, review_entries).
    ambiguous_records is never silently dropped - callers (see
    app/main.py) surface these as validation warnings so a person can
    review and resolve them, rather than the system guessing include-or-
    exclude on their behalf. review_entries is structured data for
    building a standalone review report (see app/review_report.py) - the
    same information already embedded as inline NEEDS REVIEW comments in
    dts_text, just also available separately for easy scanning without
    reading the whole file."""
    off_controller = [(r, non_tlmm_register(r)) for r in records]
    skipped_controller = [(r, reg) for r, reg in off_controller if reg]
    records = [r for r, reg in off_controller if not reg]
    # Pads that are not TLMM pins at all (DSI / CSI / USB PHY / UFS / DP / JTAG / PCIe lanes / SSC ...): a real signal, but not a
    # gpioN and with no TLMM register in the workbook. Writing them into &tlmm would invent pin names and functions the pin
    # controller does not have, so they are listed, never generated.
    # Only judged when the workbook gives its GPIO pins TLMM registers at all: a workbook without register data cannot tell a pad
    # from a TLMM pin, so nothing is dropped on that basis.
    has_tlmm_registers = any(_TLMM_REGISTER.match(str(v).strip()) for r in records
                             for k, v in r.fields.items() if k.startswith("regsetting") and v)
    skipped_pads = [r for r in records if has_tlmm_registers and _is_non_tlmm_pad(r)]
    pad_ids = {id(r) for r in skipped_pads}
    records = [r for r in records if id(r) not in pad_ids]
    # A second package ball for the same TLMM pin (dual bond, e.g. DP0_HPD = GPIO_10) is one physical pin: keep the row that
    # carries the pin's settings, so the pin is not declared twice.
    best: dict = {}
    for r in records:
        pid = _first_pin_identifier(r.fields)
        name = _to_dts_pin_name(pid) if pid else None
        if name and re.fullmatch(r"gpio\d+", name):
            key = (name, str(_first(r.fields, _INTERFACE_CANDIDATES) or "").strip().lower())
            filled = sum(1 for k in ("direction", "io_gpio_pull", "io_drv_strength") if r.fields.get(k))
            if key not in best or filled > best[key][0]:
                best[key] = (filled, r)
    keep = {id(v[1]) for v in best.values()}
    merged = [r for r in records if id(r) not in keep and (lambda n: n and re.fullmatch(r"gpio\d+", n))(
        _to_dts_pin_name(_first_pin_identifier(r.fields) or ""))]
    merged_ids = {id(r) for r in merged}
    records = [r for r in records if id(r) not in merged_ids]
    groups, ambiguous = group_records_by_node(records)
    rendered_record_count = sum(len(recs) for recs in groups.values())
    nodes = []
    needs_review_count = 0
    review_entries: list[dict] = []
    used_names: set[str] = set()
    for (iface, bucket), recs in groups.items():
        node_type = _classify(iface)
        # Split the group so that every node holds pins with identical settings. A plain GPIO is its own signal (a reset, an
        # enable, an interrupt): one node per pin, named after the signal. A bus (QUP / TDM / PCIe ...) keeps its pins together
        # only while their settings are the same.
        parts: dict = {}
        for rec in recs:
            d = derive_pin_config(rec)
            key = (id(rec),) if node_type == "gpio" else _signature(d)
            parts.setdefault(key, []).append(rec)
        for sub in parts.values():
            override = _signal_label(sub[0]) if node_type == "gpio" else None
            text, needs_review, review_entry = render_node(iface, bucket, project, sub, used_names, name_override=override)
            nodes.append(text)
            if needs_review:
                needs_review_count += 1
            if review_entry:
                review_entries.append(review_entry)

    root_template = _env.get_template("dtsi_root.dts.j2")
    dts_text = root_template.render(
        soc=soc, project=project, record_count=rendered_record_count,
        skipped_non_signal=len(records) - rendered_record_count,
        needs_review_count=needs_review_count,
        ambiguous_count=len(ambiguous),
        nodes=nodes,
    )
    if skipped_controller:
        names = [(_first_pin_identifier(r.fields) or f"row{r.row_index}", reg) for r, reg in skipped_controller]
        dts_text = dts_text.rstrip("\n") + (
            "\n\n/*\n * NOT GENERATED - " + str(len(names)) + " pin(s) on a pin controller other than TLMM. A &tlmm node would put them "
            "under the wrong controller,\n * and this tool has no confirmed DTS binding for that controller. Their Excel rows are intact:\n"
            + "".join(f" *   {n}  ({reg})\n" for n, reg in names) + " */\n")
        review_entries.append({
            "node_name": "(not generated: pin controller is not TLMM)", "pins": [n for n, _ in names],
            "interface_value": ", ".join(sorted({reg.split("(")[0] for _n, reg in names})),
            "missing_fields": ["a confirmed DTS binding for this pin controller"],
        })
    if skipped_pads:
        special = _tlmm_special_names(soc, skipped_pads)
        if special:
            dts_text = dts_text.rstrip("\n") + (
                "\n\n/*\n * TLMM SPECIAL PINS - these pads ARE on the TLMM pin controller but have no gpioN number. The workbook gives no\n"
                " * mux/config registers for them, so no state is generated; configure them in the SDHC / UFS pinctrl states by hand:\n"
                + "".join(f" *   {w} -> pins = \"{d}\"\n" for w, d in special) + " */\n")
            sp = {w for w, _d in special}
            skipped_pads = [r for r in skipped_pads
                            if _to_dts_pin_name(_first_pin_identifier(r.fields) or "") not in sp]
    if skipped_pads:
        pnames = sorted({_to_dts_pin_name(_first_pin_identifier(r.fields) or f"row{r.row_index}") for r in skipped_pads})
        dts_text = dts_text.rstrip("\n") + (
            "\n\n/*\n * NOT GENERATED - " + str(len(pnames)) + " pad(s) that are not TLMM pins (no gpioN name and no TLMM register in the\n"
            " * workbook): display / camera / USB / UFS / PCIe lanes, JTAG, SSC and similar. They are handled by their own drivers, not by\n"
            " * &tlmm pin states. Their Excel rows are intact:\n"
            + "".join(" *   " + ", ".join(pnames[i:i + 8]) + "\n" for i in range(0, len(pnames), 8)) + " */\n")
    return dts_text, ambiguous, review_entries
