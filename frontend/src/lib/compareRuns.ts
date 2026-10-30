import type { UsbToolRunResponse } from "../types";

export interface RunSnapshot {
  result: UsbToolRunResponse;
  options: string[];          // the advanced options that were set for this run, in plain words
}

interface NodeValues { pins: string; fn: string; props: string }

/** Pin-state nodes of a DTS text, keyed by label - works for nested mux/config and for flat nodes. */
export function parseNodes(text: string): Map<string, NodeValues> {
  const out = new Map<string, NodeValues>();
  const re = /(\w+):\s*[\w-]+\s*\{/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    let depth = 1, j = re.lastIndex;
    while (depth && j < text.length) { depth += (text[j] === "{" ? 1 : 0) - (text[j] === "}" ? 1 : 0); j++; }
    const body = text.slice(re.lastIndex, j - 1);
    const pins = new Set<string>();
    for (const line of body.matchAll(/pins\s*=\s*([^;]+);/g)) for (const q of line[1].matchAll(/"([^"]+)"/g)) pins.add(q[1]);
    if (!pins.size) continue;
    const fn = new Set<string>(); for (const f of body.matchAll(/function\s*=\s*"([^"]+)"/g)) fn.add(f[1]);
    const props: string[] = [];
    for (const p of body.matchAll(/^\s*((?:drive-strength|bias-[\w-]+|input-enable|output-(?:high|low))[^;]*);/gm)) props.push(p[1].trim());
    out.set(m[1], { pins: [...pins].sort().join(","), fn: [...fn].sort().join(","), props: props.sort().join(" ") });
  }
  return out;
}

export function allText(r: UsbToolRunResponse, role?: string): string {
  if (r.files?.length) return r.files.filter((f) => !role || f.role === role).map((f) => f.text).join("\n");
  return r.out_text ?? "";
}

function layoutOf(text: string): string {
  const nested = /\bmux\s*\{/.test(text);
  const indent = /^\t/m.test(text) ? "tabs" : /^ {2,}\S/m.test(text) ? "spaces" : "none";
  return `${nested ? "nested mux/config" : "flat nodes"}, ${indent}`;
}

const norm = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\s+/g, "");

export interface Comparison {
  verdict: string;
  tone: "same" | "values" | "none";
  lines: string[];
}

export function compareRuns(before: RunSnapshot, after: RunSnapshot): Comparison {
  const lines: string[] = [];
  const a = parseNodes(allText(before.result, "pinmux") || before.result.out_text);
  const b = parseNodes(allText(after.result, "pinmux") || after.result.out_text);
  const added = [...b.keys()].filter((k) => !a.has(k));
  const removed = [...a.keys()].filter((k) => !b.has(k));
  const changed = [...b.keys()].filter((k) => a.has(k) && JSON.stringify(a.get(k)) !== JSON.stringify(b.get(k)));
  const same = [...b.keys()].filter((k) => a.has(k)).length - changed.length;
  lines.push(`Pin states: ${same} identical, ${changed.length} changed, ${added.length} added, ${removed.length} removed.`);
  for (const k of changed) {
    const x = a.get(k)!, y = b.get(k)!;
    const diffs: string[] = [];
    if (x.pins !== y.pins) diffs.push(`pins ${x.pins} -> ${y.pins}`);
    if (x.fn !== y.fn) diffs.push(`function ${x.fn} -> ${y.fn}`);
    if (x.props !== y.props) diffs.push(`${x.props || "(none)"} -> ${y.props || "(none)"}`);
    lines.push(`  changed: ${k} (${y.pins}): ${diffs.join("; ")}`);
  }
  added.forEach((k) => lines.push(`  added: ${k} (${b.get(k)!.pins})`));
  removed.forEach((k) => lines.push(`  removed: ${k} (${a.get(k)!.pins})`));

  const la = layoutOf(allText(before.result, "pinmux") || before.result.out_text), lb = layoutOf(allText(after.result, "pinmux") || after.result.out_text);
  lines.push(la === lb ? `Layout of the pin-state file: unchanged (${lb}).` : `Layout of the pin-state file: ${la} -> ${lb}.`);

  const oa = before.result.files?.find((f) => f.role === "overlay")?.text ?? before.result.overlay_text ?? "";
  const ob = after.result.files?.find((f) => f.role === "overlay")?.text ?? after.result.overlay_text ?? "";
  if (oa || ob) lines.push(norm(oa) === norm(ob) ? "Regulators and USB nodes: identical." : "Regulators and USB nodes: they differ - open the overlay file to see how.");

  const va = before.result.overview?.verifier, vb = after.result.overview?.verifier;
  if (va && vb) lines.push(`Independent verifier: ${va.wrong} wrong / ${va.ok} OK before, ${vb.wrong} wrong / ${vb.ok} OK now.`);
  const da = before.result.overview?.open_decisions, db = after.result.overview?.open_decisions;
  if (da !== undefined && db !== undefined) lines.push(`Decisions still open: ${da} before, ${db} now.`);

  const valuesMoved = changed.length + added.length + removed.length > 0;
  const layoutMoved = la !== lb;
  const verdict = valuesMoved ? "The pin values CHANGED - check the lines below are what you intended."
    : layoutMoved ? "Only the layout changed. Every pin value is identical to the previous run."
    : "Nothing changed in the pin states.";
  return { verdict, tone: valuesMoved ? "values" : layoutMoved ? "same" : "none", lines };
}
