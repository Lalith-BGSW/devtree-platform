import { useEffect, useState } from "react";
import { uartToolRun, downloadTextAsFile, aiStatus } from "../api/client";
import type { AiStatus } from "../api/client";
import type { UartToolResponse } from "../types";

interface Props {
  workbookId: string;
  project: string;
}

const FAMILIES: { key: string; label: string; hint: string }[] = [
  { key: "uart", label: "UART (2-wire)", hint: "UART_QUPn: TX and RX only" },
  { key: "hsuart", label: "High-speed UART", hint: "HSUART_QUPn: with CTS / RTS flow control" },
];

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="field">
      <span>{label}</span>
      {hint ? <span className="help">{hint}</span> : null}
      {children}
    </label>
  );
}

export function UartToolPanel({ workbookId, project }: Props) {
  const [enabled, setEnabled] = useState<string[]>(FAMILIES.map((f) => f.key));
  const [kernel, setKernel] = useState("");
  const [kernelBase, setKernelBase] = useState("");
  const [templateFiles, setTemplateFiles] = useState<File[]>([]);
  const [includeText, setIncludeText] = useState("");
  const [excludeText, setExcludeText] = useState("");
  const [useAi, setUseAi] = useState(false);
  const [ai, setAi] = useState<AiStatus | null>(null);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<UartToolResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState("");
  const [copied, setCopied] = useState<string | null>(null);

  useEffect(() => {
    aiStatus().then(setAi).catch(() => setAi(null));
  }, []);

  const split = (t: string) => t.split(",").map((s) => s.trim()).filter(Boolean);

  function toggle(k: string) {
    setEnabled((cur) => (cur.includes(k) ? cur.filter((x) => x !== k) : [...cur, k]));
  }

  async function run(extra?: { include?: string[] }) {
    if (!enabled.length) {
      setError("Choose UART, high-speed UART or both.");
      return;
    }
    const inc = Array.from(new Set([...split(includeText), ...(extra?.include ?? [])]));
    if (extra?.include) setIncludeText(inc.join(", "));
    setRunning(true);
    setError(null);
    try {
      const r = await uartToolRun(workbookId, {
        project, families: enabled, includePins: inc, excludePins: split(excludeText),
        aiCheck: useAi && !!ai?.configured, kernel, kernelBase, templateFiles,
      });
      setResult(r);
      setTab("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Run failed.");
    } finally {
      setRunning(false);
    }
  }

  const ov = result?.overview;
  const decisions = (result?.items ?? []).filter((i) => i.kind === "decision");
  const infos = (result?.items ?? []).filter((i) => i.kind !== "decision");
  const ck = result?.ai_check;
  const ckFound = ck ? ck.rows.filter((r) => r.status === "finding") : [];
  const v = result?.verification;
  const tone = !ov ? "" : ov.status === "complete" ? "complete" : ov.status === "problems" ? "problems" : "review";

  const tabs = result
    ? [
        ...result.files.map((f) => ({
          key: "f:" + f.name, label: f.name, text: f.text, file: f.name,
          note: f.role === "pinmux" ? "Pin states under &tlmm. Deliverable file." : "The &uartN nodes (one per UART) that use those pin states. Deliverable file.",
        })),
        { key: "report", label: "Report", text: result.report_text, file: "uart_report.md", note: "Every pin, what was left out, and the independent check." },
      ].filter((t) => t.text && t.text.trim())
    : [];
  const active = tabs.find((t) => t.key === tab) ?? tabs[0];

  return (
    <div className="panel">
      <p className="muted" style={{ marginTop: 0, maxWidth: "64ch" }}>
        Creates the UART pin-state file and the controller file (one &uartN per UART) from the workbook. A pin counts as UART only when the workbook selects a UART function for it (for example UART_QUP10 or HSUART_QUP13), not when UART is merely listed as a possible function. UARTs of the sensors subsystem, the safety island and other SoC families are listed as left out. Debug-port selects and UART enables the board drives as plain GPIO are added as board-wired control lines.
      </p>

      {error && <div className="error-banner">{error}</div>}

      <h3 className="sub-title" style={{ marginTop: 0 }}>Bus roles to include</h3>
      <div className="chip-row" role="group" aria-label="Bus roles">
        {FAMILIES.map((f) => (
          <label key={f.key} className={`chip ${enabled.includes(f.key) ? "on" : ""}`} title={f.hint}>
            <input type="checkbox" checked={enabled.includes(f.key)} onChange={() => toggle(f.key)} />
            {f.label}
            {result ? <span className="chip-n">{result.counts[f.key] ?? 0}</span> : null}
          </label>
        ))}
      </div>

      <div className="form-grid" style={{ margin: "14px 0" }}>
        <Field label="Linux kernel version" hint="Sets the layout of the pin-state file. Leave empty for the default style.">
          <input type="text" value={kernel} onChange={(e) => setKernel(e.target.value)} placeholder="e.g. 6.6" style={{ maxWidth: 160 }} />
        </Field>
        <Field
          label="AI data check"
          hint={ai?.configured ? "The AI reads each pin's workbook cells for contradictions (it knows a TX pin on a net called UART_RXD is normal). It never writes into the files." : "Not available: set LLM_API_KEY and LLM_DEPLOYMENT in backend/.env and restart the backend."}
        >
          <select value={useAi ? "on" : "off"} disabled={!ai?.configured} onChange={(e) => setUseAi(e.target.value === "on")}>
            <option value="off">Off (no AI)</option>
            <option value="on">On</option>
          </select>
        </Field>
      </div>

      <details className="fold">
        <summary>More options <span className="hint">template files, add or remove pins</span></summary>
        <div className="fold-body">
          <div className="form-grid">
            <Field label="Template files" hint="Your project's pinmux .dtsi or overlay .dts. Only the layout is copied, never pins or names.">
              <input type="file" accept=".dts,.dtsi" multiple onChange={(e) => setTemplateFiles(Array.from(e.target.files ?? []))} />
            </Field>
            <Field label="Vendor kernel source" hint="Raw-file URL of your vendor git mirror, used with the kernel version.">
              <input type="text" value={kernelBase} onChange={(e) => setKernelBase(e.target.value)} placeholder="e.g. https://git.example.com/raw/vendor-kernel" />
            </Field>
            <Field label="Add pins" hint="Pins that are not selected as UART but belong to it. The Left out list suggests them with an Add button. Comma-separated.">
              <input type="text" value={includeText} onChange={(e) => setIncludeText(e.target.value)} placeholder="GPIO_124" />
            </Field>
            <Field label="Remove pins" hint="Comma-separated.">
              <input type="text" value={excludeText} onChange={(e) => setExcludeText(e.target.value)} placeholder="GPIO_81" />
            </Field>
          </div>
        </div>
      </details>

      <div className="btn-row" style={{ marginTop: 20 }}>
        <button className="btn btn-primary" onClick={() => run()} disabled={running}>
          {running ? "Generating and checking…" : "Generate UART files"}
        </button>
      </div>

      {result && ov && (
        <div style={{ marginTop: 36 }} aria-live="polite">
          <div className={`status ${tone}`}>
            <h3>{ov.headline}</h3>
            <p>
              {ov.pin_states} pin state{ov.pin_states === 1 ? "" : "s"}
              {ov.left_out ? `, ${ov.left_out} left out` : ""}. Checked against the workbook{result.soc_known ? ` and the ${ov.soc_table} pin table` : ""}: {ov.verifier.ok} confirmed, {ov.verifier.wrong} wrong
              {ov.verifier.ambiguous ? `, ${ov.verifier.ambiguous} ambiguous` : ""}{ov.verifier.unverifiable ? `, ${ov.verifier.unverifiable} not checkable` : ""}.
            </p>
          </div>

          {!result.soc_known && result.pins.length > 0 && (
            <div className="note warn">No verified UART pin table exists for {result.soc ?? "this SoC"}. Pins use the workbook's QUP name as the function and are marked for review.</div>
          )}
          {(result.validation_problems.length > 0 || (v && v.wrong.length > 0)) && (
            <div className="error-banner">
              <strong>Problems</strong>
              <ul>{[...result.validation_problems, ...(v?.wrong ?? [])].map((w, i) => <li key={i}>{w}</li>)}</ul>
            </div>
          )}

          {decisions.length > 0 && (
            <div className="decisions">
              <h3>Needs your decision ({decisions.length})</h3>
              <ol>{decisions.map((it, i) => <li key={i}>{it.text}</li>)}</ol>
            </div>
          )}

          {!!result.ai_facts?.length && (
            <div className="ai-block">
              <h3 className="sub-title" style={{ marginTop: 0 }}><span className="ai-tag">AI</span> Board facts written in the workbook</h3>
              <p className="muted small" style={{ margin: "0 0 6px" }}>Quoted from your cells.  nothing was written into the files.</p>
              {result.ai_facts.map((f, i) => (
                <div key={i} className="note">
                  <strong>{f.topic.replace("_", " ")}</strong>: {f.value} <span className="muted small">({f.pin}: &quot;{f.quote}&quot;)</span>
                </div>
              ))}
            </div>
          )}

          {ckFound.length > 0 && (
            <div className="ai-block">
              <h3 className="sub-title" style={{ marginTop: 0 }}><span className="ai-tag">AI</span> Check of your workbook data</h3>
              <p style={{ margin: "0 0 8px" }}>{ck?.checked} pins read. {ckFound.length} possible problem{ckFound.length === 1 ? "" : "s"} in the workbook itself:</p>
              {ckFound.map((r, i) => (
                <div key={i} className="note warn">
                  <code>{r.pin}</code> <strong>{r.type}</strong>: {r.issue}
                  <div className="muted small">&quot;{r.quote_a}&quot;{r.quote_b ? <> against &quot;{r.quote_b}&quot;</> : null}</div>
                </div>
              ))}
              <p className="muted small" style={{ margin: "6px 0 0" }}>The generated values still follow the Excel exactly; nothing was changed.</p>
            </div>
          )}

          {result.pins.length > 0 && (
            <div style={{ marginBottom: 24 }}>
              <h3 className="sub-title">Pins generated</h3>
              <div className="table-scroll">
                <table className="data-table">
                  <thead><tr><th>Pin</th><th>Signal</th><th>Role</th><th>Function</th><th>Mux (workbook / table)</th><th>Drive</th><th>Bias</th></tr></thead>
                  <tbody>
                    {result.pins.map((p) => (
                      <tr key={p.pin}>
                        <td><code>{p.pin}</code></td>
                        <td>{p.signal}</td>
                        <td>{p.family === "control" ? "control" : `${p.family} ${p.role}`}{p.instance ? ` (${p.instance.toUpperCase()})` : ""}</td>
                        <td>{p.function}{p.status === "added" ? " (added by you)" : p.status === "wired" ? " (board-wired control line)" : ""}</td>
                        <td>{p.mux ?? "-"} / {p.table_mux ?? "-"}</td>
                        <td>{p.drive ?? "?"}</td>
                        <td>{p.bias ?? "?"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {tabs.length > 0 && active && (
            <div style={{ marginBottom: 24 }}>
              <div className="btn-row" style={{ margin: "0 0 10px", justifyContent: "space-between" }}>
                <h3 className="sub-title" style={{ margin: 0 }}>Your files</h3>
                {result.bundle_zip_b64 && (
                  <button className="btn btn-small" onClick={() => {
                    const bytes = Uint8Array.from(atob(result.bundle_zip_b64 as string), (c) => c.charCodeAt(0));
                    const url = URL.createObjectURL(new Blob([bytes], { type: "application/zip" }));
                    const a = document.createElement("a");
                    a.href = url; a.download = `${project.toLowerCase()}_uart_dts_files.zip`; a.click();
                    URL.revokeObjectURL(url);
                  }}>Download all files (.zip)</button>
                )}
              </div>
              <div className="code-wrap">
                <div className="tabs small" role="tablist" aria-label="Output files">
                  {tabs.map((t) => (
                    <button key={t.key} role="tab" aria-selected={active.key === t.key} className={`tab ${active.key === t.key ? "active" : ""}`} onClick={() => setTab(t.key)}>
                      {t.label}
                    </button>
                  ))}
                  <span className="spacer" />
                  <span className="tab-actions">
                    <button className="btn btn-small" onClick={() => { navigator.clipboard.writeText(active.text); setCopied(active.key); setTimeout(() => setCopied(null), 2000); }}>
                      {copied === active.key ? "Copied" : "Copy"}
                    </button>
                    <button className="btn btn-small" onClick={() => downloadTextAsFile(active.file, active.text)}>Download</button>
                  </span>
                </div>
                {active.note && <div className="code-note">{active.note}</div>}
                <pre className="dts-preview">{active.text}</pre>
              </div>
            </div>
          )}

          {result.left_out.length > 0 && (
            <details className="fold">
              <summary>Left out ({result.left_out.length}) <span className="hint">why</span></summary>
              <div className="fold-body">
                <div className="table-scroll">
                  <table className="data-table">
                    <thead><tr><th>Pin</th><th>Signal</th><th>Why it is not in the files</th><th /></tr></thead>
                    <tbody>
                      {result.left_out.map((x, i) => (
                        <tr key={i}>
                          <td><code>{x.pin}</code>{x.tier ? <div className="muted small">{x.tier}</div> : null}</td>
                          <td>{x.signal}</td>
                          <td>
                            {x.reason}
                            {x.ai ? (
                              <div className="muted small" style={{ marginTop: 4 }}>
                                <span className="ai-tag">AI</span>{" "}
                                {x.ai.verdict === "spi_line" ? `reads this as an SPI line${x.ai.role && x.ai.role !== "other" ? ` (${x.ai.role})` : ""}` : x.ai.verdict === "not_spi" ? "does not read this as SPI" : "is not sure"}
                                {x.ai.quote ? <> - &quot;{x.ai.quote}&quot;</> : null}
                                {x.ai.note ? <> ({x.ai.note})</> : null}
                              </div>
                            ) : null}
                          </td>
                          <td>{x.gpio && x.tier ? (
                            <button className="btn btn-small" disabled={running} onClick={() => run({ include: [x.pin] })}>Add</button>
                          ) : null}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </details>
          )}

          {!!result.ai_ledger?.length && (
            <details className="fold ai">
              <summary>Where the AI was used <span className="hint">{result.ai_ledger.reduce((a, r) => a + r.calls, 0)} calls</span></summary>
              <div className="fold-body">
                <div className="table-scroll">
                  <table className="data-table">
                    <thead><tr><th>Feature</th><th>What happened</th><th>Calls</th><th>Tokens</th><th>Effect on the files</th></tr></thead>
                    <tbody>
                      {result.ai_ledger.map((r) => (
                        <tr key={r.feature}><td>{r.label}</td><td>{r.status}</td><td>{r.calls}</td><td>{r.tokens}</td><td>{r.effect}</td></tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </details>
          )}

          <details className="fold">
            <summary>How this was built <span className="hint">format, checks</span></summary>
            <div className="fold-body">
              <dl className="kv">
                <dt>Format</dt><dd>{result.files.length ? Array.from(new Set(result.files.map((f) => f.style_source))).join("; ") : "-"}</dd>
                <dt>Checked</dt><dd>{v ? `${v.ok} confirmed, ${v.wrong.length} wrong, ${v.ambiguous.length} ambiguous, ${v.unverifiable.length} not checkable` : "-"}</dd>
                <dt>AI</dt><dd>{ck ? `Read the workbook cells of ${ck.checked} pins; wrote none of these files.` : "Not used in this run."}</dd>
                {result.dtc_result && (<><dt>Compile check</dt><dd>{result.dtc_result}</dd></>)}
              </dl>
              {!!result.column_hits && Object.keys(result.column_hits).length > 0 && (
                <>
                  <p style={{ margin: "10px 0 4px" }}><strong>Where UART words appear in your workbook</strong></p>
                  <ul className="muted" style={{ margin: 0, paddingLeft: 20 }}>
                    {Object.entries(result.column_hits).slice(0, 15).map(([k, n]) => <li key={k}><code>{k}</code>: {n} row{n === 1 ? "" : "s"}</li>)}
                  </ul>
                </>
              )}
              {!!v && (v.ambiguous.length > 0 || v.unverifiable.length > 0) && (
                <ul className="muted" style={{ margin: "8px 0 0", paddingLeft: 20 }}>
                  {[...v.ambiguous, ...v.unverifiable].map((n, i) => <li key={i}>{n}</li>)}
                </ul>
              )}
              {!!result.bundle_notes.length && (
                <ul className="muted" style={{ margin: "8px 0 0", paddingLeft: 20 }}>{result.bundle_notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
              )}
            </div>
          </details>

          {infos.length > 0 && (
            <details className="fold">
              <summary>Notes ({infos.length})</summary>
              <div className="fold-body">
                <ul style={{ margin: 0, paddingLeft: 20 }}>{infos.map((it, i) => <li key={i} style={{ marginBottom: 4 }}>{it.text}</li>)}</ul>
              </div>
            </details>
          )}
        </div>
      )}
    </div>
  );
}
