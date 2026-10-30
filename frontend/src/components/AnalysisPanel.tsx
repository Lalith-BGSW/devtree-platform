import { useState } from "react";
import { buildWorkbook, classifySchema } from "../api/client";
import type { EntityRoleConfirmation, ProjectSummary, WorkbookAnalysis } from "../types";

interface Props {
  analysis: WorkbookAnalysis;
  onBuilt: (summaries: ProjectSummary[]) => void;
}

const ROLE_LABEL: Record<string, string> = {
  soc: "SoC",
  project: "Project",
  subgroup: "Part of the SoC",
  unknown: "Not recognized",
};

const ROLE_OPTIONS: { value: EntityRoleConfirmation["role"]; label: string }[] = [
  { value: "soc", label: "SoC (the chip)" },
  { value: "project", label: "Project (one board)" },
  { value: "subgroup", label: "Part of the SoC (register table)" },
  { value: "ignore", label: "Ignore" },
];

export function AnalysisPanel({ analysis, onBuilt }: Props) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [schemaName, setSchemaName] = useState("");
  const [roleByEntity, setRoleByEntity] = useState<Record<string, EntityRoleConfirmation["role"]>>(
    () => Object.fromEntries(
      analysis.entities.map((e) => [
        e.name,
        (e.role === "unknown" ? "ignore" : e.role) as EntityRoleConfirmation["role"],
      ]),
    ),
  );

  async function handleBuild() {
    setError(null);
    setLoading(true);
    try {
      const summaries = await buildWorkbook(analysis.workbook_id);
      onBuilt(summaries);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Build failed.");
    } finally {
      setLoading(false);
    }
  }

  const socCount = Object.values(roleByEntity).filter((r) => r === "soc").length;
  const projectCount = Object.values(roleByEntity).filter((r) => r === "project").length;
  const canConfirm = schemaName.trim().length > 0 && socCount === 1 && projectCount >= 1;

  async function handleConfirmClassification() {
    setError(null);
    setLoading(true);
    try {
      const confirmations: EntityRoleConfirmation[] = Object.entries(roleByEntity).map(
        ([entity, role]) => ({ entity, role }),
      );
      const summaries = await classifySchema(analysis.workbook_id, schemaName.trim(), confirmations);
      onBuilt(summaries);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Classification failed.");
    } finally {
      setLoading(false);
    }
  }

  const rowFor = (e: (typeof analysis.entities)[number]) => (
    <div className="entity-row" key={e.name}>
      <span className="entity-name">{e.name}</span>
      <span className="entity-meta">
        {e.column_count} column{e.column_count === 1 ? "" : "s"}
        {analysis.needs_classification && e.suggestion_reason && (
          <span className="small" style={{ display: "block" }}>
            Suggested: {ROLE_LABEL[e.role] ?? e.role} ({e.suggestion_reason})
          </span>
        )}
      </span>
      {analysis.needs_classification ? (
        <select
          className="role-select"
          aria-label={`Role of ${e.name}`}
          value={roleByEntity[e.name]}
          style={{ width: "auto" }}
          onChange={(ev) =>
            setRoleByEntity((prev) => ({ ...prev, [e.name]: ev.target.value as EntityRoleConfirmation["role"] }))
          }
        >
          {ROLE_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </select>
      ) : (
        <span className={`role-badge ${e.role === "soc" ? "soc" : e.role === "project" ? "project" : ""}`}>{ROLE_LABEL[e.role] ?? e.role}</span>
      )}
    </div>
  );
  // A known layout lists the chip and the projects; the many small register tables are folded away.
  const main = analysis.needs_classification ? analysis.entities : analysis.entities.filter((e) => e.role === "soc" || e.role === "project");
  const minor = analysis.needs_classification ? [] : analysis.entities.filter((e) => !(e.role === "soc" || e.role === "project"));
  const sections = (
    <>
      <div className="entity-list">{main.map(rowFor)}</div>
      {minor.length > 0 && (
        <details className="fold nested" style={{ marginTop: 8 }}>
          <summary>{minor.length} register table{minor.length === 1 ? "" : "s"} <span className="hint">part of the SoC</span></summary>
          <div className="fold-body"><div className="entity-list">{minor.map(rowFor)}</div></div>
        </details>
      )}
    </>
  );

  return (
    <div className="panel">
      <h1 className="page-title">Check the workbook structure</h1>
      <p className="lead">
        {analysis.total_columns} columns found{analysis.hidden_columns > 0 ? `, ${analysis.hidden_columns} of them hidden` : ""}.
        {" "}{analysis.needs_classification
          ? "This layout is new. Tell the app what each section is once; it will recognize the layout next time."
          : "The section roles below come from a layout the app already knows."}
      </p>

      {analysis.schema_match ? (
        <div className="note ok">Known layout: <strong>{analysis.matched_schema_name}</strong></div>
      ) : (
        <div className="note warn">
          <strong>New layout.</strong> It does not match a layout you have confirmed before.
          {analysis.schema_issues.length > 0 && (
            <ul>{analysis.schema_issues.map((issue, i) => <li key={i}>{issue}</li>)}</ul>
          )}
        </div>
      )}

      {error && <div className="error-banner">{error}</div>}

      {analysis.needs_classification && (
        <div className="field" style={{ marginBottom: 20, maxWidth: 420 }}>
          <span>Name this layout</span>
          <span className="help">For example "SA9000X ProjectAlpha".</span>
          <input type="text" value={schemaName} onChange={(e) => setSchemaName(e.target.value)} />
        </div>
      )}

      {sections}

      {analysis.needs_classification && !canConfirm && (
        <p className="muted small" style={{ marginTop: 12 }}>
          Still needed: a layout name, exactly one SoC section ({socCount} chosen) and at least one Project section ({projectCount} chosen).
        </p>
      )}

      <div className="btn-row">
        {analysis.needs_classification ? (
          <button className="btn btn-primary" disabled={loading || !canConfirm} onClick={handleConfirmClassification}>
            {loading ? "Saving…" : "Save and continue"}
          </button>
        ) : (
          <button className="btn btn-primary" disabled={loading || analysis.entities.length === 0} onClick={handleBuild}>
            {loading ? "Preparing…" : "Continue"}
          </button>
        )}
      </div>
    </div>
  );
}
