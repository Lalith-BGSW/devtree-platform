"""
Generates a USB controller consumer node (&usb0-style: oc-gpio,
vbus-out-gpio, pinctrl-0 referencing generated pinctrl states) from
matched USB pins - or, if an uploaded reference file already has one,
leaves that job to the existing, already-verified Update path instead.

Output is DELIBERATELY SPLIT into two separate pieces, matching how
this codebase's real, accepted files are actually structured (and
matching the mentor's own agent instructions' declare/enable split):
  - pinmux_dtsi_text: the &tlmm { label: label { mux{} config{} }; }
    pinctrl-state definitions - belongs in the pinmux .dtsi file.
  - consumer_node_text: the &usb0 { ... } block ONLY - references the
    pinctrl labels by name, never repeats their mux/config definition
    inline. Belongs in the overlay .dts file. Confirmed real shape
    includes properties beyond oc-gpio/vbus-out-gpio (debounce-delay-ms,
    qcom,default-mode-host, a dwc3@... child node) that have nothing to
    do with Excel PinMux data at all - see the CONSUMER_NODE_BOILERPLATE
    comment below for exactly what's fixed template content vs. derived.

Scope, deliberately narrow for this first version: only pins that fall
into one of three SAFE categories are auto-generated. Everything else
found by the "usb" substring match is listed separately as needing
manual review - never guessed into a role. See categorize_usb_records()
for the exact rules and why each category is/isn't safe.

Differential/analog PHY pads (no GPIO_N ball name at all - e.g. USB
SuperSpeed RX/TX, HS DP/DM lines) are deliberately excluded from the
generated &tlmm block and from pinctrl-0 entirely: on real hardware
these are dedicated PHY pads, never routed through the general-purpose
TLMM pinmux controller the way GPIO balls are - confirmed against the
real reference example this feature was built from, whose pinctrl-0
only ever listed GPIO-based states (oc/vbus), never a differential-pad
state.
"""

from __future__ import annotations
import re

from app.models import ColumnMeta, NormalizedRecord
from app.generators.dts_generator import (
    derive_pin_config, _first_pin_identifier, _to_dts_pin_name,
)

_BALL_NAME_CANDIDATES = ("sa8155sip_ball_name", "sa8155soc_ball_name", "ball_name", "sa7255_ball_name")
_GPIO_BALL_RE = re.compile(r"^gpio[_\s]?(\d+)$", re.IGNORECASE)
_DEFAULT_ROLE_KEYWORDS = {"oc": [r"(^|_)OC(_|$)"], "vbus": ["PWR_EN", "VBUS"]}      # used only if the data file is missing or unreadable
_role_kw_cache: dict = {}


def role_patterns() -> dict:
    """The signal-name vocabulary for the two roles Python can wire, from app/usb_role_keywords.json (or the file named by the
    environment variable USB_ROLE_KEYWORDS). Data, not code: edit the file to teach a project's naming."""
    import json
    import os
    path = os.environ.get("USB_ROLE_KEYWORDS") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "usb_role_keywords.json")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = None
    key = (path, mtime)
    if key not in _role_kw_cache:
        try:
            cfg = json.load(open(path, encoding="utf-8"))
            pats = {r: [re.compile(p, re.IGNORECASE) for p in cfg.get(r, [])] for r in ("oc", "vbus")}
        except (OSError, ValueError, re.error):
            pats = {r: [re.compile(p, re.IGNORECASE) for p in _DEFAULT_ROLE_KEYWORDS[r]] for r in ("oc", "vbus")}
        _role_kw_cache.clear()
        _role_kw_cache[key] = pats
    return _role_kw_cache[key]


def role_matches(signal_tail: str, role: str) -> bool:
    """Does the vocabulary say this signal name is an over-current ('oc') / VBUS enable ('vbus') signal?"""
    return any(p.search(signal_tail.upper()) for p in role_patterns().get(role, []))


def signal_role_from_name(signal_tail: str) -> str | None:
    """'oc' wins over 'vbus' when both match (as before); None when the vocabulary knows neither."""
    if role_matches(signal_tail, "oc"):
        return "oc"
    return "vbus" if role_matches(signal_tail, "vbus") else None


_INSTANCE_TOKEN_RE = re.compile(r"^CPU_(USB\d+(?:_\d+)?)(?=_CTRL|_|:)", re.IGNORECASE)

# Confirmed mapping from an instance token to its real devicetree
# alias - ONLY entries backed by an actual accepted reference example
# belong here. "USB30" -> "usb0" is confirmed (the real reference
# example this whole feature was built from is GPIO_81/82, both
# CPU_USB30_...). Any instance NOT in this dict has no confirmed
# alias - see resolve_instance_label() below, which never guesses one.
_CONFIRMED_INSTANCE_ALIASES = {
    "USB30": "usb0",
}

