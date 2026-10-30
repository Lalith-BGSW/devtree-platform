"""
Updates an EXISTING .dts/.dtsi file's content using the Excel workbook as
the source of truth, per the confirmed policy:

  - A pin's DTS value that disagrees with Excel gets overwritten, with an
    inline comment showing old -> new.
  - A node covering multiple pins where only some match Excel: the
    matched pins' properties get updated; unmatched pins are left alone
    with a flag comment noting they weren't found in Excel.
  - A pin counts as "covered" (excluded from the remainder file) simply
    by appearing in ANY uploaded file - correctness of its values doesn't
    affect coverage, only whether it needed updating.

Edits are surgical: only the text span of a node that actually needs a
change is replaced. Everything else in the file - comments, spacing,
unrelated nodes, the license header - is left byte-for-byte untouched.
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field

from app.models import NormalizedRecord
from app.parsers.dts_parser import ParsedPinNode, parse_dts_nodes, parse_dts_nodes_with_candidates
from app.parsers.ai_node_extractor import extract_node_data
from app.generators.dts_generator import derive_pin_config, DerivedPinConfig, _to_dts_pin_name


@dataclass
class PinChange:
    pin: str
    field: str  # "function" | "drive-strength" | "bias"
    old_value: str | None
    new_value: str


@dataclass
class NodeUpdateResult:
    label: str
    changed: bool
    changes: list[PinChange] = field(default_factory=list)
    unmatched_pins: list[str] = field(default_factory=list)
    # Set when the node's own mux/config blocks disagree about which pins
    # they describe (e.g. mux says "gpio100", config still says "gpio10")
    # - a structural inconsistency in the SOURCE FILE ITSELF, not an Excel
    # mismatch. Left completely unmodified when this fires: picking either
    # side as "authoritative" would be a guess, and a real case showed
    # that guess can silently orphan the node's real intended pin while
    # confidently absorbing an unrelated pin's Excel data instead.
    structural_issue: str | None = None
    review_note: str | None = None
    # Set only for nodes parsed via the AI-assisted fallback (see
    # app/parsers/ai_node_extractor.py) - a format the strict parser
    # didn't recognize. `changed` is ALWAYS False for these: the node's
    # text is never auto-rewritten, since we can't safely re-serialize a
    # format we don't structurally understand. `ai_advisory` carries a
    # human-readable summary of what was read and what, if anything,
    # disagrees with Excel - inserted as a comment near the node, never
    # replacing its content.
    ai_assisted: bool = False
    ai_advisory: str | None = None


@dataclass
class DtsUpdateResult:
    updated_text: str
    node_results: list[NodeUpdateResult]
    covered_pins: set[str]  # every pin name that appeared in this file at all
    ai_fallback_requested: bool = False
    ai_candidate_count: int = 0
    ai_extraction_failure_count: int = 0
    ai_fallback_status: str = ""


def _format_config_block(node: ParsedPinNode, derived: DerivedPinConfig,
                          changes: list[PinChange]) -> str:
    """Rebuilds just the `config { ... }` portion of a node with updated
    values, preserving the node's original indentation style."""
    config_indent = node.config_indent or node.node_indent + "\t"
    property_indent = node.config_property_indent or config_indent + "\t"
    lines = [f"{config_indent}config {{"]
    pins_str = ", ".join(f'"{p}"' for p in node.config_pins)
    lines.append(f"{property_indent}pins = {pins_str};")
    if derived.drive_strength:
      lines.append(f"{property_indent}drive-strength = <{derived.drive_strength}>;")
    if derived.bias_directive:
      lines.append(f"{property_indent}{derived.bias_directive};")
    # The existing node's own direction line is kept: for an output pin the Excel gives no boot level, so a person's
    # output-high / output-low must never be replaced by a guess.
    if node.direction_directive:
      lines.append(f"{property_indent}{node.direction_directive};")
    elif derived.direction_directive:
      lines.append(f"{property_indent}{derived.direction_directive};")
    elif derived.output_level_unknown:
      lines.append(f"{property_indent}/* NEEDS REVIEW: output pin - add output-high or output-low per hardware design (level is not derivable from Excel) */")
    for c in changes:
      lines.append(f"{property_indent}/* UPDATED from Excel: {c.field} {c.old_value!r} -> {c.new_value!r} */")
    lines.append(f"{config_indent}}};")
    return "\n".join(lines)


