#!/usr/bin/env python3
"""
USB DTS tool - check and update what's present, generate what's missing.

    python tool/usb_dts_tool.py --excel <workbook.xlsx|xlsm> --project <NAME>
                                [--dts <existing.dtsi>] [--out <result.dtsi>]
                                [--report <report.md>] [--match both|gen5]

Everything here is deterministic Python - no LLM decides anything:
  * USB pins are found in the Excel (Gen5 Signal Name and/or Interface Group).
  * Whether a pin is TLMM-controlled is read from its RegSetting cell.
  * Electrical values come from the workbook cells ("4: DRV_10_MA" -> 10).
  * If --dts is given, existing pinctrl nodes for those pins are UPDATED in
    place (only values that disagree with Excel), and existing &usbN
    consumer nodes are detected and never duplicated.
  * Anything missing is GENERATED: pinctrl states only for pins with no
    existing node, and a consumer node per USB controller instance,
    referencing the project's EXISTING pinctrl labels where they exist.
  * Anything ambiguous or unconfirmable (shared pins, unknown chip names,
    unrecognised signals, PHY pads) is listed in the report, never guessed.
"""
from __future__ import annotations
import argparse
import hashlib
import os
import pickle
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.parsers.workbook_parser import parse_workbook, build_analysis
from app.parsers.normalizer import normalize
from app.parsers.dts_parser import parse_dts_nodes, _find_matching_brace
from app.schema_registry import match_known_schema, confirm_new_schema
from app.md_exporter import resolve_field_slugs
from app.peripheral_extractor import resolve_gen5_signal_slug, _resolve_field_slugs_for_summary
from app.generators.dts_generator import derive_pin_config, _first_pin_identifier, _to_dts_pin_name
from app.usb_consumer_generator import (
    categorize_usb_records, detect_existing_usb_node, resolve_instance_labels,
    _ball_name, _GPIO_BALL_RE, _signal_segments,
    detect_usb_instance_token, _is_boot_strap_segment,
)

# Fixed consumer-node boilerplate - confirmed from ONE real accepted example
# (usb0). Not derived from Excel. Only emitted verbatim for the confirmed instance.
CONFIRMED_LABEL = "usb0"
# The dwc3@a600000 / usb2_phy0 boilerplate below was confirmed from ONE reference: CCS20, an SA8155P. Unit addresses and phy
# labels are SoC-specific, so it is emitted only for that SoC; any other SoC gets a NEEDS REVIEW comment instead.
CONFIRMED_SOC = "SA8155P"
DEBOUNCE_MS = 20
DWC3_BLOCK = (
    "\tdwc3@a600000 {\n"
    "\t\tusb-phy = <&usb2_phy0>, <&usb_nop_phy>;\n"
    "\t\tmaximum-speed = \"high-speed\";\n"
    "\t};\n"
)


# ---------------------------------------------------------------- loading
def _log(msg):
    print(msg, file=sys.stderr, flush=True)


def _shown(path):
    """A path as the user should read it: relative to where they ran the tool, never a machine-specific absolute path."""
    try:
        return os.path.relpath(path)
    except ValueError:                    # different drive on Windows
        return path


_CACHE_DEPS = ("parsers/workbook_parser.py", "parsers/normalizer.py", "schema_registry.py", "schema.py", "models.py")


def _cache_path(excel_path):
    """Cache key = the workbook (path, size, mtime) + the parsing code's own signature,
    so editing either the workbook or the parser can never serve stale data."""
    app_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app")
    sig = [os.path.abspath(excel_path), str(os.path.getsize(excel_path)), str(os.stat(excel_path).st_mtime_ns)]
    for rel in _CACHE_DEPS:
        f = os.path.join(app_dir, rel)
        sig.append(f"{rel}:{os.path.getsize(f)}:{os.stat(f).st_mtime_ns}")
    me = os.path.abspath(__file__)
    sig.append(f"tool:{os.path.getsize(me)}:{os.stat(me).st_mtime_ns}")
    key = hashlib.sha1("|".join(sig).encode()).hexdigest()[:20]
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".cache")
    return os.path.join(d, key + ".pkl")


def _read_sheet_signals(excel_path, needle="usb"):
    """{sheet: {project: [(signal, gpio_number|None), ...]}} for every sheet whose NAME contains `needle`
    (e.g. 'USBHUB'), other than 'Database'. These are per-interface views of the same pins; comparing them with
    the Database sheet reveals hardware-variant wiring the Database sheet alone does not show. Stale copies
    (CmpCheck, *.bak, QUP_prev ...) are ignored because their names do not contain the keyword."""
    out = {}
    try:
        import openpyxl
        wb = openpyxl.load_workbook(excel_path, read_only=True, data_only=True)
    except Exception:
        return out
    try:
        for name in wb.sheetnames:
            if name == "Database" or needle not in name.lower():
                continue
            rows = list(wb[name].iter_rows(values_only=True))
            hdr_i = next((i for i, r in enumerate(rows[:5])
                          if any(isinstance(v, str) and v.startswith("Gen5 Signal Name") for v in r)), None)
            if hdr_i is None:
                continue
            hdr = rows[hdr_i]
            for ci, h in enumerate(hdr):
                m = re.match(r"Gen5 Signal Name \((.+)\)\s*$", str(h or "").strip())
                if not m:
                    continue
                proj = m.group(1)
                pm = next((cj for cj, hh in enumerate(hdr)
                           if re.match(rf"PinMuxed Name \({re.escape(proj)}\)\s*$", str(hh or "").strip())), None)
                lst = []
                for r in rows[hdr_i + 1:]:
                    sig = r[ci] if ci < len(r) else None
                    if not sig or not str(sig).strip():
                        continue
                    pin = r[pm] if pm is not None and pm < len(r) else None
                    g = re.search(r"GPIO_(\d+)", str(pin or ""))
                    lst.append((re.sub(r"\s+", " ", str(sig)).strip(), g.group(1) if g else None))
                out.setdefault(name, {})[proj] = lst
    finally:
        wb.close()
    return out


def find_sheet_conflicts(sheet_signals, project, records, gen5_slug, needle="usb"):
    """USB signals that a USB-named sheet puts on a GPIO the Database sheet assigns to something else."""
    db = {}
    for r in records:
        n = gpio_num(r)
        if n:
            db[n] = [re.sub(r"\s+", " ", x).strip().upper() for x in _signal_segments(str(r.fields.get(gen5_slug) or ""))]
    out = []
    for sheet, per in sheet_signals.items():
        for sig, n in per.get(project, []):
            if n is None or needle not in sig.lower():
                continue
            for seg in _signal_segments(sig):
                if n not in db:
                    out.append((sheet, seg, n, "GPIO not present in the Database sheet"))
                elif seg.upper() not in db[n]:
                    out.append((sheet, seg, n, "Database sheet assigns it to: " + (", ".join(db[n]) or "no signal")))
    return out


def _read_workbook(excel_path):
    """parse + classify + normalize the WHOLE workbook (all projects). Slow on big files."""
    parsed = parse_workbook(excel_path)
    found_labels = {c.field_label for c in parsed.columns if c.field_label}
    known = match_known_schema(found_labels)
    analysis = build_analysis(parsed)
    role_by_entity = {e.name: e.role for e in analysis.entities}
    soc_entity = next((e for e, r in role_by_entity.items() if r == "soc"), None)
    project_entities = [e for e, r in role_by_entity.items() if r == "project"]
    subgroup_entities = [e for e, r in role_by_entity.items() if r == "subgroup"]

    if known is None:
        category_by_role = {"soc": "identity", "project": "project_config", "subgroup": "pinmux_register"}
        columns_by_label = {}
        for col in parsed.columns:
            if not col.field_label:
                continue
            role = role_by_entity.get(col.entity) if col.entity else None
            if col.entity and col.base_field and role not in (None, "ignore"):
                columns_by_label[col.field_label] = (col.entity, col.base_field, category_by_role[role])
            else:
                # No entity detected, or a real entity with no confirmed role - remembered as a
                # marker (same convention app/schema.py's own seed schema uses for SA8155P's
                # "_PARA_ROW"/"_END_SB") so this exact file still self-matches next run instead of
                # being reclassified every single time. See app/main.py's classify_schema() for the
                # web-endpoint twin of this same fix (confirmed real case there: SA7255P/Iveco).
                columns_by_label[col.field_label] = ("", col.field_label, "marker")
        schema = confirm_new_schema(
            name=f"cli_{os.path.basename(excel_path)}", soc_entity=soc_entity,
            project_entities=project_entities, subgroup_entities=subgroup_entities,
            columns_by_label=columns_by_label)
        for col in parsed.columns:
            kd = schema.known_columns.get(col.field_label) if col.field_label else None
            if kd:
                col.base_field, col.entity = kd.base_field, kd.entity
                col.slug, col.category, col.known = kd.slug, kd.category, True
        records = normalize(parsed, soc_entity=soc_entity, project_entities=project_entities,
                            soc_subgroup_entities=subgroup_entities)
    else:
        soc_entity = known.soc_entity
        project_entities = list(known.project_entities)
        subgroup_entities = list(known.subgroup_entities)
        records = normalize(
            parsed,
            soc_entity=soc_entity,
            project_entities=project_entities,
            soc_subgroup_entities=subgroup_entities,
        )
    return parsed, records, soc_entity, project_entities, _read_sheet_signals(excel_path)


USE_CACHE = True


def _get_data(excel_path):
    name = os.path.basename(excel_path)
    t0 = time.time()
    cp = _cache_path(excel_path) if USE_CACHE else None
    data = None
    if cp and os.path.exists(cp):
        try:
            with open(cp, "rb") as f:
                data = pickle.load(f)
            _log(f"[1] Workbook {name}: loaded from cache ({time.time() - t0:.1f}s)")
        except Exception:
            data = None
    if data is None:
        _log(f"[1] Reading workbook {name} ... a large workbook can take a minute or more the first time "
             f"(later runs use a cache)")
        data = _read_workbook(excel_path)
        _log(f"[1] Workbook read in {time.time() - t0:.1f}s")
        if cp:
            try:
                os.makedirs(os.path.dirname(cp), exist_ok=True)
                with open(cp, "wb") as f:
                    pickle.dump(data, f, protocol=pickle.HIGHEST_PROTOCOL)
            except Exception:
                pass                     # a read-only folder just means no cache
    return data


def list_projects(excel_path):
    return _get_data(excel_path)[3]


def load_sheet_signals(excel_path):
    return _get_data(excel_path)[4]


def load_records(excel_path: str, project: str):
    parsed, records, soc_entity, project_entities, _sheets = _get_data(excel_path)
    if project not in project_entities:
        raise SystemExit(f"Project {project!r} not found. Projects in this workbook: {project_entities}")
    return parsed, [r for r in records if r.project == project], soc_entity


def read_dts(path):
    """Return (text with \\n newlines, the file's own line ending). Text is normalised for
    parsing; the original style is remembered so output can be written back the same way."""
    raw = open(path, "rb").read()
    eol = "\r\n" if raw.count(b"\r\n") > raw.count(b"\n") / 2 else "\n"
    return raw.decode("utf-8", errors="replace").replace("\r\n", "\n"), eol


def write_dts(path, text, eol):
    """Write with the input file's line endings, byte-for-byte (no OS newline translation),
    so a CRLF file stays CRLF and an LF file stays LF - a reviewer's diff shows only real edits."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as f:
        f.write(text.replace("\r\n", "\n").replace("\n", eol).encode("utf-8"))


# ---------------------------------------------------------------- matching
def match_usb(records, gen5_slug, ig_slug, keyword, mode):
    needle = keyword.lower()
    out = []
    for r in records:
        g = str(r.fields.get(gen5_slug) or "").lower()
        i = str(r.fields.get(ig_slug) or "").lower() if ig_slug else ""
        if needle in g or (mode == "both" and needle in i):
            out.append(r)
    return out


def gpio_num(rec):
    ball = _ball_name(rec)
    m = _GPIO_BALL_RE.match(ball) if ball else None
    return m.group(1) if m else None


# ---------------------------------------------------------------- existing DTS
def _is_sleep(label):
    """Sleep/suspend pinctrl states intentionally differ from the active state
    (lower drive, no pull, ...). They must never be patched from Excel or
    referenced as a pin's active state."""
    return bool(re.search(r"(^|_)(sleep|suspend|idle)($|_)", label, re.IGNORECASE))


def existing_pin_nodes(dts_text):
    """pin name (e.g. 'gpio81') -> (label, is_exclusive) for nodes already in the file."""
    by_pin = {}
    for n in parse_dts_nodes(dts_text):
        if _is_sleep(n.label):
            continue
        pins = list(dict.fromkeys(n.mux_pins + n.config_pins))
        for p in pins:
            # prefer an exclusive (single-pin) node if several exist
            if p not in by_pin or (len(pins) == 1 and not by_pin[p][1]):
                by_pin[p] = (n.label, len(pins) == 1, n)
    return by_pin


def _dir_class(directive):
    if directive is None:
        return None
    if directive == "input-enable":
        return "in"
    if directive in ("output-high", "output-low"):
        return "out"
    return directive


def compare_to_excel(node, rec):
    """Compare an existing node to what Excel says. Direction is compared by
    CLASS only (in vs out): Excel's Direction field says IN/OUT but never
    high/low, so output-high vs output-low is NOT a discrepancy we can judge
    and is never reported or changed."""
    d = derive_pin_config(rec)
    if d is None:
        return "no Excel config derivable", []
    diffs = []
    if d.drive_strength is not None and str(d.drive_strength) != str(node.drive_strength):
        diffs.append(f"drive-strength {node.drive_strength} -> {d.drive_strength}")
    if d.bias_directive and d.bias_directive != node.bias_directive:
        diffs.append(f"{node.bias_directive} -> {d.bias_directive}")
    if d.direction_directive and _dir_class(d.direction_directive) != _dir_class(node.direction_directive):
        diffs.append(f"direction {node.direction_directive} -> Excel says {_dir_class(d.direction_directive).upper()} (needs a human: level not derivable)")
    return ("MATCH" if not diffs else "DIFFERS"), diffs


def _insert_in_config(raw, line):
    """Insert a property line after the `pins = ...;` line inside config { }."""
    m = re.search(r"config\s*\{", raw)
    if not m:
        return raw
    pm = re.compile(r"^([ \t]*)pins\s*=[^;]*;[ \t]*$", re.MULTILINE).search(raw, m.end())
    if not pm:
        return raw
    return raw[:pm.end()] + "\n" + pm.group(1) + line + raw[pm.end():]


def minimal_patch(text, safe_recs):
    """Patch ONLY drive-strength / bias values that disagree with Excel, in place,
    in single-pin nodes for the given records. Line order, indentation and
    comments are preserved so the diff shows just the changed value. Never
    touches function, direction, or shared multi-pin nodes."""
    nodes = [n for n in parse_dts_nodes(text) if not _is_sleep(n.label)]
    by_pin = {}
    for n in nodes:
        pins = list(dict.fromkeys(n.mux_pins + n.config_pins))
        if len(pins) == 1:
            by_pin[pins[0]] = n
    edits, changes, skipped = [], [], []
    for rec in safe_recs:
        num = gpio_num(rec)
        node = by_pin.get(f"gpio{num}") if num else None
        if node is None:
            continue
        d = derive_pin_config(rec)
        if d is None:
            continue
        raw = node.raw_text
        new = raw
        if d.drive_strength is not None and not d.drive_strength_needs_review:
            if node.drive_strength is None:
                new = _insert_in_config(new, f"drive-strength = <{d.drive_strength}>;")
                changes.append((node.label, node.mux_pins[0], "drive-strength", "(missing)", d.drive_strength))
            elif str(node.drive_strength) != str(d.drive_strength):
                new = re.sub(r"(drive-strength\s*=\s*<)\s*\d+\s*(>)", rf"\g<1>{d.drive_strength}\g<2>", new, count=1)
                changes.append((node.label, node.mux_pins[0], "drive-strength", node.drive_strength, d.drive_strength))
        if d.bias_directive and not d.bias_needs_review:
            if node.bias_directive is None:
                new = _insert_in_config(new, f"{d.bias_directive};")
                changes.append((node.label, node.mux_pins[0], "bias", "(missing)", d.bias_directive))
            elif node.bias_directive != d.bias_directive:
                new = re.sub(rf"\b{re.escape(node.bias_directive)}\b", d.bias_directive, new, count=1)
                changes.append((node.label, node.mux_pins[0], "bias", node.bias_directive, d.bias_directive))
        if d.direction_directive and node.direction_directive and \
                _dir_class(d.direction_directive) != _dir_class(node.direction_directive):
            skipped.append(f"{node.label} ({node.mux_pins[0]}): file says {node.direction_directive}, "
                           f"Excel says {_dir_class(d.direction_directive).upper()} - not changed, needs a human")
        if new != raw:
            edits.append((node.start, node.end, new))
    for start, end, new in sorted(edits, reverse=True):
        text = text[:start] + new + text[end:]
    return text, changes, skipped