# Confirmed from exactly ONE real reference example (usb0 on this
# codebase's SA8155P/CCS20 board) - these properties have nothing to
# do with Excel PinMux data, they're fixed devicetree/driver boilerplate
# for this specific USB controller IP block. Included whenever a
# consumer node is generated, but the dwc3 unit-address and PHY labels
# specifically are NOT confirmed to generalize to a second instance
# (e.g. &usb1) - flagged explicitly in the output if generating for
# any label other than "usb0".
_CONSUMER_NODE_DEBOUNCE_MS = 20
_CONSUMER_NODE_DWC3_UNIT_ADDRESS = "a600000"
_CONSUMER_NODE_USB_PHY_LABELS = ("usb2_phy0", "usb_nop_phy")
_CONSUMER_NODE_MAX_SPEED = "high-speed"


def _ball_name(rec: NormalizedRecord) -> str | None:
    for slug in _BALL_NAME_CANDIDATES:
        val = rec.fields.get(slug)
        if val is not None and str(val).strip():
            return str(val).strip()
    # Other workbooks name this column differently - fall back to the same
    # generic pin-identifier lookup the DTS generator itself uses.
    ident = _first_pin_identifier(rec.fields)
    return str(ident).strip() if ident else None


def _signal_segments(gen5_signal: str) -> list[str]:
    """A Gen5 Signal Name can list multiple PREFIX:SIGNAL segments
    (comma or newline separated) when one physical pin serves several
    logical consumers - confirmed real case: GPIO_92 carries both
    "...USB_HUB_I2C_EN" and "...USB_PWR_EN". Splitting these out is
    what lets a multi-purpose pin be detected and excluded from the
    safe auto-generated categories."""
    parts = re.split(r"[,\n]", gen5_signal)
    return [p.strip() for p in parts if p.strip()]


def detect_usb_instance_token(gen5_signal: str) -> str | None:
    """Extracts the controller-instance-identifying token from a
    single Gen5 Signal Name segment (e.g. "CPU_USB30_CTRL_1V8:USB_OC_B"
    -> "USB30", "CPU_USB30:USB3_SSRX_N" -> "USB30"). Confirmed real
    case: a workbook can describe more than one physical USB controller
    instance (USB30 and USB20 are genuinely separate, independent
    controllers, not two properties of the same one) - this is what
    lets generate_usb_consumer_dts_multi_instance() generate a
    correctly SEPARATE node per instance instead of merging them."""
    m = _INSTANCE_TOKEN_RE.match(gen5_signal.strip().upper())
    return m.group(1) if m else None


def resolve_instance_label(instance_token: str) -> tuple[str, bool]:
    """Maps an instance token (e.g. "USB30") to its devicetree alias
    label. Returns (label, is_confirmed). Only "USB30" -> "usb0" is
    backed by a real accepted reference example - any other instance
    gets an HONEST, lowercase-derived placeholder label (e.g. "USB20"
    -> "usb20") rather than a guessed numeric alias like "usb1", since
    we have no real example confirming that mapping. The placeholder
    is still usable as a label, just clearly not the same as asserting
    a confirmed real devicetree alias name.

    Kept for single-token/backward-compatible use - when generating
    for a workbook with more than one instance, prefer
    resolve_instance_labels() (plural) instead, which groups ALL
    detected instances under sequential usb0/usb1/usb2... aliases per
    direction, rather than leaving some with this function's
    non-numeric placeholder."""
    if instance_token in _CONFIRMED_INSTANCE_ALIASES:
        return _CONFIRMED_INSTANCE_ALIASES[instance_token], True
    return instance_token.lower(), False


def resolve_instance_labels(instance_tokens: list[str]) -> dict[str, tuple[str, bool]]:
    """Assigns sequential usb0/usb1/usb2... aliases to EVERY distinct
    USB controller instance found - per direction: group all detected
    instances under numeric aliases rather than leaving some with a
    non-numeric placeholder label. Generalizes to however many
    instances a given project has (1, 2, 3, ...), and to whichever
    specific tokens they are.

    An instance matching a confirmed real alias (currently only
    "USB30" -> "usb0", from the one real accepted reference example we
    have) keeps that confirmed number. Every other instance gets the
    next available number, assigned in sorted (deterministic) order of
    its token, so the same workbook always produces the same numbering
    across runs.

    Returns {instance_token: (label, is_confirmed)}. is_confirmed here
    means "the FIXED boilerplate content (dwc3 unit address, usb-phy
    labels) has a real reference for this specific instance" - not
    whether the numeric label itself is trustworthy, since numeric
    grouping is now applied uniformly to every instance per direction.
    """
    assigned: dict[str, tuple[str, bool]] = {}
    used_numbers: set[int] = set()

    remaining = []
    for token in instance_tokens:
        if token in _CONFIRMED_INSTANCE_ALIASES:
            label = _CONFIRMED_INSTANCE_ALIASES[token]
            num_m = re.search(r"\d+", label)
            if num_m:
                used_numbers.add(int(num_m.group()))
            assigned[token] = (label, True)
        else:
            remaining.append(token)

    next_num = 0
    for token in sorted(remaining):
        while next_num in used_numbers:
            next_num += 1
        assigned[token] = (f"usb{next_num}", False)
        used_numbers.add(next_num)
        next_num += 1

    return assigned