def _format_mux_block(node: ParsedPinNode, function: str | None) -> str:
    mux_indent = node.mux_indent or node.node_indent + "\t"
    property_indent = node.mux_property_indent or mux_indent + "\t"
    lines = [f"{mux_indent}mux {{"]
    pins_str = ", ".join(f'"{p}"' for p in node.mux_pins)
    lines.append(f"{property_indent}pins = {pins_str};")
    if function is not None:
        lines.append(f'{property_indent}function = "{function}";')
    lines.append(f"{mux_indent}}};")
    return "\n".join(lines)


def _function_update_is_safe(old_function: str | None, new_function: str) -> tuple[bool, str | None]:
    """Returns (should_update, review_note_or_None).

    Function-name derivation for non-QUP interfaces (see
    app/generators/dts_generator.py's render_node) is a lower-confidence
    slugify of Excel's Selected Interface value - unlike drive-strength/
    bias, there's no verified "N: LABEL" enum format backing it. If the
    file's EXISTING function name is a more specific variant of what
    Excel derives (e.g. existing="cci_i2c", derived="cci" - Excel's
    Selected Interface value "CCI" lacks the "_i2c" suffix that
    PinMuxed Name's "CCI_I2C_SDA0" happens to carry), blindly overwriting
    a working, more-specific value with a less-specific guess is a
    regression, not a correction. Confirmed on the real workbook's CCI
    camera-control pins - exactly this happened before this check
    existed. Skip the update and flag it for manual review instead of
    silently downgrading a value that was already correct.
    """
    if old_function is None or old_function == new_function:
        return True, None
    if old_function.startswith(new_function + "_") or old_function.startswith(new_function + "-"):
        return False, (f"kept existing function {old_function!r} - the workbook's derived "
                        f"value {new_function!r} is less specific than what's already here "
                        f"(likely because Excel's Selected Interface field is coarser than "
                        f"the real function name); needs manual confirmation, not "
                        f"auto-overwritten")
    return True, None


