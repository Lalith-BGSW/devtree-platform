import { useRef, useState } from "react";
import { batchUpdateDts, downloadTextAsFile, usbToolVerify } from "../api/client";
import type { DtsBatchUpdateResponse, UsbToolVerifyResponse } from "../types";

interface Props {
  workbookId: string;
  project: string;
}

interface PendingFile {
  file: File;
  outputName: string;
}

export function DtsUpdatePanel({ workbookId, project }: Props) {
  const [pending, setPending] = useState<PendingFile[]>([]);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<DtsBatchUpdateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expandedFile, setExpandedFile] = useState<string | null>(null);
  const [useAiFallback, setUseAiFallback] = useState(false);
  const [checking, setChecking] = useState(false);
  const [checks, setChecks] = useState<{ name: string; result?: UsbToolVerifyResponse; error?: string }[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  function addFiles(files: FileList) {
    const additions: PendingFile[] = Array.from(files).map((f) => ({
      file: f,
      outputName: f.name,
    }));
    setPending((p) => [...p, ...additions]);
    setResult(null);
  }

  function removeFile(idx: number) {
    setPending((p) => p.filter((_, i) => i !== idx));
  }

  function renameFile(idx: number, name: string) {
    setPending((p) => p.map((pf, i) => (i === idx ? { ...pf, outputName: name } : pf)));
  }

  async function handleCheck() {
    if (pending.length === 0) return;
    setChecking(true);
    setError(null);
    setChecks([]);
    const out: { name: string; result?: UsbToolVerifyResponse; error?: string }[] = [];
    for (const p of pending) {
      try {
        out.push({ name: p.file.name, result: await usbToolVerify(workbookId, project, p.file) });
      } catch (e) {
        out.push({ name: p.file.name, error: e instanceof Error ? e.message : "Check failed." });
      }
      setChecks([...out]);
    }
    setChecking(false);
  }

  async function handleRun() {
    if (pending.length === 0) return;
    setRunning(true);
    setError(null);
    try {
      const resp = await batchUpdateDts(
        workbookId, project, pending.map((p) => p.file), pending.map((p) => p.outputName),
        useAiFallback,
      );
      setResult(resp);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Update failed.");
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="panel">
      <p className="muted" style={{ marginTop: 0, maxWidth: "64ch" }}>
        Add your current .dts or .dtsi files. <strong>Update</strong> writes the workbook's values into the matching pin states and
        notes each old and new value; anything it does not recognize stays as it was. <strong>Check only</strong> changes nothing
        and reports each pin as wrong, ambiguous, unverifiable or confirmed.
      </p>
      <label style={{ display: "flex", alignItems: "flex-start", gap: 8, marginBottom: 16 }}>
        <input type="checkbox" checked={useAiFallback} onChange={(event) => setUseAiFallback(event.target.checked)} style={{ marginTop: 4 }} />
        <span>
          Let the AI read nodes the app does not recognize
          <span className="muted small" style={{ display: "block" }}>
            Advice only. It never edits those nodes; what it finds appears in the review report.
          </span>
        </span>
      </label>

      {error && <div className="error-banner">{error}</div>}

      <div
        className="dropzone compact"
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
        }}
      >
        <div className="dropzone-label">Drop .dts or .dtsi files here, or click to choose</div>
        <div className="dropzone-hint">You can add several files</div>
        <input
          ref={inputRef}
          type="file"
          accept=".dts,.dtsi"
          multiple
          style={{ display: "none" }}
          onChange={(e) => {
            if (e.target.files?.length) addFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </div>

      {pending.length > 0 && (
        <div className="entity-list" style={{ marginTop: 14 }}>
          {pending.map((pf, i) => (
            <div className="entity-row" key={i}>
              <span className="entity-name" style={{ minWidth: 140 }}>{pf.file.name}</span>
              <span className="muted">saves as</span>
              <input
                type="text"
                value={pf.outputName}
                onChange={(e) => renameFile(i, e.target.value)}
                style={{ flex: 1, fontSize: 12 }}
              />
              <button className="btn" onClick={() => removeFile(i)}>Remove</button>
            </div>
          ))}
        </div>
      )}

      <div className="btn-row">
        <button className="btn btn-primary" onClick={handleRun} disabled={running || pending.length === 0}>
          {running ? "Updating…" : pending.length > 1 ? `Update ${pending.length} files` : "Update file"}
        </button>
        <button className="btn" onClick={handleCheck} disabled={checking || pending.length === 0}>
          {checking ? "Checking…" : "Check only"}
        </button>
      </div>

      {checks.map((c, i) => (
        <div key={i} style={{ marginTop: 16 }}>
          <div style={{ fontWeight: 600, marginBottom: 6 }}>{c.name}</div>
          {c.error && <div className="error-banner">{c.error}</div>}
          {c.result && (
            <>
              <div style={{ display: "flex", gap: 16, marginBottom: 8, flexWrap: "wrap" }}>
                <span style={{ color: c.result.counts.wrong > 0 ? "var(--error)" : "var(--muted)", fontWeight: c.result.counts.wrong > 0 ? 600 : 400 }}>{c.result.counts.wrong} wrong</span>
                <span style={{ color: c.result.counts.ambiguous > 0 ? "var(--warn)" : "var(--muted)" }}>{c.result.counts.ambiguous} ambiguous</span>
                <span className="muted">{c.result.counts.unverifiable} unverifiable</span>
                <span style={{ color: "var(--ok)" }}>{c.result.counts.ok} confirmed</span>
              </div>
              <div className="code-wrap"><pre className="dts-preview">{c.result.report_text}</pre></div>
            </>
          )}
        </div>
      ))}

      {result && (
        <>
          <div className="stat-row" style={{ marginTop: 24 }}>
            <div className="stat">
              <span className="stat-value">{result.total_excel_signal_pins}</span>
              <span className="stat-label">Signal pins in the workbook</span>
            </div>
            <div className="stat">
              <span className="stat-value">{result.total_covered_pins}</span>
              <span className="stat-label">Covered by your files</span>
            </div>
            <div className="stat">
              <span className="stat-value">{result.remainder_pin_count}</span>
              <span className="stat-label">Not in any file</span>
            </div>
          </div>

          {result.review_report && (
            <div className="btn-row" style={{ marginTop: 8 }}>
              <button
                className="btn"
                onClick={() => downloadTextAsFile(
                  `${project.toLowerCase()}_review_report.html`, result.review_report, "text/html",
                )}
              >
                Download review report
              </button>
            </div>
          )}

          <div className="entity-list">
            {result.files.map((f, i) => (
              <div key={i}>
                <div className="entity-row">
                  <span className="entity-name" style={{ minWidth: 160 }}>{f.output_filename}</span>
                  <span className="entity-meta">
                    {f.changed_node_count} updated · {f.unmatched_node_count} node(s) with
                    unmatched pins · {f.node_results.length} total
                  </span>
                  <button
                    className="btn"
                    onClick={() => setExpandedFile(expandedFile === f.output_filename ? null : f.output_filename)}
                  >
                    {expandedFile === f.output_filename ? "Hide" : "Details"}
                  </button>
                  <button
                    className="btn btn-primary"
                    onClick={() => downloadTextAsFile(f.output_filename, f.updated_text)}
                  >
                    Download
                  </button>
                </div>
                <div className={`validation-item ${f.unrecognized_pin_state_count > 0 || f.format_role === "unknown" ? "warning" : ""}`} style={{ marginTop: 6, marginBottom: 6 }}>
                  <span className="rule">format</span>
                  <span>
                    {f.format_role} · {f.recognized_pin_state_count} pin state(s) recognized
                    {f.unrecognized_pin_state_count > 0 ? ` · ${f.unrecognized_pin_state_count} not recognized` : ""}
                    {f.format_summary ? ` · ${f.format_summary}` : ""}. {f.format_note}
                  </span>
                </div>
                {f.ai_candidate_count > 0 && (
                  <div className={`validation-item ${f.ai_extraction_failure_count > 0 || !f.ai_fallback_requested ? "warning" : ""}`} style={{ marginTop: 6, marginBottom: 6 }}>
                    <span className="rule">AI analysis</span>
                    <span>{f.ai_fallback_status} See the review report for node-level advisories.</span>
                  </div>
                )}
                {f.structural_issue_count > 0 && (
                  <div className="validation-item warning" style={{ marginTop: 6, marginBottom: 6 }}>
                    <span className="rule">inconsistent</span>
                    <span>
                      {f.structural_issue_count} node(s) have mismatched mux/config pins (left
                      unmodified - see Details below for which ones)
                    </span>
                  </div>
                )}
                {f.node_results.some((nr) => nr.ai_assisted) && (
                  <div style={{ marginTop: 6, marginBottom: 6 }}>
                    {f.node_results
                      .filter((nr) => nr.ai_assisted && nr.ai_advisory)
                      .map((nr, ni) => (
                        <div key={ni} className="validation-item warning" style={{ marginBottom: 4 }}>
                          <span className="rule">ai-read</span>
                          <span>{nr.ai_advisory}</span>
                        </div>
                      ))}
                  </div>
                )}
                {expandedFile === f.output_filename && (
                  <div className="code-wrap" style={{ margin: "8px 0" }}><pre className="dts-preview" style={{ maxHeight: 300 }}>{f.updated_text}</pre></div>
                )}
              </div>
            ))}
          </div>

          <h3 className="sub-title">Pins not in any of your files</h3>
          <div className="code-wrap"><pre className="dts-preview">{result.remainder_dts_text}</pre></div>
          <div className="btn-row">
            <button
              className="btn btn-primary"
              onClick={() => downloadTextAsFile(`${project.toLowerCase()}_remainder.dtsi`, result.remainder_dts_text)}
            >
              Download as .dtsi
            </button>
          </div>
        </>
      )}
    </div>
  );
}
