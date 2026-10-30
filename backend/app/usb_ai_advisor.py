"""
AI advisory for the USB pins the deterministic tool deliberately did NOT generate (shared pins, boot-strap
pins, unsupported / unconfirmed pin controllers).

Division of labour - this is the whole point of the design:
  * Python decides everything that ends up in a DTS file. Nothing here can change it.
  * The AI only EXPLAINS a flagged pin from evidence the workbook already contains (signal names, the
    pin's mux options, the imported schematic net trace, remarks) and lists questions for the hardware
    owner. Its answer is untrusted input and is checked by deterministic code before it is shown:
      - every quoted piece of evidence must literally occur in the evidence that was sent;
      - any specific the AI adds that is not in that evidence (an I2C address, a "vendor,model"
        compatible string, output-high/low, a GPIO flag, a "reg =") is removed and counted.
    That guard exists because an AI draft earlier in this project invented `microchip,usb2514` and
    `reg = <0x2d>` - values that compile fine and are wrong.
  * The result is a clearly labelled report section. It is never merged into generated output.
"""

from __future__ import annotations

import json
import re

from app import llm_client
from app.models import NormalizedRecord

SITUATIONS = ("shared_i2c_bus", "shared_gpio_net", "boot_strap_only", "mounting_variant",
              "unsupported_controller", "unclear")

SYSTEM_PROMPT = """You help a hardware/BSP engineer understand ONE pin of an SoC pin-mux workbook that an automated tool refused to turn into device-tree output.

You are given an EVIDENCE object copied from the workbook. Rules:
1. Use ONLY the evidence. If something is not in it, say it is unknown.
2. NEVER state or guess: I2C addresses, compatible strings, register values, output levels (high/low), GPIO polarity, or which hardware variant applies - unless that exact text appears in the evidence.
3. Do not write device-tree code.
4. Decide which situation best fits: shared_i2c_bus (several devices on one I2C/SMBus), shared_gpio_net (one GPIO feeding several roles), boot_strap_only (sampled at reset), mounting_variant (alternative fitted options), unsupported_controller (pin controller other than Qualcomm TLMM), unclear.
5. Use the strongest evidence. If schematic_net_trace is present, say where the net goes (copy connector/component names exactly as written) and what that suggests; note when it names fewer signals than the signal_names list. If selected_interface and mux_options are present, say which function is selected and that a pin can do only one at a time. When such evidence exists, quote at least one piece of it in evidence_used.
6. open_questions: at most 2, each specific to THIS pin and answerable only from the schematic, the board's variant list or the hardware owner. Do NOT ask what the tool already states (why it was flagged, whether it belongs in the device tree) and do NOT ask generic questions (such as "what are the use cases" or "is there a priority"). Good questions by situation:
   - shared_i2c_bus: which of the listed devices are fitted on this board variant, and where each one's I2C address is documented.
   - shared_gpio_net: is this ONE net feeding all the listed roles, or are they alternative fitted options (resistor/mounting options)?
   - boot_strap_only: ask nothing unless the evidence shows a runtime function; then ask which one applies.
   - unsupported_controller: which pin-controller binding or reference DTS applies to this SoC.
7. Answer with a single JSON object and nothing else:
{"situation": "<one of the values above>",
 "summary": "<at most two sentences, plain language, only facts from the evidence>",
 "evidence_used": ["<short snippets copied VERBATIM from the evidence>"],
 "open_questions": ["<at most 2, per rule 6>"]}"""

_SPECIFIC = re.compile(
    r"0x[0-9a-fA-F]+|\b[a-z][a-z0-9]*,[a-z0-9][\w\-]*\b|output-(?:high|low)|\bGPIO_ACTIVE_(?:HIGH|LOW)\b|\breg\s*=",
    re.IGNORECASE)
_REMOVED = "[removed: contained a specific that is not in the workbook evidence]"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip().lower()


def build_evidence(rec: NormalizedRecord, ball: str, gen5_slug: str, interface_group_slug: str | None,
                   why: str, remark_text: str = "", signal_segments=None) -> dict:
    """Deterministically gather what the workbook says about this pin. Nothing is inferred here."""
    f = rec.fields
    ev: dict = {"pin": ball, "why_the_tool_flagged_it": why}
    segs = signal_segments(str(f.get(gen5_slug) or "")) if signal_segments else []
    if segs:
        ev["signal_names"] = segs
    if interface_group_slug and f.get(interface_group_slug):
        ev["interface_group"] = re.sub(r"\s+", " ", str(f.get(interface_group_slug))).strip()
    for key in ("selected_interface", "pinmuxed_name", "direction"):
        if f.get(key) not in (None, ""):
            ev[key] = re.sub(r"\s+", " ", str(f[key])).strip()
    options = []
    for n in range(0, 24):
        port, inst, reg = f.get(f"port{n}"), f.get(f"instance{n}"), f.get(f"regsetting{n}")
        if any(v not in (None, "") for v in (port, inst, reg)):
            options.append({k: re.sub(r"\s+", " ", str(v)).strip() for k, v in
                            (("port", port), ("instance", inst), ("register", reg)) if v not in (None, "")})
    if options:
        ev["mux_options"] = options
    # The net trace is the field that actually contains endpoints ("... ENDPNT"). Matching on the column name
    # alone picked an unrelated "schematic ..." column (its value was just "PinMux1"), so the AI was told the
    # net trace was "PinMux1" - found by the end-to-end test.
    trace = next((v for k, v in f.items() if "schematic" in k and isinstance(v, str) and "ENDPNT" in v), None)
    if trace is None:
        trace = next((v for v in f.values() if isinstance(v, str) and "ENDPNT" in v), None)
    if trace:
        ev["schematic_net_trace"] = re.sub(r"\s+", " ", trace).strip()[:600]
    if remark_text:
        ev["excel_remark"] = remark_text
    return ev


