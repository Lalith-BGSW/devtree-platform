import { useEffect, useState } from "react";
import { compareRuns, type RunSnapshot } from "../lib/compareRuns";
import { RunComparison } from "./RunComparison";
import type { ReactNode } from "react";
import { usbToolRun, downloadTextAsFile, aiStatus } from "../api/client";
import type { AiStatus } from "../api/client";
import type { UsbToolRunResponse } from "../types";

interface Props {
  workbookId: string;
  project: string;
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="field">
      <span>{label}</span>
      {hint ? <span className="help">{hint}</span> : null}
      {children}
    </label>
  );
}

export function UsbToolPanel({ workbookId, project }: Props) {
  const [pattern, setPattern] = useState<"auto" | "controller" | "regulator">("auto");
  const [dtsFile, setDtsFile] = useState<File | null>(null);
  const [overlayFile, setOverlayFile] = useState<File | null>(null);
  const [insertAfter, setInsertAfter] = useState("");
  const [vbusPinText, setVbusPinText] = useState("");
  const [aiMode, setAiMode] = useState<"off" | "explain" | "hybrid">("off");
  const [docsFiles, setDocsFiles] = useState<File[]>([]);
  const [templateFiles, setTemplateFiles] = useState<File[]>([]);
  const [facts, setFacts] = useState("");
  const [rolesText, setRolesText] = useState("");
  const [includePins, setIncludePins] = useState<string[]>([]);
  const [excludePins, setExcludePins] = useState<string[]>([]);
  const [levelSource, setLevelSource] = useState("convention");
  const [kernel, setKernel] = useState("");
  const [kernelBase, setKernelBase] = useState("");
  const [tab, setTab] = useState<string>("");
  const [aiDraftFile, setAiDraftFile] = useState<File | null>(null);
  const [ai, setAi] = useState<AiStatus | null>(null);

  useEffect(() => {
    aiStatus().then(setAi).catch(() => setAi(null));
  }, []);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<UsbToolRunResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [snap, setSnap] = useState<RunSnapshot | null>(null);
  const [cmp, setCmp] = useState<{ before: RunSnapshot; after: RunSnapshot } | null>(null);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);



  function splitList(text: string): string[] {
    return text.split(",").map((s) => s.trim()).filter(Boolean);
  }

  function handleCopy(text: string, key: string) {
    navigator.clipboard.writeText(text);
    setCopiedKey(key);
    setTimeout(() => setCopiedKey(null), 2000);
  }

  async function handleRun(patternOverride?: "auto" | "controller" | "regulator", extra?: { roles?: string[]; includePins?: string[]; excludePins?: string[] }) {
    const roleList = Array.from(new Set([...splitList(rolesText), ...(extra?.roles ?? [])]));
    const pinList = Array.from(new Set([...includePins, ...(extra?.includePins ?? [])]));
    if (extra?.roles) setRolesText(roleList.join(", "));
    if (extra?.includePins) setIncludePins(pinList);
    const exList = Array.from(new Set([...excludePins, ...(extra?.excludePins ?? [])]));
    if (extra?.excludePins) setExcludePins(exList);
    setRunning(true);
    setError(null);
    setResult(null);
    setCmp(null);
    const options: string[] = [];
    if (templateFiles.length) options.push(`template: ${templateFiles.map((f) => f.name).join(", ")}`);
    if (kernel.trim()) options.push(`kernel ${kernel.trim()}${kernelBase.trim() ? " (vendor tree)" : ""}`);
    if ((patternOverride ?? pattern) !== "auto") options.push(`pattern: ${patternOverride ?? pattern}`);
    if (levelSource !== "convention") options.push(`output levels: ${levelSource}`);
    if (splitList(vbusPinText).length) options.push(`VBUS pins: ${splitList(vbusPinText).join(", ")}`);
    if (roleList.length) options.push(`roles: ${roleList.join(", ")}`);
    if (pinList.length) options.push(`included: ${pinList.join(", ")}`);
    if (exList.length) options.push(`excluded: ${exList.join(", ")}`);
    if (dtsFile) options.push(`existing DTS: ${dtsFile.name}`);
    if (aiMode !== "off") options.push(`AI: ${aiMode}`);
    if (facts.trim()) options.push("facts given");
    if (docsFiles.length) options.push(`${docsFiles.length} document(s)`);
    if (aiDraftFile) options.push("draft to correct");
    try {
      const resp = await usbToolRun(workbookId, {
        project,
        pattern: patternOverride ?? pattern,
        vbusPin: splitList(vbusPinText),
        aiAdvice: aiMode === "explain" && !!ai?.configured,      // Hybrid does not also run the explainer: it adds tokens, not output
        aiPropose: (aiMode === "hybrid" || !!aiDraftFile || docsFiles.length > 0) && !!ai?.configured,
        docsFiles,
        templateFiles,
        roles: roleList,
        includePins: pinList,
        excludePins: exList,
        aiSuggest: aiMode === "hybrid" && !!ai?.configured,
        levelSource,
        kernel,
        kernelBase,
        facts,
        aiDraftFile,
        insertAfter: insertAfter.trim() || undefined,
        dtsFile,
        overlayFile,
      });
      setResult(resp);
      const now: RunSnapshot = { result: resp, options };
      if (snap) setCmp({ before: snap, after: now });
      setSnap(now);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Run failed.");
    } finally {
      setRunning(false);
    }
  }


  // ---- derived pieces of the result ----
  const ov = result?.overview;
  const hy = result?.ai_proposal;
  const kinds = hy?.open_item_kinds ?? (hy?.open_items ?? []).map(() => "decision");
  const decisions = (hy?.open_items ?? []).filter((_, i) => kinds[i] === "decision");
  const infoItems = (hy?.open_items ?? []).filter((_, i) => kinds[i] !== "decision");
  const ck = result?.ai_check;
  const ckFound = ck ? ck.rows.filter((r) => r.status === "finding") : [];
  const sg = result?.ai_suggest;
  const sgAcc = (sg?.roles.rows ?? []).filter((r) => r.status === "accepted");
  const sgConf = (sg?.roles.rows ?? []).filter((r) => r.status === "conflict");
  const sgInc = (sg?.audit.rows ?? []).filter((r) => r.status === "include");
  const sgVar = (sg?.audit.rows ?? []).filter((r) => r.status === "variant");
  const sgHasAction = !!sg && (sgAcc.length + sgConf.length + sgInc.length + sgVar.length > 0 || excludePins.length > 0);
  const aiCalls = (result?.ai_ledger ?? []).reduce((a, r) => a + r.calls, 0);
  const aiTokens = (result?.ai_ledger ?? []).reduce((a, r) => a + r.tokens, 0);

  const checkBlock = ck && ck.status !== "none" ? (
    <div>
      <h3 className="sub-title" style={{ marginTop: 0 }}><span className="ai-tag">AI</span> Check of your workbook data</h3>
      {ck.status !== "ok" ? (
        <p className="muted" style={{ margin: 0 }}>Not available: {ck.note}. The app's own checks still ran.</p>
      ) : ckFound.length === 0 ? (
        <p className="muted" style={{ margin: 0 }}>{ck.checked} pins read. No contradictions found in the data you gave.</p>
      ) : (
        <>
          <p style={{ margin: "0 0 8px" }}>{ck.checked} pins read. {ckFound.length} possible problem{ckFound.length === 1 ? "" : "s"} in the workbook itself. Compare with the schematic:</p>
          {ckFound.map((r, i) => (
            <div key={i} className="note warn">
              <code>{r.pin}</code> <strong>{r.type}</strong>: {r.issue}
              <div className="muted small">&quot;{r.quote_a}&quot;{r.quote_b ? <> against &quot;{r.quote_b}&quot;</> : null}</div>
            </div>
          ))}
        </>
      )}
      {ck.rows.length - ckFound.length > 0 && (
        <p className="muted small" style={{ margin: "6px 0 0" }}>{ck.rows.length - ckFound.length} AI finding(s) were dropped because they could not be verified against the workbook.</p>
      )}
      <p className="muted small" style={{ margin: "6px 0 0" }}>The generated values still follow the Excel exactly; nothing was changed.</p>
    </div>
  ) : null;

  const suggestBlock = sg && sgHasAction ? (
    <div>
      <h3 className="sub-title" style={{ marginTop: 0 }}><span className="ai-tag">AI</span> Suggestions</h3>
      <p className="muted small" style={{ margin: "0 0 8px" }}>Nothing changes until you confirm.</p>

      {(sg.roles.rows ?? []).map((r, i) => (
        <div key={"r" + i} style={{ marginBottom: 8 }}>
          <code>{r.pin}</code> {r.tail || "?"}: the AI says <strong>{r.role}</strong>
          {r.quote ? <span className="muted"> (quote: &quot;{r.quote}&quot;)</span> : null}
          {r.status === "accepted" ? <span style={{ color: "var(--ok)" }}> Accepted{r.why ? `: ${r.why}` : ""}</span>
            : r.status === "conflict" ? <span style={{ color: "var(--warn)" }}> Conflict, needs you: {r.why}</span>
            : <span className="muted"> {r.status === "other" ? "Not an over-current or VBUS pin." : `Rejected: ${r.why}`}</span>}
          {r.reason ? <div className="muted small">{r.reason}</div> : null}
        </div>
      ))}
      {sgAcc.length > 0 && (
        <button className="btn btn-small" onClick={() => handleRun(undefined, { roles: sgAcc.map((r) => r.role_arg) })} disabled={running}>
          Use the {sgAcc.length} accepted role{sgAcc.length === 1 ? "" : "s"} and run again
        </button>
      )}
      {sgConf.length > 0 && (
        <p style={{ margin: "8px 0 0" }}>
          <button className="btn btn-small" onClick={() => handleRun(undefined, { roles: sgConf.map((r) => r.role_arg) })} disabled={running}>
            I know the board: use the {sgConf.length} conflicting role{sgConf.length === 1 ? "" : "s"} anyway
          </button>
          <span className="muted small"> The contradiction stays flagged on the pin.</span>
        </p>
      )}

      {(sg.audit.rows ?? []).map((r, i) => (
        <div key={"a" + i} style={{ margin: "10px 0" }}>
          <code>{r.pin}</code>: the AI thinks this is <strong>{r.verdict === "usb_gpio" ? "a USB pin" : r.verdict === "not_usb" ? "not a USB pin" : "unclear"}</strong>
          {r.status === "include" ? <span> (it quotes &quot;{r.quote}&quot;).</span>
            : r.status === "variant" ? <span> (it quotes &quot;{r.quote}&quot;). Only a USB-specific sheet lists it and the Database sheet gives it no signal, so nothing is generated.</span>
            : <span className="muted"> {r.status === "not_usb" ? "Not USB." : r.status === "unclear" ? "Unclear." : `Rejected: ${r.why}`}</span>}
          {r.status === "variant" && (
            <button className="btn btn-small" style={{ marginLeft: 8 }} disabled={running} onClick={() => handleRun(undefined, { excludePins: [r.pin] })}>
              Not for this project: ignore it
            </button>
          )}
          {r.reason ? <div className="muted small">{r.reason}</div> : null}
        </div>
      ))}
      {sgInc.length > 0 && (
        <button className="btn btn-small" onClick={() => handleRun(undefined, { includePins: sgInc.map((r) => r.pin) })} disabled={running}>
          Include the {sgInc.length} suggested pin{sgInc.length === 1 ? "" : "s"} and run again
        </button>
      )}
      {excludePins.length > 0 && (
        <p className="muted" style={{ margin: "8px 0 0" }}>
          Ignored for this project: {excludePins.join(", ")}.{" "}
          <button className="btn-link" disabled={running} onClick={() => setExcludePins([])}>Undo (applies on the next run)</button>
        </p>
      )}
    </div>
  ) : null;

  // Tabs for the code viewer: the deliverable files first, then the other views.
  const fileTabs = (result?.files ?? []).map((f) => ({
    key: "file:" + f.name, label: f.name, text: f.text, file: f.name,
    note: f.role === "pinmux" ? "Pin states under &tlmm. Deliverable file." : "USB controller and regulator nodes that use those pin states. Deliverable file.",
    warn: f.note || "",
  }));
  const overlayText = result?.overlay_text ? "\n\n/* ---- overlay / regulators ---- */\n" + result.overlay_text : "";
  const tabs: { key: string; label: string; text: string; file: string; note?: string; warn?: string }[] = result ? [
    ...fileTabs,
    { key: "final", label: fileTabs.length ? "All in one file" : hy?.final_text ? "Final output" : "Verified DTS",
      text: fileTabs.length ? (result.files ?? []).map((f) => f.text.trim()).join("\n\n") + "\n" : (hy?.final_text ?? (result.out_text + overlayText)),
      file: `${project.toLowerCase()}_usb_final.dtsi`,
      note: fileTabs.length ? "The files above joined into one, in the same format. For reading; the build uses the separate files." : "Everything in one file." },
    { key: "combined", label: "With source comments",
      text: hy?.final_combined_text ?? result.combined_text ?? "", file: `${project.toLowerCase()}_usb_combined.dts`,
      note: "The same result with comments saying where each line came from (Python, template or AI). Always in the default format. Not for the build." },
    ...(hy?.final_text ? [{ key: "python", label: "Python only", text: fileTabs.length ? (result.files ?? []).map((f) => f.text.trim()).join("\n\n") + "\n" : result.out_text + overlayText,
      file: `${project.toLowerCase()}_usb_python_only.dtsi`,
      note: "What Python generated before the wiring style was completed from the accepted example." }] : []),
    { key: "report", label: "Report", text: result.report_text, file: "validation_report.md", note: "Every decision, the format source and the AI usage in full." },
  ].filter((t) => t.text && t.text.trim()) : [];
  const active = tabs.find((t) => t.key === tab) ?? tabs[0];

  const verifierText = ov?.verifier
    ? `${ov.verifier.ok} confirmed, ${ov.verifier.wrong} wrong${ov.verifier.ambiguous ? `, ${ov.verifier.ambiguous} ambiguous` : ""}${ov.verifier.unverifiable ? `, ${ov.verifier.unverifiable} not checkable from the workbook` : ""}`
    : "";
  const statusTone = !ov ? (result && result.validation_problems.length === 0 ? "complete" : "problems") : ov.status === "complete" ? "complete" : ov.status === "problems" ? "problems" : "review";

  return (
    <div className="panel">
      <p className="muted" style={{ marginTop: 0, maxWidth: "64ch" }}>
        Creates the pin-state file and the USB controller file from the workbook, then checks every value against it.
      </p>

      {error && (
        <div className="error-banner">
          <div>{error}</div>
          {error.includes("convention found") && (
            <div className="btn-row" style={{ marginTop: 8 }}>
              <button className="btn btn-small btn-primary" onClick={() => { setPattern("controller"); setError(null); }}>
                Use controller style
              </button>
              <button className="btn btn-small" onClick={() => { setPattern("regulator"); setError(null); }}>
                Use regulator style
              </button>
            </div>
          )}
        </div>
      )}

      <div className="form-grid" style={{ marginBottom: 14 }}>
        <Field label="Linux kernel version" hint="Sets the layout of the pin-state file. Leave empty for the default style.">
          <input type="text" value={kernel} onChange={(e) => setKernel(e.target.value)} placeholder="e.g. 6.6" style={{ maxWidth: 160 }} />
        </Field>
        <Field
          label="AI assistance"
          hint={ai?.configured
            ? "The app generates and checks every line. The AI only suggests, and nothing changes until you confirm."
            : "Not available: set LLM_API_KEY and LLM_DEPLOYMENT in backend/.env and restart the backend."}
        >
          <select value={aiMode} disabled={!ai?.configured} onChange={(e) => setAiMode(e.target.value as "off" | "explain" | "hybrid")}>
            <option value="off">Off (no AI)</option>
            <option value="hybrid">On, recommended</option>
            <option value="explain">Only explain flagged pins</option>
          </select>
        </Field>
      </div>

      <details className="fold">
        <summary>More options <span className="hint">template files, wiring style, your decisions, existing files</span></summary>
        <div className="fold-body">
          <div>
            <h3 className="sub-title" style={{ marginTop: 0 }}>File format</h3>
            <div className="form-grid">
              <Field label="Template files" hint="Your project's pinmux .dtsi or overlay .dts. Only the layout is copied, never pins or names. A template overrides the kernel version.">
                <input type="file" accept=".dts,.dtsi" multiple onChange={(e) => setTemplateFiles(Array.from(e.target.files ?? []))} />
              </Field>
              <Field label="Vendor kernel source" hint="Raw-file URL of your vendor git mirror, used with the kernel version. Empty means mainline Linux.">
                <input type="text" value={kernelBase} onChange={(e) => setKernelBase(e.target.value)} placeholder="e.g. https://git.example.com/raw/vendor-kernel" />
              </Field>
            </div>
          </div>

          <div>
            <h3 className="sub-title" style={{ marginTop: 0 }}>Wiring style</h3>
            <Field label="How VBUS and over-current are wired" hint="Automatic reads it from your files and asks if it cannot tell.">
              <select value={pattern} onChange={(e) => setPattern(e.target.value as "auto" | "controller" | "regulator")} style={{ maxWidth: 420 }}>
                <option value="auto">Automatic</option>
                <option value="controller">Controller: oc-gpio and vbus-out-gpio on &amp;usbN</option>
                <option value="regulator">Regulator: a regulator-fixed node per controller</option>
              </select>
            </Field>
          </div>

          <div>
            <h3 className="sub-title" style={{ marginTop: 0 }}>Your decisions</h3>
            <p className="muted small" style={{ margin: "0 0 10px" }}>Only needed when the results ask for them.</p>
            <div className="form-grid">
              <Field label="Output levels at boot" hint="The workbook has no real boot-level column.">
                <select value={levelSource} onChange={(e) => setLevelSource(e.target.value)}>
                  <option value="convention">Convention: VBUS enables off, others left for review</option>
                  <option value="excel">Excel ResetValue (usually low on every pin)</option>
                  <option value="none">Leave every output level for review</option>
                </select>
              </Field>
              <Field label="Shared VBUS-enable pins" hint="Comma-separated, for example GPIO_12.">
                <input type="text" value={vbusPinText} onChange={(e) => setVbusPinText(e.target.value)} placeholder="GPIO_12" />
              </Field>
              <Field label="Signal roles" hint="For example SIGNAL_A=oc, SIGNAL_B=vbus.">
                <input type="text" value={rolesText} onChange={(e) => setRolesText(e.target.value)} placeholder="SIGNAL_A=oc, SIGNAL_B=vbus" />
              </Field>
            </div>
          </div>

          <div>
            <h3 className="sub-title" style={{ marginTop: 0 }}>Existing files</h3>
            <div className="form-grid">
              <Field label="Existing DTS to update" hint="Leave empty to generate from scratch.">
                <input type="file" onChange={(e) => setDtsFile(e.target.files?.[0] ?? null)} />
              </Field>
              {pattern === "regulator" && (
                <Field label="Overlay .dts" hint="Where the regulator and &usbN blocks go.">
                  <input type="file" onChange={(e) => setOverlayFile(e.target.files?.[0] ?? null)} />
                </Field>
              )}
              {pattern === "regulator" && overlayFile && (
                <Field label="Insert regulators after node">
                  <input type="text" value={insertAfter} onChange={(e) => setInsertAfter(e.target.value)} placeholder="e.g. emrst" />
                </Field>
              )}
            </div>
          </div>

          {aiMode === "hybrid" && ai?.configured && (
            <div>
              <h3 className="sub-title" style={{ marginTop: 0 }}>Extra information for the AI</h3>
              <div className="form-grid">
                <Field label="Documents" hint=".txt or .md (convert PDFs to text first). The app drops any fact whose quote is not in the document.">
                  <input type="file" multiple accept=".txt,.md" onChange={(e) => setDocsFiles(Array.from(e.target.files ?? []))} />
                </Field>
                <Field label="A draft to correct" hint="Yours, a colleague's or Copilot's. It is checked against the workbook first.">
                  <input type="file" accept=".dts,.dtsi,.txt" onChange={(e) => setAiDraftFile(e.target.files?.[0] ?? null)} />
                </Field>
              </div>
              <Field label="Facts you know, one per line" hint="Anything not stated here, in a document or in the workbook stays marked NEEDS REVIEW.">
                <textarea value={facts} onChange={(e) => setFacts(e.target.value)} rows={3} placeholder="gpio82 boots output-low per the schematic" />
              </Field>
            </div>
          )}
        </div>
      </details>

      <div className="btn-row" style={{ marginTop: 20 }}>
        <button className="btn btn-primary" onClick={() => handleRun()} disabled={running}>
          {running ? "Generating and checking…" : "Generate USB files"}
        </button>
      </div>

      {result && (
        <div style={{ marginTop: 36 }} aria-live="polite">
          {/* RESULT AT A GLANCE - every number is counted from the FINAL files, never from an intermediate step */}
          <div className={`status ${statusTone}`}>
            <h3>{ov ? ov.headline : result.validation_problems.length === 0 ? "Generated and verified" : `${result.validation_problems.length} validation problem(s)`}</h3>
            <p>
              {ov
                ? <>
                    {ov.parts.filter((pt) => pt.count > 0).map((pt) => `${pt.count} ${/^[A-Z]{2,}/.test(pt.title) ? pt.title : pt.title.toLowerCase()}`).join(", ") || "Nothing generated"}.
                    {verifierText ? <> Checked against the workbook: {verifierText}.</> : null}
                  </>
                : <>Pattern: {result.pattern}. {result.summary.consumer_nodes} consumer nodes, {result.summary.needs_review} to review.</>}
            </p>
          </div>

          {result.validation_problems.length > 0 && (
            <div className="error-banner">Validation problems: {result.validation_problems.join("; ")}</div>
          )}

          {result.roles_applied !== undefined && splitList(rolesText).length > 0 && result.roles_applied.length === 0 && (
            <div className="error-banner">
              Your signal roles were not applied: the backend that answered is an older version. Stop it (Ctrl+C), start it again and run again.
            </div>
          )}
          {result.included_pins !== undefined && includePins.length > 0 && result.included_pins.length === 0 && (
            <div className="error-banner">
              Your included pins were not applied: the backend that answered is an older version. Stop it (Ctrl+C), start it again and run again.
            </div>
          )}
          {!!result.role_warnings?.length && (
            <div className="note warn">
              <strong>Role against the workbook</strong>
              <ul>{result.role_warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
            </div>
          )}

          {decisions.length > 0 && (
            <div className="decisions">
              <h3>Needs your decision ({decisions.length})</h3>
              <ol>{decisions.map((it, i) => <li key={i}>{it}</li>)}</ol>
              {!!result.sheet_conflict_pins?.length && (
                <button className="btn btn-small" disabled={running} onClick={() => handleRun(undefined, { excludePins: result.sheet_conflict_pins })}>
                  Use the Database sheet for {result.sheet_conflict_pins.join(", ")} and run again
                </button>
              )}
            </div>
          )}

          {ckFound.length > 0 && <div className="ai-block">{checkBlock}</div>}
          {sgHasAction && <div className="ai-block">{suggestBlock}</div>}

          {tabs.length > 0 && active && (
            <div style={{ marginBottom: 24 }}>
              <div className="btn-row" style={{ margin: "0 0 10px", justifyContent: "space-between" }}>
                <h3 className="sub-title" style={{ margin: 0 }}>Your files</h3>
                {result.bundle_zip_b64 && (
                  <button className="btn btn-small" onClick={() => {
                    const bytes = Uint8Array.from(atob(result.bundle_zip_b64 as string), (c) => c.charCodeAt(0));
                    const url = URL.createObjectURL(new Blob([bytes], { type: "application/zip" }));
                    const a = document.createElement("a");
                    a.href = url; a.download = `${project.toLowerCase()}_usb_dts_files.zip`; a.click();
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
                    <button className="btn btn-small" onClick={() => handleCopy(active.text, "tab-" + active.key)}>
                      {copiedKey === "tab-" + active.key ? "Copied" : "Copy"}
                    </button>
                    <button className="btn btn-small" onClick={() => downloadTextAsFile(active.file, active.text)}>Download</button>
                  </span>
                </div>
                {active.note && <div className="code-note">{active.note}</div>}
                {active.warn && <div className="code-note" style={{ color: "var(--warn)" }}>{active.warn}</div>}
                <pre className="dts-preview">{active.text}</pre>
              </div>
            </div>
          )}

          {/* everything technical, collapsed */}
          {cmp && (
            <details className="fold" open>
              <summary>Compared with the previous run</summary>
              <div className="fold-body"><RunComparison cmp={compareRuns(cmp.before, cmp.after)} before={cmp.before.options} after={cmp.after.options} /></div>
            </details>
          )}

          {!!result.ai_ledger?.length && (
            <details className="fold ai">
              <summary>Where the AI was used <span className="hint">{aiCalls} call{aiCalls === 1 ? "" : "s"}, {aiTokens} tokens</span></summary>
              <div className="fold-body">
                <div className="table-scroll">
                  <table className="data-table">
                    <thead><tr><th>Feature</th><th>Setting</th><th>What happened</th><th>Calls</th><th>Tokens</th><th>Effect on the files</th></tr></thead>
                    <tbody>
                      {result.ai_ledger.map((r) => (
                        <tr key={r.feature}><td>{r.label}</td><td>{r.switch}</td><td>{r.status}</td><td>{r.calls}</td><td>{r.tokens}</td><td>{r.effect}</td></tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {ckFound.length === 0 && checkBlock}
                {!sgHasAction && suggestBlock}
              </div>
            </details>
          )}

          <details className="fold">
            <summary>How this was built <span className="hint">wiring style, file format, checks</span></summary>
            <div className="fold-body">
              {ov ? (
                <>
                  <dl className="kv">
                    <dt>Wiring style</dt><dd>{ov.wiring}</dd>
                    <dt>File format</dt><dd>{ov.format_source.length ? ov.format_source.join("; ") : "Default format"}</dd>
                    <dt>Checked</dt><dd>{ov.verifier ? verifierText : "Not checked"}</dd>
                    <dt>AI</dt>
                    <dd>{ov.ai.changed_output
                      ? <>Changed lines in the output. See &quot;With source comments&quot;.</>
                      : <>Wrote none of these files{ov.ai.calls > 0 ? ` (${ov.ai.calls} call${ov.ai.calls === 1 ? "" : "s"}, suggestions only)` : " (not used in this run)"}.</>}</dd>
                    {hy?.verification && (<><dt>Compile check</dt><dd>{hy.verification.dtc}</dd></>)}
                  </dl>
                  <div className="files">
                    {ov.parts.map((pt) => (
                      <div className="file-row" key={pt.key}>
                        <span>
                          <strong>{pt.title}</strong>: {pt.count > 0 ? <>{pt.count} generated in <code>{pt.file}</code>. <span className="muted">{pt.what}.</span></> : <span className="muted">{pt.why_not}</span>}
                        </span>
                        {pt.count > 0 && (
                          <button className="btn btn-small" onClick={() => handleCopy(pt.text, "part-" + pt.key)}>
                            {copiedKey === "part-" + pt.key ? "Copied" : "Copy"}
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                </>
              ) : (
                hy?.verification && (
                  <p style={{ margin: 0 }}>
                    Verifier: {hy.verification.wrong} wrong, {hy.verification.ambiguous} ambiguous, {hy.verification.unverifiable} unverifiable, {hy.verification.ok} confirmed. {hy.verification.dtc}
                  </p>
                )
              )}
              {hy?.template_assumed && !ov && (
                <div className="note warn">
                  <strong>Wiring style assumed.</strong> No project file says how VBUS and over-current are wired, so the output follows the{" "}
                  <strong>{hy.template_pattern}</strong> style of {hy.template_source}.
                  {hy.template_same_soc === false && " That example is for a different SoC, so the evidence is weaker."} Choose a style or add the project's DTS.
                </div>
              )}
              {hy?.ai_note && !ov && (
                <div className="note">The AI wrote none of this output. Its &quot;complete the DTS&quot; step was skipped: {hy.ai_note}.</div>
              )}
              {!!hy?.unsupported?.length && (
                <div className="note warn">
                  <strong>Kept as comments, not active</strong> (not in the workbook, the verified output or your facts):{" "}
                  {hy.unsupported.map((u) => `line ${u.line}: ${u.kind} ${u.token}`).join("; ")}
                </div>
              )}
              {hy?.facts_extracted !== undefined && (hy.facts_extracted.length > 0 || !!hy.facts_rejected || !!hy.facts_note) && (
                <p style={{ margin: 0 }}>
                  <strong>Facts read from your documents:</strong> {hy.facts_extracted.length} accepted, {hy.facts_rejected ?? 0} rejected (quote not in the document)
                  {hy.facts_note ? `. ${hy.facts_note}` : ""}
                </p>
              )}
              {!!hy?.facts_report?.length && (
                <div>
                  <strong>What happened to each fact you gave</strong>
                  <ul style={{ margin: "4px 0 0", paddingLeft: 20 }}>
                    {hy.facts_report.map((f) => (
                      <li key={f.n}><strong>{f.status}</strong>: fact {f.n}, {f.fact} <span className="muted">({f.note})</span></li>
                    ))}
                  </ul>
                </div>
              )}
              {!!hy?.open_findings?.length && (
                <p style={{ margin: 0 }}><strong>Still failing the checks:</strong> {hy.open_findings.slice(0, 6).join("; ")}</p>
              )}
              {!!result.bundle_notes?.length && (
                <ul className="muted" style={{ margin: 0, paddingLeft: 20 }}>{result.bundle_notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
              )}
            </div>
          </details>

          {infoItems.length > 0 && (
            <details className="fold">
              <summary>Left out on purpose ({infoItems.length}) <span className="hint">act only if your BSP needs it</span></summary>
              <div className="fold-body">
                <ul style={{ margin: 0, paddingLeft: 20 }}>{infoItems.map((it, i) => <li key={i} style={{ marginBottom: 4 }}>{it}</li>)}</ul>
              </div>
            </details>
          )}
        </div>
      )}
    </div>
  );
}