def _evaluate_ai_candidate(candidate, pin_index: dict[str, NormalizedRecord]
                            ) -> tuple[NodeUpdateResult, list[str]] | None:
    """Attempts AI-assisted extraction on a node the strict parser
    couldn't recognize, then compares whatever was read against Excel -
    but NEVER rewrites the node's text. Returns None if extraction
    genuinely produced nothing usable (caller leaves the node exactly as
    the strict parser already does today - untouched, unreported).
    Returns (result, all_extracted_pins) otherwise - the pin list is
    returned separately so the caller can update pin coverage for BOTH
    matched and unmatched pins (a pin genuinely appearing in this file
    counts as covered regardless of whether Excel found a match for it -
    same "appearing is enough" policy as strict-parsed nodes)."""
    extracted = extract_node_data(candidate.raw_text)
    if extracted is None:
        return None

    matched: list[tuple[str, DerivedPinConfig]] = []
    unmatched: list[str] = []
    for pin in extracted.pins:
        normalized = _to_dts_pin_name(pin)
        rec = pin_index.get(normalized) or pin_index.get(pin.lower())
        if rec is None:
            unmatched.append(pin)
            continue
        derived = derive_pin_config(rec)
        if derived is None:
            unmatched.append(pin)
            continue
        matched.append((pin, derived))

    lines = [
        f"AI-ASSISTED PARSE (confidence: {extracted.confidence}) - this node's "
        f"format wasn't recognized by the standard parser, so it was NOT "
        f"automatically updated. What was read: pins={extracted.pins}, "
        f"function={extracted.function!r}, drive_strength={extracted.drive_strength!r}, "
        f"bias={extracted.bias_directive!r}.",
    ]
    if unmatched:
        lines.append(f"Not found in Excel: {unmatched}.")
    if matched:
        primary_pin, primary = matched[0]
        disagreements = []
        if extracted.function is not None and primary.function != extracted.function:
            if primary.node_type == "generic":
                disagreements.append(
                    f"function: file says {extracted.function!r}; Excel Selected Interface "
                    f"{primary.interface_value!r} only yields the unverified function candidate "
                    f"{primary.function!r}"
                )
            else:
                disagreements.append(f"function: file says {extracted.function!r}, Excel derives {primary.function!r}")
        if primary.drive_strength and extracted.drive_strength != primary.drive_strength:
            disagreements.append(f"drive-strength: file says {extracted.drive_strength!r}, Excel says {primary.drive_strength!r}")
        if primary.bias_directive and extracted.bias_directive != primary.bias_directive:
            disagreements.append(f"bias: file says {extracted.bias_directive!r}, Excel says {primary.bias_directive!r}")
        if disagreements:
            lines.append("Excel disagrees on: " + "; ".join(disagreements) + ". Please review and update manually.")
        else:
            lines.append("Values appear to already match Excel.")

    return NodeUpdateResult(
        label=candidate.label, changed=False, unmatched_pins=unmatched,
        ai_assisted=True, ai_advisory=" ".join(lines),
    ), extracted.pins