def _is_boot_strap_segment(segment: str) -> bool:
    """True for signal segments that are boot-time straps, not runtime signals."""
    prefix, _, tail = segment.partition(":")
    prefix_u, tail_u = prefix.upper(), (tail or prefix).strip().upper()
    if "BOOTCFG" in prefix_u or "CPU_BOOT" in prefix_u:
        return True
    return bool(re.fullmatch(r"(MD\d+|BOOT_MODE\d*)", tail_u))



def resolve_regsetting_slug(
    pinmux_setting, columns: list[ColumnMeta], project_slugs: set[str],
) -> str | None:
    """Given a row's own Pinmux Setting value, finds the RegSetting-
    shaped column belonging to the SAME subgroup entity that setting
    refers to - resolved by entity NAME membership (the same approach
    used to fix the Honda workbook's decoupled Port/Instance numbering),
    not by assuming the setting number equals the RegSetting field's
    own internal suffix. "Q3" -> entity matching "PinMuxQ3"; a bare
    "1" -> entity matching "PinMux0*1" (tolerant of zero-padding, since
    confirmed real entities use it inconsistently - "PinMux01" for
    Nissan/Iveco/Honda's numbered groups, "PinMuxQ3" with no padding
    for Q-groups). A legitimate setting of 0 (subgroup "PinMux00"/"PinMux0",
    the first one) must not be treated as "no value" - 0 is a valid,
    real setting, not an absence; checking for None/empty specifically
    (rather than Python truthiness, which treats 0 as falsy) avoids
    that."""
    if pinmux_setting is None or str(pinmux_setting).strip() == "":
        return None
    setting = str(pinmux_setting).strip().upper()
    q_m = re.match(r"^Q(\d+)$", setting)
    n_m = re.match(r"^(\d+)$", setting)
    if q_m:
        entity_re = re.compile(rf"^PinMuxQ0*{q_m.group(1)}$", re.IGNORECASE)
    elif n_m:
        entity_re = re.compile(rf"^PinMux0*{n_m.group(1)}$", re.IGNORECASE)
    else:
        return None

    for c in columns:
        if not c.slug or not c.entity or c.slug not in project_slugs:
            continue
        if not entity_re.match(c.entity):
            continue
        normalized = re.sub(r"[^a-z0-9]", "", (c.base_field or "").lower())
        if normalized.startswith("regsetting"):
            return c.slug
    return None


def pin_is_tlmm_controlled(
    rec: NormalizedRecord, pinmux_setting_slug: str, columns: list[ColumnMeta], project_slugs: set[str],
    project_has_any_regsetting: bool = True,
) -> bool | None:
    """True only when the workbook identifies a TLMM GPIO register (TLMM_GPIO_CFGnnn).

    A non-empty RegSetting is not enough: SA7255 SAIL_CFG rows belong to a different pin controller
    and must not be emitted inside an &tlmm node (False here; the caller lists them as
    unsupported-controller pins).

    None means this check cannot say: either no RegSetting column resolves for the row, or
    project_has_any_regsetting is False - the field was never filled in for ANY row of the project
    (confirmed real case: a different SoC vendor's export where every RegSettingN cell is empty), so
    an empty value on this row tells nothing about it and must not be read as "not TLMM"."""
    setting = rec.fields.get(pinmux_setting_slug)
    regsetting_slug = resolve_regsetting_slug(setting, columns, project_slugs)
    if regsetting_slug is None:
        return None
    value = rec.fields.get(regsetting_slug)
    if value and re.match(r"^TLMM_GPIO_CFG\d+\b", str(value).strip(), re.IGNORECASE):
        return True
    if value and str(value).strip():
        return False
    return False if project_has_any_regsetting else None


def _ball_has_gpio_capable_instance(rec: NormalizedRecord) -> bool:
    """True if ANY of this ball's own PortN/InstanceN fields (its list of available mux functions)
    shows a GPIO-style instance. Direct workbook evidence that the ball CAN be a plain GPIO - but NOT
    evidence of WHICH pin controller drives it, so it never makes a pin TLMM by itself."""
    for key, value in rec.fields.items():
        if key.startswith("instance") and key[8:].isdigit() and str(value or "").strip().upper().startswith("GPIO"):
            return True
    return False


