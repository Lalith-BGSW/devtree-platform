import { useState } from "react";
import { UploadPanel } from "./components/UploadPanel";
import { AnalysisPanel } from "./components/AnalysisPanel";
import { Workspace } from "./components/Workspace";
import type { ProjectSummary, WorkbookAnalysis } from "./types";

type Step = "upload" | "analyze" | "workspace";

const STEPS: { key: Step; label: string }[] = [
  { key: "upload", label: "Upload workbook" },
  { key: "analyze", label: "Check structure" },
  { key: "workspace", label: "Generate files" },
];

export default function App() {
  const [step, setStep] = useState<Step>("upload");
  const [analysis, setAnalysis] = useState<WorkbookAnalysis | null>(null);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);

  function reset() {
    setStep("upload");
    setAnalysis(null);
    setProjects([]);
  }

  const current = STEPS.findIndex((s) => s.key === step);

  return (
    <div className="app-shell">
      <header className="app-header">
        <span className="app-title">PinMux to Device Tree</span>
        {analysis && (
          <span className="app-file">
            <span className="sheet-name">Sheet: {analysis.sheet_name}</span>
            <button className="btn btn-small" onClick={reset}>Start over</button>
          </span>
        )}
      </header>

      <main className="app-main">
        <ol className="steps" aria-label="Progress">
          {STEPS.map((s, i) => (
            <li key={s.key} className={i === current ? "active" : i < current ? "done" : ""} aria-current={i === current ? "step" : undefined}>
              <span className="n">{i < current ? "✓" : i + 1}</span>
              {s.label}
            </li>
          ))}
        </ol>

        {step === "upload" && (
          <UploadPanel
            onAnalyzed={(a) => {
              setAnalysis(a);
              setStep("analyze");
            }}
          />
        )}

        {step === "analyze" && analysis && (
          <AnalysisPanel
            analysis={analysis}
            onBuilt={(summaries) => {
              setProjects(summaries);
              setStep("workspace");
            }}
          />
        )}

        {step === "workspace" && analysis && projects.length > 0 && (
          <Workspace workbookId={analysis.workbook_id} projects={projects} />
        )}
      </main>
    </div>
  );
}