def _update_single_node(node: ParsedPinNode, pin_index: dict[str, NormalizedRecord]
                         ) -> tuple[str, NodeUpdateResult]:
    """Returns (new_node_text, result). new_node_text == node.raw_text
    when nothing needed to change."""
    if set(node.mux_pins) != set(node.config_pins):
        # The node's own mux and config blocks disagree about which
        # physical pin(s) they describe - e.g. mux lists "gpio100" but
        # config still lists "gpio10". Neither side is more
        # "authoritative" - silently trusting one (as an earlier version
        # of this code did, always via mux_pins) can confidently absorb
        # an unrelated pin's real Excel data into a node while silently
        # orphaning the pin the node was actually meant to represent.
        # Left completely untouched; surfaced so the person can fix the
        # source file's internal inconsistency themselves.
        issue = (f"mux pins {node.mux_pins} do not match config pins "
                 f"{node.config_pins} - node left unmodified")
        return node.raw_text, NodeUpdateResult(
            label=node.label, changed=False, structural_issue=issue,
        )

    matched: list[tuple[str, DerivedPinConfig]] = []
    unmatched: list[str] = []

    for pin in node.mux_pins:
        rec = pin_index.get(pin)
        if rec is None:
            unmatched.append(pin)
            continue
        derived = derive_pin_config(rec)
        if derived is None:
            unmatched.append(pin)
            continue
        matched.append((pin, derived))

    state_specific = bool(re.search(
        r"(?:^|[_-])(?:default|sleep|suspend|off|low_power|lp)(?:$|[_-])", node.label, re.IGNORECASE,
    ))
    if state_specific:
        return node.raw_text, NodeUpdateResult(
            label=node.label,
            changed=False,
            unmatched_pins=unmatched,
            review_note=("This node is named as a default or low-power state; the workbook has no "
                         "state-specific configuration, so its pin settings were left unchanged."),
        )

    if not matched:
        return node.raw_text, NodeUpdateResult(
            label=node.label, changed=False, unmatched_pins=unmatched,
        )

    # Use the first matched pin's derived config as authoritative for this
    # node's shared (per-node, not per-pin) properties. If other matched
    # pins disagree, that's a real data inconsistency worth surfacing,
    # not silently averaging away.
    primary_pin, primary = matched[0]
    changes: list[PinChange] = []
    review_notes: list[str] = []

    effective_function = primary.function
    if node.mux_function != primary.function:
        if primary.node_type == "generic":
            effective_function = node.mux_function
            review_notes.append(
                f"kept existing function {node.mux_function!r}; Excel Selected Interface "
                f"{primary.interface_value!r} only yields the unverified function candidate "
                f"{primary.function!r}, so the mux function needs manual confirmation"
            )
        else:
            should_update, review_note = _function_update_is_safe(node.mux_function, primary.function)
            if should_update:
                changes.append(PinChange(primary_pin, "function", node.mux_function, primary.function))
            else:
                effective_function = node.mux_function  # keep the existing, more-specific value
                review_notes.append(review_note)
    if primary.drive_strength and node.drive_strength != primary.drive_strength:
        changes.append(PinChange(primary_pin, "drive-strength", node.drive_strength, primary.drive_strength))
    if primary.bias_directive and node.bias_directive != primary.bias_directive:
        changes.append(PinChange(primary_pin, "bias", node.bias_directive, primary.bias_directive))

    # Flag (not silently resolve) disagreement between multiple matched
    # pins sharing this node's properties.
    inconsistent_notes = []
    for pin, derived in matched[1:]:
        if derived.function != primary.function:
            inconsistent_notes.append(f"pin {pin!r} Excel function is {derived.function!r}, differs from {primary_pin!r}'s {primary.function!r}")
        if derived.drive_strength and derived.drive_strength != primary.drive_strength:
            inconsistent_notes.append(f"pin {pin!r} Excel drive-strength is {derived.drive_strength!r}, differs from {primary_pin!r}'s {primary.drive_strength!r}")
        if derived.bias_directive and derived.bias_directive != primary.bias_directive:
            inconsistent_notes.append(f"pin {pin!r} Excel bias is {derived.bias_directive!r}, differs from {primary_pin!r}'s {primary.bias_directive!r}")

    if not changes and not unmatched and not inconsistent_notes and not review_notes:
        return node.raw_text, NodeUpdateResult(label=node.label, changed=False)

    mux_text = _format_mux_block(node, effective_function)
    config_text = _format_config_block(node, primary, changes)

    extra_comments = []
    child_indent = node.mux_indent or node.node_indent + "\t"
    if unmatched:
        extra_comments.append(f"{child_indent}/* NOT FOUND IN EXCEL - left unmodified: {', '.join(unmatched)} */")
    for note in inconsistent_notes:
        extra_comments.append(f"{child_indent}/* INCONSISTENT: {note} */")
    for note in review_notes:
        extra_comments.append(f"{child_indent}/* REVIEW: {note} */")

    new_text = f"{node.label}: {node.label} {{\n{mux_text}\n\n{config_text}\n" + \
               ("\n".join(extra_comments) + "\n" if extra_comments else "") + f"{node.node_indent}}}"

    return new_text, NodeUpdateResult(
        label=node.label, changed=bool(changes), changes=changes, unmatched_pins=unmatched,
        review_note="; ".join(review_notes) if review_notes else None,
    )


