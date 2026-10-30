import { useState } from "react";
import { combinedRun, downloadTextAsFile, genericPreviewRun } from "../api/client";
import type { CombinedToolResponse, GenericPreviewResponse } from "../types";

interface Props {
  workbookId: string;
  project: string;
}

const PERIPHERALS = [
  { key: "usb", label: "USB" },
  { key: "ethernet", label: "Ethernet" },
  { key: "spi", label: "SPI" },
  { key: "uart", label: "UART" },
  { key: "i2c", label: "I2C" },
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

export function CombinedPanel({ workbookId, project }: Props) {
  const [enabled, setEnabled] = useState<string[]>(PERIPHERALS.map((p) => p.key));
  const [kernel, setKernel] = useState("");
  const [kernelBase, setKernelBase] = useState("");
  const [usbPattern, setUsbPattern] = useState("auto");
  const [templateFiles, setTemplateFiles] = useState<File[]>([]);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<CombinedToolResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState("");
  const [copied, setCopied] = useState<string | null>(null);
  const [gen, setGen] = useState<GenericPreviewResponse | null>(null);
  const [genBusy, setGenBusy] = useState(false);
  const [genAi, setGenAi] = useState(false);
  const [genTab, setGenTab] = useState("report");
  const [genErr, setGenErr] = useState<string | null>(null);

  async function runGeneric() {
    setGenBusy(true);
    setGenErr(null);
    try {
      const r = await genericPreviewRun(workbookId, project, genAi);
      setGen(r);
      setGenTab(r.files.length ? r.files[0].name : "report");
    } catch (e) {
      setGenErr(e instanceof Error ? e.message : "Preview failed.");
    } finally {
      setGenBusy(false);
    }
  }

  const toggle = (k: string) => setEnabled((cur) => (cur.includes(k) ? cur.filter((x) => x !== k) : [...cur, k]));

  async function run() {
    if (!enabled.length) {
      setError("Choose at least one peripheral.");
      return;
    }
    setRunning(true);
    setError(null);
    try {
      setResult(await combinedRun(workbookId, { project, peripherals: enabled, usbPattern, kernel, kernelBase, templateFiles }));
      setTab("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Run failed.");
    } finally {
      setRunning(false);
    }
  }

  const ov = result?.overview;
  const tone = !ov ? "" : ov.status === "complete" ? "complete" : ov.status === "problems" ? "problems" : "review";
  const tabs = result
    ? [
        ...result.files.map((f) => ({
          key: "f:" + f.name, label: f.name, text: f.text, file: f.name,
          note: f.role === "pinmux" ? "Every pin state of the chosen peripherals under one &tlmm. Deliverable file." : "Every controller node of the chosen peripherals. Deliverable file.",
        })),
        { key: "report", label: "Report", text: result.report_text, file: "combined_report.md", note: "What each engine produced and what was checked between them." },
      ].filter((t) => t.text && t.text.trim())
    : [];
  const active = tabs.find((t) => t.key === tab) ?? tabs[0];

  return (
    <div className="panel">
      <p className="muted" style={{ marginTop: 0, maxWidth: "64ch" }}>
        One pin-state file and one controller file for everything generated so far. Each peripheral is produced and checked by its own engine, then merged here and checked against the others: the same pin used twice, the same name defined twice, or a reference to a state that does not exist.
      </p>

      {error && <div className="error-banner">{error}</div>}

      <h3 className="sub-title" style={{ marginTop: 0 }}>Peripherals to include</h3>
      <div className="chip-row" role="group" aria-label="Peripherals">
        {PERIPHERALS.map((p) => (
          <label key={p.key} className={`chip ${enabled.includes(p.key) ? "on" : ""}`}>
            <input type="checkbox" checked={enabled.includes(p.key)} onChange={() => toggle(p.key)} />
            {p.label}
            {result ? <span className="chip-n">{result.sections.find((s) => s.peripheral === p.key)?.pin_states ?? 0}</span> : null}
          </label>
        ))}
      </div>

      <div className="form-grid" style={{ margin: "14px 0" }}>
        <Field label="Linux kernel version" hint="Sets the layout of the pin-state file for every peripheral. Leave empty for the default style.">
          <input type="text" value={kernel} onChange={(e) => setKernel(e.target.value)} placeholder="e.g. 6.6" style={{ maxWidth: 160 }} />
        </Field>
        <Field label="USB wiring style" hint="How VBUS and over-current are wired in your BSP. Auto stops at pin states when it cannot tell.">
          <select value={usbPattern} onChange={(e) => setUsbPattern(e.target.value)}>
            <option value="auto">Auto (controller style if nothing says otherwise)</option>
            <option value="controller">On the controller node</option>
            <option value="regulator">Regulator nodes</option>
          </select>
        </Field>
      </div>

      <details className="fold">
        <summary>More options <span className="hint">template files, vendor kernel</span></summary>
        <div className="fold-body">
          <div className="form-grid">
            <Field label="Template files" hint="Your project's pinmux .dtsi or overlay .dts. Only the layout is copied.">
              <input type="file" accept=".dts,.dtsi" multiple onChange={(e) => setTemplateFiles(Array.from(e.target.files ?? []))} />
            </Field>
            <Field label="Vendor kernel source" hint="Raw-file URL of your vendor git mirror, used with the kernel version.">
              <input type="text" value={kernelBase} onChange={(e) => setKernelBase(e.target.value)} placeholder="e.g. https://git.example.com/raw/vendor-kernel" />
            </Field>
          </div>
          <p className="muted small">Pin choices (add or remove pins, master or slave, AI checks) are made in each peripheral&apos;s own tab. This tab uses the defaults.</p>
        </div>
      </details>

      <div className="btn-row" style={{ marginTop: 20 }}>
        <button className="btn btn-primary" onClick={run} disabled={running}>{running ? "Generating, merging and checking…" : "Generate combined files"}</button>
      </div>

      {result && ov && (
        <div style={{ marginTop: 36 }} aria-live="polite">
          <div className={`status ${tone}`}>
            <h3>{ov.headline}</h3>
            <p>{ov.pin_states} pin state{ov.pin_states === 1 ? "" : "s"} in {ov.files.length} file{ov.files.length === 1 ? "" : "s"}.</p>
          </div>

          {result.overview.pin_states === 0 && (result.peripherals.some((p) => /TLMM/.test(`${p.headline} ${p.notes.join(" ")}`)) || result.notes.some((n) => /TLMM/.test(n))) && (
            <div className="status review" style={{ marginTop: 16 }}>
              <h3>These pins are not Qualcomm TLMM pins</h3>
              <p>The Qualcomm engines cannot write them. You can get an <strong>unverified preview</strong> built from the workbook alone (SPI, UART and I2C, pin names as the workbook gives them). It is for review, not for merging.</p>
              <label className="check"><input type="checkbox" checked={genAi} onChange={(e) => setGenAi(e.target.checked)} /> Also ask the AI for a draft (separate file, marked unverified; needs <code>generic_draft</code> in LLM_FEATURES)</label>
              <div className="btn-row" style={{ marginTop: 12 }}>
                <button className="btn btn-primary" onClick={runGeneric} disabled={genBusy}>{genBusy ? "Building preview…" : "Build unverified preview"}</button>
              </div>
              {genErr && <div className="error-banner">{genErr}</div>}
              {gen && (
                <div style={{ marginTop: 16 }}>
                  <p><strong>{gen.overview.headline}</strong></p>
                  <p className="muted small">Checked against the workbook: {gen.overview.verifier.ok} of {gen.overview.verifier.checked} pins confirmed, {gen.overview.verifier.wrong} wrong. This does not check the SoC's real pinctrl format.</p>
                  {gen.ai_draft && <p className="muted small">AI draft: {gen.ai_draft.status}. {gen.ai_draft.note ?? ""}</p>}
                  <div className="code-wrap">
                    <div className="tabs small" role="tablist" aria-label="Preview files">
                      {gen.files.map((f) => (
                        <button key={f.name} role="tab" aria-selected={genTab === f.name} className={`tab ${genTab === f.name ? "active" : ""}`} onClick={() => setGenTab(f.name)}>{f.name}</button>
                      ))}
                      <button role="tab" aria-selected={genTab === "report"} className={`tab ${genTab === "report" ? "active" : ""}`} onClick={() => setGenTab("report")}>Report</button>
                      <span className="spacer" />
                      <span className="tab-actions">
                        <button className="btn btn-small" onClick={() => downloadTextAsFile(genTab === "report" ? `${project}_generic_report.md` : genTab, genTab === "report" ? gen.report_text : (gen.files.find((f) => f.name === genTab)?.text ?? ""))}>Download</button>
                      </span>
                    </div>
                    <pre className="dts-preview">{genTab === "report" ? gen.report_text : (gen.files.find((f) => f.name === genTab)?.text ?? "")}</pre>
                  </div>
                </div>
              )}
            </div>
          )}

          {result.conflicts.length > 0 && (
            <div className="error-banner">
              <strong>Conflicts between peripherals</strong>
              <ul>{result.conflicts.map((c, i) => <li key={i}>{c}</li>)}</ul>
            </div>
          )}

          <h3 className="sub-title">Per peripheral</h3>
          <div className="table-scroll">
            <table className="data-table">
              <thead><tr><th>Peripheral</th><th>Result</th><th>Pin states</th><th>Files</th></tr></thead>
              <tbody>
                {result.peripherals.map((p) => (
                  <tr key={p.peripheral}>
                    <td>{p.peripheral.toUpperCase()}</td>
                    <td>{p.headline || p.status}{p.notes.map((n, i) => <div key={i} className="muted small">{n}</div>)}</td>
                    <td>{p.pin_states}</td>
                    <td>{p.files.join(", ") || "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {tabs.length > 0 && active && (
            <div style={{ margin: "24px 0" }}>
              <div className="btn-row" style={{ margin: "0 0 10px", justifyContent: "space-between" }}>
                <h3 className="sub-title" style={{ margin: 0 }}>Your files</h3>
                {result.bundle_zip_b64 && (
                  <button className="btn btn-small" onClick={() => {
                    const bytes = Uint8Array.from(atob(result.bundle_zip_b64 as string), (c) => c.charCodeAt(0));
                    const url = URL.createObjectURL(new Blob([bytes], { type: "application/zip" }));
                    const a = document.createElement("a");
                    a.href = url; a.download = `${project.toLowerCase()}_combined_dts_files.zip`; a.click();
                    URL.revokeObjectURL(url);
                  }}>Download all files (.zip)</button>
                )}
              </div>
              <div className="code-wrap">
                <div className="tabs small" role="tablist" aria-label="Output files">
                  {tabs.map((t) => (
                    <button key={t.key} role="tab" aria-selected={active.key === t.key} className={`tab ${active.key === t.key ? "active" : ""}`} onClick={() => setTab(t.key)}>{t.label}</button>
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

          <details className="fold" open={result.decisions.length > 0}>
            <summary>Open decisions ({result.decisions.length}) <span className="hint">what a person still has to decide</span></summary>
            <div className="fold-body">
              {result.decisions.length ? (
                <ul style={{ margin: 0, paddingLeft: 20 }}>{result.decisions.map((d, i) => <li key={i}><strong>{d.peripheral.toUpperCase()}</strong>: {d.text}</li>)}</ul>
              ) : <p className="muted small">None.</p>}
            </div>
          </details>

          <details className="fold">
            <summary>Left out, not generated ({result.left_out.length}) <span className="hint">pins the engines did not write, with the reason</span></summary>
            <div className="fold-body">
              {result.left_out.length ? (
                <div className="table-scroll">
                  <table className="data-table">
                    <thead><tr><th>Peripheral</th><th>Pin</th><th>Signal</th><th>Why</th></tr></thead>
                    <tbody>
                      {result.left_out.map((x, i) => (
                        <tr key={i}><td>{x.peripheral.toUpperCase()}</td><td>{x.pin}</td><td>{x.signal}</td><td>{x.reason}</td></tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : <p className="muted small">Nothing.</p>}
            </div>
          </details>

          {result.notes.length > 0 && (
            <details className="fold">
              <summary>Notes ({result.notes.length})</summary>
              <div className="fold-body"><ul style={{ margin: 0, paddingLeft: 20 }}>{result.notes.map((n, i) => <li key={i}>{n}</li>)}</ul></div>
            </details>
          )}
        </div>
      )}
    </div>
  );
}