# ---------------------------------------------------------------- rendering
def render_consumer(label, oc, vbus, pinctrl_labels, unlinked_notes, confirmed, oc_remark="", vbus_remark=""):
    host = (label == CONFIRMED_LABEL)      # qcom,default-mode-host on usb0: confirmed by BOTH accepted examples
    lines = [f"&{label} {{"]
    if oc:
        if oc_remark:
            lines.append(f"\t/* Excel remark (GPIO_{oc[0]}): {oc_remark} */")
        lines.append(f"\toc-gpio = <&tlmm {oc[0]} {oc[1]}>;")
    if vbus:
        if vbus_remark:
            lines.append(f"\t/* Excel remark (GPIO_{vbus[0]}): {vbus_remark} */")
        lines.append(f"\tvbus-out-gpio = <&tlmm {vbus[0]} {vbus[1]}>;")
    if confirmed:
        lines.append(f"\tdebounce-delay-ms = <{DEBOUNCE_MS}>;")
    if pinctrl_labels:
        lines.append('\tpinctrl-names = "default";')
        lines.append("\tpinctrl-0 = <" + " ".join(f"&{l}" for l in pinctrl_labels) + ">;")
    for note in unlinked_notes:
        lines.append(f"\t/* NEEDS REVIEW: {note} */")
    lines.append("")
    if host:
        lines.append("\tqcom,default-mode-host;")
        lines.append("")
    if confirmed:
        lines.append(DWC3_BLOCK.rstrip("\n"))
    else:
        missing = ["debounce-delay-ms", "the dwc3 child (unit address, usb-phy, maximum-speed)"]
        if not host:
            missing.insert(1, "qcom,default-mode-host")
        lines.append(f"\t/* NEEDS REVIEW: {', '.join(missing)} - only confirmed for the reference instance;\n"
                     f"\t * add from a real reference if this instance needs them. */")
    lines.append("};")
    return "\n".join(lines) + "\n"


def render_reg_consumer(label, reg_label, state_label):
    """&usbN block for the regulator pattern (shape taken from the accepted change)."""
    lines = [f"&{label} {{", '\tstatus = "okay";', f"\tvbus_dwc3-supply = <&{reg_label}>;"]
    if state_label:
        lines.append('\tpinctrl-names = "default";')
        lines.append(f"\tpinctrl-0 = <&{state_label}>;")
    if label == CONFIRMED_LABEL:            # confirmed on usb0 by both accepted examples
        lines.append("\tqcom,default-mode-host;")
    else:
        lines.append("\t/* NEEDS REVIEW: qcom,default-mode-host is only confirmed for usb0 - add it if this "
                     "instance should default to host mode. */")
    lines.append("};")
    return "\n".join(lines) + "\n"


def state_name(token, kind, fallback):
    """The project's own naming convention for VBUS/OC pinctrl states. The five real labels seen
    (usbv3_power_enable, usbv3_fault_detect, usbv2_fault_detect, usbv2_0_power_enable,
    usbv2_1_power_enable) all follow: 'usbv' + <major digit of USBx0> + ('_' + instance index when the
    signal has one, e.g. USB20_0) + '_power_enable' (VBUS enable) | '_fault_detect' (over-current)."""
    m = re.fullmatch(r"USB(\d)0(?:_(\d+))?", token or "")
    if not m:
        return fallback
    core = f"usbv{m.group(1)}" + (f"_{m.group(2)}" if m.group(2) else "")
    return core + ("_fault_detect" if kind == "oc_gpio" else "_power_enable")


def derive_label_from_signal(signal, used_labels):
    """A readable pinctrl label for a pin whose ROLE we don't recognize (see "other_gpio" in
    categorize_usb_records) - built from the pin's own real signal name, never a guessed role
    name. E.g. "CPU_USBHUB_CTRL_1V8:RST" -> "cpu_usbhub_ctrl_rst" (the "_1V8" voltage-rail suffix
    is stripped - it names the supply domain, not the signal - and any duplicate word already in
    the tail is dropped once, e.g. "SA8155_USB_CFG:USB_PHY_PORT_SELECT" -> keeps the full tail
    rather than repeating "USB"). Suffixed with a number if the derived name collides."""
    prefix, _, tail = signal.partition(":")
    prefix = re.sub(r"_\d+[Vv]\d+$", "", prefix)             # drop a trailing voltage-rail suffix
    prefix_words = [w for w in re.split(r"[^A-Za-z0-9]+", prefix) if w]
    tail_words = [w for w in re.split(r"[^A-Za-z0-9]+", tail) if w]
    tail_first = {w.upper() for w in tail_words[:1]}
    words = [w for w in prefix_words if w.upper() not in tail_first] + tail_words
    base = "_".join(words).lower() or "usb_gpio"
    label = base
    i = 2
    while label in used_labels:
        label = f"{base}_{i}"
        i += 1
    return label


def label_sep(text):
    """Match the file's own label style ('a:a {' vs 'a: a {'), counting active (non-sleep) nodes."""
    nospace = space = 0
    for m in re.finditer(r"^[ \t]*([A-Za-z_]\w*):( ?)[A-Za-z_]\w*\s*\{", text, re.MULTILINE):
        if _is_sleep(m.group(1)):
            continue
        if m.group(2):
            space += 1
        else:
            nospace += 1
    return ":" if nospace > space else ": "


# Where an OUTPUT pin's high/low comes from:
#   excel       - the workbook's own ResetValue column ("0: low" / "1: high"), taken as it stands, tagged with its source
#   convention  - the accepted change's "regulator OFF at boot" inference for VBUS enables; a NEEDS REVIEW placeholder otherwise
#   none        - always a NEEDS REVIEW placeholder
LEVEL_SOURCE = "convention"


def excel_level(rec):
    """(directive, raw) from the workbook's ResetValue column, or None. "0: low" -> output-low, "1: high" -> output-high."""
    raw = str(rec.fields.get("resetvalue") or "").strip()
    if not raw:
        return None
    m = re.match(r"^([01])\b", raw)
    if m:
        return ("output-high" if m.group(1) == "1" else "output-low"), raw
    if re.search(r"\bhigh\b", raw, re.I):
        return "output-high", raw
    if re.search(r"\blow\b", raw, re.I):
        return "output-low", raw
    return None


def render_state(label, rec, dir_slug, level=None, sep=": ", polarity=None, role=None):
    """Pinctrl state for a pin that a consumer references as a GPIO (oc-gpio / vbus-out-gpio / regulator
    gpio). Such a pin MUST be muxed as "gpio", whatever its Selected Interface says (a VBUS pin with a
    boot-strap role reports "boot"). Property order follows the accepted change:
    drive-strength, direction/level, bias.
    OUTPUT LEVEL: Excel's Direction says OUT but no column encodes high/low (Reset Value is "0: low" even on
    pins whose accepted config is output-high). Callers may pass `level` as a FLAGGED inference; without it
    the level is left as a NEEDS REVIEW comment."""
    pin = _to_dts_pin_name(_first_pin_identifier(rec.fields))
    d = derive_pin_config(rec)
    lines = [f"\t{label}{sep}{label} {{", "\t\tmux {", f'\t\t\tpins = "{pin}";',
             '\t\t\tfunction = "gpio";', "\t\t};", "\t\tconfig {", f'\t\t\tpins = "{pin}";']
    if d is not None and d.drive_strength is not None and not d.drive_strength_needs_review:
        lines.append(f"\t\t\tdrive-strength = <{d.drive_strength}>;")
    else:
        lines.append("\t\t\t/* NEEDS REVIEW: drive-strength not determined from Excel */")
    raw_dir = str(rec.fields.get(dir_slug) or "").upper() if dir_slug else ""
    if re.search(r"\bIN\b", raw_dir) and "OUT" not in raw_dir:
        lines.append("\t\t\tinput-enable;")
        if role == "vbus":      # the pin's role (from its name or a decision) contradicts the workbook: say so ON the pin
            lines.append("\t\t\t/* NEEDS REVIEW: treated as a VBUS enable (an output) but the workbook says this pin's direction is INPUT - "
                         "confirm the direction before using this state */")
    elif "OUT" in raw_dir:
        if role == "oc":
            lines.append("\t\t\t/* NEEDS REVIEW: treated as an over-current pin (an input) but the workbook says this pin's direction is OUTPUT - "
                         "confirm the direction before using this state */")
        xl = excel_level(rec) if LEVEL_SOURCE == "excel" else None
        if LEVEL_SOURCE == "none":
            level = None
        if xl:
            lvl, raw = xl
            tag = f'source: Excel ResetValue "{raw}"'
            if level and level != lvl:
                tag += f" - the accepted-change convention would be {level}"
            lines.append(f"\t\t\t{lvl};  /* {tag} */")
            # a VBUS enable whose Excel level switches the supply ON at boot is stated, never silently emitted
            if (polarity == "GPIO_ACTIVE_LOW" and lvl == "output-low") or (polarity == "GPIO_ACTIVE_HIGH" and lvl == "output-high"):
                lines.append("\t\t\t/* NEEDS REVIEW: with this enable polarity the supply is ON at boot (Excel level) - confirm it is intended */")
        elif level:
            lines.append(f"\t\t\t{level};  /* NEEDS REVIEW: level inferred as 'regulator OFF at boot' - "
                         f"matches the 2 accepted examples, not derivable from Excel; confirm */")
        else:
            lines.append("\t\t\t/* NEEDS REVIEW: output pin - add output-high or output-low per hardware "
                         "design (level is not derivable from Excel) */")
    if d is not None and d.bias_directive and not d.bias_needs_review:
        lines.append(f"\t\t\t{d.bias_directive};")
    else:
        lines.append("\t\t\t/* NEEDS REVIEW: bias not determined from Excel */")
    lines += ["\t\t};", "\t};"]
    return "\n".join(lines) + "\n"


def insert_states(text, blocks, anchor_labels):
    """Insert new state nodes INSIDE an existing &tlmm block (the one already holding this project's USB
    pin nodes if any, else the last one), not in a new block at the end of the file."""
    spans = []
    for m in re.finditer(r"^&tlmm\s*\{", text, re.MULTILINE):
        spans.append((m.start(), _find_matching_brace(text, m.end() - 1)))
    if not spans:
        return text.rstrip("\n") + "\n\n&tlmm {\n" + blocks + "};\n", "in a new &tlmm block (none existed)"
    chosen = None
    for a in anchor_labels:
        pos = re.search(rf"^[ \t]*{re.escape(a)}\s*:", text, re.MULTILINE)
        if pos:
            chosen = next(((s0, e0) for s0, e0 in spans if s0 <= pos.start() < e0), None)
        if chosen:
            break
    chosen = chosen or spans[-1]
    close = chosen[1] - 1                       # index of the block's closing '}'
    head = text[:close]
    if not head.endswith("\n"):
        head += "\n"
    return head + blocks + text[close:], "inside the existing &tlmm block"


# ---------------------------------------------------------------- pattern
def detect_pattern(text, requested):
    """Which VBUS/OC shape does THIS project use? Never guesses: with no evidence in the supplied
    files and no --pattern, returns (None, reason) instead of picking one - the caller still
    generates every pin's own real electrical config (pinctrl state), since that never depended on
    knowing controller-vs-regulator in the first place; only the CONSUMER WIRING (the &usb0 property
    vs. a separate regulator-fixed node) genuinely needs this decision, and is skipped until it's
    made. Genuinely conflicting evidence (both patterns found) still stops hard - that is a real
    ambiguity in the supplied files, not an absence of information."""
    if requested in ("controller", "regulator"):
        return requested, f"explicitly requested (--pattern {requested})"
    ctrl = re.search(r"oc-gpio|vbus-out-gpio|fault_detect|power_enable", text)
    reg = re.search(r"regulator-fixed", text) and re.search(r"vbus", text, re.I)
    if ctrl and not reg:
        return "controller", f"evidence in files: '{ctrl.group(0)}' (oc-gpio / vbus-out-gpio style)"
    if reg and not ctrl:
        return "regulator", "evidence in files: regulator-fixed VBUS node(s)"
    if ctrl and reg:
        raise SystemExit("Both controller-style (oc-gpio/vbus-out-gpio) and regulator-fixed VBUS evidence "
                         "found - ambiguous. Re-run with --pattern controller or --pattern regulator.")
    return None, ("no existing VBUS/OC convention found in the supplied files, and no --pattern given - "
                  "safe TLMM pin states may be generated; ambiguous or unsupported-controller pins "
                  "are excluded and listed in the report. Re-run with --pattern controller or "
                  "--pattern regulator to also get the consumer wiring (&usb0/&usb1 blocks or "
                  "regulator-fixed nodes).")


def render_regulator(label, gpio, polarity, note=""):
    lines = [f"{label}_vbus_regulator: {label}_vbus_regulator {{",
             '\tcompatible = "regulator-fixed";',
             f'\tregulator-name = "{label}_vbus_regulator";',
             "\tregulator-min-microvolt = <5000000>;",
             "\tregulator-max-microvolt = <5000000>;"]
    if polarity == "GPIO_ACTIVE_HIGH":
        lines.append("\tenable-active-high;")
    if note:
        lines.append(f"\t/* NEEDS REVIEW: {note} */")
    lines.append(f"\tgpio = <&tlmm {gpio} {polarity}>;")
    lines.append("};")
    return "\n".join(lines) + "\n"


def insert_after_node(text, node_name, block):
    m = re.search(rf"^([ \t]*)(?:[\w-]+:\s*)?{re.escape(node_name)}\s*\{{", text, re.MULTILINE)
    if not m:
        return None
    open_pos = text.index("{", m.start())
    close = _find_matching_brace(text, open_pos)
    end = close
    if text[end:end + 1] == ";":
        end += 1
    indent = m.group(1)
    indented = "\n".join((indent + l if l.strip() else l) for l in block.rstrip("\n").split("\n"))
    return text[:end] + "\n\n" + indented + text[end:]



REM_SLUG = None


def remark(rec):
    """The workbook's own Remarks cell for this pin (e.g. 'mounting option only for EU/US subPCB'),
    single-lined and safe to place inside a DTS comment."""
    if not REM_SLUG:
        return ""
    v = re.sub(r"\s+", " ", str(rec.fields.get(REM_SLUG) or "")).strip()
    return v.replace("*/", "* /")


def net_trace(rec):
    """The workbook's imported schematic net trace for this pin (SoC ball -> connector pins), single-lined and
    shortened. Evidence for a human (or an AI) deciding a flagged pin; the tool itself never interprets it."""
    # The trace is the field that actually contains endpoints ("... ENDPNT"). Taking the first column whose NAME
    # contains "schematic" returned an unrelated one (its value was just "PinMux1"), so the report showed a pin-mux
    # group name where the net trace should be.
    v = next((x for k, x in rec.fields.items() if "schematic" in k and isinstance(x, str) and "ENDPNT" in x), None)
    if v is None:
        v = next((x for x in rec.fields.values() if isinstance(x, str) and "ENDPNT" in x), None)
    if not v:
        return ""
    return re.sub(r"\s+", " ", v).strip()[:260].replace("|", "/").replace("*/", "* /")


def net_trace_full(rec):
    """The whole net trace (not shortened) - for parsing, never for display."""
    v = next((x for k, x in rec.fields.items() if "schematic" in k and isinstance(x, str) and "ENDPNT" in x), None)
    if v is None:
        v = next((x for x in rec.fields.values() if isinstance(x, str) and "ENDPNT" in x), None)
    return v or ""


IG_SLUG = None


def is_hub_related(rec, gen5_slug):
    """True if "HUB" appears in this pin's Gen5 Signal Name or Interface Group - confirmed as a
    reliable split against real data: every genuinely hub-related USB pin (hub reset, hub I2C
    enable, the hub's I2C bus pins) carries "HUB" in one or both fields on the real CCS20 workbook
    (e.g. "CPU_USBHUB_CTRL_1V8:RST", Interface Group "USB20HUB"), while every pure USB-controller
    pin (oc-gpio/vbus-out-gpio, PHY port select) does not. This reads the Database sheet's own
    text - it does not infer hub membership from a ball name or any other heuristic."""
    gen5 = str(rec.fields.get(gen5_slug) or "")
    ig = str(rec.fields.get(IG_SLUG) or "") if IG_SLUG else ""
    return "HUB" in gen5.upper() or "HUB" in ig.upper()


