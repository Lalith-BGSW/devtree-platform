"""
ONE combined DTS for every peripheral the platform generates (USB, Ethernet, SPI): a single pin-state file with one `&tlmm { }` and a single
consumer file with every controller node, each peripheral in its own commented section.

Nothing is regenerated here. Each engine produces and verifies its own files first; this module only MERGES them and then checks what no single
engine can see: the same GPIO claimed by two peripherals, the same node label defined twice, the same controller node written twice, and every
`pinctrl-N` reference resolving to a state that exists in the combined text. A conflict is reported, never resolved silently.
"""
from __future__ import annotations

import re

from app import dts_style

SECTION = "/* ======================= {name} ======================= */"
_COMMENT = re.compile(r"/\*.*?\*/", re.S)


def _split_head(text: str):
    """-> (head, blocks). head = everything before the first top-level block WITHOUT the generator's own notice comment (the last comment)."""
    t = dts_style._norm(text)
    blocks = dts_style.top_level_blocks(t)
    if not blocks:
        return t, []
    head = t[:blocks[0]["start"]]
    cs = list(_COMMENT.finditer(head))
    if cs:
        head = head[:cs[-1].start()] + head[cs[-1].end():]
    return head.strip("\n"), blocks


def _strip(s: str) -> str:
    return s.strip("\n")


def merge(parts: list[dict], project: str, generator: str = "combined_dts_tool") -> dict:
    """parts: [{"peripheral": "usb", "files": [{"name","role","text"}, ...]}] -> {files, conflicts, sections, notes}."""
    pm, ov = [], []                       # (peripheral, file text)
    for p in parts:
        for f in p.get("files") or []:
            (pm if f.get("role") == "pinmux" else ov).append((p["peripheral"], f["text"]))
    conflicts, notes, sections = [], [], []

    # ---- pinmux
    tlmm_inner, head_pm, owner_label, owner_gpio = [], "", {}, {}
    n_nodes = 0
    for per, text in pm:
        head, blocks = _split_head(text)
        head_pm = head_pm or head
        for b in [x for x in blocks if x["header"].startswith("&tlmm")]:
            inner = dts_style._inner(b["text"])
            nodes, _un = dts_style.parse_pin_nodes(inner)
            for n in nodes:
                n_nodes += 1
                if n["label"] in owner_label and owner_label[n["label"]] != per:
                    conflicts.append(f"the node label \"{n['label']}\" is defined by both {owner_label[n['label']].upper()} and {per.upper()}")
                owner_label.setdefault(n["label"], per)
                for g in set(re.findall(r"gpio\d+", n["mux_pins"])):
                    if g in owner_gpio and owner_gpio[g][0] != per:
                        conflicts.append(f"{g} is claimed by {owner_gpio[g][0].upper()} ({owner_gpio[g][1]}) and by {per.upper()} ({n['label']}) - one pin cannot serve both")
                    owner_gpio.setdefault(g, (per, n["label"]))
            tlmm_inner.append((per, _strip(inner)))
    pins_by = {}
    for g, (per, _l) in owner_gpio.items():
        pins_by[per] = pins_by.get(per, 0) + 1
    files = []
    if tlmm_inner:
        body = "&tlmm {\n" + "\n\n".join(f"\t{SECTION.format(name=per.upper() + ' pin states')}\n\n{inner}" for per, inner in tlmm_inner) + "\n};"
        names = ", ".join(sorted({per.upper() for per, _ in tlmm_inner}))
        notice = f"/*\n * Combined pin states for {project} ({names}) - merged by {generator} from the per-peripheral engines.\n * Every NEEDS REVIEW comment is an open decision; nothing marked so is verified.\n */"
        files.append({"name": f"{_slug(project)}_all_pinmux.dtsi", "role": "pinmux", "text": ((head_pm + "\n\n") if head_pm else "") + notice + "\n\n" + body + "\n"})

    # ---- overlay
    head_ov, chunks, seen_hdr = "", [], {}
    for per, text in ov:
        head, blocks = _split_head(text)
        head_ov = head_ov or head
        keep = []
        for b in blocks:
            h = re.sub(r"\s+", " ", b["header"]).strip()
            body_ = re.sub(r"\s+", " ", b["text"][b["text"].find("{") + 1:].rstrip("; }")).strip()
            if h in seen_hdr and seen_hdr[h][0] != per and h.startswith("&"):
                if body_ == seen_hdr[h][1] and body_ == 'status = "okay";':
                    continue                  # the same QUP wrapper enabled by two peripherals (SPI and UART): identical, written once
                conflicts.append(f"the node {h.split(' ')[0]} is written by both {seen_hdr[h][0].upper()} and {per.upper()} - merge them by hand")
            seen_hdr.setdefault(h, (per, body_))
            keep.append(b["text"])
        chunks.append((per, "\n\n".join(keep)))
    if chunks:
        names = ", ".join(sorted({per.upper() for per, _ in chunks}))
        notice = f"/*\n * Combined consumer nodes for {project} ({names}) - merged by {generator} from the per-peripheral engines.\n * Every NEEDS REVIEW comment is an open decision; nothing marked so is verified.\n */"
        body = "\n\n".join(f"{SECTION.format(name=per.upper())}\n\n{c}" for per, c in chunks)
        files.append({"name": f"{_slug(project)}_all_overlay.dts", "role": "overlay", "text": ((head_ov + "\n\n") if head_ov else "") + notice + "\n\n" + body + "\n"})

    # ---- check the MERGED text again, as a reader would
    allt = "\n".join(f["text"] for f in files)
    t = dts_style._norm(allt)
    labels = set()
    for b in dts_style.top_level_blocks(t):
        if b["header"].startswith("&tlmm"):
            labels |= {n["label"] for n in dts_style.parse_pin_nodes(dts_style._inner(b["text"]))[0]}
    refs = []
    for m in re.finditer(r"pinctrl-\d+\s*=\s*<([^>]*)>", t):
        refs += re.findall(r"&([A-Za-z_]\w*)", m.group(1))
    missing = sorted({r for r in refs if r not in labels})
    for r in missing:
        conflicts.append(f"a pinctrl reference points at &{r}, which no pin-state file defines")
    if len(labels) != len(set(owner_label)):
        conflicts.append(f"the combined text has {len(labels)} pin states but the engines produced {len(set(owner_label))}")
    unused = sorted(labels - set(refs))
    if unused:
        notes.append(f"{len(unused)} pin state(s) are defined but no pinctrl references them (for example board-wired control lines): " + ", ".join(unused[:8]) + (" ..." if len(unused) > 8 else ""))
    sections = [{"peripheral": per, "pin_states": pins_by.get(per, 0)} for per, _ in tlmm_inner]
    return {"files": files, "conflicts": conflicts, "sections": sections, "notes": notes, "pin_states": len(labels)}


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").lower() or "project"