def categorize_usb_records(
    records: list[NormalizedRecord], gen5_slug: str, pinmux_setting_slug: str,
    columns: list[ColumnMeta], project_slugs: set[str],
    interface_group_slug: str | None = "interface_group",
    role_map: dict | None = None,
    force_include_ids: set | None = None,
) -> tuple[dict[str, list], dict[str, int]]:
    """Sorts USB-matched records into:
      - "differential": NOT GPIO-multiplexed (SerDes/analog PHY pads, supplies) - always excluded from
        &tlmm and pinctrl-0, never a consumer attribute.
      - "unsupported_controller": has a register setting, but not a TLMM_GPIO_CFG one (e.g. SA7255
        SAIL_CFG) - a different pin controller whose DTS binding this tool has no confirmed example
        of. Never emitted into &tlmm; listed in the report.
      - "unconfirmed_tlmm": RegSetting data is unavailable for the whole project, so TLMM membership
        cannot be confirmed, AND the ball is not a plain numeric "GPIO_N". It looks like a real GPIO
        (its own Port/Instance data lists a GPIO function, or the ball name starts with "gpio") but
        nothing says which pin controller drives it. Not filed as a PHY pad (that would hide a real
        pin) and not emitted into &tlmm (that would assume Qualcomm TLMM). Listed for a human.
      - "oc_gpio": single-purpose TLMM pin whose signal has "OC" as its own segment.
      - "vbus_gpio": single-purpose TLMM pin whose signal has "PWR_EN" or "VBUS".
      - "needs_review": ambiguous or unusable - a shared pin (several runtime roles), no Gen5 Signal
        Name, a boot-strap-only pin, or a pin that matched "usb" ONLY through a boot-strap segment
        (e.g. FORCE_USB) while its real runtime signal is something else.
      - "other_gpio": single-purpose TLMM pin whose signal matches no known role keyword - its real
        Excel values are still surfaced, only the consumer wiring is a human decision.
    When RegSetting cannot be resolved, a plain numeric ball name ("GPIO_81") is the reported fallback
    for TLMM membership. Returns (result, fallback_counts) with fallback_counts =
    {"instance_fallback": n, "ball_name_fallback": n}, each use tracked, never silent.
    """
    result: dict[str, list] = {
        "differential": [], "unsupported_controller": [], "unconfirmed_tlmm": [], "oc_gpio": [],
        "vbus_gpio": [], "needs_review": [], "other_gpio": [],
    }
    fallback_counts = {"instance_fallback": 0, "ball_name_fallback": 0}

    # Once per project, not per pin: does ANY row carry a real RegSetting value? If none does, an
    # empty value says nothing about any specific pin (see pin_is_tlmm_controlled).
    def _row_has_real_regsetting(rec):
        slug = resolve_regsetting_slug(rec.fields.get(pinmux_setting_slug), columns, project_slugs)
        if slug is None:
            return False
        v = rec.fields.get(slug)
        return bool(v and str(v).strip())

    project_has_any_regsetting = any(_row_has_real_regsetting(r) for r in records) if records else True

    for rec in records:
        ball = _ball_name(rec)
        gen5 = rec.fields.get(gen5_slug)
        all_segments = _signal_segments(str(gen5)) if gen5 and str(gen5).strip() else []
        segments = [x for x in all_segments if not _is_boot_strap_segment(x)]
        if all_segments and not segments and id(rec) in (force_include_ids or ()):
            segments = all_segments          # you decided this boot-strap pin needs a runtime state: its values come from the Excel
        if all_segments and not segments:
            result["needs_review"].append({
                "record": rec,
                "reason": "Boot-strap only - read at reset, not a runtime USB signal (excluded from runtime DTS)",
            })
            continue

        is_tlmm = pin_is_tlmm_controlled(rec, pinmux_setting_slug, columns, project_slugs,
                                         project_has_any_regsetting)
        if is_tlmm is None:
            has_instance = _ball_has_gpio_capable_instance(rec)
            fallback_counts["instance_fallback" if has_instance else "ball_name_fallback"] += 1
            if ball and _GPIO_BALL_RE.match(ball):
                is_tlmm = True                       # the explicitly reported numeric-ball fallback
            elif has_instance or (ball and ball.lower().startswith("gpio")):
                result["unconfirmed_tlmm"].append({
                    "record": rec,
                    "reason": ("looks like a real GPIO ("
                               + ("its own Port/Instance data lists a GPIO function" if has_instance
                                  else "ball name starts with 'gpio'")
                               + ") but this workbook has no register-setting data identifying its pin "
                                 "controller, so it is not known to be Qualcomm TLMM - not emitted into "
                                 "&tlmm; needs that SoC's real pinctrl binding"),
                })
                continue
            else:
                is_tlmm = False
        if not is_tlmm:
            setting = rec.fields.get(pinmux_setting_slug)
            regsetting_slug = resolve_regsetting_slug(setting, columns, project_slugs)
            regsetting = rec.fields.get(regsetting_slug) if regsetting_slug else None
            if regsetting and str(regsetting).strip():
                result["unsupported_controller"].append({
                    "record": rec,
                    "regsetting": str(regsetting).strip(),
                })
            else:
                result["differential"].append(rec)
            continue

        if not gen5 or not str(gen5).strip():
            result["needs_review"].append({"record": rec, "reason": "No Gen5 Signal Name value"})
            continue

        interface_group = str(rec.fields.get(interface_group_slug) or "") if interface_group_slug else ""
        # A pin the engineer explicitly included (--include-pin) is USB by decision: it need not say "USB" anywhere.
        if (not any("USB" in segment.upper() for segment in segments)
                and "USB" not in interface_group.upper()
                and id(rec) not in (force_include_ids or ())):
            result["needs_review"].append({
                "record": rec,
                "reason": "USB match came only from a boot-strap signal; remaining runtime signal is not USB-related",
            })
            continue
        if len(segments) != 1:
            tails = [x.split(":")[-1].strip().upper() for x in segments]
            if all(re.fullmatch(r"(?:I2C_)?(?:SDA|SCL)\d*|SMB(?:US)?_(?:DATA|CLK)", t) for t in tails):
                devices = ", ".join(re.sub(r"_\d+V\d+$", "", x.split(":")[0]) for x in segments)
                result["needs_review"].append({
                    "record": rec,
                    "reason": (f"Shared I2C bus pin - {len(segments)} devices on one bus ({devices}); it is configured "
                               f"by the I2C controller's pinctrl (see the I2C tool), not by a USB node. Multi-purpose pin - "
                               f"{len(segments)} distinct signal roles on this ball"),
                })
                continue
            # One physical pin on several nets: the pin's own electrical setting (mux, drive, bias, direction) belongs to the PIN, not to a net, so it is
            # generated ONCE and marked shared; which node owns the GPIO (only one driver can claim a GPIO line) stays a decision for a person.
            # (the pin stays in needs_review - its ROLE is still undecided, advisors and --vbus-pin work on it - and carries `shared` so usb_dts_tool also writes its pin state)
            result["needs_review"].append({
                "record": rec,
                "reason": f"Multi-purpose pin - {len(segments)} distinct signal roles on this ball",
                "shared": list(segments),
            })
            continue

        signal = segments[0]
        signal_tail = signal.split(":")[-1].strip().upper()
        is_active_low = bool(re.search(r"(^|_)(B|N)$", signal_tail))
        polarity = "GPIO_ACTIVE_LOW" if is_active_low else "GPIO_ACTIVE_HIGH"

        instance_token = detect_usb_instance_token(signal)
        entry = {"record": rec, "polarity": polarity, "signal": signal, "instance_token": instance_token}
        # role_map (--role SIGNAL=oc|vbus) teaches the tool a project's own signal naming without a code change; it wins over
        # the built-in keywords, which come from the Gen5 naming seen so far (USB_OC_B, USB_PWR_EN, VBUS_PWR_EN).
        forced = (role_map or {}).get(signal_tail)
        by_name = signal_role_from_name(signal_tail) if forced is None else None       # vocabulary: app/usb_role_keywords.json
        if forced == "oc" or by_name == "oc":
            result["oc_gpio"].append(entry)
        elif forced == "vbus" or by_name == "vbus":
            result["vbus_gpio"].append(entry)
        else:
            result["other_gpio"].append(entry)
    return result, fallback_counts