def update_dts_file(text: str, pin_index: dict[str, NormalizedRecord],
                     use_ai_fallback: bool = False) -> DtsUpdateResult:
    """Parses `text`, updates every recognizable pin-group node's
    properties against `pin_index` (built by
    app.generators.dts_generator.build_pin_index), and returns the
    updated file text plus a per-node change report. Text outside
    recognized nodes is never touched.

    use_ai_fallback (default False - opt-in): when a node's format isn't
    recognized by the strict parser, additionally try AI-assisted
    extraction on it and, if that succeeds, insert an advisory comment
    reporting what was read and how it compares to Excel - but NEVER
    rewrite the node's own text, since re-serializing a format we don't
    structurally understand risks producing invalid devicetree source.
    Requires GROQ_API_KEY; silently produces no advisories (same as
    use_ai_fallback=False) if no key is configured.
    """
    nodes, candidates = parse_dts_nodes_with_candidates(text)

    node_results: list[NodeUpdateResult] = []
    covered_pins: set[str] = set()
    # (start_offset, result) pairs so the final report can be sorted back
    # into original file order regardless of whether a result came from a
    # strict-parsed node or an AI-assisted candidate.
    ordered_results: list[tuple[int, NodeUpdateResult]] = []

    # Combine node replacements and candidate-advisory insertions into one
    # list of (start, end, new_text, is_insertion) and apply them in a
    # single back-to-front pass, so offsets computed before any edit stay
    # valid throughout - mixing two separate edit passes would invalidate
    # each other's offsets.
    edits: list[tuple[int, int, str, bool]] = []

    for node in nodes:
        covered_pins.update(node.mux_pins)
        covered_pins.update(node.config_pins)
        new_text, result = _update_single_node(node, pin_index)
        ordered_results.append((node.start, result))
        if new_text != node.raw_text:
            edits.append((node.start, node.end, new_text, False))

    ai_extraction_failures = 0
    for candidate in candidates if use_ai_fallback else []:
        outcome = _evaluate_ai_candidate(candidate, pin_index)
        if outcome is None:
            ai_extraction_failures += 1
            continue
        result, extracted_pins = outcome
        ordered_results.append((candidate.start, result))
        covered_pins.update(_to_dts_pin_name(p) for p in extracted_pins)
        # candidate.end is right after the node's closing '}', but DTS
        # node syntax requires a trailing ';' after that brace - insert
        # the advisory AFTER that ';', not between the brace and it (which
        # would split "};" into "}" + advisory + ";", malformed output).
        insert_at = candidate.end
        while insert_at < len(text) and text[insert_at] in " \t":
            insert_at += 1
        if insert_at < len(text) and text[insert_at] == ";":
            insert_at += 1
        advisory_comment = f"\n\t/* {result.ai_advisory} */\n"
        edits.append((insert_at, insert_at, advisory_comment, True))

    edits.sort(key=lambda e: e[0], reverse=True)
    updated_text = text
    for start, end, new_text, is_insertion in edits:
        if is_insertion:
            updated_text = updated_text[:start] + new_text + updated_text[start:]
        else:
            updated_text = updated_text[:start] + new_text + updated_text[end:]

    ordered_results.sort(key=lambda pair: pair[0])  # restore original file order
    node_results = [result for _start, result in ordered_results]
    ai_advisory_count = sum(1 for result in node_results if result.ai_assisted)
    if not use_ai_fallback:
        ai_status = (f"AI fallback is off; {len(candidates)} unrecognized node(s) were left untouched."
                     if candidates else "No unrecognized nodes needed AI analysis.")
    elif not candidates:
        ai_status = "No unrecognized nodes needed AI analysis."
    elif ai_extraction_failures:
        from app import llm_client
        llm_config = llm_client.config()
        if not (llm_config["key"] and llm_config["deployment"]):
            unavailable_reason = "Bosch Model Farm is not configured (API key or deployment is missing)."
        elif "node_extractor" not in llm_config["features"]:
            unavailable_reason = "node_extractor is not enabled in LLM_FEATURES."
        else:
            unavailable_reason = "The gateway call failed or returned data the safe extractor could not use."
        ai_status = (f"AI fallback was requested for {len(candidates)} unrecognized node(s); "
                     f"{ai_advisory_count} advisory/advisories produced and {ai_extraction_failures} node(s) had no usable response. "
                     f"{unavailable_reason} Unrecognized nodes were not rewritten.")
    else:
        ai_status = (f"AI produced read-only advisories for {ai_advisory_count} unrecognized node(s). "
                     "Unrecognized nodes were not rewritten.")
    return DtsUpdateResult(
        updated_text=updated_text, node_results=node_results, covered_pins=covered_pins,
        ai_fallback_requested=use_ai_fallback, ai_candidate_count=len(candidates),
        ai_extraction_failure_count=ai_extraction_failures, ai_fallback_status=ai_status,
    )
