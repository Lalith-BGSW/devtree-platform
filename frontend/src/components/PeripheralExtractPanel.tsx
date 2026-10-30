import { useState } from "react";
import { fetchPeripheralSummary, fetchPeripheralDts, downloadTextAsFile } from "../api/client";
import type { GenerateDtsResponse } from "../types";

interface Props {
  workbookId: string;
  project: string;
}

/** Parses the simple, self-generated "| a | b | c |" pipe-table format
 * (this app's own md_exporter/peripheral_extractor output) into rows for
 * an actual HTML table - easier to scan than raw pipe syntax, and safe
 * to do with a plain-string parser since the format is fully controlled
 * by our own backend, not arbitrary external Markdown. */
function parsePipeTable(md: string): { headers: string[]; rows: string[][] } {
  // Only the FIRST table block: stop at the first non-table line after it started. (Taking every line that starts with "|"
  // merged a second table's header and separator into this one as data rows, and inflated the pin count.)
  const lines: string[] = [];
  for (const l of md.split("\n")) {
    if (l.trim().startsWith("|")) lines.push(l);
    else if (lines.length > 0) break;
  }
  if (lines.length < 2) return { headers: [], rows: [] };
  const splitRow = (line: string) =>
    line.split("|").slice(1, -1).map((c) => c.trim().replace(/\\\|/g, "|"));
  const headers = splitRow(lines[0]);
  const rows = lines.slice(2).map(splitRow); // lines[1] is the |---|---| separator
  return { headers, rows };
}

export function PeripheralExtractPanel({ workbookId, project }: Props) {
  const [keyword, setKeyword] = useState("usb");
  const [loadingSummary, setLoadingSummary] = useState(false);
  const [loadingDts, setLoadingDts] = useState(false);
  const [summaryMd, setSummaryMd] = useState<string | null>(null);
  const [matchedCount, setMatchedCount] = useState<number | null>(null);
  const [dts, setDts] = useState<GenerateDtsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleExtract() {
    setLoadingSummary(true);
    setError(null);
    setSummaryMd(null);
    setDts(null);
    try {
      const { mdText, matchedCount } = await fetchPeripheralSummary(workbookId, project, keyword);
      setSummaryMd(mdText);
      setMatchedCount(matchedCount);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Extraction failed.");
    } finally {
      setLoadingSummary(false);
    }
  }

  async function handleGenerateDts() {
    setLoadingDts(true);
    setError(null);
    try {
      const resp = await fetchPeripheralDts(workbookId, project, keyword);
      setDts(resp);
    } catch (e) {
      setError(e instanceof Error ? e.message : "DTS generation failed.");
    } finally {
      setLoadingDts(false);
    }
  }

  const table = summaryMd ? parsePipeTable(summaryMd) : null;
  const relatedIdx = summaryMd ? summaryMd.indexOf("### Related pins") : -1;
  const related = summaryMd && relatedIdx >= 0 ? parsePipeTable(summaryMd.slice(relatedIdx)) : null;

  return (
    <div className="panel">
      <h2 className="panel-title">Find pins by signal name</h2>
      <p className="muted" style={{ marginTop: 0, maxWidth: "64ch" }}>
        Type part of a signal name to list the matching pins. Check the list, then generate a Device Tree for just those pins.
      </p>

      {error && <div className="error-banner">{error}</div>}

      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 16 }}>
        <input
          type="text"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          placeholder="usb"
          aria-label="Signal name contains" style={{ flex: "0 0 240px" }}
        />
        <button className="btn btn-primary" onClick={handleExtract} disabled={loadingSummary || !keyword.trim()}>
          {loadingSummary ? "Searching…" : "Find pins"}
        </button>
      </div>

      {/* Stage 1: Markdown summary */}
      {table && (
        <div style={{ marginBottom: 20 }}>
          <div className="muted" style={{ marginBottom: 8 }}>
            {matchedCount} pin{matchedCount === 1 ? "" : "s"} match "{keyword}"
          </div>
          {table.rows.length > 0 ? (
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    {table.headers.map((h) => (
                      <th key={h}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {table.rows.map((row, i) => (
                    <tr key={i}>
                      {row.map((cell, j) => (
                        <td key={j}>{cell}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div style={{ color: "var(--text-muted)", fontSize: 13 }}>
              No pins matched "{keyword}" against Gen5 Signal Name.
            </div>
          )}

          {related && related.rows.length > 0 && (
            <details className="fold" style={{ marginTop: 12 }}>
              <summary>
                {related.rows.length} related pin{related.rows.length === 1 ? "" : "s"} <span className="hint">same interface group, name does not contain "{keyword}"; not in the Device Tree</span>
              </summary>
              <div className="table-scroll" style={{ marginTop: 8 }}>
                <table className="data-table">
                  <thead>
                    <tr>{related.headers.map((h) => <th key={h}>{h}</th>)}</tr>
                  </thead>
                  <tbody>
                    {related.rows.map((row, i) => (
                      <tr key={i}>{row.map((cell, j) => <td key={j}>{cell}</td>)}</tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          )}

          {table.rows.length > 0 && keyword.trim().toLowerCase().includes("usb") && (
            <p className="muted" style={{ marginTop: 16 }}>
              For USB, use the <strong>Generate USB files</strong> tab. It also wires the controllers, flags every output level it had to assume and verifies the result.
            </p>
          )}
          {table.rows.length > 0 && !keyword.trim().toLowerCase().includes("usb") && (
            <button className="btn btn-primary" onClick={handleGenerateDts} disabled={loadingDts} style={{ marginTop: 16 }}>
              {loadingDts ? "Generating…" : `Generate Device Tree for these ${table.rows.length} pins`}
            </button>
          )}
        </div>
      )}

      {/* Stage 2: scoped DTS, only shown after the summary stage and after generation runs */}
      {dts && (
        <div>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
            <span className="muted">
              Generated from {dts.used_records.length} pin{dts.used_records.length === 1 ? "" : "s"}
            </span>
            <button
              className="btn btn-small"
              onClick={() => downloadTextAsFile(`${project.toLowerCase()}_${keyword}.dtsi`, dts.dts_text)}
            >
              Download .dtsi
            </button>
          </div>
          <div className="code-wrap"><pre className="dts-preview">{dts.dts_text}</pre></div>
          {dts.validation.length > 0 && (
            <div className="muted small" style={{ marginTop: 8 }}>
              {dts.validation.length} validation note{dts.validation.length === 1 ? "" : "s"}.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