def write_inventory(path, project, matched, cat, fs, ig_slug, gen5_slug):
    """Every USB-matched pin with the workbook's own values and what the tool decided for it."""
    decision = {}
    for kind, label in (("oc_gpio", "over-current pin - used"), ("vbus_gpio", "VBUS-enable pin - used")):
        for e in cat[kind]:
            decision[id(e["record"])] = label
    for e in cat["needs_review"]:
        decision[id(e["record"])] = "NEEDS REVIEW - " + e["reason"]
    for e in cat["unsupported_controller"]:
        decision[id(e["record"])] = f"NOT GENERATED - unsupported pin controller register {e['regsetting']}"
    for e in cat["unconfirmed_tlmm"]:
        decision[id(e["record"])] = "NOT GENERATED - GPIO-like pin, pin controller unconfirmed"
    for rec in cat["differential"]:
        decision[id(rec)] = "PHY / differential pad - not TLMM, excluded"
    unsupported_ids = {id(e["record"]) for e in cat["unsupported_controller"]}
    unconfirmed_ids = {id(e["record"]) for e in cat["unconfirmed_tlmm"]}

    def cell(rec, key):
        v = rec.fields.get(fs.get(key)) if fs.get(key) else None
        return re.sub(r"\s+", " ", str(v)).strip() if v not in (None, "") else "-"

    rows = ["| Project | Interface Group | Signal | Pin | Ball name | Direction | Reset | Pull | Drive | Excel remark | Tool decision |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    for rec in matched:
        n = gpio_num(rec)
        pin = f"gpio{n}" if n else ("N/A (non-TLMM controller)" if id(rec) in unsupported_ids else
                                   "N/A (controller unconfirmed)" if id(rec) in unconfirmed_ids else
                                   "N/A (no TLMM GPIO number)" if decision.get(id(rec), "").startswith("NEEDS REVIEW") else "N/A (PHY pad)")
        ig = re.sub(r"\s+", " ", str(rec.fields.get(ig_slug) or "-")) if ig_slug else "-"
        sig = re.sub(r"\s+", " ", str(rec.fields.get(gen5_slug) or "-")).replace("|", "/")
        rows.append(f"| {project} | {ig} | {sig} | {pin} | {cell(rec, 'ball_name')} | {cell(rec, 'direction')} | "
                    f"{cell(rec, 'reset_value')} | {cell(rec, 'io_gpio_pull')} | {cell(rec, 'io_drv_strength')} | "
                    f"{remark(rec) or '-'} | {decision.get(id(rec), '-')} |")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# USB signal inventory - {project}\n\nEvery value below is read from the workbook cell "
                f"({len(matched)} USB-matched pins). 'Tool decision' is what usb_dts_tool.py did with the pin.\n\n"
                + "\n".join(rows) + "\n")


def build_combined(project, pattern, consumers, state_labels, out_text, regulators, hub_labels=None, previews=None,
                   unsupported_controller_count=0):
    """ONE file with every USB piece, in the reference order (controllers, pinctrl, regulators), for reading and
    review. The pieces belong in different places in the real tree, so this is a VIEW, not a drop-in file.
    Section 2 (pinctrl) is split into "without hub" / "with hub" using hub_labels - a real, separate
    sub-system (an I2C-attached hub IC) from the USB controller itself, not a cosmetic grouping.
    When the pattern is unconfirmed, `previews` (from main()'s pattern_previews) renders BOTH possible
    consumer-wiring shapes side by side here, from the same real GPIO/polarity data - for comparison
    only; neither is written to --out, since picking between them is a real hardware decision this
    tool cannot make from the Excel sheet alone."""
    hub_labels = hub_labels or set()
    previews = previews or []
    nodes = {n.label: n for n in parse_dts_nodes(out_text)}

    def section(labels):
        txt = ""
        for lbl in dict.fromkeys(labels):
            if lbl in nodes:
                t = nodes[lbl].raw_text.strip()
                txt += "\t" + (t if t.endswith(";") else t + ";") + "\n\n"
        return txt

    non_hub_labels = [l for l in state_labels if l not in hub_labels]
    hub_only_labels = [l for l in state_labels if l in hub_labels]
    non_hub_txt, hub_txt = section(non_hub_labels), section(hub_only_labels)
    pinctrl_parts = []
    if non_hub_txt:
        pinctrl_parts.append("/* 2a. Without hub (pure USB controller) */\n\n&tlmm {\n" + non_hub_txt.rstrip("\n") + "\n};\n")
    if hub_txt:
        pinctrl_parts.append("/* 2b. With hub (I2C-attached hub IC) */\n\n&tlmm {\n" + hub_txt.rstrip("\n") + "\n};\n")
    pinctrl_section = "\n".join(pinctrl_parts) if pinctrl_parts else "/* none */\n"

    if consumers:
        controller_section = "\n".join(consumers)
    elif pattern == "unconfirmed" and previews:
        controller_section = (
            "/* pattern unconfirmed - BOTH possible shapes are previewed below (section 1-preview), from "
            "the same real GPIO/polarity data. Neither is confirmed; pick one and re-run with --pattern to "
            "get it written into --out. */\n"
        )
    elif pattern == "unconfirmed":
        controller_section = (
            "/* No consumer preview generated: no safe TLMM over-current/VBUS candidate was found. "
            "Unsupported-controller pins are listed in the report and were not emitted. */\n"
        )
    elif unsupported_controller_count:
        controller_section = (
            f"/* No consumer blocks generated: no safe TLMM OC/VBUS candidates were available; "
            f"{unsupported_controller_count} non-TLMM controller pin(s) were excluded. See the report. */\n"
        )
    else:
        controller_section = "/* none generated - already present in the target file, or none needed */\n"
    parts = [
        "/*\n"
        f" * USB DTS - {project} ({pattern} style) - everything in one place, for review.\n"
        " * Generated by tools/tool/usb_dts_tool.py. Every NEEDS REVIEW comment and the report's review tables are\n"
        " * open decisions - nothing marked so is verified.\n"
        " *\n"
        " * WHERE EACH PART BELONGS in the real tree (this file is a view, not a drop-in file):\n"
        " *   1. controller blocks  -> overlay .dts, top level\n"
        " *   2. TLMM pinctrl nodes -> pinmux .dtsi inside &tlmm (only safely generated TLMM states appear in --out;\n"
        " *                           unsupported controller pins are listed in the report, not emitted here) -\n"
        " *                           split below into without-hub / with-hub (see \"HUB\" in the Gen5 Signal\n"
        " *                           Name or Interface Group - these are the pins that belong to a separate,\n"
        " *                           I2C-attached hub IC, not the USB controller itself)\n"
        " *   3. regulators         -> INSIDE a parent node of the overlay, NOT top level\n"
        " */\n",
        "/* 1. USB CONTROLLER BLOCKS */\n\n" + controller_section,
        "/* 2. USB PINCTRL (existing nodes as updated, plus any new ones) */\n\n" + pinctrl_section,
    ]
    if previews:
        pv_parts = ["/* 1-PREVIEW. BOTH possible consumer-wiring shapes, for comparison - NEITHER is\n"
                   " * confirmed or written to --out. Pick one (ask whoever owns this project's BSP/base\n"
                   " * devicetree which one it actually expects) and re-run with --pattern to confirm it. */\n"]
        for tok, lbl, ctrl_txt, reg_txt in previews:
            pv_parts.append(f"\n/* {lbl} ({tok}) - IF controller style: */\n" +
                            (ctrl_txt if ctrl_txt else "/* no oc/vbus pin found for this instance */\n"))
            pv_parts.append(f"\n/* {lbl} ({tok}) - IF regulator style: */\n" +
                            (reg_txt if reg_txt else "/* no vbus pin found for this instance */\n"))
        parts.insert(2, "".join(pv_parts))
    if pattern == "regulator":
        parts.append("/* 3. USB VBUS FIXED REGULATORS - place INSIDE a parent node, not top level */\n\n" +
                     ("\n".join(regulators) if regulators else "/* none generated */\n"))
    return "\n".join(parts)


# ---------------------------------------------------------------- validation
def dtc_wrap(text):
    """Make a generated fragment compilable on its own: stub every &label it uses but does not define
    (the base tree's &tlmm, &usb0 ... are not in the fragment) and expand the two GPIO flag macros, which
    come from a header dtc itself does not read. This checks SYNTAX and label consistency only - it cannot
    check bindings, because the real base tree is not available here."""
    body = re.sub(r"\bGPIO_ACTIVE_HIGH\b", "0", re.sub(r"\bGPIO_ACTIVE_LOW\b", "1", text))
    defined = set(re.findall(r"^[ \t]*([A-Za-z_]\w*)[ \t]*:", body, re.MULTILINE))
    used = set(re.findall(r"&([A-Za-z_]\w*)", body))
    stubs = "".join(f"\t{l}: stub_{l} {{ }};\n" for l in sorted(used - defined))
    header = "" if "/dts-v1/" in body else "/dts-v1/;\n"
    return header + "/ {\n" + stubs + "};\n" + body


def run_dtc(text):
    """(ok, message) for a dtc syntax compile of `text`, or (None, reason) when dtc is not installed."""
    dtc = shutil.which("dtc")
    if not dtc:
        return None, "dtc not installed here - run your normal DTS build to compile-check."
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".dts", delete=False) as f:
        f.write(dtc_wrap(text))
        tmp = f.name
    try:
        p = subprocess.run([dtc, "-I", "dts", "-O", "dtb", "-o", os.devnull, tmp], capture_output=True, text=True)
    finally:
        os.unlink(tmp)
    if p.returncode == 0:
        return True, "dtc OK (syntax and labels; base-tree labels stubbed - bindings are NOT checked)"
    return False, "dtc reported: " + re.sub(r"\s+", " ", p.stderr.strip())[:400]


def validate(text):
    problems = []
    if text.count("{") != text.count("}"):
        problems.append(f"brace mismatch: {text.count('{')} '{{' vs {text.count('}')} '}}'")
    labels = re.findall(r"^\s*([A-Za-z_][\w]*)\s*:\s*[\w@,.\-]+\s*\{", text, re.MULTILINE)
    dupes = sorted({l for l in labels if labels.count(l) > 1})
    if dupes:
        problems.append(f"duplicate node labels: {dupes}")
    ok, dtc_result = run_dtc(text)
    if ok is False:
        problems.append(dtc_result)
    return problems, (dtc_result if ok is not None else None)


# ---------------------------------------------------------------- main
def build_argparser():
    ap = argparse.ArgumentParser(description="USB DTS updater/generator (deterministic).")
    ap.add_argument("--excel", required=True)
    ap.add_argument("--project", help="project name exactly as in the workbook (see --list-projects)")
    ap.add_argument("--list-projects", action="store_true", help="print the projects in the workbook and exit")
    ap.add_argument("--dts", help="existing target .dts/.dtsi to check and update (optional)")
    ap.add_argument("--out", help="output file (default: <dts>.updated or usb_generated.dtsi)")
    ap.add_argument("--report", default="usb_dts_report.md")
    ap.add_argument("--pattern", choices=["auto", "controller", "regulator"], default="auto",
                    help="VBUS/OC shape: controller = oc-gpio/vbus-out-gpio on &usbN; regulator = "
                         "regulator-fixed node per instance. auto = detect from the files, or stop and ask.")
    ap.add_argument("--overlay", help="(regulator pattern) existing overlay .dts to place regulator nodes in")
    ap.add_argument("--insert-after", help="(regulator pattern) node name in --overlay to insert the new "
                                           "regulator nodes after, at that node's indentation")
    ap.add_argument("--vbus-pin", action="append", default=[], metavar="GPIO_N",
                    help="HUMAN DECISION (repeatable): accept a pin the tool set aside as shared/ambiguous as the VBUS "
                         "enable of its USB controller. Only valid when exactly one of its runtime roles is a "
                         "PWR_EN/VBUS signal. The decision is recorded in the report and as a NEEDS REVIEW comment.")
    ap.add_argument("--combined", help="also write ONE file with every USB piece (controller blocks, pinctrl, "
                                       "regulators) in the reference order, for review")
    ap.add_argument("--inventory", help="also write a markdown inventory of every USB-matched pin (values from the "
                                        "workbook, plus what the tool decided for each)")
    ap.add_argument("--ai-advice", action="store_true",
                    help="append an AI-written EXPLANATION of the flagged pins to the report (needs the LLM set up in "
                         "backend/.env). It never changes the generated DTS - the output is byte-identical with or without it.")
    ap.add_argument("--ai-max-pins", type=int, default=12, help="most flagged pins to send to the AI (default 12)")
    ap.add_argument("--level-source", choices=("excel", "convention", "none"), default="convention",
                    help="where an OUTPUT pin's high/low comes from. convention (default): the accepted change's 'regulator off at boot' "
                         "inference for VBUS enables, flagged; any other output pin stays NEEDS REVIEW. excel: the workbook's ResetValue "
                         "column, taken as it stands and tagged - it is a register default ('0: low' on almost every pin), so an "
                         "active-low enable ends up ON at boot (stated in the file). none: always NEEDS REVIEW. A fact you state always wins.")
    ap.add_argument("--role", action="append", default=[], metavar="SIGNAL=oc|vbus",
                    help="HUMAN DECISION (repeatable, or comma-separated): teach the tool a project's own signal naming - the part "
                         "after the colon in the Gen5 Signal Name. E.g. --role SIGNAL_A=oc --role SIGNAL_B=vbus. "
                         "Polarity still comes from the _B/_N suffix. Recorded in the report.")
    ap.add_argument("--template", action="append", default=[], metavar="FILE",
                    help="a DTS file of THIS project's BSP whose FORMAT the output should follow (repeatable: e.g. a pinmux .dtsi and an overlay "
                         ".dts). Only the format is learned - indentation, label style, mux/config vs flat nodes, blank lines, the copyright "
                         "header - never the template's pins, labels or includes. The output is written as separate files by role.")
    ap.add_argument("--kernel", default=None, metavar="VERSION",
                    help="Linux kernel version (e.g. 6.6, 5.15) when no template is available: the pin-state FORMAT follows that kernel "
                         "(nested mux{}/config{} up to 6.1, flat '-state' nodes from 6.2). The format is learned from that kernel tag's "
                         "reference file when the network allows, else from a built-in table of measured rules; outside the measured range "
                         "nothing is guessed. A --template always wins. Mainline conventions: a vendor BSP kernel may differ.")
    ap.add_argument("--kernel-base", default=None, metavar="URL",
                    help="where the kernel tree is read from, as <URL>/<tag>/<path>: point it at YOUR vendor tree (an internal git mirror's raw-file "
                         "URL) to learn the vendor kernel's format instead of mainline's. Also settable with env KERNEL_RAW_BASE.")
    ap.add_argument("--kernel-reference", default=None, metavar="PATH",
                    help="path inside the kernel tree of the file to learn the format from (default: listed per SoC in app/kernel_profiles.json)")
    ap.add_argument("--out-dir", default=None, metavar="DIR",
                    help="where the separate output files (pinmux .dtsi, overlay .dts) are written (default: <--out without extension>_files)")
    ap.add_argument("--include-pin", action="append", default=[], metavar="GPIO_N",
                    help="HUMAN DECISION (repeatable, or comma-separated): treat this pin as USB-related although the USB match did not "
                         "include it (e.g. one the AI audit flagged). Python generates its values from the Excel; recorded in the report.")
    ap.add_argument("--exclude-pin", action="append", default=[], metavar="GPIO_N",
                    help="HUMAN DECISION (repeatable, or comma-separated): this pin belongs to ANOTHER project (e.g. a USB-named sheet "
                         "lists it for a different board). It is dropped from the cross-sheet conflicts and the AI suggestions for this "
                         "run and listed in the report. Nothing was generated for it, so no output changes.")
    ap.add_argument("--ai-suggest", action="store_true",
                    help="AI SUGGESTIONS to confirm, never applied: a role (oc / vbus) for signals Python could wire but does not recognise, "
                         "and a check of unmatched GPIOs for missed USB pins. Python validates every answer (quote, direction, free slot).")
    ap.add_argument("--ai-propose", action="store_true",
                    help="ask the AI to DRAFT a completed DTS from the verified output + the evidence + --facts. Python then "
                         "verifies the draft against the workbook (repair loop), compiles it with dtc, and marks every line "
                         "whose values are in none of the allowed sources. The draft goes to its own file and the report; "
                         "it never replaces --out.")
    ap.add_argument("--facts", help="facts/decisions the AI may use, one per line (a text file, or the text itself) - e.g. "
                                    "\"gpio<N> boots output-low (schematic)\", \"<device>: <compatible> at <address> (datasheet)\". "
                                    "They are the only source for anything the workbook does not say.")
    ap.add_argument("--docs", nargs="*", default=[],
                    help="text/markdown documents (datasheet notes, schematic text, a reference change description) for the AI "
                         "to READ. It extracts facts, each with a verbatim quote; Python rejects any fact whose quote is not "
                         "literally in the document. Accepted facts feed the generation. (PDF: convert to text first.)")
    ap.add_argument("--ai-draft", help="a DTS draft (yours, a colleague's, another AI's) for the AI to CORRECT: it is checked "
                                       "against the workbook first and the exact findings are handed to the AI to fix")
    ap.add_argument("--ai-proposal-out", help="where to write the HYBRID final file (default: <out>.hybrid.dtsi)")
    ap.add_argument("--no-cache", action="store_true",
                    help="re-read the workbook instead of using the parsed-workbook cache in .cache/")
    ap.add_argument("--no-update", action="store_true",
                    help="check-only for existing nodes: report differences but do not modify them")
    ap.add_argument("--keyword", default="usb")
    ap.add_argument("--match", choices=["both", "gen5"], default="both",
                    help="match keyword in Gen5 Signal Name AND/OR Interface Group (default both)")
    return ap


def _reference_template(soc):
    """Which wiring style to assume when nothing confirms one: the accepted example for THIS SoC if there is one, else the default.
    Data, not code: app/reference_templates.json. Returns {pattern, source, same_soc}."""
    import json
    path = os.environ.get("USB_REFERENCE_TEMPLATES") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "reference_templates.json")
    try:
        cfg = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {"templates": [], "default": {"pattern": "regulator", "source": "the accepted Suzuki change (a different SoC)"}}
    for t in cfg.get("templates", []):
        if str(t.get("soc", "")).strip().upper() == str(soc or "").strip().upper() and t.get("pattern") in ("controller", "regulator"):
            return {"pattern": t["pattern"], "source": t.get("source", "an accepted example"), "same_soc": True}
    d = cfg.get("default", {})
    return {"pattern": d.get("pattern") if d.get("pattern") in ("controller", "regulator") else "regulator",
            "source": d.get("source", "an accepted example (a different SoC)"), "same_soc": False}


