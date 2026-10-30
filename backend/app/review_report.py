"""
Builds a STANDALONE review report as real HTML (an actual <table>, not
markdown pipe-syntax) - separate from the DTS file itself, listing every
"NEEDS REVIEW" item and every UPDATED field in one place. Opens directly
in any browser with a double-click and renders as a genuine formatted
table with borders/shading - no markdown-aware viewer needed. Markdown
pipe-tables render as literal pipe characters in a plain text editor,
which is exactly what this replaces.

Covers both real workflows:
  - Fresh generation (generate_dts) - nodes where drive-strength/bias/
    pin identity couldn't be determined from the workbook.
  - The update workflow (update_dts_file) - every value that got
    changed (old -> new), every unmatched pin, every structural
    inconsistency (mux/config mismatch), and every AI-assisted advisory.

Pure formatting over data these modules already compute and expose
structurally (NodeUpdateResult, the review_entries list from
generate_dts) - no new logic/guessing happens here, only presentation.
"""

from __future__ import annotations
from html import escape as _e

_STYLE = """
<style>
  body { font-family: -apple-system, Segoe UI, Helvetica, Arial, sans-serif;
         max-width: 980px; margin: 24px auto; padding: 0 16px; color: #1a1a1a; }
  h1 { font-size: 22px; border-bottom: 2px solid #333; padding-bottom: 8px; }
  h2 { font-size: 18px; margin-top: 32px; color: #333; }
  h3 { font-size: 15px; margin-top: 20px; margin-bottom: 4px; }
  .summary { background: #f4f4f4; border-left: 4px solid #666; padding: 10px 14px;
             margin: 12px 0; font-size: 14px; }
  .explain { font-size: 13px; color: #555; font-style: italic; margin: 2px 0 8px; }
  table { border-collapse: collapse; width: 100%; margin: 8px 0 16px; font-size: 13px; }
  th, td { border: 1px solid #ccc; padding: 6px 10px; text-align: left; }
  th { background: #333; color: #fff; }
  tr:nth-child(even) { background: #f7f7f7; }
  code, .mono { font-family: Consolas, Menlo, monospace; background: #eee;
                padding: 1px 5px; border-radius: 3px; font-size: 12px; }
  .none-note { color: #888; }
</style>
"""


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{_e(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return f"<table><tr>{head}</tr>{body}</table>"


def _mono(text: str) -> str:
    return f'<span class="mono">{_e(str(text))}</span>'


def build_generation_review_report(project: str, soc: str,
                                    review_entries: list[dict],
                                    ambiguous_count: int) -> str:
    """review_entries: the list returned by generate_dts() - each a dict
    with node_name/pins/interface_value/missing_fields."""
    parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        f"<title>Review Report - {_e(project)}</title>", _STYLE, "</head><body>",
        f"<h1>Review Report &mdash; {_e(project)} ({_e(soc)})</h1>",
        "<p>Generated alongside a fresh DTS file. Lists every node where the "
        "workbook didn't have enough data to determine a value with confidence "
        "- these are the same items marked <code>NEEDS REVIEW</code> inline in "
        "the DTS file, collected here for easy scanning.</p>",
        f"<div class='summary'><strong>{len(review_entries)}</strong> node(s) need review. "
        f"<strong>{ambiguous_count}</strong> record(s) could not be classified as signal or "
        f"non-signal at all and were excluded entirely (not listed below - see the DTS "
        f"file's header comment).</div>",
    ]

    if not review_entries:
        parts.append("<p>Nothing to review - every node had complete data.</p>")
    else:
        rows = [
            [_mono(e["node_name"]), ", ".join(_mono(p) for p in e["pins"]),
             _e(e["interface_value"]), "; ".join(_e(m) for m in e["missing_fields"])]
            for e in review_entries
        ]
        parts.append(_table(["Node", "Pins", "Selected Interface", "Missing"], rows))

    parts.append("</body></html>")
    return "\n".join(parts)


_FIELD_EXPLANATIONS = {
    "function": "which peripheral block this pin is wired to (e.g. a UART/SPI/I2C QUP block, "
                "a TDM audio slot, or a plain GPIO) - this is the pin's overall role",
    "drive-strength": "output current strength in milliamps - how hard the pin can drive a "
                       "signal, not which signal it carries",
    "bias": "the pin's internal pull resistor state (pulled up, pulled down, or disabled) "
            "when nothing else is actively driving it",
}
_FIELD_LABELS = {"function": "Function changes", "drive-strength": "Drive-strength changes", "bias": "Bias changes"}


def build_update_review_report(project: str, file_results: list[dict]) -> str:
    """file_results: one dict per updated file, each with
    output_filename and node_results (list of dicts matching
    NodeUpdateOut's shape: label/changed/changes/unmatched_pins/
    structural_issue/ai_assisted/ai_advisory)."""
    total_changed = sum(1 for f in file_results for nr in f["node_results"] if nr["changed"])
    total_unmatched = sum(1 for f in file_results for nr in f["node_results"] if nr["unmatched_pins"])
    total_structural = sum(1 for f in file_results for nr in f["node_results"] if nr["structural_issue"])
    total_ai = sum(1 for f in file_results for nr in f["node_results"] if nr["ai_assisted"])
    review_note_rows = [
        (f["output_filename"], nr["label"], nr["review_note"])
        for f in file_results for nr in f["node_results"] if nr.get("review_note")
    ]
    ai_candidate_total = sum(int(f.get("ai_candidate_count", 0)) for f in file_results)
    ai_failure_total = sum(int(f.get("ai_extraction_failure_count", 0)) for f in file_results)

    parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        f"<title>Review Report - {_e(project)} Update</title>", _STYLE, "</head><body>",
        f"<h1>Review Report &mdash; {_e(project)} DTS Update</h1>",
        f"<div class='summary'><strong>{total_changed}</strong> node(s) updated &middot; "
        f"<strong>{total_unmatched}</strong> node(s) with unmatched pins &middot; "
        f"<strong>{total_structural}</strong> structural issue(s) &middot; "
        f"<strong>{total_ai}</strong> AI-assisted node(s) &middot; "
        f"<strong>{len(review_note_rows)}</strong> manual review note(s) &middot; "
        f"<strong>{ai_candidate_total}</strong> unrecognized node(s) considered for AI &middot; "
        f"<strong>{ai_failure_total}</strong> without a usable AI response, across {len(file_results)} file(s).</div>",
        "<p>Excel is the source of truth for deterministic updates to recognized nodes. "
        "AI-assisted findings for unrecognized nodes are read-only advisories and never rewrite "
        "those nodes. The sections below distinguish applied changes from items left for review.</p>",
    ]

    for f in file_results:
        parts.append(f"<h2>{_e(f['output_filename'])}</h2>")
        if f.get("ai_fallback_status") and f.get("ai_candidate_count", 0):
            parts.append(f"<p><strong>AI format fallback:</strong> {_e(f['ai_fallback_status'])}</p>")

        all_changes = [
            (nr["label"], c["pin"], c["field"], c["old_value"], c["new_value"])
            for nr in f["node_results"] for c in nr["changes"]
        ]

        if all_changes:
            summary_items = []
            for field_key in ("function", "bias", "drive-strength"):
                count = sum(1 for _, _, fld, _, _ in all_changes if fld == field_key)
                if count:
                    summary_items.append(f"<li><strong>{count}</strong> {_FIELD_LABELS[field_key].lower()}</li>")
            parts.append(f"<h3>Summary</h3><ul>{''.join(summary_items)}</ul>")

        for field_key in ("function", "bias", "drive-strength"):
            rows = [(n, p, o, nv) for n, p, fld, o, nv in all_changes if fld == field_key]
            if not rows:
                continue
            parts.append(f"<h3>{_FIELD_LABELS[field_key]} ({len(rows)})</h3>")
            parts.append(f"<div class='explain'>{_e(_FIELD_EXPLANATIONS[field_key])}</div>")

            never_set = [(n, p, nv) for n, p, o, nv in rows if o in (None, "None")]
            reassigned = [(n, p, o, nv) for n, p, o, nv in rows if o not in (None, "None")]

            if never_set:
                parts.append(f"<p><strong>Was never set before ({len(never_set)}):</strong></p>")
                table_rows = [[_mono(n), _mono(p), _mono(nv)] for n, p, nv in never_set]
                parts.append(_table(["Node", "Pin", "Assigned"], table_rows))

            if reassigned:
                parts.append(f"<p><strong>Changed from a different value ({len(reassigned)}):</strong></p>")
                table_rows = [[_mono(n), _mono(p), _mono(o), _mono(nv)] for n, p, o, nv in reassigned]
                parts.append(_table(["Node", "Pin", "Old", "New"], table_rows))

        unmatched_rows = [(nr["label"], nr["unmatched_pins"]) for nr in f["node_results"] if nr["unmatched_pins"]]
        if unmatched_rows:
            parts.append("<h3>Pins not found in Excel (left unmodified)</h3>")
            table_rows = [[_mono(n), ", ".join(_mono(p) for p in pins)] for n, pins in unmatched_rows]
            parts.append(_table(["Node", "Pins"], table_rows))

        structural_rows = [(nr["label"], nr["structural_issue"]) for nr in f["node_results"] if nr["structural_issue"]]
        if structural_rows:
            parts.append("<h3>Structural issues (left unmodified)</h3><ul>")
            for node, issue in structural_rows:
                parts.append(f"<li>{_mono(node)}: {_e(issue)}</li>")
            parts.append("</ul>")

        file_review_rows = [(label, note) for filename, label, note in review_note_rows
                            if filename == f["output_filename"]]
        if file_review_rows:
            parts.append("<h3>Manual review notes (values left unchanged)</h3><ul>")
            for node, note in file_review_rows:
                parts.append(f"<li>{_mono(node)}: {_e(note)}</li>")
            parts.append("</ul>")

        ai_rows = [(nr["label"], nr["ai_advisory"]) for nr in f["node_results"] if nr["ai_assisted"] and nr["ai_advisory"]]
        if ai_rows:
            parts.append("<h3>AI-assisted advisories (node text never rewritten)</h3><ul>")
            for node, advisory in ai_rows:
                parts.append(f"<li>{_mono(node)}: {_e(advisory)}</li>")
            parts.append("</ul>")

        if not (all_changes or unmatched_rows or structural_rows or file_review_rows or ai_rows
            or f.get("ai_candidate_count", 0)):
            parts.append("<p>No changes, unmatched pins, or issues in this file.</p>")

    parts.append("</body></html>")
    return "\n".join(parts)
