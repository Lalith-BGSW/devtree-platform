"""
Hardcoded schema for the PinMux workbook's `Database` sheet.

Per the user's confirmation, every workbook of this type has the SAME 124
columns, same order, same entities - this is not guessed or inferred
anywhere in this file. Every entry below was extracted directly by
parsing row 2 (group header) and row 3 (field label) of the real
workbook, cross-checked against the file's total column count (124) with
nothing left over. This replaces the old dynamic entity-detection +
role-guessing approach in workbook_parser.py, which had no ground truth
to validate itself against and could silently miscount (e.g. treating a
3-column sub-table as 4).

If a future workbook's headers don't match this schema exactly, that's a
real structural change worth surfacing loudly (see validate_columns() in
workbook_parser.py) rather than silently re-guessing.

Each column is keyed by its EXACT row-3 field label text (not by column
position/letter) - this makes matching robust to the sheet being
re-saved with columns in a different order or extra unrelated columns
appended, as long as the labels themselves are unchanged.
"""

from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class ColumnDef:
    label: str            # exact row-3 text, e.g. "ID (SA8155P)"
    entity: str           # e.g. "SA8155P"
    base_field: str       # e.g. "ID"
    slug: str             # e.g. "id"
    category: str         # identity | electrical | pinmux_register | project_config | marker
    description: str      # what this field means


# --- Entity roles - known, not guessed ---
SOC_ENTITY = "SA8155P"
PROJECT_ENTITIES = ["CCS20", "POC_NESSY2"]
SUBGROUP_ENTITIES = [
    "PinMuxEGPIO",
    "PinMux01", "PinMux02", "PinMux03", "PinMux04",
    "PinMux05", "PinMux06", "PinMux07", "PinMux08",
    "PinMuxQ1", "PinMuxQ2", "PinMuxQ3", "PinMuxQ4",
    "PinMuxQ5", "PinMuxQ6", "PinMuxQ7", "PinMuxQ8",
]
ALL_KNOWN_ENTITIES = [SOC_ENTITY] + PROJECT_ENTITIES + SUBGROUP_ENTITIES


def _soc_cols(defs: list[tuple[str, str, str]]) -> list[ColumnDef]:
    """defs: list of (base_field, category, description)."""
    out = []
    for base_field, category, desc in defs:
        label = f"{base_field} ({SOC_ENTITY})"
        out.append(ColumnDef(label, SOC_ENTITY, base_field, _slug(base_field), category, desc))
    return out


def _slug(base_field: str) -> str:
    import re
    s = re.sub(r"[^a-zA-Z0-9]+", "_", base_field.strip().lower())
    return s.strip("_")