def _parse(raw: str) -> dict | None:
    raw = (raw or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    try:
        obj = json.loads(raw)
    except ValueError:
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except ValueError:
            return None
    return obj if isinstance(obj, dict) else None


def guard(answer: dict, evidence: dict) -> dict:
    """Deterministic check of the AI's answer against the evidence that was sent. Returns the cleaned
    answer plus how many statements were removed."""
    ev_text = _norm(json.dumps(evidence, sort_keys=True))
    removed = 0

    def unsupported(text: str) -> bool:
        return any(_norm(tok) not in ev_text for tok in _SPECIFIC.findall(text))

    situation = answer.get("situation") if answer.get("situation") in SITUATIONS else "unclear"
    summary = str(answer.get("summary") or "").strip()[:500]
    if not summary:
        summary = "(no summary)"
    elif unsupported(summary):
        summary, removed = _REMOVED, removed + 1
    quotes = []
    for q in (answer.get("evidence_used") or [])[:8]:
        q = str(q).strip()
        if q and _norm(q) in ev_text:
            quotes.append(q[:160])
        else:
            removed += 1
    questions = []
    for q in (answer.get("open_questions") or [])[:2]:
        q = str(q).strip()
        if not q:
            continue
        if unsupported(q):
            removed += 1
        else:
            questions.append(q[:240])
    return {"situation": situation, "summary": summary, "evidence_used": quotes,
            "open_questions": questions, "removed": removed}


def _cell(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).replace("|", "/").strip() or "-"


def advise(evidence_list: list[dict], max_pins: int = 12) -> list[str]:
    """Markdown lines for the report's AI section. Never raises: any failure becomes a visible note."""
    head = ["\n## AI advisory - explanation only, NOT used to generate or change anything\n"]
    if not evidence_list:
        return head + ["Nothing was flagged, so there was nothing to explain."]
    client = llm_client.get_client("usb_advice")
    if client is None:
        why = ("no LLM is configured (set LLM_API_KEY and LLM_DEPLOYMENT in backend/.env, then run "
               "`python -m app.llm_client check`)" if not llm_client.is_configured()
               else "the 'usb_advice' feature is not enabled in LLM_FEATURES")
        return head + [f"AI advisory was requested but is unavailable: {why}. The deterministic report above is complete without it."]

    before = dict(llm_client.usage_totals)
    rows, removed_total, error = [], 0, None
    todo = evidence_list[:max_pins]
    for ev in todo:
        try:
            resp = client.chat.completions.create(
                messages=[{"role": "system", "content": SYSTEM_PROMPT},
                          {"role": "user", "content": "EVIDENCE:\n" + json.dumps(ev, indent=1)}],
                temperature=0.0, max_tokens=600)
        except llm_client.LLMError as e:
            error = str(e)
            break
        parsed = _parse(resp.choices[0].message.content)
        if parsed is None:
            rows.append((ev["pin"], "unclear", "The AI did not return usable JSON for this pin.", [], [], 0))
            continue
        g = guard(parsed, ev)
        removed_total += g["removed"]
        rows.append((ev["pin"], g["situation"], g["summary"], g["evidence_used"], g["open_questions"], g["removed"]))

    lines = head + ["_AI-generated. Treat as a reading aid, not as a fact. Every quote below was checked to occur in "
                    "the workbook evidence; any specific the AI added that is not in that evidence was removed._\n"]
    if rows:
        lines.append("| Pin | Situation | Summary | Open questions for the hardware owner | Evidence quoted |")
        lines.append("|---|---|---|---|---|")
        for pin, sit, summ, quotes, qs, rem in rows:
            lines.append(f"| {_cell(pin)} | {sit} | {_cell(summ)} | {_cell(' / '.join(qs))} | {_cell(' / '.join(quotes))} |")
    if error:
        lines.append(f"\nAI advisory stopped early: {error}. The remaining pins were not explained; nothing else is affected.")
    if len(evidence_list) > max_pins:
        lines.append(f"\n{len(evidence_list) - max_pins} further flagged pin(s) were not sent (limit {max_pins}).")
    used = {k: llm_client.usage_totals[k] - before[k] for k in before}
    lines.append(f"\nModel/deployment: {llm_client.get_model_name()} | calls: {used['calls']} | tokens: "
                 f"{used['prompt_tokens']} in / {used['completion_tokens']} out | statements removed by the guard: {removed_total}")
    return lines