def _template_completion(args, state_labels, pattern_name="regulator"):
    """HYBRID MODE ONLY. When nothing confirms the VBUS/OC style and none was chosen, complete the output with the accepted
    change's template (controller style, or regulator-fixed + &usbN consumers with levels by its convention) - by running this
    same tool with --pattern <controller|regulator> on a scratch copy of the arguments. Pure Python: no AI. Returns
    (verified_text, combined_text) or None. The caller's own (Python-only) outputs are never touched."""
    import contextlib
    import copy
    import io
    import tempfile
    td = tempfile.mkdtemp(prefix="tmpl_")
    try:
        a2 = copy.copy(args)
        a2.pattern, a2.out, a2.report = pattern_name, os.path.join(td, "t.dtsi"), os.path.join(td, "t.md")
        a2.combined, a2.inventory, a2.overlay, a2.insert_after = os.path.join(td, "t_combined.dts"), None, None, None
        a2.ai_advice = a2.ai_propose = a2.ai_suggest = False       # the scratch run is pure Python: no AI call of its own (they cost tokens twice)
        a2.docs, a2.ai_draft, a2.facts, a2.ai_proposal_out = [], None, "", None
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            rb = run(a2)
        nodes = {n.label: n.raw_text.strip() for n in parse_dts_nodes(rb.out_text)}
        body = "".join("\t" + (nodes[l] if nodes[l].endswith(";") else nodes[l] + ";") + "\n\n"
                       for l in dict.fromkeys(state_labels) if l in nodes)
        text = ("&tlmm {\n" + body.rstrip("\n") + "\n};\n") if body else ""
        regs = cons = ""
        if pattern_name == "controller":
            cons = "\n\n".join(re.findall(r"^&usb\d+ \{.*?^\};", rb.out_text, re.S | re.M))
            if cons:
                text += "\n" + cons + "\n"
        elif rb.overlay_path and os.path.exists(rb.overlay_path):
            parts = re.split(r"/\* === SECTION \d.*?=== \*/", open(rb.overlay_path, encoding="utf-8").read(), flags=re.S)
            regs = parts[1].strip("\n") if len(parts) > 1 else ""
            cons = parts[2].strip("\n") if len(parts) > 2 else ""
            if cons:
                text += "\n" + cons + "\n"
            if regs:
                regs = "\n".join(("\t" + l if l.strip() else l) for l in regs.splitlines())
                text += "\n/ {\n" + regs + "\n};\n"
        combined = open(a2.combined, encoding="utf-8").read() if os.path.exists(a2.combined) else None
        return text, combined, bool(regs.strip() or cons.strip())
    except SystemExit:
        return None
    finally:
        shutil.rmtree(td, ignore_errors=True)


def _verify_text(args, role_map, text):
    """The independent verifier (verify_dts) on a text - the same check the hybrid result gets - for the generated files."""
    import tempfile
    import types
    import verify_dts as _vd
    tmpd = tempfile.mkdtemp(prefix="bundle_")
    try:
        dp = os.path.join(tmpd, "files.dts")
        with open(dp, "w", encoding="utf-8") as fh:
            fh.write(text)
        vr = _vd.run(types.SimpleNamespace(
            excel=args.excel, project=args.project, dts=dp, report=os.path.join(tmpd, "v.md"), existing=[], no_cache=args.no_cache,
            all_pins=False, decided=list(args.vbus_pin or []), decided_roles=[f"{k}={v}" for k, v in sorted(role_map.items())]))
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)
    return {"wrong": list(vr.wrong), "ambiguous": list(vr.ambiguous), "unverifiable": list(vr.unverifiable), "ok": len(vr.ok)}


class UsbToolResult:
    """Returned by run() for programmatic callers (e.g. an API). Holds the same information the CLI
    prints and writes to files, without requiring the caller to read files back off disk."""
    def __init__(self):
        self.out_path = self.report_path = self.inventory_path = self.combined_path = self.overlay_path = None
        self.out_text = self.report_text = self.pattern = self.pattern_evidence = None
        self.summary = {}
        self.roles_applied = []
        self.included_pins = []
        self.excluded_pins = []
        self.sheet_conflict_pins = []
        self.role_warnings = []
        self.files, self.files_dir, self.bundle_notes, self.bundle_verification = [], None, [], None
        self.ai_ledger = []
        self.ai_suggest = None
        self.ai_check = None
        self.ai_proposal = None
        self.validation_problems = []
        self.dtc_result = None
        self.projects = None       # set only when list_projects was requested
        self.kernel_resolution = None
        self.overview = None
        self.exit_code = 0




class _Items(list):
    """The open-items list. Each item is either a DECISION (a person must answer it) or INFO (a note about what was deliberately not
    generated). A site marks a decision with `items.kind = "decision"` just before appending; the default - and the state after every
    append - is "info". `kinds` stays aligned with the items, also after insert(0, ...)."""
    def __init__(self):
        super().__init__()
        self.kinds, self.kind = [], "info"

    def append(self, text):
        super().append(text); self.kinds.append(self.kind); self.kind = "info"

    def insert(self, i, text):
        super().insert(i, text); self.kinds.insert(i, self.kind); self.kind = "info"


def _build_overview(result, problems, python_needs_review):
    """What the person actually gets, counted from the FINAL text (the written files; else the hybrid / Python text) - never from an
    intermediate step - so the headline cannot disagree with the files shown below it."""
    import re as _re
    from app import dts_style as _ds
    ap = result.ai_proposal or {}
    if result.files:
        text = "\n".join(f["text"] for f in result.files).replace("\r\n", "\n")
    else:
        text = (ap.get("final_text") if ap.get("status") == "clean" and ap.get("final_text") else result.out_text) or ""
    pin_states = 0
    for b in _ds.top_level_blocks(_ds._norm(text)):
        if b["header"].startswith("&tlmm"):
            pin_states += len(_ds.parse_pin_nodes(_ds._inner(b["text"]))[0])
    consumers = sorted(set(_re.findall(r"^&(usb\w*)\s*\{", text, _re.M)))
    regulators = len(_re.findall(r'compatible\s*=\s*"regulator-fixed"', text))
    review_lines = len(_re.findall(r"NEEDS REVIEW", text))
    _kinds = ap.get("open_item_kinds") or ["decision"] * len(ap.get("open_items") or [])
    open_items = sum(1 for k in _kinds if k == "decision")
    open_info = len(_kinds) - open_items
    bv = result.bundle_verification or (ap.get("verification") and {k: ap["verification"].get(k, 0) for k in ("wrong", "ambiguous", "unverifiable", "ok")}) or None
    if bv:
        bv = {"wrong": len(bv["wrong"]) if isinstance(bv.get("wrong"), list) else bv.get("wrong", 0),
              "ambiguous": len(bv["ambiguous"]) if isinstance(bv.get("ambiguous"), list) else bv.get("ambiguous", 0),
              "unverifiable": len(bv["unverifiable"]) if isinstance(bv.get("unverifiable"), list) else bv.get("unverifiable", 0),
              "ok": bv.get("ok", 0)}
    wiring = None
    if consumers or regulators:
        if ap.get("template_assumed"):
            wiring = (f"{ap.get('template_pattern')} style - ASSUMED from {ap.get('template_source')}; no project file confirms it (choose a pattern or attach the project's DTS to remove the assumption)"
                      + ("" if ap.get("template_same_soc", True) else " (a different SoC: weaker evidence)"))
        else:
            wiring = f"{result.pattern} style ({result.pattern_evidence})"
    else:
        wiring = "no consumer nodes generated (the wiring style is not confirmed - see the decisions list)"
    ledger = result.ai_ledger or []
    ran = [r for r in ledger if r.get("calls", 0) > 0]
    changed = bool(ap.get("diff") and any(l[:1] in "+-" and not l.startswith(("+++", "---")) for l in str(ap.get("diff")).splitlines()))
    if problems or (bv and bv["wrong"]):
        status, headline = "problems", f"{len(problems) + (bv['wrong'] if bv else 0)} problem(s) found - read them before using the files"
    elif open_items or review_lines or python_needs_review:
        n = max(open_items, 0)
        status = "needs_decisions"
        headline = (f"Generated and verified - {n} decision(s) still need a person" if n
                    else f"Generated and verified - {review_lines} line(s) marked NEEDS REVIEW")
    else:
        status, headline = "complete", "Generated and verified - nothing left to decide"
    # the THREE parts of an accepted change, each from the final text: regulators (root nodes), pin states (&tlmm), USB nodes (&usbN)
    blocks = _ds.top_level_blocks(_ds._norm(text))
    reg_txt = "\n\n".join(b["text"] for b in blocks if b["header"].strip().startswith("/") and "regulator-fixed" in b["text"])
    tl_txt = "\n\n".join(b["text"] for b in blocks if b["header"].startswith("&tlmm"))
    usb_txt = "\n\n".join(b["text"] for b in blocks if _re.match(r"&usb\w*", b["header"].strip()))
    where = lambda role: next((f["name"] for f in (result.files or []) if f["role"] == role), "the combined output")
    ctrl = (not regulators) and bool(consumers) and "controller" in str(ap.get("template_pattern") or result.pattern)
    parts = [
        {"key": "regulators", "title": "Regulators", "what": "regulator-fixed nodes (the VBUS supply switched by a GPIO)", "count": regulators,
         "file": where("overlay") if regulators else None, "text": reg_txt,
         "why_not": None if regulators else ("not used: this wiring style puts the over-current / VBUS-enable GPIOs directly on &usbN (controller style), so no regulator node exists"
                                              if ctrl else "not generated: the wiring style is not confirmed, or no VBUS-enable pin was found (see the decisions list)")},
        {"key": "pin_states", "title": "Pin states", "what": "pinctrl states under &tlmm (function, drive-strength, bias, output level)", "count": pin_states,
         "file": where("pinmux") if pin_states else None, "text": tl_txt,
         "why_not": None if pin_states else "none: no USB pin could be generated from the workbook"},
        {"key": "usb_nodes", "title": "USB nodes", "what": "&usbN nodes that use the pin states / regulators", "count": len(consumers),
         "file": where("overlay") if consumers else None, "text": usb_txt,
         "why_not": None if consumers else "not generated: the wiring style is not confirmed - choose a pattern or attach the project's DTS"},
    ]
    return {"status": status, "headline": headline, "parts": parts, "pin_states": pin_states, "consumers": consumers, "regulators": regulators,
            "review_lines": review_lines, "open_decisions": open_items, "open_info": open_info, "wiring": wiring, "verifier": bv,
            "files": [f["name"] for f in (result.files or [])],
            "format_source": sorted({f["style_source"] for f in (result.files or [])}),
            "ai": {"changed_output": changed, "ran": [r.get("label") or r.get("feature") for r in ran],
                   "calls": sum(r.get("calls", 0) for r in ran), "tokens": sum(r.get("tokens", 0) for r in ran)}}


