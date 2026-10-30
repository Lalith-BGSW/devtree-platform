import { useRef, useState } from "react";
import { uploadWorkbook } from "../api/client";
import type { WorkbookAnalysis } from "../types";

interface Props {
  onAnalyzed: (analysis: WorkbookAnalysis) => void;
}

export function UploadPanel({ onAnalyzed }: Props) {
  const [dragActive, setDragActive] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  async function handleFile(file: File) {
    setError(null);
    setLoading(true);
    try {
      const analysis = await uploadWorkbook(file);
      onAnalyzed(analysis);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="panel">
      <h1 className="page-title">Upload your pinmux workbook</h1>
      <p className="lead">The Excel file is read once. Everything after this uses it to generate and check Device Tree files.</p>
      {error && <div className="error-banner">{error}</div>}
      <div
        className={`dropzone ${dragActive ? "drag-active" : ""}`}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDragActive(true); }}
        onDragLeave={() => setDragActive(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragActive(false);
          const file = e.dataTransfer.files?.[0];
          if (file) handleFile(file);
        }}
      >
        <div className="dropzone-label">
          {loading ? "Reading the workbook…" : "Drop the workbook here, or click to choose a file"}
        </div>
        <div className="dropzone-hint">
          Excel files (.xlsx or .xlsm)
        </div>
        <input
          ref={inputRef}
          type="file"
          accept=".xlsm,.xlsx"
          style={{ display: "none" }}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) handleFile(file);
          }}
        />
      </div>
    </div>
  );
}
