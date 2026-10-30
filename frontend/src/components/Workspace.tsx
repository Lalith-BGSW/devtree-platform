import { useState } from "react";
import { downloadDtsUrl, downloadTextAsFile, exportRecordsUrl, exportMdUrl, generateDts } from "../api/client";
import type { GenerateDtsResponse, ProjectSummary } from "../types";
import { DtsUpdatePanel } from "./DtsUpdatePanel";
import { PeripheralExtractPanel } from "./PeripheralExtractPanel";
import { UsbToolPanel } from "./UsbToolPanel";
import { EthToolPanel } from "./EthToolPanel";
import { SpiToolPanel } from "./SpiToolPanel";
import { UartToolPanel } from "./UartToolPanel";
import { I2cToolPanel } from "./I2cToolPanel";
import { CombinedPanel } from "./CombinedPanel";

interface Props {
  workbookId: string;
  projects: ProjectSummary[];
}

type Task = "usb" | "eth" | "spi" | "uart" | "i2c" | "all" | "update" | "other" | "export";

const TASKS: { key: Task; label: string }[] = [
  { key: "usb", label: "Generate USB files" },
  { key: "eth", label: "Generate Ethernet files" },
  { key: "spi", label: "Generate SPI files" },
  { key: "uart", label: "Generate UART files" },
  { key: "i2c", label: "Generate I2C files" },
  { key: "all", label: "Combined DTS (all)" },
  { key: "update", label: "Update or check files" },
  { key: "other", label: "Other peripherals" },
  { key: "export", label: "Export data" },
];

export function Workspace({ workbookId, projects }: Props) {
  const [activeProject, setActiveProject] = useState(projects[0]?.project ?? "");
  const [task, setTask] = useState<Task>("usb");

  const [dts, setDts] = useState<GenerateDtsResponse | null>(null);
  const [generating, setGenerating] = useState(false);
  const [genQuery, setGenQuery] = useState("full");
  const [error, setError] = useState<string | null>(null);

  const current = projects.find((p) => p.project === activeProject);

  function switchProject(p: string) {
    setActiveProject(p);
    setDts(null);
    setError(null);
  }

  async function handleGenerate() {
    setGenerating(true);
    setError(null);
    try {
      setDts(await generateDts(workbookId, activeProject, genQuery || "full"));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Generation failed.");
    } finally {
      setGenerating(false);
    }
  }

  // Each task stays mounted while hidden, so switching tabs never throws away a result.
  const show = (t: Task) => ({ display: task === t ? undefined : ("none" as const) });

  return (
    <div>
      {projects.length > 1 ? (
        <div className="project-tabs" role="tablist" aria-label="Project">
          {projects.map((p) => (
            <button
              key={p.project}
              role="tab"
              aria-selected={p.project === activeProject}
              className={`project-tab ${p.project === activeProject ? "active" : ""}`}
              onClick={() => switchProject(p.project)}
            >
              {p.project}
            </button>
          ))}
          {current && <span className="muted" style={{ alignSelf: "center" }}>{current.record_count} pins</span>}
        </div>
      ) : (
        <h1 className="page-title">
          {activeProject}
          {current ? <span className="muted" style={{ fontWeight: 400, fontSize: 16 }}> &nbsp;{current.record_count} pins</span> : null}
        </h1>
      )}

      <div className="tabs" role="tablist" aria-label="What do you want to do?">
        {TASKS.map((t) => (
          <button
            key={t.key}
            role="tab"
            aria-selected={task === t.key}
            className={`tab ${task === t.key ? "active" : ""}`}
            onClick={() => setTask(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div style={show("usb")}>
        <UsbToolPanel key={activeProject} workbookId={workbookId} project={activeProject} />
      </div>

      <div style={show("eth")}>
        <EthToolPanel key={activeProject} workbookId={workbookId} project={activeProject} />
      </div>

      <div style={show("spi")}>
        <SpiToolPanel key={activeProject} workbookId={workbookId} project={activeProject} />
      </div>

      <div style={show("uart")}>
        <UartToolPanel key={activeProject} workbookId={workbookId} project={activeProject} />
      </div>

      <div style={show("i2c")}>
        <I2cToolPanel key={activeProject} workbookId={workbookId} project={activeProject} />
      </div>

      <div style={show("all")}>
        <CombinedPanel key={activeProject} workbookId={workbookId} project={activeProject} />
      </div>

      <div style={show("update")}>
        <DtsUpdatePanel key={activeProject} workbookId={workbookId} project={activeProject} />
      </div>

      <div style={show("other")}>
        <PeripheralExtractPanel key={activeProject} workbookId={workbookId} project={activeProject} />

        <hr className="divider" />

        <div className="panel">
          <h2 className="panel-title">Draft for any signal group</h2>
          <p className="muted" style={{ marginTop: 0 }}>
            Type a word such as "bluetooth uart", or "full" for the whole project. Output levels are left for you to state.
          </p>
          {error && <div className="error-banner">{error}</div>}
          <div className="chat-input-row" style={{ marginBottom: 16 }}>
            <input
              type="text"
              aria-label="Signal group"
              value={genQuery}
              onChange={(e) => setGenQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleGenerate()}
            />
            <button className="btn btn-primary" onClick={handleGenerate} disabled={generating}>
              {generating ? "Generating…" : "Generate"}
            </button>
          </div>

          {dts && (
            <>
              <div className="code-wrap">
                <pre className="dts-preview">{dts.dts_text}</pre>
              </div>
              {dts.validation.length > 0 && (
                <div className="validation-list">
                  {dts.validation.map((v, i) => (
                    <div key={i} className={`validation-item ${v.severity}`}>
                      <span className="rule">{v.rule}</span>
                      <span>{v.message}</span>
                    </div>
                  ))}
                </div>
              )}
              <div className="btn-row">
                <a className="btn btn-primary" href={downloadDtsUrl(workbookId, activeProject)} download={`${activeProject.toLowerCase()}.dtsi`}>
                  Download .dtsi
                </a>
                {dts.review_report && (
                  <button
                    className="btn"
                    onClick={() => downloadTextAsFile(`${activeProject.toLowerCase()}_review_report.html`, dts.review_report, "text/html")}
                  >
                    Download review report
                  </button>
                )}
              </div>
            </>
          )}
        </div>
      </div>

      <div style={show("export")}>
        <div className="panel">
          <h2 className="panel-title">Export the workbook data</h2>
          <p className="muted" style={{ marginTop: 0 }}>The parsed pin records, for other tools or for reading.</p>
          <div className="files">
            <div className="file-row">
              <span>{activeProject} pin records (JSON)</span>
              <a className="btn btn-small" href={exportRecordsUrl(workbookId, activeProject)} download={`${activeProject.toLowerCase()}_records.json`}>Download</a>
            </div>
            <div className="file-row">
              <span>{activeProject} pinmux reference (Markdown)</span>
              <a className="btn btn-small" href={exportMdUrl(workbookId, activeProject)} download={`${activeProject.toLowerCase()}_database_export.md`}>Download</a>
            </div>
            <div className="file-row">
              <span>All projects (JSON)</span>
              <a className="btn btn-small" href={exportRecordsUrl(workbookId)} download="workbook_records.json">Download</a>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