def run(args):
    """The tool's whole pipeline, given a populated args object (from build_argparser().parse_args(),
    or any object with the same attributes - e.g. built directly by an API caller). Returns a
    UsbToolResult. Raises SystemExit for the same user errors the CLI reports (unknown project,
    ambiguous pattern, etc.)."""
    result = UsbToolResult()
    # A template that is also an output would be overwritten before it is read - refuse BEFORE anything is written.
    _writes = {os.path.abspath(x) for x in (getattr(args, "out", None), getattr(args, "report", None), getattr(args, "combined", None),
                                            getattr(args, "inventory", None), getattr(args, "ai_proposal_out", None)) if x}
    _od = getattr(args, "out_dir", None)
    for _tp in (getattr(args, "template", None) or []):
        if os.path.abspath(_tp) in _writes or (_od and os.path.dirname(os.path.abspath(_tp)) == os.path.abspath(_od)
                                              and os.path.basename(_tp) == os.path.basename(_tp)
                                              and re.search(r"_usb_(pinmux\.dtsi|overlay\.dts)$", os.path.basename(_tp))):
            raise SystemExit(f"--template {_tp!r} is also a file this run writes; it would be overwritten before it is read. "
                             f"Use a different --out / --report / --out-dir.")
    global USE_CACHE
    USE_CACHE = not args.no_cache
    if args.list_projects:
        result.projects = list_projects(args.excel)
        print("Projects in this workbook: " + ", ".join(result.projects))
        return result
    if not args.project:
        raise SystemExit("--project is required (use --list-projects to see the names)")

    parsed, records, soc_entity = load_records(args.excel, args.project)
    _log("[2] Finding USB pins and checking TLMM membership ...")
    sheet_conflicts = find_sheet_conflicts(load_sheet_signals(args.excel), args.project, records,
                                           resolve_gen5_signal_slug(args.project, soc_entity, parsed.columns), args.keyword)
    excluded_nums = set()
    for raw in (getattr(args, "exclude_pin", None) or []):
        for part in str(raw).split(","):
            part = part.strip()
            if not part:
                continue
            if not re.fullmatch(r"(?i)gpio[_ ]?\d+|\d+", part):
                raise SystemExit(f"--exclude-pin {part!r}: expected GPIO_N")
            excluded_nums.add(re.sub(r"\D", "", part))
    excluded_conf = [c for c in sheet_conflicts if c[2] in excluded_nums]
    sheet_conflicts = [c for c in sheet_conflicts if c[2] not in excluded_nums]
    gen5_slug = resolve_gen5_signal_slug(args.project, soc_entity, parsed.columns)
    if not gen5_slug:
        raise SystemExit("Could not find a Gen5 Signal Name column for this project.")
    ig_slug = _resolve_field_slugs_for_summary(args.project, soc_entity, parsed.columns).get("interface_group")

    slug_to_base = {c.slug: c.base_field for c in parsed.columns if c.slug and c.base_field}
    project_slugs = {c.slug for c in parsed.columns if c.slug and c.entity == args.project} | \
        {c.slug for c in parsed.columns if c.slug and c.entity == soc_entity} | \
        {c.slug for c in parsed.columns if c.slug and c.category == "pinmux_register"}
    _fs = resolve_field_slugs(project_slugs, slug_to_base, soc_entity)
    pms_slug, dir_slug = _fs.get("pinmux_setting"), _fs.get("direction")
    global REM_SLUG, IG_SLUG, LEVEL_SOURCE
    LEVEL_SOURCE = getattr(args, "level_source", "convention")      # direct callers that do not pass it keep the older behaviour
    REM_SLUG = next((c.slug for c in parsed.columns if c.slug and c.entity == args.project
                     and re.search(r"remark", (c.base_field or c.slug), re.IGNORECASE)), None)
    IG_SLUG = ig_slug
    if not pms_slug:
        raise SystemExit("Could not find the Pinmux Setting column (needed to check TLMM membership).")

    matched = match_usb(records, gen5_slug, ig_slug, args.keyword, args.match)
    if not matched:
        raise SystemExit(f"No pins matched {args.keyword!r}.")
    included = []
    for raw in (getattr(args, "include_pin", None) or []):
        for part in str(raw).split(","):
            part = part.strip()
            if not part:
                continue
            n_inc = re.sub(r"\D", "", part) if re.fullmatch(r"(?i)gpio[_ ]?\d+|\d+", part) else None
            hit = [r for r in records if (n_inc and gpio_num(r) == n_inc) or _ball_name(r) == part]
            if not hit:
                raise SystemExit(f"--include-pin {part!r}: no such pin in project {args.project!r}")
            for r in hit:
                if id(r) not in {id(m) for m in included}:
                    included.append(r)       # also a pin the match already found but set aside (e.g. boot-strap): your decision wins
    if included:
        _have = {id(m) for m in matched}
        matched = list(matched) + [r for r in included if id(r) not in _have]
    role_map = {}
    for raw in (getattr(args, "role", None) or []):
        for part in str(raw).split(","):
            if not part.strip():
                continue
            if "=" not in part:
                raise SystemExit(f"--role {part.strip()!r}: expected SIGNAL=oc or SIGNAL=vbus")
            sig, role = part.split("=", 1)
            role = role.strip().lower()
            if role not in ("oc", "vbus"):
                raise SystemExit(f"--role {part.strip()!r}: the role must be 'oc' or 'vbus'")
            role_map[sig.strip().split(":")[-1].upper()] = role
    cat, fallback = categorize_usb_records(
        matched, gen5_slug, pms_slug, parsed.columns, project_slugs, ig_slug, role_map=role_map,
        force_include_ids={id(r) for r in included},
    )
    result.roles_applied = [f"{k}={v}" for k, v in sorted(role_map.items())]
    result.included_pins = [_ball_name(r) for r in included]
    result.excluded_pins = [f"GPIO_{n}" for n in sorted(excluded_nums, key=int)]
    result.sheet_conflict_pins = [f"GPIO_{n}" for n in sorted({c[2] for c in sheet_conflicts}, key=int)]     # still open: a person can accept the Database sheet for them
    # A role must fit the workbook's own direction: an over-current pin is an INPUT, a VBUS enable is an OUTPUT. Checked for every
    # role, whether it came from the signal's name or from --role / an AI suggestion a person confirmed. A warning, never a block.
    for _kind, _what, _bad in (("oc_gpio", "an over-current pin", "OUT"), ("vbus_gpio", "a VBUS enable", "IN")):
        for _e in cat[_kind]:
            _d = str(_e["record"].fields.get(dir_slug) or "").upper() if dir_slug else ""
            _in, _out = bool(re.search(r"\bIN\b", _d)), "OUT" in _d
            if (_bad == "OUT" and _out and not _in) or (_bad == "IN" and _in and not _out):
                result.role_warnings.append(
                    f"{_ball_name(_e['record'])} ({_e['signal'].split(':')[-1]}) is treated as {_what}, but the workbook says its direction is "
                    f"'{_d.strip()}' - {_what} should be an {'input' if _bad == 'OUT' else 'output'}; check the role")

    override_notes = {}          # gpio number -> human-decision note, shown in the report and on the generated node
    for raw in args.vbus_pin:
        n = re.sub(r"\D", "", raw)
        entry = next((e for e in cat["needs_review"] if gpio_num(e["record"]) == n), None)
        if entry is None:
            raise SystemExit(f"--vbus-pin {raw}: GPIO_{n} is not a pin this tool set aside for review (it is either "
                             f"already used automatically, or not a USB pin in this project).")
        rec = entry["record"]
        segs = [x for x in _signal_segments(str(rec.fields.get(gen5_slug) or "")) if not _is_boot_strap_segment(x)]
        cands = [x for x in segs if re.search(r"PWR_EN|VBUS", x.split(":")[-1].upper())]
        if len(cands) != 1:
            raise SystemExit(f"--vbus-pin {raw}: expected exactly ONE VBUS-enable role on GPIO_{n}, found "
                             f"{len(cands)} among: {segs}")
        sig = cands[0]
        tail = sig.split(":")[-1].strip().upper()
        pol = "GPIO_ACTIVE_LOW" if re.search(r"(^|_)(B|N)$", tail) else "GPIO_ACTIVE_HIGH"
        others = [x for x in segs if x != sig]
        cat["needs_review"].remove(entry)
        cat["vbus_gpio"].append({"record": rec, "polarity": pol, "signal": sig,
                                 "instance_token": detect_usb_instance_token(sig)})
        override_notes[n] = (f"GPIO_{n} is a shared pin (also: {', '.join(x.split(':')[-1] for x in others) or 'none'}) - "
                             f"accepted as the VBUS enable for {sig.split(':')[0]} by an explicit --vbus-pin decision, "
                             f"not derived from the workbook")

    dts_text, dts_eol = read_dts(args.dts) if args.dts else ("", "\n")
    existing = existing_pin_nodes(dts_text) if dts_text else {}

    # ---- group safe candidates per controller instance
    by_instance = {}
    for kind in ("oc_gpio", "vbus_gpio"):
        for e in cat[kind]:
            tok = e.get("instance_token") or "UNKNOWN"
            by_instance.setdefault(tok, {"oc_gpio": [], "vbus_gpio": []})[kind].append(e)
    labels = resolve_instance_labels(sorted(by_instance))

    # ---- 1. update existing pinctrl nodes (safe pins only, minimal in-place patch)
    # "other_gpio" pins are included here too: their role is unrecognized, but their pin
    # config (drive-strength/bias) is real Excel data like any other TLMM pin, and if the
    # pin already has a node in the target file its stale values get corrected the same way.
    safe_recs = [e["record"] for k in ("oc_gpio", "vbus_gpio", "other_gpio") for e in cat[k]]
    updated_text = dts_text
    update_changes, update_skipped = [], []
    if dts_text and safe_recs and not args.no_update:
        updated_text, update_changes, update_skipped = minimal_patch(dts_text, safe_recs)
    existing_after = existing_pin_nodes(updated_text) if updated_text else {}

    # ---- 2. verification table for every USB-related TLMM pin already in the file
    verify_rows = []
    all_tlmm = safe_recs + [e["record"] for e in cat["needs_review"]]
    for rec in all_tlmm:
        n = gpio_num(rec)
        pin = f"gpio{n}" if n else None
        hit = existing.get(pin) if pin else None
        if hit:
            status, diffs = compare_to_excel(hit[2], rec)
            if status == "DIFFERS":
                status = ("UPDATED by this run" if any(c[0] == hit[0] for c in update_changes)
                          else ("DIFFERS - left unchanged (--no-update)" if args.no_update
                                else "DIFFERS - NOT changed, needs a human"))
            verify_rows.append((_ball_name(rec), hit[0], status, "; ".join(diffs) or "-"))
        else:
            verify_rows.append((_ball_name(rec), "(none in file)", "-", "-"))

    _log("[3] Updating existing nodes / generating missing ones ...")
    # ---- 3. consumer nodes / regulators + any new pinctrl states
    overlay_text, overlay_eol = read_dts(args.overlay) if args.overlay else ("", "\n")
    pattern, pattern_evidence = detect_pattern(updated_text + "\n" + overlay_text, args.pattern)
    new_state_blocks, consumer_blocks, regulator_blocks = [], [], []
    notes = []          # (severity, text) for the report
    used_labels = set(re.findall(r"^\s*([A-Za-z_]\w*)\s*:", updated_text + overlay_text, re.MULTILINE))

    sep = label_sep(updated_text)
    inferred = []       # (label, pin, level) - flagged inferences, listed in the report
    state_labels_used = []   # pinctrl labels the generated consumers reference (existing or new)
    hub_state_labels = set()   # subset of state_labels_used whose pin is hub-related (for --combined's split)
    reg_consumer_blocks = []

    def state_for(rec, kind, label, tok, i, level=None):
        """Label of an existing exclusive node for this pin, or a newly generated state."""
        n = gpio_num(rec)
        pin = f"gpio{n}"
        hit = existing_after.get(pin)
        if hit and hit[1]:
            return hit[0], None
        if hit and not hit[1]:
            return None, f"{_ball_name(rec)} is configured in shared node '{hit[0]}' - not referenced automatically"
        base = state_name(tok, kind, f"{label}_{'oc' if kind == 'oc_gpio' else 'vbus'}_active")
        lbl = base if base not in used_labels else f"{base}_{i + 2}"
        used_labels.add(lbl)
        pol = next((e["polarity"] for e in cat["vbus_gpio"] if e["record"] is rec), None) if kind == "vbus_gpio" else None
        new_state_blocks.append(render_state(lbl, rec, dir_slug, level, sep, pol, "oc" if kind == "oc_gpio" else "vbus"))
        if level:
            inferred.append((lbl, pin, level))
        return lbl, None

    pattern_previews = []   # [(tok, label, controller_preview_text, regulator_preview_text)] - for --combined only

    for tok in sorted(by_instance, key=lambda t: labels[t][0]):     # usb0 before usb1 in the output
        label, confirmed = labels[tok]
        confirmed = confirmed and soc_entity == CONFIRMED_SOC
        if pattern is None:
            # No VBUS/OC convention known (no --pattern, no evidence in the supplied files). Still
            # generate each pin's own real electrical config - that never depended on knowing
            # controller-vs-regulator - and skip ONLY the consumer wiring, which genuinely does.
            oc = vbus = None
            oc_label = vbus_label = None
            pin_labels = []
            for kind in ("oc_gpio", "vbus_gpio"):
                for i, e in enumerate(by_instance[tok][kind]):
                    rec = e["record"]
                    n = gpio_num(rec)
                    if kind == "oc_gpio" and oc is None:
                        oc = (n, e["polarity"])
                    if kind == "vbus_gpio" and vbus is None:
                        vbus = (n, e["polarity"])
                    lbl, warn = state_for(rec, kind, label, tok, i)
                    if lbl:
                        state_labels_used.append(lbl)
                        pin_labels.append(lbl)
                        if kind == "oc_gpio" and oc_label is None:
                            oc_label = lbl
                        if kind == "vbus_gpio" and vbus_label is None:
                            vbus_label = lbl
                        if is_hub_related(rec, gen5_slug):
                            hub_state_labels.add(lbl)
                    if warn:
                        notes.append(("review", warn))
            notes.append(("review", f"&{label} ({tok}): electrical config generated above (section 2), but NOT "
                                    f"wired into any consumer - pattern unconfirmed; re-run with --pattern "
                                    f"controller or --pattern regulator to get this instance's &usb consumer block."))
            # Both possible shapes, rendered from the SAME real GPIO/polarity data - for comparison
            # in --combined ONLY. Neither is written to --out: picking between them is still a real
            # hardware decision (which this project's base devicetree/BSP actually expects), not
            # something derivable from the Excel sheet - showing both is not the same as confirming
            # either one.
            ctrl_preview = render_consumer(label, oc, vbus, pin_labels, [], confirmed) if (oc or vbus) else ""
            reg_preview = ""
            if vbus:
                reg_label = f"{label}_vbus_regulator"
                reg_preview = (render_regulator(label, vbus[0], vbus[1]) + "\n" +
                              render_reg_consumer(label, reg_label, vbus_label))
                if oc:
                    reg_preview += (f"\n/* over-current (GPIO_{oc[0]}, {oc[1]}) has no confirmed wiring under "
                                   f"the regulator pattern in this codebase - not previewed */\n")
            pattern_previews.append((tok, label, ctrl_preview, reg_preview))
            continue
        if pattern == "controller":
            det = detect_existing_usb_node(updated_text, label) if updated_text else {"kind": "none"}
            if det["kind"] == "reference":
                notes.append(("info", f"&{label} ({tok}): an override node already exists - left untouched. "
                                       f"Verify it contains the oc-gpio/vbus-out-gpio lines below."))
                for kind, prop in (("oc_gpio", "oc-gpio"), ("vbus_gpio", "vbus-out-gpio")):
                    for e in by_instance[tok][kind]:
                        n = gpio_num(e["record"])
                        want = f"{prop} = <&tlmm {n} {e['polarity']}>"
                        present = want in re.sub(r"\s+", " ", updated_text)
                        notes.append(("info", f"    expected `{want};` - "
                                               f"{'present' if present else 'NOT FOUND in file - add it'}"))
                continue
            if det["kind"] == "full_definition":
                notes.append(("review", f"&{label} ({tok}): {det['detail']}. The controller is already defined "
                                         f"elsewhere - not generating a competing override. Review manually."))
                continue
            oc = vbus = None
            pin_labels, unlinked = [], []
            for kind in ("oc_gpio", "vbus_gpio"):
                for i, e in enumerate(by_instance[tok][kind]):
                    rec = e["record"]
                    n = gpio_num(rec)
                    if kind == "oc_gpio" and oc is None:
                        oc = (n, e["polarity"])
                    if kind == "vbus_gpio" and vbus is None:
                        vbus = (n, e["polarity"])
                    lbl, warn = state_for(rec, kind, label, tok, i)
                    if lbl:
                        pin_labels.append(lbl)
                        if is_hub_related(rec, gen5_slug):
                            hub_state_labels.add(lbl)
                    if warn:
                        unlinked.append(warn)
            oc_rec = next((e["record"] for e in by_instance[tok]["oc_gpio"]), None)
            vb_rec = next((e["record"] for e in by_instance[tok]["vbus_gpio"]), None)
            for used_n in [x[0] for x in (oc, vbus) if x]:
                if used_n in override_notes:
                    unlinked.append(override_notes[used_n])
                for sh, seg, n, why in sheet_conflicts:
                    if n == used_n:
                        unlinked.append(f"sheet '{sh}' puts {seg} on GPIO_{n}; {why} - wiring depends on the hardware "
                                        f"variant, confirm which applies")
            state_labels_used.extend(pin_labels)
            consumer_blocks.append(render_consumer(label, oc, vbus, pin_labels, unlinked, confirmed,
                                                   remark(oc_rec) if oc_rec else "", remark(vb_rec) if vb_rec else ""))
            notes.append(("info", f"&{label} ({tok}): generated"
                                  f"{'' if confirmed else ' - template lines only confirmed for the reference instance left as NEEDS REVIEW'}"))
        else:  # regulator pattern: regulator-fixed + &usbN consumer per instance
            reg_label = f"{label}_vbus_regulator"
            vb, ocs = by_instance[tok]["vbus_gpio"], by_instance[tok]["oc_gpio"]
            both = overlay_text + "\n" + updated_text
            det = detect_existing_usb_node(both, label) if both.strip() else {"kind": "none"}
            if re.search(rf"\b{reg_label}\s*:", both):
                notes.append(("info", f"{reg_label} ({tok}): already exists - left untouched."))
            elif not vb:
                notes.append(("review", f"{tok}: no safe VBUS-enable pin found - no regulator generated."))
            else:
                e = vb[0]
                n = gpio_num(e["record"])
                # FLAGGED inference: pin starts in the regulator's OFF state (matches both accepted examples)
                level = "output-high" if e["polarity"] == "GPIO_ACTIVE_LOW" else "output-low"
                lbl, warn = state_for(e["record"], "vbus_gpio", label, tok, 0, level)
                if warn:
                    notes.append(("review", warn))
                for sh, seg, cn, why in sheet_conflicts:
                    if cn == n:
                        notes.append(("review", f"{reg_label}: sheet '{sh}' puts {seg} on GPIO_{n}; {why} - confirm the variant"))
                regulator_blocks.append(render_regulator(label, n, e["polarity"], override_notes.get(n, "")))
                notes.append(("info", f"{reg_label} ({tok}): generated from {_ball_name(e['record'])} "
                                       f"({e['polarity']}{', enable-active-high' if e['polarity'] == 'GPIO_ACTIVE_HIGH' else ''})"))
                if det["kind"] == "none":
                    reg_consumer_blocks.append(render_reg_consumer(label, reg_label, lbl))
                if lbl:
                    state_labels_used.append(lbl)
                    if is_hub_related(e["record"], gen5_slug):
                        hub_state_labels.add(lbl)
                    notes.append(("info", f"&{label} ({tok}): consumer generated (status, vbus_dwc3-supply, pinctrl-0"
                                           f"{'' if label == CONFIRMED_LABEL else '; qcom,default-mode-host left as NEEDS REVIEW'})"))
                elif det["kind"] == "reference":
                    notes.append(("review", f"&{label} ({tok}): an override node already exists - not regenerated. "
                                             f"Verify it has vbus_dwc3-supply = <&{reg_label}> and "
                                             f"pinctrl-0 = <&{lbl}>."))
                else:
                    notes.append(("review", f"&{label} ({tok}): {det['detail']} - not generating a competing override."))
                for extra in vb[1:]:
                    notes.append(("review", f"{tok}: additional VBUS candidate {_ball_name(extra['record'])} "
                                             f"not used - one regulator per instance."))
            for i, e in enumerate(ocs):
                # The over-current pin's own electrical config is real Excel data whatever the wiring style - never drop it.
                # What is NOT confirmed is how a regulator-style consumer wires it, so the state is generated but not referenced.
                oc_lbl, oc_warn = state_for(e["record"], "oc_gpio", label, tok, i)
                if oc_warn:
                    notes.append(("review", oc_warn))
                if oc_lbl:
                    state_labels_used.append(oc_lbl)
                    if is_hub_related(e["record"], gen5_slug):
                        hub_state_labels.add(oc_lbl)
                notes.append(("review", f"{tok}: over-current pin {_ball_name(e['record'])} ({e['polarity']}): its pinctrl state "
                                         f"{'(' + oc_lbl + ') ' if oc_lbl else ''}is generated from the Excel but NOT referenced by the "
                                         f"consumer - no confirmed over-current wiring for the regulator pattern."))

    # ---- 3b. "other_gpio": pins with a real, single-purpose signal but no recognized role.
    # Never left out of the output: reuse the existing node if one covers this pin exclusively,
    # otherwise generate a real pinctrl state (mux/config, real drive-strength/bias/direction)
    # labeled from the pin's own signal - the role/consumer decision is a human's, but the pin's
    # own electrical config is not withheld pending that decision.
    other_gpio_rows = []   # (ball, label, status) for the report
    shared_rows = []
    for e in cat["other_gpio"]:
        rec = e["record"]
        n = gpio_num(rec)
        pin = f"gpio{n}"
        hit = existing_after.get(pin) if pin else None
        if hit and hit[1]:
            other_gpio_rows.append((_ball_name(rec), hit[0], "existing node reused/updated"))
        elif hit and not hit[1]:
            other_gpio_rows.append((_ball_name(rec), hit[0],
                                    "configured in a SHARED node - not referenced individually"))
        else:
            lbl = derive_label_from_signal(e["signal"], used_labels)
            used_labels.add(lbl)
            new_state_blocks.append(render_state(lbl, rec, dir_slug, None, sep))
            state_labels_used.append(lbl)
            if is_hub_related(rec, gen5_slug):
                hub_state_labels.add(lbl)
            other_gpio_rows.append((_ball_name(rec), lbl, "new node generated"))
        if hit and hit[1]:
            state_labels_used.append(hit[0])
            if is_hub_related(rec, gen5_slug):
                hub_state_labels.add(hit[0])

    # ---- 3c. SHARED pins: one physical GPIO on several nets. The pin's own electrical setting belongs to the PIN, so its state is generated ONCE
    # and flagged; which node owns the GPIO (only one driver can claim it) stays a decision. The pin remains in the NEEDS REVIEW list.
    for e in cat["needs_review"]:
        if not e.get("shared"):
            continue
        rec = e["record"]
        n = gpio_num(rec)
        if not n or (existing_after.get(f"gpio{n}") or (None, None))[0]:
            continue
        # owner: when exactly ONE of the nets names the hub (the Database text says HUB), the pin is that hub's own control line (e.g. its I2C enable) and the
        # other nets only ride on it - decided from the workbook's own text, flagged so it can be confirmed; otherwise the owner stays a human decision
        hub_nets = [x.strip() for x in e["shared"] if "HUB" in x.upper()]
        owner = hub_nets[0] if len(hub_nets) == 1 else None
        sig0 = owner or e["shared"][0].strip()
        lbl = derive_label_from_signal(sig0, used_labels)
        used_labels.add(lbl)
        blk = render_state(lbl, rec, dir_slug, None, sep)
        nets = "; ".join(x.strip() for x in e["shared"])
        if owner:
            e["owner"] = owner
            note = ("\t\t\t/* decided: SHARED PIN - one physical GPIO on %d nets (%s). The pin is owned by the hub line \"%s\" (the only net that names the hub); "
                    "the other net(s) depend on it - confirm on the schematic */\n" % (len(e["shared"]), nets.replace("*/", "* /"), owner.replace("*/", "* /")))
        else:
            note = ("\t\t\t/* NEEDS REVIEW: SHARED PIN - one physical GPIO on %d nets (%s). One pin state is generated for the pin; only ONE node can own the GPIO "
                    "(gpios = <&tlmm %s ...>), the other should depend on it (for example through a shared regulator or supply) - decide which, from the schematic */\n"
                    % (len(e["shared"]), nets.replace("*/", "* /"), n))
        blk = re.sub(r"(\t\tconfig \{\n\t\t\tpins = [^\n]*\n)", lambda m: m.group(1) + note, blk, count=1)
        new_state_blocks.append(blk)
        state_labels_used.append(lbl)
        e["reason"] += f" - the pin state {lbl} is generated once (shared); " + (f"owner decided: {owner}" if owner else "the owner of the GPIO is undecided")
        shared_rows.append((_ball_name(rec), lbl))

    # ---- 4. assemble output
    out_text = updated_text
    if new_state_blocks:
        usb_nums = {gpio_num(r) for r in matched} - {None}
        anchors = [hit[0] for pin, hit in existing_after.items() if pin[4:] in usb_nums]
        out_text, where = insert_states(out_text, "".join(new_state_blocks), anchors)
        notes.append(("info", f"{len(new_state_blocks)} new pinctrl state(s) inserted {where}"))
    if consumer_blocks:
        out_text = out_text.rstrip("\n") + "\n\n" + "\n".join(consumer_blocks)
    if not out_text.strip():
        out_text = "/* nothing to generate */\n"

    overlay_out = None
    if regulator_blocks or reg_consumer_blocks:
        reg_text, cons_text = "\n".join(regulator_blocks), "\n".join(reg_consumer_blocks)
        if args.overlay and args.insert_after:
            placed = overlay_text
            if reg_text:
                placed = insert_after_node(overlay_text, args.insert_after, reg_text)
                if placed is None:
                    raise SystemExit(f"--insert-after: node {args.insert_after!r} not found in {args.overlay}")
            if cons_text:
                placed = placed.rstrip("\n") + "\n\n" + cons_text
            overlay_out = args.overlay + ".updated"
            write_dts(overlay_out, placed, overlay_eol)
            notes.append(("info", f"regulators inserted after '{args.insert_after}' and &usbN consumers appended "
                                   f"at the end of {_shown(overlay_out)}"))
        else:
            overlay_out = os.path.join(os.path.dirname(os.path.abspath(args.out or "x")) if args.out else ".",
                                       "usb_vbus_regulators.dts")
            parts = []
            if reg_text:
                parts.append("/* === SECTION 1 - regulators: place INSIDE the same parent node as the project's other\n"
                             " * board nodes (not top-level). The tool cannot know that parent; use\n"
                             " * --overlay + --insert-after to automate. === */\n" + reg_text)
            if cons_text:
                parts.append("/* === SECTION 2 - top-level: append to the overlay file. === */\n" + cons_text)
            write_dts(overlay_out, "\n".join(parts), dts_eol)
            notes.append(("review", f"regulators/consumers written to {_shown(overlay_out)} as a snippet - place SECTION 1 inside "
                                     f"the correct parent node (use --overlay + --insert-after)."))

    out_path = args.out or ((args.dts + ".updated") if args.dts else "usb_generated.dtsi")
    for _p in (out_path, args.report):
        os.makedirs(os.path.dirname(os.path.abspath(_p)), exist_ok=True)
    write_dts(out_path, out_text, dts_eol)
    problems, dtc_result = validate(out_text)
    if cat["unsupported_controller"]:
        controllers = sorted({re.match(r"^[A-Za-z_]+", e["regsetting"]).group(0)
                              for e in cat["unsupported_controller"]})
        problems.append(
            "USB rows use non-TLMM pin-controller settings (" + ", ".join(controllers) +
            "); they were excluded from &tlmm output because this tool has no confirmed DTS binding for that controller."
        )
    if cat["unconfirmed_tlmm"]:
        problems.append(
            f"{len(cat['unconfirmed_tlmm'])} GPIO-like USB pin(s) were not emitted: this workbook has no "
            "register-setting data identifying their pin controller, so they are not known to be Qualcomm TLMM. "
            "They are listed in the report with their Excel values."
        )

    # ---- 5. report
    R = []
    R.append(f"# USB DTS report - {args.project}\n")
    R.append(f"- Workbook: `{os.path.basename(args.excel)}`  |  Target DTS: "
             f"`{os.path.basename(args.dts) if args.dts else '(none - generated from scratch)'}`")
    R.append(f"- Output: `{out_path}`")
    R.append(f"- USB-matched pins: **{len(matched)}** (match mode: {args.match})  |  "
             f"oc/vbus: {len(cat['oc_gpio']) + len(cat['vbus_gpio'])}  |  "
             f"other GPIOs: {len(cat['other_gpio'])}  |  PHY pads: {len(cat['differential'])}  |  "
             f"unsupported controller: {len(cat['unsupported_controller'])}  |  "
             f"controller unconfirmed: {len(cat['unconfirmed_tlmm'])}  |  "
             f"needs review: {len(cat['needs_review'])}")
    R.append(f"- VBUS/OC pattern: **{pattern or 'unconfirmed'}** ({pattern_evidence})")
    for _w in result.role_warnings:
        R.append(f"- **WARNING - role vs workbook:** {_w}")
    if included:
        R.append("- Pins you chose to include (--include-pin): " + ", ".join(_ball_name(r) for r in included) +
                 " - not matched by the USB keyword; their values come from the Excel, their role is not assumed")
    if role_map:
        R.append("- Signal roles set by you (--role): " + ", ".join(f"{k} = {v}" for k, v in sorted(role_map.items())) +
                 " - not derived from the workbook's naming")
    if overlay_out:
        R.append(f"- Overlay output: `{_shown(overlay_out)}`")
    R.append(f"- Detected controller instances: "
             f"{', '.join(f'{t} -> &{labels[t][0]}' for t in sorted(by_instance)) or 'none'}")
    if fallback["instance_fallback"] or fallback["ball_name_fallback"]:
        R.append(f"- WARNING: RegSetting not resolvable for {fallback['instance_fallback'] + fallback['ball_name_fallback']} "
                 f"pin(s) (no usable register-setting data for them in this workbook) - "
                 f"{fallback['instance_fallback']} show a GPIO function in their own Port/Instance data, "
                 f"{fallback['ball_name_fallback']} were judged by ball name only. See the sections below.")
    upd_start = len(R)
    R.append("\n## Updated in existing file\n")
    if update_changes:
        R.append("| Node | Pin | Field | Old | New (Excel) |\n|---|---|---|---|---|")
        for lbl, pin, fld, old, new in update_changes:
            R.append(f"| {lbl} | {pin} | {fld} | {old} | {new} |")
    else:
        R.append("Nothing needed updating." if dts_text else "No existing file given.")
    for sk in update_skipped:
        R.append(f"- NOT CHANGED: {sk}")
    if not args.dts and not update_changes and not update_skipped:
        del R[upd_start:]        # "No existing file given." says nothing
    if override_notes:
        R.append("\n## Human decisions applied (--vbus-pin)\n")
        for n, t in override_notes.items():
            R.append(f"- {t}.")
    R.append("\n## Consumer nodes / new states\n")
    for sev, t in notes:
        R.append(f"- {t}")
    if args.dts:        # a table of "(none in file)" rows says nothing when there is no existing file to compare against
        R.append("\n## Verification: every USB-related TLMM GPIO, existing file as it was BEFORE this run\n")
        R.append("| Pin | Existing node | Status | Difference |\n|---|---|---|---|")
        for b, lbl, st, d in verify_rows:
            R.append(f"| {b} | {lbl} | {st} | {d} |")
    R.append("\n## Other USB-related GPIOs - real signal, role not auto-assigned (nothing withheld)\n")
    R.append("Single-purpose, TLMM-controlled pins whose signal doesn't match a known role keyword "
             "(OC/PWR_EN/VBUS). Unlike NEEDS REVIEW below, these are NOT ambiguous - each has exactly "
             "one real signal - so their real Excel values are included either as an update to their "
             "existing node or as a newly generated one. What's still a human decision: which consumer "
             "(if any) should reference the label, and for an output pin, its level. Split below by "
             "whether \"HUB\" appears in the pin's own Gen5 Signal Name / Interface Group - a real, "
             "separate sub-system (I2C-attached hub IC) from the USB controller itself.\n")
    other_hub = [e for e in cat["other_gpio"] if is_hub_related(e["record"], gen5_slug)]
    other_non_hub = [e for e in cat["other_gpio"] if e not in other_hub]

    def other_table(entries, heading):
        R.append(f"\n**{heading}**\n")
        if not entries:
            R.append("None.")
            return
        R.append("| Pin | Label | Status | Gen5 Signal Name | Excel remark |\n|---|---|---|---|---|")
        for e in entries:
            rec = e["record"]
            ball = _ball_name(rec)
            row = next((r for r in other_gpio_rows if r[0] == ball), None)
            sig = re.sub(r"\s+", " ", str(rec.fields.get(gen5_slug) or ""))
            R.append(f"| {ball} | {row[1] if row else '-'} | {row[2] if row else '-'} | {sig} | {remark(rec) or '-'} |")

    other_table(other_non_hub, "Without hub (pure USB controller)")
    other_table(other_hub, "With hub (I2C-attached hub IC)")

    if cat["unsupported_controller"]:
        R.append("\n## USB pins on an unsupported pin controller - not generated\n")
        R.append("These rows have register settings, but they are not `TLMM_GPIO_CFG...` settings. They must not be emitted as GPIO pinctrl states inside `&tlmm`. Add a controller-specific DTS renderer only after confirming that controller's binding and syntax.\n")
        R.append("| Pin | Register setting | Gen5 Signal Name |\n|---|---|---|")
        for entry in cat["unsupported_controller"]:
            rec = entry["record"]
            signal = re.sub(r"\s+", " ", str(rec.fields.get(gen5_slug) or "-"))
            R.append(f"| {_ball_name(rec)} | {entry['regsetting']} | {signal} |")

    R.append("\n## NEEDS REVIEW - not generated (never guessed)\n")
    review_hub = [e for e in cat["needs_review"] if is_hub_related(e["record"], gen5_slug)]
    review_non_hub = [e for e in cat["needs_review"] if e not in review_hub]

    def review_table(entries, heading):
        R.append(f"\n**{heading}**\n")
        if not entries:
            R.append("None.")
            return
        R.append("| Pin | Gen5 Signal Name | Why | Excel remark | Schematic net (from the workbook) |\n|---|---|---|---|---|")
        for e in entries:
            rec = e["record"]
            sig = re.sub(r"\s+", " ", str(rec.fields.get(gen5_slug) or ""))
            R.append(f"| {_ball_name(rec)} | {sig} | {e['reason']} | {remark(rec) or '-'} | {net_trace(rec) or '-'} |")

    review_table(review_non_hub, "Without hub")
    review_table(review_hub, "With hub")
    if excluded_nums:
        R.append("\n## Pins you excluded (--exclude-pin) - belong to another project\n")
        R.append("Dropped from the cross-sheet conflicts and the AI suggestions for this run: " + ", ".join(f"GPIO_{n}" for n in sorted(excluded_nums, key=int)) +
                 (". Sheet entries dismissed: " + "; ".join(f"{sh}: {seg}" for sh, seg, _n, _w in excluded_conf) if excluded_conf else "") + ".")
    if sheet_conflicts:
        R.append("\n## Cross-sheet conflicts - a USB sheet disagrees with the Database sheet\n")
        R.append("The Database sheet is what this tool reads. These USB-named sheets put the same signals on "
                 "different GPIOs - usually a hardware/mounting variant (check the Remarks column). "
                 "Nothing below was used to change the output; decide which variant applies.\n")
        R.append("| Sheet | Signal | GPIO (per sheet) | Database sheet says |\n|---|---|---|---|")
        for sh, seg, n, why in sheet_conflicts:
            R.append(f"| {sh} | {seg} | GPIO_{n} | {why} |")
    R.append("\n## PHY / differential pads - not TLMM, excluded from pinctrl\n")
    if cat["differential"]:
        R.append("| Pad | Gen5 Signal Name |\n|---|---|")
        for rec in cat["differential"]:
            sig = re.sub(r"\s+", " ", str(rec.fields.get(gen5_slug) or "-"))
            R.append(f"| {_ball_name(rec)} | {sig} |")
    else:
        R.append("None.")
    if cat["unconfirmed_tlmm"]:
        R.append("\n## GPIO-like pins whose pin controller is unconfirmed - not generated\n")
        R.append("Deliberately NOT in the PHY list above (that would hide real pins) and NOT emitted into `&tlmm` "
                 "(nothing in this workbook says these are Qualcomm TLMM pins - the pin-controller node, its "
                 "`pins = ...` naming and function names are that SoC's own). Their Excel values are kept here "
                 "so the data is not lost; a node can be generated once a real pinctrl DTS for this SoC is "
                 "available as a reference.\n")
        R.append("| Pin | Gen5 Signal Name | Direction | Drive | Pull | Why |\n|---|---|---|---|---|---|")

        _alt = {"io_drv_strength": ("drive_strength",), "io_gpio_pull": ("pull_select", "pull_enable")}

        def _cell(rec, key):
            # the concept's resolved slug first, then this workbook family's own column names
            for slug in [_fs.get(key)] + list(_alt.get(key, ())):
                v = rec.fields.get(slug) if slug else None
                if v not in (None, ""):
                    return re.sub(r"\s+", " ", str(v)).strip()
            return "-"

        for e in cat["unconfirmed_tlmm"]:
            rec = e["record"]
            sig = re.sub(r"\s+", " ", str(rec.fields.get(gen5_slug) or "-"))
            R.append(f"| {_ball_name(rec)} | {sig} | {_cell(rec, 'direction')} | {_cell(rec, 'io_drv_strength')} | "
                     f"{_cell(rec, 'io_gpio_pull')} | {e['reason']} |")
    if inferred:
        R.append("\n## Flagged inferences (present in the output, marked NEEDS REVIEW)\n")
        R.append("| State | Pin | Level | Basis |\n|---|---|---|---|")
        for lbl, pin, lvl in inferred:
            R.append(f"| {lbl} | {pin} | {lvl} | pin starts in the regulator's OFF state - consistent with the 2 accepted "
                     f"examples (an active-low enable -> output-high, an active-high enable -> output-low); Excel has no level column |")
    R.append("\n## Not confirmed by any data source (deliberately absent from output)\n")
    R.append("- Any USB hub IC `compatible` string / I2C address, and any hub reset/port-select "
             "property names: nothing in the workbook or existing DTS defines them.")
    R.append("- `debounce-delay-ms`, `qcom,default-mode-host` and the `dwc3` child for instances other than usb0.")
    R.append("- Whether an `&usbN` label maps to a given controller in your base devicetree "
             "(instances are numbered usb0, usb1, ... in detection order).")
    R.append("\n## Validation\n")
    R.append("- " + ("Structure OK (braces balanced, no duplicate labels)." if not problems
                     else "PROBLEMS: " + "; ".join(problems)))
    R.append("- " + (dtc_result or "dtc not installed here - run your normal DTS build to compile-check."))
    def _flagged_evidence():
        from app import usb_ai_advisor
        # Boot-strap-only pins are fully explained by Python (sampled at reset); sending them to the AI adds noise, not insight.
        flagged = [(e["record"], e["reason"]) for e in cat["needs_review"]
                   if not e["reason"].startswith(("Boot-strap", "USB match came only from a boot-strap"))]
        flagged += [(e["record"], f"pin controller is not TLMM ({e['regsetting']}) - not generated")
                    for e in cat["unsupported_controller"]]
        flagged += [(e["record"], e["reason"]) for e in cat["unconfirmed_tlmm"]]
        return [usb_ai_advisor.build_evidence(rec, _ball_name(rec), gen5_slug, ig_slug, why, remark(rec), _signal_segments)
                for rec, why in flagged]

    # WHERE THE AI WAS USED IN THIS RUN: one row per AI feature with its real calls and tokens, built from the gateway's own counters.
    from app import llm_client as _llm
    _ai_ledger = []

    def _snap():
        return dict(_llm.usage_totals)

    def _spent(before):
        now = _snap()
        return (now["calls"] - before["calls"],
                (now["prompt_tokens"] - before["prompt_tokens"]) + (now["completion_tokens"] - before["completion_tokens"]))

    if getattr(args, "ai_suggest", False):
        _b_sug = _snap()
        # SUGGESTIONS to confirm. Nothing here is read back into out_text or any generated value.
        from app import usb_ai_assist

        def _dir(rec):
            return str(rec.fields.get(dir_slug) or "") if dir_slug else ""

        def _ig(rec):
            return str(rec.fields.get(ig_slug) or "") if ig_slug else ""

        def _text(rec):
            parts = [str(rec.fields.get(k) or "") for k in (gen5_slug, ig_slug, "pinmuxed_name") if k] + [remark(rec) or "", net_trace_full(rec)]
            return re.sub(r"\s+", " ", " | ".join(p for p in parts if p))

        sheet_pins = {re.sub(r"\D", "", str(n)): f"{sheet}: {seg}" for sheet, seg, n, _m in sheet_conflicts}
        if not cat["other_gpio"]:
            _none = "there are no unclassified USB GPIO signals to classify"
        elif not any(e.get("instance_token") for e in cat["other_gpio"]):
            _none = ("none of the signals Python could not classify says which USB controller it belongs to (no USB20 / USB30 ... in its name), "
                 "so even a role guess could not be attached to a controller")
        else:
            _none = "every controller instance already has its over-current and VBUS pin"
        roles_res = usb_ai_assist.suggest_roles(
            usb_ai_assist.role_candidates(cat, _dir, _ball_name, lambda r: remark(r) or "", _ig), _none)
        aud_res = usb_ai_assist.audit(usb_ai_assist.audit_candidates([r for r in records if gpio_num(r) not in excluded_nums], matched, _text, _ball_name, gpio_num, sheet_pins))
        result.ai_suggest = {"roles": roles_res, "audit": aud_res}
        R.extend(usb_ai_assist.render(roles_res, aud_res))
        _c, _t = _spent(_b_sug)          # roles + audit only; the data check below is counted on its own ledger row
        # DATA CHECK: the AI reads every generated pin's own workbook cells and looks for contradictions in the data that WAS given.
        _b_chk = _snap()
        _items = []
        for _kind, _role in (("oc_gpio", "over-current"), ("vbus_gpio", "VBUS enable"), ("other_gpio", "other USB GPIO")):
            for _e in cat[_kind]:
                _r = _e["record"]
                _f = _r.fields
                _cells = [f"Signal: {_f.get(gen5_slug) or ''}", f"Interface group: {_ig(_r)}", f"Pinmuxed name: {_f.get('pinmuxed_name') or ''}",
                          f"Direction: {_dir(_r)}", f"Pull: {_f.get('io_gpio_pull') or ''}", f"Drive strength: {_f.get('io_drv_strength') or ''}",
                          f"ResetValue: {_f.get('resetvalue') or ''}", f"Remark: {remark(_r) or ''}", f"Net: {net_trace_full(_r)}"]
                _txt = re.sub(r"\s+", " ", " | ".join(c for c in _cells if c.split(": ", 1)[1].strip()))[:700]
                _items.append({"pin": _ball_name(_r), "text": _txt, "generated": f"treated as: {_role}"})
        chk_res = usb_ai_assist.check(usb_ai_assist.check_candidates(_items))
        result.ai_check = chk_res
        R.extend(usb_ai_assist.render_check(chk_res))
        _cc, _ct = _spent(_b_chk)
        _nf = sum(r["status"] == "finding" for r in chk_res["rows"])
        _ai_ledger.append({
            "feature": "usb_check", "calls": _cc, "tokens": _ct,
            "status": ("ran" if _cc else ("nothing to check" if chk_res["status"] == "none" else "not available: " + chk_res["note"])),
            "effect": f"{chk_res['checked']} pin(s) read; {_nf} verified finding(s), {sum(r['status'] != 'finding' for r in chk_res['rows'])} dropped - nothing in the DTS was changed"})
        _rr = roles_res["rows"]
        _unavail = [x["note"] for x in (roles_res, aud_res) if x["status"] in ("unavailable", "error")]
        _ai_ledger.append({
            "feature": "usb_suggest", "calls": _c, "tokens": _t,
            "status": ("ran" if _c else ("nothing to ask" if not _unavail else "not available: " + _unavail[0])),
            "effect": (f"roles: {sum(r['status'] == 'accepted' for r in _rr)} accepted, {sum(r['status'] == 'conflict' for r in _rr)} conflict(s), "
                       f"{sum(r['status'] == 'rejected' for r in _rr)} rejected; audit: "
                       f"{sum(r['status'] == 'include' for r in aud_res['rows'])} pin(s) suggested for inclusion - nothing applied until you confirm")})

    if getattr(args, "ai_advice", False):
        # Explanation only. Built from the categories above and appended AFTER everything else; nothing it
        # returns is read back into out_text, the overlay, or any generated value.
        from app import usb_ai_advisor
        _b_adv = _snap()
        _adv_lines = usb_ai_advisor.advise(_flagged_evidence(), getattr(args, "ai_max_pins", 12))
        R.extend(_adv_lines)
        _c, _t = _spent(_b_adv)
        _ai_ledger.append({"feature": "usb_advice", "calls": _c, "tokens": _t,
                           "status": "ran" if _c else "not used: " + ("nothing flagged needs an explanation" if _llm.is_configured() else "not configured"),
                           "effect": "explanations in the report only - the DTS is untouched"})

    if getattr(args, "ai_propose", False) or getattr(args, "ai_draft", None) or getattr(args, "docs", None):
        _b_prop = _snap()
        # HYBRID: Python-verified nodes + the AI additions that passed every check, in its OWN file.
        # out_text / --out are never touched by it.
        import tempfile
        import types
        from app import usb_ai_proposer
        import verify_dts as _vd
        facts_arg = getattr(args, "facts", "") or ""
        facts_text = (open(facts_arg, encoding="utf-8", errors="replace").read()
                      if facts_arg and os.path.isfile(facts_arg) else facts_arg)
        draft_text = read_dts(args.ai_draft)[0] if getattr(args, "ai_draft", None) else None

        # AI FIRST, THEN PYTHON: the AI reads the documents and proposes facts; Python keeps only those whose quote
        # literally occurs in the document. The accepted facts are what the generation below may use.
        fx = None
        if getattr(args, "docs", None):
            missing = [pth for pth in args.docs if not os.path.isfile(pth)]
            if missing:
                raise SystemExit("--docs: file not found: " + ", ".join(missing) + "  (a relative path is relative to the "
                                 "folder you ran the command from: " + os.getcwd() + ")")
            docs = {os.path.basename(pth): usb_ai_proposer.read_document(pth) for pth in args.docs}
            fx = usb_ai_proposer.extract_facts(docs)
            for d in fx.get("docs", []):
                _log(f"[AI] document {d['doc']}: {d['chars']} characters read -> {d['accepted']} fact(s) accepted, "
                     f"{d['rejected']} rejected" + (f" ({d['note']})" if d["note"] else ""))
            if fx.get("unavailable"):
                _log(f"[AI] document reading unavailable: {fx['unavailable']}")
            if fx["facts"]:
                facts_text = (facts_text + "\n" if facts_text else "") + "\n".join(fx["facts"])

        template_combined = None
        template_done = False

        # The VERIFIED material the AI starts from: only this run's USB nodes (never the whole target file).
        nodes_by_label = {n.label: n for n in parse_dts_nodes(out_text)}
        body = ""
        for lbl in dict.fromkeys(state_labels_used):
            if lbl in nodes_by_label:
                t = nodes_by_label[lbl].raw_text.strip()
                body += "\t" + (t if t.endswith(";") else t + ";") + "\n\n"
        verified_text = ("&tlmm {\n" + body.rstrip("\n") + "\n};\n") if body else ""
        for blk in consumer_blocks + reg_consumer_blocks:
            verified_text += "\n" + blk
        if regulator_blocks:
            verified_text += "\n/ {\n" + "\n".join(regulator_blocks) + "};\n"

        tmpl_ref = _reference_template(soc_entity)
        if pattern is None:
            tmpl = _template_completion(args, state_labels_used, tmpl_ref["pattern"])
            if tmpl and tmpl[0].strip() and tmpl[2]:        # only if the template ADDED something (regulators / consumers)
                verified_text, template_combined = tmpl[0], tmpl[1]
                template_done = True

        def _open_items():
            """Everything the output cannot state, each with the one action that resolves it - so nothing is silently dropped."""
            items = _Items()
            for tok in sorted(by_instance, key=lambda t: labels[t][0]):
                lbl = labels[tok][0]
                if by_instance[tok]["vbus_gpio"]:
                    continue
                cands = []
                for e in cat["needs_review"]:
                    segs = _signal_segments(str(e["record"].fields.get(gen5_slug) or ""))
                    mine = [sg for sg in segs if detect_usb_instance_token(sg) == tok
                            and re.search(r"PWR_EN|VBUS", sg.split(":")[-1].upper())]
                    if mine:
                        others = [sg.split(":")[-1] for sg in segs if sg not in mine]
                        cands.append(f"{_ball_name(e['record'])} (also carries {', '.join(others) or 'no other signal'}; "
                                     f"schematic net: {net_trace(e['record'])[:140] or 'not in the workbook'})")
                items.kind = "decision"
                items.append(f"&{lbl} ({tok}) has no VBUS-enable pin, so no regulator or consumer was generated for it. "
                             + (("Candidate: " + "; ".join(cands) + ". If it is the VBUS enable of this controller, enter it in "
                                 "'shared VBUS-enable pins' (--vbus-pin) and run again.") if cands else
                                "The workbook names no candidate pin - state the pin as a fact."))
            # Judge the FINAL text: a level a fact has already settled is no longer an open decision.
            _basis = (res_prop.get("final_text") if res_prop.get("status") not in (None, "empty", "failed", "unavailable") else None) or verified_text
            inferred_pins = usb_ai_proposer.pins_with_marker(_basis, "level inferred")
            unset_pins = usb_ai_proposer.pins_with_marker(_basis, "NEEDS REVIEW: output pin")
            excel_pins = usb_ai_proposer.pins_with_marker(_basis, "source: Excel ResetValue")
            on_pins = usb_ai_proposer.pins_with_marker(_basis, "the supply is ON at boot")
            if excel_pins:
                lv = {}
                for pin_ in excel_pins:
                    r_ = next((r for r in matched if gpio_num(r) == pin_[4:]), None)
                    xl_ = excel_level(r_) if r_ is not None else None
                    lv[pin_] = f"{pin_} {xl_[0]}" if xl_ else pin_
                items.kind = "decision"
                items.append(f"{len(excel_pins)} output level(s) are taken as they stand in the Excel's ResetValue column "
                             f"({', '.join(lv.values())}). ResetValue is the level at reset - not necessarily the level wanted once the "
                             f"driver runs - so confirm against the schematic, or state a different one as a fact, e.g. "
                             f"\"{excel_pins[0]} boots output-high\".")
            if on_pins:
                items.kind = "decision"
                items.append("With the Excel's level these VBUS enables switch their supply ON at boot: " + ", ".join(on_pins) +
                             " (an active-low enable driven low, or an active-high enable driven high). Confirm that is intended.")
            if inferred_pins:
                items.kind = "decision"
                items.append(f"{len(inferred_pins)} output level(s) are INFERRED by the accepted change's convention (regulator off at "
                             f"boot), not read from the Excel ({', '.join(inferred_pins)}) - confirm each against the schematic, or "
                             f"state it as a fact, e.g. \"{inferred_pins[0]} boots output-low\" (or output-high).")
            if unset_pins:
                items.kind = "decision"
                items.append(f"{len(unset_pins)} output pin(s) have no level yet ({', '.join(unset_pins)}) - the Excel gives no boot level "
                             f"for them (see the ResetValue note below). State each as a fact: \"{unset_pins[0]} boots output-high\" or "
                             f"\"{unset_pins[0]} boots output-low\" - for an enable, the evidence line says which one switches the supply on.")
            if inferred_pins or unset_pins:
                from collections import Counter
                rv = Counter(str(r.fields.get("resetvalue")).strip() for r in records if r.fields.get("resetvalue") not in (None, ""))
                if rv and LEVEL_SOURCE != "excel":
                    top, k = rv.most_common(1)[0]
                    items.append(f"About the Excel's ResetValue column: it says '{top}' on {k} of {sum(rv.values())} pins in this project - the "
                                 f"register's reset default, not this board's boot level - so it cannot tell one output pin from another. "
                                 f"Where it disagrees with an inferred level, the pin's polarity decides what the choice does: for an "
                                 f"active-low enable, output-low turns the supply ON at boot; for an active-high enable, output-high does.")
                from app import pin_hints
                by_gpio = {gpio_num(r): r for r in matched if gpio_num(r)}
                pol = {gpio_num(e["record"]): e["polarity"] == "GPIO_ACTIVE_LOW" for e in cat["vbus_gpio"]}
                ev = []
                for pin in dict.fromkeys(inferred_pins + unset_pins):
                    rec = by_gpio.get(pin[4:])
                    if rec is None:
                        continue
                    h = pin_hints.level_evidence(net_trace_full(rec), remark(rec), pol.get(pin[4:]), str(rec.fields.get("resetvalue") or ""))
                    if h:
                        ev.append(f"{pin}: {h}")
                if ev:
                    items.append("Evidence in the workbook for those levels (for the decision only - not used to set any value; 'undriven' = "
                                 "while the SoC pin is not driving the line, e.g. in reset, before a driver loads): " + " | ".join(ev))
            if pattern is None and not template_done and cat["other_gpio"] and not cat["oc_gpio"] and not cat["vbus_gpio"]:
                names = ", ".join(f"{e['signal'].split(':')[-1]} ({_ball_name(e['record'])})" for e in cat["other_gpio"])
                items.kind = "decision"
                items.insert(0, "No over-current or VBUS-enable pin was recognized from this project's signal names, so no regulator or "
                                f"consumer could be generated. Signals the tool could not classify: {names}. If one is the over-current or "
                                "VBUS enable, set its role in 'Signal roles' (SIGNAL=oc or SIGNAL=vbus; CLI: --role) and run again.")
            for _w in result.role_warnings:
                items.kind = "decision"
                items.insert(0, "ROLE vs WORKBOOK: " + _w + ".")
            if template_done and tmpl_ref["pattern"] == "regulator" and cat["oc_gpio"]:
                balls = ", ".join(_ball_name(e["record"]) for e in cat["oc_gpio"])
                items.kind = "decision"
                items.append(f"Over-current pin(s) {balls}: their pinctrl states are generated from the Excel but NO consumer references "
                             f"them - the accepted change (regulator style) has no over-current wiring. In the controller style they "
                             f"become `oc-gpio`. Decide which style this project uses.")
            unref = [e for e in cat["other_gpio"]]
            if unref:
                items.append("Pin state(s) generated from the Excel but not referenced by any node (which node uses them is a "
                             "hardware/BSP decision): " + ", ".join(f"{_ball_name(e['record'])} ({e['signal'].split(':')[-1]})"
                                                                     for e in unref) + ".")
            i2c = [e for e in cat["needs_review"] if "Shared I2C bus" in e["reason"]]
            if i2c:
                items.append("Shared I2C bus pin(s) " + ", ".join(_ball_name(e["record"]) for e in i2c) + " are configured by the I2C "
                             "controller's own pinctrl, not by a USB node, so no USB output is generated for them. If this project "
                             "needs a state for them, give the function name and the I2C node label as facts.")
            only_bs = [e for e in cat["needs_review"] if e["reason"].startswith("USB match came only from a boot-strap")]
            for e in only_bs:
                run_sigs = [sg for sg in _signal_segments(str(e["record"].fields.get(gen5_slug) or "")) if not _is_boot_strap_segment(sg)]
                items.append(f"{_ball_name(e['record'])} matched 'usb' only through a boot-strap signal (FORCE_USB-style); its real runtime "
                             f"function is {', '.join(run_sigs) or 'not USB'}, so it is deliberately not generated here.")
            bs = [e for e in cat["needs_review"] if e["reason"].startswith("Boot-strap")]
            if bs:
                items.append("Boot-strap pin(s) " + ", ".join(_ball_name(e["record"]) for e in bs) + ": sampled once at reset, so "
                             "deliberately no runtime state. Say so if your BSP needs one.")
            if any(is_hub_related(e["record"], gen5_slug) for k in ("needs_review", "other_gpio") for e in cat[k]):
                items.append("The hub IC's own node (compatible string, I2C address, bus label) is not generated: the workbook cannot "
                             "state it. Give them as facts, or attach the hub's datasheet notes under Documents.")
            for tok in sorted(by_instance, key=lambda t: labels[t][0]):
                if labels[tok][0] != CONFIRMED_LABEL:
                    items.append(f"&{labels[tok][0]} ({tok}): debounce-delay-ms and the dwc3 child (unit address, phy, speed) are "
                                 f"confirmed only for {CONFIRMED_LABEL}; state them as facts if this instance needs them.")
            if cat["unsupported_controller"] or cat["unconfirmed_tlmm"]:
                _names = [_ball_name(e["record"]) for e in cat["unsupported_controller"] + cat["unconfirmed_tlmm"]]
                _ctrls = sorted({m.group(1) for e in cat["unsupported_controller"]
                                 for m in [re.match(r"([A-Za-z_]+?)_?\d", str(e.get("regsetting") or ""))] if m})
                items.append(f"{len(_names)} GPIO-like pin(s) are on a pin controller this tool has no confirmed binding for"
                             + (f" ({', '.join(_ctrls)})" if _ctrls else "") + ": " + ", ".join(_names[:8])
                             + (f" and {len(_names) - 8} more" if len(_names) > 8 else "")
                             + ". They are listed with their Excel values in the report and stay a manual item: nothing is generated for them "
                               "until the tool supports that controller.")
            if sheet_conflicts:
                items.kind = "decision"
                items.append(f"{len(sheet_conflicts)} signal(s) are placed differently by a USB-named sheet than by the Database sheet "
                             f"(a hardware/mounting variant) - decide which variant applies.")
            return items

        def _verify(text):
            tmpd = tempfile.mkdtemp(prefix="aiprop_")
            try:
                dp = os.path.join(tmpd, "draft.dts")
                with open(dp, "w", encoding="utf-8") as fh:
                    fh.write(text)
                vr = _vd.run(types.SimpleNamespace(
                    excel=args.excel, project=args.project, dts=dp, report=os.path.join(tmpd, "v.md"),
                    existing=[args.dts] if args.dts else [], no_cache=args.no_cache, all_pins=False,
                    decided=list(args.vbus_pin or []),
                    decided_roles=[f"{k}={v}" for k, v in sorted(role_map.items())]))
            finally:
                shutil.rmtree(tmpd, ignore_errors=True)
            ok, msg = run_dtc(text)
            return {"wrong": list(vr.wrong), "ambiguous": list(vr.ambiguous), "unverifiable": list(vr.unverifiable),
                    "ok": len(vr.ok), "dtc": (ok, msg)}

        res_prop = usb_ai_proposer.propose(verified_text, _flagged_evidence(), facts_text,
                                           tmpl_ref["pattern"] if template_done else (pattern or "unconfirmed"), _verify, draft_text,
                                           allow_no_ai=bool(template_done or facts_text.strip()),
                                           skip_ai=not (facts_text.strip() or draft_text))
        if res_prop.get("status") != "unavailable":
            res_prop["template_assumed"] = template_done
            if template_done:
                res_prop["template_pattern"], res_prop["template_source"] = tmpl_ref["pattern"], tmpl_ref["source"]
                res_prop["template_same_soc"] = tmpl_ref["same_soc"]
            _oi = _open_items()
            res_prop["open_items"], res_prop["open_item_kinds"] = list(_oi), list(_oi.kinds)
        prop_path = None
        if res_prop.get("final_text") and res_prop.get("status") != "empty":
            prop_path = (getattr(args, "ai_proposal_out", None)
                         or os.path.splitext(out_path)[0] + ".hybrid.dtsi")
            write_dts(prop_path, res_prop["final_text"], dts_eol)
        if fx is not None:
            R.extend(usb_ai_proposer.render_facts_report(fx))
        R.extend(usb_ai_proposer.render_report(res_prop, _shown(prop_path) if prop_path else None))
        result.ai_proposal = dict(res_prop, path=prop_path,
                                  facts_extracted=(fx["facts"] if fx else []), facts_rejected=(fx["rejected"] if fx else 0),
                                  facts_note=(fx.get("unavailable") or fx.get("error")) if fx else None)
        _c, _t = _spent(_b_prop)
        if res_prop.get("status") == "unavailable":
            _st, _ef = "not available: " + str(res_prop.get("reason")), "none"
        elif res_prop.get("ai_note"):
            _st, _ef = "skipped: " + str(res_prop["ai_note"]), "none - Python's verified output is the result"
        else:
            _chg = sum(1 for _l in (res_prop.get("diff") or "").splitlines() if _l[:1] in "+-" and not _l.startswith(("+++", "---")))
            _used = sum(1 for _f in res_prop.get("facts_report", []) if _f.get("status") == "used by the AI")
            _applied = sum(1 for _f in res_prop.get("facts_report", []) if str(_f.get("status", "")).startswith("applied"))
            _st = "ran"
            _ef = (f"{_chg} line(s) of the final file differ from Python-only; {len(res_prop.get('unsupported', []))} AI line(s) demoted to "
                   f"comments; {_applied} fact(s) applied by Python, {_used} used by the AI")
        _ai_ledger.append({"feature": "usb_propose", "calls": _c, "tokens": _t, "status": _st, "effect": _ef})
    # OUTPUT FILES: one file per role, in the format of the templates (or the default format), proven value-identical to the verified output
    if args.dts:
        result.bundle_notes.append("an existing DTS was given: the updated file keeps its own format (it IS the template); no separate files were generated")
    else:
        from app import dts_style
        from app.usb_ai_proposer import read_document as _read_template
        _styles, _skipped, _mixed = {}, [], []
        for _tp in (getattr(args, "template", None) or []):
            try:
                _tt = _read_template(_tp)
            except OSError as _e:
                raise SystemExit(f"--template {_tp!r}: cannot read it ({_e})")
            _tn = os.path.basename(_tp)
            _st = dts_style.learn_style(_tt, _tn)
            if _st.unrecognized_pin_states:
                _examples = "; ".join(_st.unrecognized_pin_state_headers[:4])
                result.bundle_notes.append(
                    f"template '{_tn}' has {_st.unrecognized_pin_states} unrecognized pin-state block(s) "
                    f"({_st.recognized_pin_states} recognized; examples: {_examples}); ignored so its format is not learned from incomplete evidence")
                continue
            if _st.role == "unknown":
                result.bundle_notes.append(f"template '{_tn}' has no recognisable pin states or overlay content: ignored")
                continue
            if _st.role in _styles:
                result.bundle_notes.append(f"a second {_st.role} template ('{_tn}') was ignored: the first one is used")
                continue
            _styles[_st.role] = _st
            _skipped += [f"{_tn}: {x}" for x in _st.skipped_includes]
            _mixed += [f"{_tn}: {x}" for x in _st.mixed]
        _tmpl_roles = set(_styles)          # formats that came from the person's own template files
        if getattr(args, "kernel", None):
            from app import kernel_format
            try:
                _kr = kernel_format.resolve(args.kernel, soc_entity, getattr(args, "kernel_reference", None), base=getattr(args, "kernel_base", None))
            except ValueError as _e:
                raise SystemExit(str(_e))
            result.kernel_resolution = {"version": _kr["version"], "kind": _kr["kind"], "source": _kr["source"], "notes": _kr["notes"]}
            if "pinmux" in _styles:
                result.bundle_notes.append(f"--kernel {args.kernel} was not used for the pinmux file: the template '{_styles['pinmux'].source}' wins")
                if _kr["style"] is not None and (_kr["style"].layout, _kr["style"].name_style) != (_styles["pinmux"].layout, _styles["pinmux"].name_style):
                    result.bundle_notes.append(
                        f"your template's pin-state layout ({_styles['pinmux'].layout}) differs from the one {_kr['source']} uses ({_kr['style'].layout}) "
                        f"- expected for a vendor BSP kernel; your template is the authority and was followed")
            elif _kr["style"] is not None:
                _styles["pinmux"] = _kr["style"]
                result.bundle_notes.append(f"pinmux format taken from Linux {_kr['version']}: {_kr['source']}")
                result.bundle_notes += [f"kernel: {x}" for x in _kr["notes"]]
            else:
                result.bundle_notes += [f"kernel: {x}" for x in _kr["notes"]]
            result.bundle_notes.append("the overlay file has no kernel-version convention to follow: it uses " +
                                       (f"the template '{_styles['overlay'].source}'" if "overlay" in _styles else "the default format"))
        if getattr(args, "template", None) or getattr(args, "kernel", None):
            for _role in ("pinmux", "overlay"):
                if _role not in _styles:
                    if _role == "overlay" and getattr(args, "kernel", None):
                        continue          # already stated above
                    if _role == "pinmux" and getattr(args, "kernel", None) and "pinmux" not in _styles:
                        result.bundle_notes.append("no pinmux template and no kernel format: the pinmux file uses the default format")
                        continue
                    result.bundle_notes.append(f"no {_role} template was given: the {_role} file uses the default format")
        _ap = result.ai_proposal
        if _ap and _ap.get("final_text") and _ap.get("status") == "clean":
            _final = _ap["final_text"]
        else:
            if _ap and _ap.get("status") == "needs_attention":
                # Files are what people take away: they never come from a hybrid result that still has findings.
                result.bundle_notes.append("the hybrid result needs attention (see its findings), so these files were made from Python's verified "
                                           "output instead - no unverified AI text is in them")
            _parts = [out_text]
            if reg_consumer_blocks:
                _parts.append("\n".join(reg_consumer_blocks))
            if regulator_blocks:
                _parts.append("/ {\n" + "\n".join(("\t" + l_ if l_.strip() else l_) for l_ in "\n".join(regulator_blocks).splitlines()) + "\n};")
            _final = "\n\n".join(_parts)
        _bundle = dts_style.build_bundle(_final, args.project, _styles)
        result.files = _bundle["files"]
        result.bundle_notes += _bundle["notes"]
        if result.files:
            result.files_dir = getattr(args, "out_dir", None) or (os.path.splitext(out_path)[0] + "_files")
            os.makedirs(result.files_dir, exist_ok=True)
            for _f in result.files:
                with open(os.path.join(result.files_dir, _f["name"]), "w", encoding="utf-8", newline="") as _fh:
                    _fh.write(_f["text"])
            _vt = "\n".join(f_["text"].replace("\r\n", "\n") for f_ in result.files)
            result.bundle_verification = _verify_text(args, role_map, _vt)
            R.append("\n## Output files and format\n")
            R.append("| File | Role | Format it follows |")
            _prov = ("your template (accepted by your project)" if "pinmux" in _tmpl_roles else
                     (f"kernel reference - {result.kernel_resolution['kind']}" if result.kernel_resolution and result.kernel_resolution["kind"] in ("fetched", "table")
                      else "built-in default - NOT confirmed to match your BSP"))
            R.insert(len(R) - 1, f"Format source: **{_prov}**." + ("" if "pinmux" in _tmpl_roles else " For a vendor BSP, give an accepted file of your project as `--template` (it is the only source that proves your project's format)."))
            R.insert(len(R) - 1, "")
            R.append("|---|---|---|")
            for _f in result.files:
                R.append(f"| `{_f['name']}` | {_f['role']} | {_f['style_source']} - {_f['style_summary']} |")
            _bv = result.bundle_verification
            R.append(f"\n- Written to `{_shown(result.files_dir)}`. Values are identical to the verified output: every restyled pin node was parsed back "
                     f"and compared (label, pins, function, properties) - a file that did not match would have kept the default format.")
            R.append(f"- The independent verifier read the files again: {len(_bv['wrong'])} wrong | {len(_bv['ambiguous'])} ambiguous | "
                     f"{len(_bv['unverifiable'])} unverifiable | {_bv['ok']} confirmed OK.")
            if _skipped:
                R.append("- Template `#include` lines were NOT copied (they are SoC / project specific - add the ones your overlay needs): "
                         + "; ".join(_skipped[:8]) + (f" and {len(_skipped) - 8} more" if len(_skipped) > 8 else ""))
            if _mixed:
                R.append("- The template is inconsistent about: " + "; ".join(_mixed) + " - the majority habit was used.")
            for _n in result.bundle_notes:
                R.append(f"- {_n}")
        elif result.bundle_notes:
            R.append("\n## Output files and format\n")
            R.extend(f"- {_n}" for _n in result.bundle_notes)

    if any(getattr(args, a, None) for a in ("ai_advice", "ai_propose", "ai_suggest", "ai_draft", "docs")):
        _labels = [("usb_advice", "Explain flagged pins (USB, Explain mode)"),
                   ("usb_propose", "Complete the DTS from facts / documents / a draft (USB, Hybrid)"),
                   ("usb_suggest", "Suggest signal roles and audit missed pins (USB, Hybrid)"),
                   ("value_decoder", "Decode unknown cell values (generic generator)"),
                   ("node_extractor", "Read unrecognised DTS formats (DTS update)"),
                   ("usb_check", "Cross-check your workbook data for contradictions (USB, Hybrid)")]
        _cfg, _ran = _llm.config(), {r_["feature"]: r_ for r_ in _ai_ledger}
        _rows = []
        for _f, _label in _labels:
            _on = _llm.is_configured() and {"usb_check": "usb_suggest"}.get(_f, _f) in _cfg["features"]
            if _f in _ran:
                _row = dict(_ran[_f])
            elif _f.startswith("usb_"):      # usb_check is gated by the usb_suggest switch
                _row = {"calls": 0, "tokens": 0, "status": "not requested in this run" if _on else "OFF - not in LLM_FEATURES", "effect": "-"}
            else:
                _row = {"calls": 0, "tokens": 0, "effect": "-",
                        "status": "enabled - used outside USB runs" if _on else "OFF - not in LLM_FEATURES (the non-AI fallback is used)"}
            _row.update(feature=_f, label=_label, switch="on" if _on else "off")
            _rows.append(_row)
        result.ai_ledger = _rows
        R.append("\n## Where the AI was used in this run\n")
        R.append("_Python generates and verifies every value; the AI never changes a verified line. This table is the real call count from the gateway._\n")
        R.append("| AI feature | Switch | What happened | Calls | Tokens | Effect on the DTS |")
        R.append("|---|---|---|---|---|---|")
        for _r in _rows:
            R.append(f"| {_r['label']} | {_r['switch']} | {_r['status']} | {_r['calls']} | {_r['tokens']} | {_r['effect']} |".replace("\n", " "))
    with open(args.report, "w", encoding="utf-8") as f:
        f.write("\n".join(R) + "\n")

    if args.combined:
        combined_text = build_combined(args.project, pattern or "unconfirmed", consumer_blocks + reg_consumer_blocks,
                                       state_labels_used, out_text, regulator_blocks, hub_state_labels,
                                       pattern_previews, len(cat["unsupported_controller"]))
        write_dts(args.combined, combined_text, dts_eol)
        print(f"Combined: {args.combined}")
        result.combined_path = args.combined
        if result.ai_proposal and result.ai_proposal.get("final_text"):
            # The Combined View, updated by the hybrid: changed nodes replaced in place, AI additions in their own section.
            from app import usb_ai_proposer
            hc, n_changes = usb_ai_proposer.hybrid_combined(template_combined or combined_text,
                                                           result.ai_proposal["python_only_text"],
                                                           result.ai_proposal["final_text"])
            if hc is None and template_combined:
                hc = template_combined          # nothing for the AI to change, but the template completion itself IS the result
            result.ai_proposal["final_combined_text"] = hc
            result.ai_proposal["combined_changes"] = n_changes
            if hc:
                hc_path = os.path.splitext(args.combined)[0] + ".hybrid.dts"
                write_dts(hc_path, hc, dts_eol)
                print(f"Hybrid combined: {hc_path}")
    if args.inventory:
        write_inventory(args.inventory, args.project, matched, cat, _fs, ig_slug, gen5_slug)
        print(f"Inventory: {args.inventory}")
        result.inventory_path = args.inventory
    print(f"Output : {out_path}")
    print(f"Report : {args.report}")
    print(f"Pattern: {pattern or 'unconfirmed'} ({pattern_evidence})")
    if overlay_out:
        print(f"Overlay: {_shown(overlay_out)}")
    print(f"Updated values: {len(update_changes)} | consumer nodes generated: {len(consumer_blocks)} | "
          f"regulators generated: {len(regulator_blocks)} | "
          f"new pinctrl states: {len(new_state_blocks)} | other GPIOs included: {len(other_gpio_rows)} | "
          f"unsupported controller pins: {len(cat['unsupported_controller'])} | "
          f"controller unconfirmed: {len(cat['unconfirmed_tlmm'])} | "
          f"needs review: {len(cat['needs_review'])}")
    result.out_path, result.report_path, result.overlay_path = out_path, args.report, overlay_out
    result.out_text, result.report_text = out_text, "\n".join(R)
    result.pattern, result.pattern_evidence = pattern or "unconfirmed", pattern_evidence
    result.summary = {"updated": len(update_changes), "consumer_nodes": len(consumer_blocks),
                      "regulators": len(regulator_blocks), "new_states": len(new_state_blocks),
                      "other_gpio": len(other_gpio_rows),
                      "unsupported_controller": len(cat["unsupported_controller"]),
                      "unconfirmed_controller": len(cat["unconfirmed_tlmm"]),
                      "needs_review": len(cat["needs_review"])}
    result.shared_pins = [{"pin": _ball_name(e["record"]), "signals": [x.strip() for x in e["shared"]], "owner": e.get("owner")} for e in cat["needs_review"] if e.get("shared")]
    result.validation_problems = problems
    result.dtc_result = dtc_result
    result.overview = _build_overview(result, problems, len(cat["needs_review"]))
    if problems:
        print("VALIDATION PROBLEMS:", "; ".join(problems))
        result.exit_code = 2
    return result


def main():
    run(build_argparser().parse_args())


if __name__ == "__main__":
    main()