def _extra_review(cat: dict) -> list[dict]:
    """Pins the simpler generators below cannot render (non-TLMM controller, unconfirmed controller),
    as review entries - so they are reported instead of silently vanishing from every count."""
    out = [{"record": e["record"],
            "reason": f"pin controller is not TLMM ({e['regsetting']}) - no confirmed DTS binding, not generated"}
           for e in cat["unsupported_controller"]]
    out += [{"record": e["record"], "reason": e["reason"]} for e in cat["unconfirmed_tlmm"]]
    return out


def detect_existing_usb_node(dts_text: str, consumer_node_label: str = "usb0") -> dict:
    """Checks for USB controller content already present in the file,
    in EITHER shape a real file might use:
      - "reference": an override/extension of an already-defined node,
        e.g. "&usb0 { ... };" - what generate_usb_consumer_dts()
        produces, and what the existing Update feature can safely
        patch in place.
      - "full_definition": the controller defined outright, e.g.
        "usb0: ssusb@a600000 { compatible = \"qcom,dwc-usb3-msm\"; ... };"
        - confirmed real case. This is NOT safe to treat the same as
        "nothing found" - generating a fresh "&usb0 { ... };" override
        alongside an existing full definition would silently create
        a second, competing node instead of one correct one.
    Returns {"kind": "reference"|"full_definition"|"none", ...}."""
    if re.search(rf"&{re.escape(consumer_node_label)}\s*\{{", dts_text):
        return {"kind": "reference", "detail": f"&{consumer_node_label} {{ ... }} override found"}

    label_def = re.search(rf"\b{re.escape(consumer_node_label)}:\s*(\S+)\s*\{{", dts_text)
    if label_def:
        return {"kind": "full_definition",
                "detail": f"Full node definition found: {consumer_node_label}: {label_def.group(1)} {{ ... }}"}

    if re.search(r'compatible\s*=\s*"[^"]*(dwc3|dwc-usb)[^"]*"', dts_text):
        return {"kind": "full_definition",
                "detail": "A dwc3/dwc-usb compatible node exists, but with no label matching "
                           f"{consumer_node_label!r} - can't confirm it's the same instance"}

    return {"kind": "none", "detail": "No USB controller content found"}