# --- SA8155P (SoC) - 29 columns ---
_SOC_DEFS = [
    ("ID", "identity", "Sequential row-sequence number (1, 2, 3...) - NOT a pin/ball identifier, do not use for pin naming or uniqueness checks."),
    ("SA8155SIP Ball Name", "identity", "Physical BGA ball's net/pin name on the SA8155-SIP chip-revision package variant."),
    ("SA8155SIP Ball ID", "identity", "Physical BGA ball grid coordinate (e.g. AF12) on the SA8155-SIP package variant. Unique per ball - the correct key for duplicate-assignment checks."),
    ("SA8195SIP Ball Name", "identity", "Physical BGA ball's net/pin name on the SA8195-SIP chip-revision package variant (alternate/newer silicon revision)."),
    ("SA8195SIP Ball ID", "identity", "Physical BGA ball grid coordinate on the SA8195-SIP package variant."),
    ("SA8155SoC Ball Name", "identity", "Physical BGA ball's net/pin name on the bare SA8155 SoC (non-SIP) package variant."),
    ("SA8155SoC Ball ID", "identity", "Physical BGA ball grid coordinate on the bare SA8155 SoC package variant."),
    ("SiPV1 Ball Name", "identity", "Physical BGA ball's net/pin name on the SiP version 1 package variant."),
    ("SiPV1 Ball ID", "identity", "Physical BGA ball grid coordinate on the SiP version 1 package variant."),
    ("PRE-SiPV2 Ball Name", "identity", "Physical BGA ball's net/pin name on the pre-release SiP version 2 package variant."),
    ("PRE-SiPV2 Ball ID", "identity", "Physical BGA ball grid coordinate on the pre-release SiP version 2 package variant."),
    ("Power Domain", "electrical", "Which chip power rail/domain this ball belongs to (e.g. VDD_1P8)."),
    ("SoC Schematic Block Name", "identity", "Schematic reference designator/block grouping for this ball on the bare SoC schematic."),
    ("SiPV1 Schematic Block Name", "identity", "Schematic reference designator/block grouping on the SiP v1 schematic."),
    ("SiPV2 Schematic Block Name", "identity", "Schematic reference designator/block grouping on the SiP v2 schematic."),
    ("SiP8195Schematic Block Name", "identity", "Schematic reference designator/block grouping on the SA8195-SIP schematic."),
    ("Pad Grp", "electrical", "I/O pad group classification (electrical characteristics grouping) for this ball."),
    ("Pad Pull", "electrical", "The pad's internal pull configuration as defined by the chip (not project-specific)."),
    ("Default Pull", "electrical", "Factory/default pull-up/pull-down/none state of this pad before any project configuration."),
    ("REG_IO_GPIO_PULL", "pinmux_register", "Raw register field controlling GPIO pull configuration at the chip level (decoded by an embedded legend table elsewhere in the sheet)."),
    ("REG_IO_DRV_STRENGTH", "pinmux_register", "Raw register field controlling drive strength at the chip level (decoded by an embedded legend table elsewhere in the sheet)."),
    ("REG_GPIO_OE", "pinmux_register", "Raw register field controlling GPIO output-enable at the chip level."),
    ("REG_GPIO_IN_OUT", "pinmux_register", "Raw register field controlling GPIO input/output direction at the chip level."),
    ("PDC_GIC", "electrical", "Power Domain Controller / Generic Interrupt Controller routing info for this pin, if applicable."),
    ("PDCAvailable", "electrical", "Whether this pin can be routed through the Power Domain Controller (wakeup source capability)."),
    ("WakeUp Int", "electrical", "Whether/which wakeup interrupt this pin can trigger."),
    ("BootMode", "electrical", "Whether this pin has a special function during chip boot/strapping."),
    ("PinMux exists", "pinmux_register", "Whether this ball has any PinMux (muxable function) configuration at all, vs. being a fixed-function/power/ground ball."),
    ("Interfaces", "identity", "Comma-separated list of all interfaces this SoC ball could theoretically be muxed to (chip-level capability, not what any given project actually selected)."),
]

# --- PinMux sub-group tables (17 entities, 3 columns each) - raw register data, always folded into the SoC record ---
_SUBGROUP_FIELD_TEMPLATES = [
    ("Port{n}", "pinmux_register", "Which physical pin/port this PinMux register slot {n} refers to."),
    ("Instance{n}", "pinmux_register", "The peripheral instance number selectable via PinMux slot {n}."),
    ("RegSetting{n}", "pinmux_register", "Raw register value/setting for PinMux slot {n}."),
]

_QUP_FIELD_TEMPLATES = [
    ("QUPPort{n}", "pinmux_register", "Which physical pin this QUP (Qualcomm Universal Peripheral) PinMux register slot {n} refers to."),
    ("InstanceQUP{n}", "pinmux_register", "The QUP peripheral instance number selectable via slot {n} (drives UART/I2C/SPI muxing)."),
    ("RegSettingQUP{n}", "pinmux_register", "Raw register value/setting for QUP PinMux slot {n}."),
]


def _egpio_cols() -> list[ColumnDef]:
    fields = [("PortEGPIO", "pinmux_register", "Which physical pin this EGPIO (extended GPIO) register slot refers to."),
              ("InstanceEGPIO", "pinmux_register", "The EGPIO instance number for this slot."),
              ("RegSettingEGPIO", "pinmux_register", "Raw register value/setting for this EGPIO slot.")]
    return [ColumnDef(f"{bf} (PinMuxEGPIO)", "PinMuxEGPIO", bf, _slug(bf), cat, desc) for bf, cat, desc in fields]


