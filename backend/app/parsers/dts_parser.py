"""
Parses an EXISTING .dts/.dtsi file (uploaded by the user, e.g. a real
reference file to be updated) into a list of pin-group nodes, each with
its exact character span in the original text - so updates can be
surgical string replacements that leave everything else (comments,
spacing, unrelated nodes, header) byte-for-byte untouched.

Deliberately NOT a full devicetree-source grammar parser - that's more
machinery than this needs and riskier to get subtly wrong. Instead this
recognizes the specific pin-group shape confirmed against the real
reference file:

    label: label {
        mux {
            pins = "gpio43", "gpio46";
            function = "qup13";
        };
        config {
            pins = "gpio43", "gpio46";
            drive-strength = <2>;
            bias-pull-up;
        };
    };

Anything that doesn't match this shape (comments, #include lines, other
node types) is left alone - it's simply not returned as a parseable node,
and the updater must never touch text outside a returned node's span.
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field


@dataclass
class ParsedPinNode:
    label: str
    start: int  # character offset in the original text where this node begins
    end: int    # character offset where it ends (exclusive)
    mux_pins: list[str]
    mux_function: str | None
    config_pins: list[str]
    drive_strength: str | None  # the raw number inside <...>, e.g. "2"
    bias_directive: str | None  # e.g. "bias-pull-up", "bias-disable"
    direction_directive: str | None  # e.g. "input-enable", "output-high"
    raw_text: str  # the exact original text of this node (start:end)
    node_indent: str = ""
    mux_indent: str = ""
    mux_property_indent: str = ""
    config_indent: str = ""
    config_property_indent: str = ""


@dataclass
class UnrecognizedNodeCandidate:
    """A `label: label { ... }` block the strict parser found but could
    NOT recognize as the confirmed mux/config pin-group shape - e.g. a
    node using different property names, a different block structure, or
    genuinely something else entirely. Exists so a caller can OPTIONALLY
    try an AI-assisted extraction on exactly this text (see
    app/parsers/ai_node_extractor.py) instead of the block being silently
    discarded forever. The strict parser's own behavior is unchanged -
    these are simply also returned now instead of only being skipped."""
    label: str
    start: int
    end: int
    raw_text: str


_NODE_START_RE = re.compile(r'(?P<label>[\w-]+)\s*:\s*(?P=label)\s*\{', re.MULTILINE)
_PINS_RE = re.compile(r'pins\s*=\s*((?:"[^"]*"\s*,?\s*)+);')
_QUOTED_RE = re.compile(r'"([^"]*)"')
_FUNCTION_RE = re.compile(r'function\s*=\s*"([^"]*)"\s*;')
_DRIVE_STRENGTH_RE = re.compile(r'drive-strength\s*=\s*<(\d+)>\s*;')
_BIAS_RE = re.compile(r'\b(bias-pull-up|bias-pull-down|bias-disable)\s*;')
_DIRECTION_RE = re.compile(r'\b(input-enable|output-high|output-low)\s*;')


def _find_matching_brace(text: str, open_pos: int) -> int:
    """Given the index of a '{', returns the index of its matching '}'
    (exclusive end, i.e. one past the '}'). Simple depth counter - assumes
    no braces inside string literals other than what's already excluded
    by this file's node shape (pin/function values don't contain braces
    in any real DTS)."""
    depth = 0
    i = open_pos
    while i < len(text):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError(f"Unbalanced braces starting at offset {open_pos}")


def _extract_sub_block(text: str, name: str) -> str | None:
    """Finds `name { ... }` within `text` and returns its inner content
    (between the braces), or None if not present."""
    m = re.search(rf'\b{name}\s*\{{', text)
    if not m:
        return None
    open_pos = m.end() - 1
    close_pos = _find_matching_brace(text, open_pos)
    return text[open_pos + 1:close_pos - 1]


def parse_dts_nodes(text: str) -> list[ParsedPinNode]:
    """Scans the whole file text and returns every recognizable pin-group
    node. Order matches their order of appearance in the file. Backward-
    compatible signature - existing callers (the non-AI-fallback update
    path) are unaffected. See parse_dts_nodes_with_candidates() for the
    version that also surfaces unrecognized node-shaped blocks."""
    nodes, _candidates = parse_dts_nodes_with_candidates(text)
    return nodes


def parse_dts_nodes_with_candidates(
    text: str,
) -> tuple[list[ParsedPinNode], list[UnrecognizedNodeCandidate]]:
    """Same scan as parse_dts_nodes(), but ALSO returns every
    `label: label { ... }` block that was found but did NOT match the
    strict mux/config shape - real node-shaped text that used to be
    silently discarded. Callers that want the AI-assisted parsing
    fallback use the second list; callers that don't can ignore it."""
    nodes: list[ParsedPinNode] = []
    candidates: list[UnrecognizedNodeCandidate] = []

    # First pass: find every labeled span, so nesting can be checked
    # before deciding how to treat each one.
    all_spans: list[tuple[str, int, int]] = []  # (label, start, end)
    for m in _NODE_START_RE.finditer(text):
        open_brace_pos = m.end() - 1
        try:
            end = _find_matching_brace(text, open_brace_pos)
        except ValueError:
            continue  # malformed - skip rather than crash the whole parse
        all_spans.append((m.group("label"), m.start(), end))

    for label, start, end in all_spans:
        raw = text[start:end]

        # A GROUPING container: some real DTS files nest several genuine
        # pin nodes inside one outer "label: label { ... }" wrapper that
        # has no mux/config of its own (see monaco_auto-pinctrl.dtsi,
        # confirmed real case: qupv3_se0_i2c_pins wraps three separate
        # child nodes, each with their own mux/config). Naively running
        # _extract_sub_block on the OUTER span finds the FIRST CHILD's
        # mux/config (since search() doesn't know about the nesting),
        # wrongly attributes it to the wrapper, and the resulting text
        # reconstruction corrupts the boundary with the next sibling -
        # confirmed real corruption produced exactly this way. Detected
        # by checking whether any OTHER labeled span starts strictly
        # inside this one; if so, this is a pure wrapper - skip it
        # entirely (neither a node nor a candidate) and let its real
        # children be found and processed independently below, each
        # correctly scoped to its own span.
        has_nested_labels = any(
            other_start > start and other_end <= end
            for other_label, other_start, other_end in all_spans
            if not (other_start == start and other_end == end)
        )
        if has_nested_labels:
            continue

        mux_block = _extract_sub_block(raw, "mux")
        config_block = _extract_sub_block(raw, "config")
        if mux_block is None or config_block is None:
            candidates.append(UnrecognizedNodeCandidate(
                label=label, start=start, end=end, raw_text=raw,
            ))
            continue  # doesn't match the pin-group shape - not our node type

        mux_pins_m = _PINS_RE.search(mux_block)
        mux_pins = _QUOTED_RE.findall(mux_pins_m.group(1)) if mux_pins_m else []
        function_m = _FUNCTION_RE.search(mux_block)
        mux_function = function_m.group(1) if function_m else None

        config_pins_m = _PINS_RE.search(config_block)
        config_pins = _QUOTED_RE.findall(config_pins_m.group(1)) if config_pins_m else []
        drv_m = _DRIVE_STRENGTH_RE.search(config_block)
        drive_strength = drv_m.group(1) if drv_m else None
        bias_m = _BIAS_RE.search(config_block)
        bias_directive = bias_m.group(1) if bias_m else None
        dir_m = _DIRECTION_RE.search(config_block)
        direction_directive = dir_m.group(1) if dir_m else None

        def block_indent(block_name: str, fallback: str) -> str:
            match = re.search(rf"(?m)^([ \t]*){block_name}\s*\{{", raw)
            return match.group(1) if match else fallback

        def property_indent(block_name: str, fallback: str) -> str:
            section = _extract_sub_block(raw, block_name)
            match = re.search(r"(?m)^([ \t]*)pins\s*=", section or "")
            return match.group(1) if match else fallback

        line_start = text.rfind("\n", 0, start) + 1
        node_indent = text[line_start:start]
        mux_indent = block_indent("mux", node_indent + "\t")
        config_indent = block_indent("config", mux_indent)

        nodes.append(ParsedPinNode(
            label=label, start=start, end=end,
            mux_pins=mux_pins, mux_function=mux_function,
            config_pins=config_pins, drive_strength=drive_strength,
            bias_directive=bias_directive, direction_directive=direction_directive,
            raw_text=raw,
            node_indent=node_indent,
            mux_indent=mux_indent,
            mux_property_indent=property_indent("mux", mux_indent + "\t"),
            config_indent=config_indent,
            config_property_indent=property_indent("config", config_indent + "\t"),
        ))
    return nodes, candidates