def _render_pinctrl_state(label: str, rec: NormalizedRecord) -> tuple[str, bool]:
    """Renders one dedicated, individually-labeled pinctrl state node
    for a single pin - deliberately NOT using generate_dts()'s
    multi-pin grouping, since a consumer node's pinctrl-0 list needs
    to reference specific, individually meaningful labels (oc vs vbus),
    not a bundle of unrelated pins that merely share electrical values.
    Returns (rendered_text, needs_review)."""
    pin_id = _first_pin_identifier(rec.fields) or f"row{rec.row_index}"
    pin_name = _to_dts_pin_name(pin_id)
    derived = derive_pin_config(rec)
    if derived is None:
        return (
            f"\t{label}: {label} {{\n"
            f"\t\tmux {{\n\t\t\tpins = \"{pin_name}\";\n\t\t\tfunction = \"gpio\";\n\t\t}};\n"
            f"\t\tconfig {{\n\t\t\tpins = \"{pin_name}\";\n"
            f"\t\t\t/* NEEDS REVIEW: could not derive pin configuration from workbook */\n\t\t}};\n\t}};\n",
            True,
        )
    lines = [f"\t{label}: {label} {{", "\t\tmux {", f"\t\t\tpins = \"{pin_name}\";",
              f"\t\t\tfunction = \"{derived.function}\";", "\t\t};", "", "\t\tconfig {",
              f"\t\t\tpins = \"{pin_name}\";"]
    needs_review = False
    if derived.drive_strength is not None:
        comment = "  /* NEEDS REVIEW: best-effort */" if derived.drive_strength_needs_review else ""
        lines.append(f"\t\t\tdrive-strength = <{derived.drive_strength}>;{comment}")
        needs_review = needs_review or derived.drive_strength_needs_review
    if derived.bias_directive:
        comment = "  /* NEEDS REVIEW: best-effort */" if derived.bias_needs_review else ""
        lines.append(f"\t\t\t{derived.bias_directive};{comment}")
        needs_review = needs_review or derived.bias_needs_review
    if derived.direction_directive:
        lines.append(f"\t\t\t{derived.direction_directive};")
    lines += ["\t\t};", "\t};"]
    return "\n".join(lines) + "\n", needs_review


def _render_instance_output(
    project: str, soc: str, consumer_node_label: str, is_confirmed_instance: bool,
    oc_candidates: list[dict], vbus_candidates: list[dict],
    differential_count: int, needs_review_count: int, total_matched: int,
) -> tuple[str, str, bool]:
    """Renders the pinmux-states text and consumer-node text for ONE
    instance's own oc_gpio/vbus_gpio candidates - pure rendering, no
    categorization. Returns (pinmux_dtsi_text, consumer_node_text,
    any_pin_config_needs_review)."""
    pinctrl_blocks: list[str] = []
    pinctrl_labels: list[str] = []
    any_needs_review = False

    oc_gpio_line = None
    for i, entry in enumerate(oc_candidates):
        rec = entry["record"]
        ball = _ball_name(rec)
        gpio_num = _GPIO_BALL_RE.match(ball).group(1)
        label = f"{consumer_node_label}_oc_active" if i == 0 else f"{consumer_node_label}_oc_active_{i+1}"
        text, nr = _render_pinctrl_state(label, rec)
        pinctrl_blocks.append(text)
        pinctrl_labels.append(label)
        any_needs_review = any_needs_review or nr
        if i == 0:
            oc_gpio_line = f"\toc-gpio = <&tlmm {gpio_num} {entry['polarity']}>;"

    vbus_gpio_line = None
    for i, entry in enumerate(vbus_candidates):
        rec = entry["record"]
        ball = _ball_name(rec)
        gpio_num = _GPIO_BALL_RE.match(ball).group(1)
        label = f"{consumer_node_label}_vbus_active" if i == 0 else f"{consumer_node_label}_vbus_active_{i+1}"
        text, nr = _render_pinctrl_state(label, rec)
        pinctrl_blocks.append(text)
        pinctrl_labels.append(label)
        any_needs_review = any_needs_review or nr
        if i == 0:
            vbus_gpio_line = f"\tvbus-out-gpio = <&tlmm {gpio_num} {entry['polarity']}>;"

    # --- Piece 1: pinmux states (&tlmm block) - for the pinmux .dtsi file ---
    pinmux_header = (
        f"/*\n"
        f" * Auto-generated by devtree-platform - USB pinctrl states\n"
        f" * SoC: {soc}\n"
        f" * Project: {project}\n"
        f" * Consumer node: &{consumer_node_label}\n"
        f" * Generated from {total_matched} \"usb\"-matched workbook record(s) overall:\n"
        f" *   {len(oc_candidates)} over-current gpio candidate(s) for THIS instance\n"
        f" *   {len(vbus_candidates)} vbus-enable gpio candidate(s) for THIS instance\n"
        f" *   {differential_count} differential/PHY pad(s) overall - NOT included below,\n"
        f" *     these are not routed through &tlmm on real hardware\n"
        f" *   {needs_review_count} pin(s) excluded overall - see needs_review\n"
        f" * This file is deterministic template output - it is NOT LLM-generated.\n"
        f" */\n\n"
    )
    pinmux_dtsi_text = pinmux_header + "&tlmm {\n" + "\n".join(pinctrl_blocks) + "};\n"

    # --- Piece 2: consumer node (&usbN block) - for the overlay .dts file ---
    # References pinctrl labels by name ONLY - never repeats their
    # mux/config definition here, matching the real codebase's own
    # declare-in-dtsi / enable-in-overlay split.
    consumer_lines = [f"&{consumer_node_label} {{"]
    if oc_gpio_line:
        consumer_lines.append(oc_gpio_line)
    if vbus_gpio_line:
        consumer_lines.append(vbus_gpio_line)
    if not oc_gpio_line and not vbus_gpio_line:
        consumer_lines.append("\t/* NEEDS REVIEW: no oc-gpio or vbus-out-gpio candidate found for this instance */")
    consumer_lines.append(f"\tdebounce-delay-ms = <{_CONSUMER_NODE_DEBOUNCE_MS}>;")
    consumer_lines.append('\tpinctrl-names = "default";')
    refs = " ".join(f"&{l}" for l in pinctrl_labels)
    consumer_lines.append(f"\tpinctrl-0 = <{refs}>;")
    consumer_lines.append("")
    consumer_lines.append("\tqcom,default-mode-host;")
    consumer_lines.append("")
    consumer_lines.append(f"\tdwc3@{_CONSUMER_NODE_DWC3_UNIT_ADDRESS} {{")
    phy_refs = ", ".join(f"<&{p}>" for p in _CONSUMER_NODE_USB_PHY_LABELS)
    consumer_lines.append(f"\t\tusb-phy = {phy_refs};")
    consumer_lines.append(f'\t\tmaximum-speed = "{_CONSUMER_NODE_MAX_SPEED}";')
    consumer_lines.append("\t};")
    consumer_lines.append("};")

    consumer_header = (
        "/*\n"
        " * Auto-generated by devtree-platform - USB consumer node\n"
        f" * SoC: {soc}\n"
        f" * Project: {project}\n"
        " * oc-gpio / vbus-out-gpio / pinctrl-0 are derived from the workbook.\n"
        " * debounce-delay-ms, qcom,default-mode-host, and the dwc3@ child node\n"
        " * (unit address + usb-phy labels + maximum-speed) are FIXED TEMPLATE\n"
        " * content confirmed from one real accepted reference example (usb0) -\n"
        " * NOT derived from Excel. "
        + ("This is that confirmed instance."
           if is_confirmed_instance else
           "This is a DIFFERENT instance (" + consumer_node_label + ") - the "
           "dwc3 unit address and usb-phy labels below are NOT confirmed to "
           "generalize and should be verified against a real reference for "
           "this instance before use.")
        + "\n"
        " * This file is deterministic template output - it is NOT LLM-generated.\n"
        " */\n\n"
    )
    consumer_node_text = consumer_header + "\n".join(consumer_lines) + "\n"

    return pinmux_dtsi_text, consumer_node_text, any_needs_review


