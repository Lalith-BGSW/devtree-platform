#!/usr/bin/env python3
"""
Finds out HOW a workbook writes Ethernet, before anything is generated. Read-only; changes nothing.

  python eth_discover.py --excel board.xlsx                    (every project)
  python eth_discover.py --excel board.xlsx --project "Iveco CDC B1" --out eth_discovery.md

For each project it reports:
  1. the SoC and its pin table status;
  2. every column that contains an Ethernet word (RGMII, RMII, MDIO, MDC, EMAC, SGMII, ETH, PHY ...), with how many rows, how many
     DIFFERENT values, and sample values - and whether the column looks like a list of possibilities (several families in one cell);
  3. for each row with an Ethernet word anywhere: the pin, and the cells that matter (selected function, signal, interface group, mux
     register value, direction, pull, drive) so the structure is visible;
  4. a verdict per row: what the current rules would do with it and why.
Send the .md file back (or read it yourself): it shows which column holds the real selected function in THIS workbook.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import usb_dts_tool as _u
from app import eth_generator as eg

KEY_COLS = ("selected_interface", "pinmuxed_name", "gen5_signal_name", "interface_group", "direction", "io_gpio_pull", "io_drv_strength", "resetvalue")


def _cell(v, n=90):
    return re.sub(r"\s+", " ", str(v or "")).strip().replace("|", "/")[:n]


def discover_project(excel, project) -> str:
    parsed, records, soc = _u.load_records(excel, project)
    names = {c.slug: (c.base_field or c.slug) for c in parsed.columns if c.slug}
    soc_name, _prof = eg.soc_profile(soc)
    L = [f"## Project `{project}` - SoC `{soc}`", "", f"{len(records)} rows. Verified Ethernet pin table: {soc_name or 'none'}.", ""]

    col = {}                                   # slug -> {rows, values:set, multi:int, samples:[]}
    rows = []
    for r in records:
        pin, g = eg._gpio_of(r)
        row_hits = []
        for k, v in r.fields.items():
            t = _cell(v, 400)
            if not t or len(t) > 400:
                continue
            fams = {f for f in eg.ORDER if eg._fam_match(f, t)}
            if not fams:
                continue
            d = col.setdefault(k, {"rows": 0, "values": set(), "multi": 0, "samples": []})
            d["rows"] += 1
            d["values"].add(t[:60])
            if len(fams) > 1 or t.count(",") >= 2:
                d["multi"] += 1
            if len(d["samples"]) < 3:
                d["samples"].append(f"{pin or g or '-'}: {t[:60]}")
            row_hits.append(k)
        if row_hits:
            rows.append((r, pin, g, row_hits))

    L += ["### Columns that contain an Ethernet word", ""]
    if not col:
        L += ["None. No cell in this project contains RGMII / RMII / MDIO / MDC / EMAC / SGMII / ETH / PHY-interrupt wording.", ""]
    else:
        L += ["| Column (slug) | Header in the workbook | Rows | Different values | Looks like a capability list | Samples |", "|---|---|---|---|---|---|"]
        for k, d in sorted(col.items(), key=lambda kv: -kv[1]["rows"]):
            cap = "YES" if d["multi"] * 2 >= d["rows"] else ("some" if d["multi"] else "no")
            L.append(f"| `{k}` | {_cell(names.get(k, ''), 50)} | {d['rows']} | {len(d['values'])} | {cap} | {'; '.join(_cell(s, 70) for s in d['samples'])} |")
        L.append("")

    L += ["### Rows with Ethernet wording and what the current rules do with each", ""]
    if not rows:
        L += ["No rows.", ""]
    else:
        enabled = set(eg.ORDER)
        cl = eg.classify([x[0] for x in rows], enabled)
        why = {}
        for r, fam in cl["selected"]:
            why[id(r)] = f"SELECTED as {fam.upper()} (Selected Interface says so) -> " + ("pin state generated" if soc_name or fam in ("rgmii", "rmii") else "chosen, but NOT generated: no pin table for this SoC gives the function name")
        for r in [x[0] for x in rows]:
            why.setdefault(id(r), "Selected Interface is not Ethernet -> not used (the Ethernet word is only a possibility, a name or a note)")
        L += ["| Pin | Cells that matter | Ethernet word found in | Verdict |", "|---|---|---|---|"]
        for r, pin, g, hits in rows[:60]:
            key = "; ".join(f"{k}={_cell(r.fields.get(k), 40)}" for k in KEY_COLS if r.fields.get(k))
            mux = eg._mux_value(r.fields, g)
            key += f"; mux={mux}" if mux is not None else ""
            L.append(f"| {pin or '(no TLMM pin)'} | {key} | {', '.join(hits)} | {why.get(id(r), 'not classified')} |")
        if len(rows) > 60:
            L.append(f"\n... and {len(rows) - 60} more rows.")
        L.append("")
    L += ["### Every column of this project (so the right one can be named)", "",
          ", ".join(f"`{k}` ({_cell(n, 30)})" for k, n in names.items()), ""]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Show how a workbook writes Ethernet (read-only).")
    ap.add_argument("--excel", required=True)
    ap.add_argument("--project")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    projects = [a.project] if a.project else _u.list_projects(a.excel)
    text = "# Ethernet discovery\n\n" + "\n\n".join(discover_project(a.excel, p) for p in projects) + "\n"
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"written: {a.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
