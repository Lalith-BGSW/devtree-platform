"""
TEMPLATE-DRIVEN OUTPUT FILES.

A template DTS file tells us HOW the project writes DTS - never WHAT to write. This module learns the format of a template
(indentation, `label: label` vs `label:label`, `mux{}`/`config{}`, `pinmux{}`/`pinconf{}` or flat nodes, blank lines, the copyright header, `/dts-v1/`-style
preamble, line endings) and writes the already-verified output in that format, as separate files by role:

    pinmux file   - the `&tlmm { ... }` pin states                -> <project>_usb_pinmux.dtsi
    overlay file  - the `&usbN` consumers, regulators, ...        -> <project>_usb_overlay.dts

Guarantees (enforced here, not hoped for):
  * Content is never copied from a template: not its pins, not its labels, not its project-specific `#include "..."` lines (those are only
    listed, so a person can decide). Only the header comment and generic preamble (`/dts-v1/;`, `/plugin/;`) are reused.
  * Restyling cannot change a value: the restyled pin nodes are parsed back and must carry exactly the same labels, pins, functions and
    properties as the verified output. If they do not, that file is written in the default format instead, and the report says why.
  * A template that cannot be read, or has no recognisable pin states, falls back to the default format for that role - stated, not silent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

DEFAULT_SOURCE = "default format (the vendor BSP style of the samples: tabs, mux{} / config{}, 'label: label')"


@dataclass
class Style:
    role: str = "pinmux"                 # pinmux | overlay
    source: str = DEFAULT_SOURCE
    indent: str = "\t"
    label_sep: str = ": "
    layout: str = "mux_config"           # mux_config | pinmux_pinconf | flat
    blank_in_node: bool = False          # a blank line between nested pin-state blocks
    blank_between_nodes: bool = True
    name_style: str = "same"             # same: `label: label {`   hyphen_state: `label: label-with-hyphens-state {`
    eol: str = "\n"
    header: str = ""                     # the template's leading copyright / comment block
    preamble: list = field(default_factory=list)          # /dts-v1/;  /plugin/;   (structure: reused)
    skipped_includes: list = field(default_factory=list)  # every #include of the template: listed, never copied (SoC / project specific)
    learned: bool = False
    mixed: list = field(default_factory=list)    # habits the template itself is inconsistent about (kept: the majority)
    recognized_pin_states: int = 0
    unrecognized_pin_states: int = 0
    unrecognized_pin_state_headers: list = field(default_factory=list)

    def summary(self) -> str:
        ind = "tabs" if self.indent == "\t" else f"{len(self.indent)} spaces"
        lay = {"mux_config": "mux{}/config{}", "pinmux_pinconf": "pinmux{}/pinconf{}"}.get(self.layout, "flat")
        sep = "'label: node'" if self.label_sep == ": " else "'label:node'"
        pin = (f", {lay} pin states, {(str(self.recognized_pin_states) + ' state(s) analyzed, ') if self.learned and self.recognized_pin_states else ''}blank line between nodes: {'yes' if self.blank_between_nodes else 'no'}"
               f"{', blank line inside a node: yes' if self.blank_in_node else ''}") if self.role == "pinmux" else ""
        return (f"{ind}, {sep}{pin}, "
                f"{'CRLF' if self.eol == chr(13) + chr(10) else 'LF'} line endings"
                f"{', copyright header copied' if self.header else ''}")


# ------------------------------------------------------------------------------------------------------------ scanning
def _norm(text: str) -> str:
    return (text or "").lstrip("\ufeff").replace("\r\n", "\n")


def _skip_non_code(text: str, i: int) -> int:
    """If a comment or string starts at i, return the index after it; else i."""
    n = len(text)
    if text.startswith("/*", i):
        j = text.find("*/", i + 2)
        return n if j < 0 else j + 2
    if text.startswith("//", i):
        j = text.find("\n", i)
        return n if j < 0 else j
    if text[i] == '"':
        j = i + 1
        while j < n and text[j] != '"':
            j += 2 if text[j] == "\\" else 1
        return j + 1
    return i


def top_level_blocks(text: str) -> list[dict]:
    """[{header, text, start, end}] for each brace block at depth 0. `text` runs from the start of the header line through the closing `};`."""
    blocks, depth, i, n, start = [], 0, 0, len(text), None
    while i < n:
        j = _skip_non_code(text, i)
        if j != i:
            i = j
            continue
        c = text[i]
        if c == "{":
            if depth == 0:
                start = text.rfind("\n", 0, i) + 1
            depth += 1
        elif c == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                k = i + 1
                while k < n and text[k] in " \t":
                    k += 1
                end = k + 1 if k < n and text[k] == ";" else i + 1
                blocks.append({"header": text[start:text.index("{", start)].strip(), "text": text[start:end], "start": start, "end": end})
                start = None
        i += 1
    return blocks


def _inner(block_text: str) -> str:
    return block_text[block_text.index("{") + 1:block_text.rindex("}")]


def _without_comments(text: str) -> str:
    """Remove DTS comments without interpreting comment markers inside quoted strings."""
    out, i = [], 0
    while i < len(text):
        if text.startswith(("/*", "//"), i):
            end = _skip_non_code(text, i)
            out.extend("\n" if char == "\n" else " " for char in text[i:end])
            i = end
        elif text[i] == '"':
            end = _skip_non_code(text, i)
            out.append(text[i:end])
            i = end
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


# ------------------------------------------------------------------------------------------------------------ pin nodes
_NODE_HDR = re.compile(r"^\s*([A-Za-z_]\w*)\s*:\s*([A-Za-z_][\w,.\-]*)\s*\{")


def parse_pin_nodes(tlmm_inner: str):
    """-> (nodes, unparsed). node = {label, name, layout, mux_pins, function, cfg_pins, props[]}. `unparsed` = raw texts of anything that is not a
    plain pin state (kept as-is by the caller). Works for mux/config, pinmux/pinconf, and flat nodes, in any indentation."""
    nodes, unparsed = [], []
    for b in top_level_blocks(tlmm_inner):
        m = _NODE_HDR.match(b["text"])
        if not m:
            unparsed.append(b["text"])
            continue
        body = _inner(b["text"])
        kids = top_level_blocks(body)
        by_name = {k["header"].strip(): k for k in kids}
        def props_of(txt, keep_comments=False):
            clean = _without_comments(txt)
            matches = list(re.finditer(r"\b(pins|function)\s*=\s*(.*?);", clean, re.S))
            pins = next((match.group(2).strip() for match in matches if match.group(1) == "pins"), None)
            fn = next((match.group(2).strip() for match in matches if match.group(1) == "function"), None)
            residual = list(clean)
            for match in matches:
                for index in range(match.start(), match.end()):
                    if residual[index] != "\n":
                        residual[index] = " "
            props = [statement.strip() + ";" for statement in "".join(residual).split(";") if statement.strip()]
            # Comments are part of the output (a NEEDS REVIEW marker is an open decision): keep them, in their original place among the
            # properties. Their text never counts for the meaning check (see `signature`).
            marks, i = [], 0
            while keep_comments and i < len(txt):
                if txt.startswith(("/*", "//"), i):
                    end = _skip_non_code(txt, i)
                    before = re.sub(r"\b(pins|function)\s*=\s*(.*?);", "", _without_comments(txt[:i]), flags=re.S)
                    marks.append((len([x for x in before.split(";") if x.strip()]), txt[i:end].strip()))
                    i = end
                elif txt[i] == '"':
                    i = _skip_non_code(txt, i)
                else:
                    i += 1
            for n_prop, comment in reversed(marks):
                props.insert(min(n_prop, len(props)), comment)
            return pins, fn, props
        if "mux" in by_name and "config" in by_name and len(kids) == 2:
            mpins, mfn, mprops = props_of(_inner(by_name["mux"]["text"]))
            cpins, cfn, cprops = props_of(_inner(by_name["config"]["text"]), True)
            if mpins and mfn and cpins and not mprops and cfn is None:
                nodes.append({"label": m.group(1), "name": m.group(2), "layout": "mux_config", "mux_pins": mpins, "function": mfn,
                              "cfg_pins": cpins, "props": cprops, "lead": ""})
                continue
        elif "pinmux" in by_name and "pinconf" in by_name and len(kids) == 2:
            mpins, mfn, mprops = props_of(_inner(by_name["pinmux"]["text"]))
            cpins, cfn, cprops = props_of(_inner(by_name["pinconf"]["text"]), True)
            if mpins and mfn and cpins and not mprops and cfn is None:
                nodes.append({"label": m.group(1), "name": m.group(2), "layout": "pinmux_pinconf", "mux_pins": mpins, "function": mfn,
                              "cfg_pins": cpins, "props": cprops, "lead": ""})
                continue
        elif not kids:
            pins, fn, props = props_of(body, True)
            if pins and fn:
                nodes.append({"label": m.group(1), "name": m.group(2), "layout": "flat", "mux_pins": pins, "function": fn,
                              "cfg_pins": pins, "props": props, "lead": ""})
                continue
        unparsed.append(b["text"])
    return nodes, unparsed


def _pinmux_regions(text: str) -> list[str]:
    """Find TLMM bodies, including a TLMM controller declared inside a SoC node."""
    regions = []

    def walk(container):
        for block in top_level_blocks(container):
            header = block["header"]
            if header.startswith("&tlmm"):
                regions.append(_inner(block["text"]))
                continue
            if re.search(r"\btlmm\s*:|(?:^|\s)pinctrl(?:@|\s*\{)", header):
                regions.append(_inner(block["text"]))
                continue
            if "{" in block["text"]:
                walk(_inner(block["text"]))

    walk(_norm(text))
    return regions


def _scan_pin_states(container: str):
    """Recursively collect recognized pin-state leaves and count unsupported state-shaped leaves."""
    states, unrecognized, inside_blanks, between_blanks = [], [], [], []

    def walk(body):
        nonlocal unrecognized
        blocks = top_level_blocks(body)
        direct_states = []
        for block in blocks:
            parsed, _ = parse_pin_nodes(block["text"])
            if len(parsed) == 1:
                state = parsed[0]
                states.append((state, block))
                direct_states.append(block)
                config_name = "config" if state["layout"] == "mux_config" else "pinconf"
                inside_blanks.append(bool(re.search(
                    rf"\}};[ \t]*\n[ \t]*\n[ \t]*{config_name}\s*\{{", block["text"])))
                continue

            children = top_level_blocks(_inner(block["text"])) if "{" in block["text"] else []
            if children:
                header = _NODE_HDR.match(block["text"])
                child_names = {child["header"].strip() for child in children}
                if header and child_names.intersection({"mux", "config", "pinmux", "pinconf"}):
                    unrecognized.append(block["header"])
                else:
                    walk(_inner(block["text"]))
            elif re.search(r"\b(?:pins|groups|function)\s*=", _inner(block["text"])):
                unrecognized.append(block["header"])

        if len(direct_states) > 1:
            gaps = [body[direct_states[i]["end"]:direct_states[i + 1]["start"]]
                    for i in range(len(direct_states) - 1)]
            between_blanks.extend(bool(re.search(r"\n[ \t]*\n", gap)) for gap in gaps)

    walk(container)
    return states, unrecognized, inside_blanks, between_blanks


def signature(nodes) -> dict:
    """What a node MEANS, independent of layout and formatting: label -> (pins, function, config properties without comments)."""
    out = {}
    for n in nodes:
        props = tuple(sorted(re.sub(r"\s+", " ", p) for p in n["props"] if not p.startswith(("/*", "//"))))
        out[n["label"]] = (re.sub(r"\s+", "", n["mux_pins"]), n["function"].strip(), re.sub(r"\s+", "", n["cfg_pins"]), props)
    return out


def render_pin_nodes(nodes, style: Style, wrapper: str = "&tlmm") -> str:
    U, sep = style.indent, style.label_sep
    parts = []
    for n in nodes:
        name = n["name"] if style.name_style == "same" else n["label"].replace("_", "-") + "-state"
        L = [f"{U}{n['label']}{sep}{name} {{"]
        if style.layout in ("mux_config", "pinmux_pinconf"):
            mux_name, config_name = ("pinmux", "pinconf") if style.layout == "pinmux_pinconf" else ("mux", "config")
            L += [f"{U}{U}{mux_name} {{", f"{U}{U}{U}pins = {n['mux_pins']};", f"{U}{U}{U}function = {n['function']};", f"{U}{U}}};"]
            if style.blank_in_node:
                L.append("")
            L += [f"{U}{U}{config_name} {{", f"{U}{U}{U}pins = {n['cfg_pins']};"] + [f"{U}{U}{U}{p}" for p in n["props"]] + [f"{U}{U}}};"]
        else:
            L += [f"{U}{U}pins = {n['mux_pins']};", f"{U}{U}function = {n['function']};"] + [f"{U}{U}{p}" for p in n["props"]]
        L.append(f"{U}}};")
        parts.append("\n".join(L))
    return f"{wrapper} {{\n" + ("\n\n" if style.blank_between_nodes else "\n").join(parts) + "\n};"


def restyle_tlmm(block_text: str, style: Style):
    """-> (text, note). Re-renders a `&tlmm { ... }` block in `style`, or returns it unchanged with the reason when it cannot be done safely."""
    wrapper = block_text[:block_text.index("{")].strip()
    nodes, unparsed = parse_pin_nodes(_inner(block_text))
    if not nodes:
        return block_text, "no pin states to restyle"
    out = render_pin_nodes(nodes, style, wrapper)
    # the proof: parse the result back; it must mean exactly what the verified text meant
    back, back_unparsed = parse_pin_nodes(_inner(out))
    if signature(back) != signature(nodes) or len(back) != len(nodes):
        return block_text, "restyling was refused: the restyled nodes did not match the verified output exactly, so this block keeps the default format"
    if unparsed:                                   # something that is not a plain pin state: keep it, indented to the style
        out = out[:out.rindex("};")].rstrip("\n") + "\n\n" + "\n\n".join(convert_indent(u, style) for u in unparsed) + "\n};"
        return out, f"{len(unparsed)} block(s) that are not plain pin states were kept as generated"
    return out, ""


# ------------------------------------------------------------------------------------------------------------ other text
def convert_indent(text: str, style: Style) -> str:
    """Leading tabs -> the style's indentation, and `label: node {` -> the style's label separator. Nothing else changes."""
    out = []
    for line in text.split("\n"):
        m = re.match(r"^(\t*)(.*)$", line)
        body = m.group(2)
        if style.label_sep != ": ":
            body = re.sub(r"^([A-Za-z_]\w*)\s*:\s*([A-Za-z_][\w@,.\-]*\s*\{)", lambda x: f"{x.group(1)}{style.label_sep}{x.group(2)}", body)
        out.append(style.indent * len(m.group(1)) + body)
    return "\n".join(out)


# ------------------------------------------------------------------------------------------------------------ learning
def classify_role(text: str) -> str:
    t = _norm(text)
    regions = _pinmux_regions(t)
    if any((scan := _scan_pin_states(region))[0] or scan[1] for region in regions):
        return "pinmux"
    if re.search(r"/dts-v1/|/plugin/|__overlay__|regulator-fixed|^&usb\w*\s*\{", t, re.M):
        return "overlay"
    return "unknown"


def learn_style(text: str, source_name: str, role: str | None = None) -> Style:
    raw = text or ""
    t = _norm(raw)
    st = Style(role=role or classify_role(t), source=f"learned from the template '{source_name}'", learned=True)
    st.eol = "\r\n" if raw.count("\r\n") > max(1, raw.count("\n")) // 2 else "\n"
    # header: the leading comment block
    h = t.lstrip("\n")
    if h.startswith("/*") and "*/" in h:
        st.header = h[:h.index("*/") + 2].strip("\n")
    # preamble: generic only; project-specific quoted includes are reported, never copied
    for line in t.split("\n"):
        s = line.strip()
        if re.match(r"^(/dts-v1/;|/plugin/;)\s*$", s) and s not in st.preamble:
            st.preamble.append(s)
        elif re.match(r"^#include\s*[<\"]", s) and s not in st.skipped_includes:      # SoC- / project-specific: listed for a person, never copied
            st.skipped_includes.append(s)
    # indentation (first indented line of code) and label separator (first `label: node {`)
    for line in t.split("\n"):
        m = re.match(r"^([ \t]+)\S", line)
        if m and not line.strip().startswith(("*", "/*", "//")):
            st.indent = "\t" if m.group(1).startswith("\t") else " " * len(m.group(1))
            break
    for line in t.split("\n"):
        m = re.match(r"^\s*([A-Za-z_]\w*)(\s*:\s*)([A-Za-z_][\w@,.\-]*)\s*\{", line)
        if m:
            st.label_sep = ": " if re.search(r":\s+", m.group(2)) else ":"
            break
    # Learn from every recognized leaf state, even when a pin-group wraps it.
    scanned = [_scan_pin_states(region) for region in _pinmux_regions(t)]
    states = [item for result in scanned for item in result[0]]
    st.recognized_pin_states = len(states)
    st.unrecognized_pin_state_headers = [header for result in scanned for header in result[1]]
    st.unrecognized_pin_states = len(st.unrecognized_pin_state_headers)
    if states:
        nodes = [item[0] for item in states]
        st.layout = nodes[0]["layout"]
        first = nodes[0]
        if first["name"] != first["label"] and first["name"].endswith("-state"):
            st.name_style = "hyphen_state"
        inside = [value for result in scanned for value in result[2]]
        if inside:
            st.blank_in_node = sum(inside) * 2 > len(inside)
            if 30 <= 100 * sum(inside) // len(inside) <= 70:
                st.mixed.append(f"a blank line inside a node (the template does it in {100 * sum(inside) // len(inside)}% of its nodes)")
        between = [value for result in scanned for value in result[3]]
        if between:
            st.blank_between_nodes = sum(between) * 2 > len(between)
            if 30 <= 100 * sum(between) // len(between) <= 70:
                st.mixed.append(f"a blank line between nodes ({100 * sum(between) // len(between)}% of the gaps)")
    return st


# ------------------------------------------------------------------------------------------------------------ the bundle
def zip_bytes(files: list) -> bytes:
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.writestr(f["name"], f["text"])
    return buf.getvalue()


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").lower() or "project"


def build_bundle(final_text: str, project: str, styles: dict, generator: str = "usb_dts_tool", peripheral: str = "usb") -> dict:
    """final_text: the verified output (pin states, consumers, regulators). styles: {'pinmux': Style, 'overlay': Style}.
    -> {files: [{name, role, text, style_source, style_summary, note}], notes: [...]}"""
    default_pm, default_ov = Style(role="pinmux"), Style(role="overlay")
    spm, sov = styles.get("pinmux", default_pm), styles.get("overlay", default_ov)
    t = _norm(final_text)
    blocks = top_level_blocks(t)
    tlmm = [b for b in blocks if b["header"].startswith("&tlmm")]
    other = [b for b in blocks if not b["header"].startswith("&tlmm")]
    files, notes = [], []

    def make(role, style, body, notice_lines):
        head = []
        if style.header:
            head.append(style.header)
        pre = [p for p in style.preamble if role == "overlay"]       # /dts-v1/ and #include <...> belong to the overlay, not the pinmux dtsi
        if pre:
            head.append("\n".join(pre))
        head.append("/*\n" + "\n".join(f" * {x}" for x in notice_lines) + "\n */")
        text = "\n\n".join(head) + "\n\n" + body.strip("\n") + "\n"
        return text.replace("\n", style.eol)

    if tlmm:
        parts, why = [], []
        for b in tlmm:
            txt, note = restyle_tlmm(b["text"], spm)
            parts.append(txt)
            if note:
                why.append(note)
        refused = any("refused" in w for w in why)
        shown = spm if not refused else default_pm
        notes += [f"pinmux: {w}" for w in why]
        files.append({"name": f"{_slug(project)}_{peripheral}_pinmux.dtsi", "role": "pinmux",
                      "text": make("pinmux", shown, "\n\n".join(parts if not refused else [b["text"] for b in tlmm]),
                                   [f"{peripheral.upper()} pin states for {project} - generated by {generator} from the PinMux workbook.",
                                    "Every NEEDS REVIEW comment is an open decision; nothing marked so is verified.",
                                    f"Format: {shown.source}."]),
                      "style_source": shown.source, "style_summary": shown.summary(), "note": "; ".join(why)})
    if other:
        # the accepted change lists the regulators (root `/ { }` nodes) first, then the &usbN nodes that use them
        other = sorted(other, key=lambda b: 0 if b["header"].strip().startswith("/") else 1)
        body = "\n\n".join(convert_indent(b["text"], sov) for b in other)
        files.append({"name": f"{_slug(project)}_{peripheral}_overlay.dts", "role": "overlay",
                      "text": make("overlay", sov, body,
                                   [f"{peripheral.upper()} {'controller node' if peripheral != 'usb' else 'consumers / regulators'} for {project} - generated by {generator} from the PinMux workbook.",
                                    "Every NEEDS REVIEW comment is an open decision; nothing marked so is verified.",
                                    f"Format: {sov.source}."]),
                      "style_source": sov.source, "style_summary": sov.summary(), "note": ""})
    else:
        notes.append("no overlay file: this output has no consumer or regulator blocks (the wiring style is unconfirmed - choose a pattern to get them)")
    return {"files": files, "notes": notes}