def generate_usb_consumer_dts(
    project: str, soc: str, matched: list[NormalizedRecord], gen5_slug: str,
    pinmux_setting_slug: str, columns: list[ColumnMeta], project_slugs: set[str],
    consumer_node_label: str = "usb0",
) -> dict:
    """Single-instance entry point: generates ONE consumer node using
    ALL safe oc_gpio/vbus_gpio candidates found, regardless of which
    controller instance they actually belong to. Kept for simple
    cases and backward compatibility - if the matched set spans more
    than one real controller instance (confirmed real case: USB30 and
    USB20 are separate), prefer generate_usb_consumer_dts_multi_instance()
    instead, which correctly generates one node per instance rather
    than silently merging them (confirmed real bug this caused: a
    second instance's oc-gpio candidate was rendered as a pinctrl
    state but never wired to any oc-gpio property at all)."""
    cat, fallback_counts = categorize_usb_records(
        matched, gen5_slug, pinmux_setting_slug, columns, project_slugs)

    excluded_non_tlmm = [
        {"ball_name": _ball_name(entry), "gen5_signal_name": entry.fields.get(gen5_slug)}
        for entry in cat["differential"]
    ]
    # This simpler generator has no machinery to update/generate a node for a real,
    # single-purpose pin whose ROLE it doesn't recognize (see "other_gpio" in
    # categorize_usb_records - the CLI tool / "USB DTS Tool" panel does handle this).
    # Reported here as review items too, with a reason that says so explicitly, rather
    # than silently vanishing from every count and table this function returns.
    review_and_other = cat["needs_review"] + _extra_review(cat) + [
        {"record": e["record"], "reason": f"real, single-purpose signal ({e['signal']}) - role not "
                                          f"recognized by this view; use the USB DTS Tool for full coverage"}
        for e in cat["other_gpio"]
    ]
    needs_review_out = [
        {"ball_name": _ball_name(entry["record"]), "gen5_signal_name": entry["record"].fields.get(gen5_slug),
         "reason": entry["reason"]}
        for entry in review_and_other
    ]

    is_confirmed_instance = (consumer_node_label == "usb0")
    pinmux_dtsi_text, consumer_node_text, any_needs_review = _render_instance_output(
        project, soc, consumer_node_label, is_confirmed_instance,
        cat["oc_gpio"], cat["vbus_gpio"],
        len(cat["differential"]), len(review_and_other), len(matched),
    )

    return {
        "pinmux_dtsi_text": pinmux_dtsi_text,
        "consumer_node_text": consumer_node_text,
        "needs_review": needs_review_out,
        "excluded_non_tlmm": excluded_non_tlmm,
        "any_pin_config_needs_review": any_needs_review,
        "used_ball_name_fallback_count": fallback_counts["ball_name_fallback"] + fallback_counts["instance_fallback"],
        "is_confirmed_instance": is_confirmed_instance,
        "counts": {
            "differential": len(cat["differential"]),
            "oc_gpio": len(cat["oc_gpio"]),
            "vbus_gpio": len(cat["vbus_gpio"]),
            "needs_review": len(review_and_other),
        },
    }


