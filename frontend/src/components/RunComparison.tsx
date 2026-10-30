import type { Comparison } from "../lib/compareRuns";

export function RunComparison({ cmp, before, after }: { cmp: Comparison; before: string[]; after: string[] }) {
  const fmt = (o: string[]) => (o.length ? o.join(", ") : "default options");
  return (
    <div>
      <p style={{ margin: "0 0 6px", fontWeight: 600, color: cmp.tone === "values" ? "var(--warn)" : "var(--ok)" }}>{cmp.verdict}</p>
      <dl className="kv" style={{ marginBottom: 8 }}>
        <dt>Before</dt><dd>{fmt(before)}</dd>
        <dt>Now</dt><dd>{fmt(after)}</dd>
      </dl>
      <ul className="muted" style={{ margin: 0, paddingLeft: 20 }}>
        {cmp.lines.map((l, i) => <li key={i}>{l}</li>)}
      </ul>
    </div>
  );
}