def _pinmux_cols(n: int) -> list[ColumnDef]:
    entity = f"PinMux{n:02d}"
    out = []
    for tmpl, cat, desc_tmpl in _SUBGROUP_FIELD_TEMPLATES:
        bf = tmpl.format(n=n)
        out.append(ColumnDef(f"{bf} ({entity})", entity, bf, _slug(bf), cat, desc_tmpl.format(n=n)))
    return out


def _qup_cols(n: int) -> list[ColumnDef]:
    entity = f"PinMuxQ{n}"
    out = []
    for tmpl, cat, desc_tmpl in _QUP_FIELD_TEMPLATES:
        bf = tmpl.format(n=n)
        out.append(ColumnDef(f"{bf} ({entity})", entity, bf, _slug(bf), cat, desc_tmpl.format(n=n)))
    return out


# --- Project fields (CCS20 / POC_NESSY2) - same field meanings on both, only Netlist/Imported* fields visible column order differs slightly ---
_PROJECT_FIELD_DEFS = [
    ("Changes to previous SB", "project_config", "Free-text note on what changed for this pin versus the previous schematic baseline (SB) revision."),
    ("PinMuxed Name", "project_config", "The project's assigned net/pin label for this ball - e.g. 'GPIO43', or a non-signal marker like 'DNC'/'NC'/'AGND'/a power-rail name if this ball isn't a muxed signal in this project."),
    ("PinMuxSHDW0", "project_config", "Shadow/backup PinMux setting - purpose not yet confirmed with real values; possibly an alternate configuration slot."),
    ("Selected Interface", "project_config", "The specific peripheral interface this project has assigned to this pin (e.g. HSUART_QUP13). Non-signal balls hold markers like 'DNC'/'NC'/'Power' instead."),
    ("SelectedSHDW", "project_config", "Shadow/backup selected-interface value - purpose not yet confirmed with real values."),
    ("PinMux Setting", "project_config", "Which PinMux register slot (see PinMux01-08/PinMuxQ1-08) this project uses to achieve the Selected Interface."),
    ("Gen5 Signal Name", "project_config", "The schematic/netlist signal name for this pin in the 5th-generation platform naming convention."),
    ("Interface Group", "project_config", "High-level functional grouping of the Selected Interface (e.g. Bluetooth, Camera, Ethernet)."),
    ("Remarks", "project_config", "Free-text engineering notes about this pin's configuration."),
    ("OperatingSystem/Domains", "project_config", "Which OS/execution domain (e.g. which VM or processor) owns/controls this pin."),
    ("SW driver names", "project_config", "Name(s) of the software driver(s) responsible for this pin/interface."),
    ("GM VCU SW comments", "project_config", "Software-team comments specific to the GM Vehicle Control Unit integration."),
    ("IRQ", "project_config", "Interrupt line/number associated with this pin, if it's used as an interrupt source."),
    ("Direction", "project_config", "Signal direction for this pin in this project: in / out / bidirectional."),
    ("ResetValue", "electrical", "The pin's electrical state immediately after chip reset, before software configures it."),
    ("External Termination", "electrical", "External (board-level, off-chip) termination/pull resistor present on this net, if any."),
    ("IO_GPIO_PULL", "electrical", "Project-selected pull-up/pull-down/none configuration for this pin (maps toward Device Tree bias-pull-* properties - exact value format not yet confirmed against real data)."),
    ("IO_DRV_STRENGTH", "electrical", "Project-selected drive strength for this pin (maps toward Device Tree drive-strength property - exact value format not yet confirmed against real data)."),
    ("Netlist review result", "project_config", "Status/outcome of a netlist cross-check review for this pin's configuration."),
    ("Imported Schematic Signal Name", "project_config", "The signal name as it appears in the imported schematic tool output (may differ from Gen5 Signal Name)."),
    ("Imported External Termination", "project_config", "External termination value as imported from the schematic tool, for cross-check against the manually-entered External Termination field."),
]