def generate_usb_consumer_dts_multi_instance(
    project: str, soc: str, matched: list[NormalizedRecord], gen5_slug: str,
    pinmux_setting_slug: str, columns: list[ColumnMeta], project_slugs: set[str],
) -> dict:
    """Detects every distinct USB controller instance among the safe
    oc_gpio/vbus_gpio candidates (via detect_usb_instance_token) and
    generates a SEPARATE consumer node for each one - confirmed real
    case this fixes: USB30 (GPIO_81 oc, GPIO_82 vbus) and USB20
    (GPIO_102 oc, no confirmed vbus - GPIO_92 was excluded as multi-
    purpose) are genuinely different controllers; merging them into
    one node meant USB20's oc-gpio candidate was generated as a
    pinctrl state but never actually wired to any oc-gpio property.

    needs_review and excluded_non_tlmm are GLOBAL (not tied to any one
    instance) - shown once, not duplicated per instance. A candidate
    whose instance token can't be detected at all is treated as its
    own "unknown"-labelled instance rather than silently dropped or
    silently merged into a different one.

    Returns {"instances": [...], "needs_review": [...],
    "excluded_non_tlmm": [...], "used_ball_name_fallback_count": int}."""
    cat, fallback_counts = categorize_usb_records(
        matched, gen5_slug, pinmux_setting_slug, columns, project_slugs)

    excluded_non_tlmm = [
        {"ball_name": _ball_name(entry), "gen5_signal_name": entry.fields.get(gen5_slug)}
        for entry in cat["differential"]
    ]
    # This simpler generator has no machinery to update/generate a node for a real,
    # single-purpose pin whose ROLE it doesn't recognize (see "other_gpio" in
    # categorize_usb_records - the CLI tool / "USB DTS Tool" panel does handle this).
    # Reported here as review items too, with a reason that says so explicitly, rather
    # than silently vanishing from every count and table this function returns.
    review_and_other = cat["needs_review"] + _extra_review(cat) + [
        {"record": e["record"], "reason": f"real, single-purpose signal ({e['signal']}) - role not "
                                          f"recognized by this view; use the USB DTS Tool for full coverage"}
        for e in cat["other_gpio"]
    ]
    needs_review_out = [
        {"ball_name": _ball_name(entry["record"]), "gen5_signal_name": entry["record"].fields.get(gen5_slug),
         "reason": entry["reason"]}
        for entry in review_and_other
    ]

    by_instance: dict[str, dict[str, list]] = {}
    for entry in cat["oc_gpio"]:
        token = entry.get("instance_token") or "UNKNOWN"
        by_instance.setdefault(token, {"oc_gpio": [], "vbus_gpio": []})["oc_gpio"].append(entry)
    for entry in cat["vbus_gpio"]:
        token = entry.get("instance_token") or "UNKNOWN"
        by_instance.setdefault(token, {"oc_gpio": [], "vbus_gpio": []})["vbus_gpio"].append(entry)

    instances = []
    instance_labels = resolve_instance_labels(sorted(by_instance.keys()))
    for token in sorted(by_instance.keys()):
        candidates = by_instance[token]
        label, is_confirmed = instance_labels[token]
        pinmux_dtsi_text, consumer_node_text, any_needs_review = _render_instance_output(
            project, soc, label, is_confirmed,
            candidates["oc_gpio"], candidates["vbus_gpio"],
            len(cat["differential"]), len(review_and_other), len(matched),
        )
        instances.append({
            "instance_token": token,
            "consumer_node_label": label,
            "is_confirmed_instance": is_confirmed,
            "pinmux_dtsi_text": pinmux_dtsi_text,
            "consumer_node_text": consumer_node_text,
            "any_pin_config_needs_review": any_needs_review,
            "counts": {
                "oc_gpio": len(candidates["oc_gpio"]),
                "vbus_gpio": len(candidates["vbus_gpio"]),
            },
        })

    return {
        "instances": instances,
        "needs_review": needs_review_out,
        "excluded_non_tlmm": excluded_non_tlmm,
        "used_ball_name_fallback_count": fallback_counts["ball_name_fallback"] + fallback_counts["instance_fallback"],
        "counts": {
            "differential": len(cat["differential"]),
            "needs_review": len(review_and_other),
        },
    }