# --- Special marker columns (not part of any entity) ---
_MARKER_COLUMNS = [
    ColumnDef("_PARA_ROW", "", "_PARA_ROW", "para_row", "marker",
              "Row-type marker. Blank for real per-pin data rows (the vast majority). A handful of rows instead hold values like 'DRV_STRENGTH'/'GPIO_PULL'/'GPIO_OE'/'GPIO_VALUE' marking embedded legend/lookup rows that decode the SoC's REG_* raw register values - these are NOT pin data and should be excluded from normalization. One row holds '_END_ROW', marking the last real data row (that row IS still real data, not a marker to skip)."),
    ColumnDef("_END_SB", "", "_END_SB", "end_sb", "marker",
              "Column-side sentinel marking the end of the project-block columns in row 2. Not a per-row data field."),
]


def _build_project_cols(entity: str) -> list[ColumnDef]:
    return [
        ColumnDef(f"{bf} ({entity})", entity, bf, _slug(bf), cat, desc)
        for bf, cat, desc in _PROJECT_FIELD_DEFS
    ]


def _build_all_columns() -> dict[str, ColumnDef]:
    cols: list[ColumnDef] = []
    cols += _soc_cols(_SOC_DEFS)
    cols += _egpio_cols()
    for n in range(1, 9):
        cols += _pinmux_cols(n)
    for n in range(1, 9):
        cols += _qup_cols(n)
    for entity in PROJECT_ENTITIES:
        cols += _build_project_cols(entity)
    cols += _MARKER_COLUMNS
    return {c.label: c for c in cols}


# label (exact row-3 text) -> ColumnDef. Source of truth for the parser.
KNOWN_COLUMNS: dict[str, ColumnDef] = _build_all_columns()

# Expected total data columns (124 in the real workbook: 29 SoC + 17*3
# sub-group + 21*2 project + 2 markers).
EXPECTED_COLUMN_COUNT = len(KNOWN_COLUMNS)


def format_data_dictionary() -> str:
    """Renders a compact glossary of what every field means, for injection
    into the LLM's system prompt so explanations are grounded in real
    definitions instead of inferred from field names. Deduplicated: the 21
    project-config fields mean the same thing on CCS20 and POC_NESSY2, so
    they're listed once, and the 17 PinMux/QUP register sub-tables (which
    only differ by slot number 1-8) are summarized as one pattern rather
    than 51 near-identical lines.
    """
    lines = ["=== SoC identity/electrical fields (SA8155P) ==="]
    for base_field, category, desc in _SOC_DEFS:
        lines.append(f"- {base_field}: {desc}")

    lines.append("\n=== PinMux/QUP register sub-tables ===")
    lines.append(
        "PinMuxEGPIO and PinMux01-08 each have 3 columns: PortN (which pin this "
        "register slot N refers to), InstanceN (peripheral instance selectable via "
        "slot N), RegSettingN (raw register value for slot N). PinMuxQ1-Q8 are the "
        "same pattern for QUP (Qualcomm Universal Peripheral - drives UART/I2C/SPI "
        "muxing) instead of generic GPIO. These are chip-level register data, folded "
        "into the SoC's fields on each row."
    )

    lines.append("\n=== Project config fields (same meaning on both CCS20 and POC_NESSY2) ===")
    for base_field, category, desc in _PROJECT_FIELD_DEFS:
        lines.append(f"- {base_field}: {desc}")

    lines.append("\n=== Non-signal markers (IMPORTANT) ===")
    lines.append(
        "A ball's 'PinMuxed Name'/'Selected Interface' can hold explicit non-signal "
        "markers instead of a real interface: DNC (Do Not Connect), NC (No Connect), "
        "Power, GND/AGND (ground), or a VDD*/VSYS* rail name. These are NOT missing "
        "data - they mean this physical ball isn't a muxable signal pin in this "
        "project. On the real workbook, roughly 63% of balls fall into one of these "
        "categories. Never treat these as errors or as candidates for DTS pinctrl "
        "generation."
    )

    lines.append("\n=== Row-level notes ===")
    lines.append(
        "The 'ID' field is a plain row-sequence number, NOT a pin identifier - never "
        "use it to identify or compare pins. The unique physical identifier for a "
        "ball is its Ball ID (e.g. 'AF12'), not its Ball Name or PinMuxed Name, which "
        "can repeat across many different physical balls (e.g. 'AGND' is the "
        "PinMuxed Name on hundreds of different balls)."
    )

    return "\n".join(lines)
