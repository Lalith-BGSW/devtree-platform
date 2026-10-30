import type {
  WorkbookAnalysis, ProjectSummary, ChatQueryResponse, GenerateDtsResponse,
  DtsBatchUpdateResponse,
  EntityRoleConfirmation, UsbToolRunResponse, UsbToolVerifyResponse, EthToolResponse, SpiToolResponse, UartToolResponse, I2cToolResponse, CombinedToolResponse, GenericPreviewResponse,
} from "../types";

const BASE = "/api";

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export async function uploadWorkbook(file: File): Promise<WorkbookAnalysis> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${BASE}/workbook/upload`, { method: "POST", body: form });
  return handle<WorkbookAnalysis>(res);
}

export async function buildWorkbook(workbookId: string): Promise<ProjectSummary[]> {
  const res = await fetch(`${BASE}/workbook/${workbookId}/build`, { method: "POST" });
  return handle<ProjectSummary[]>(res);
}

export async function classifySchema(
  workbookId: string, schemaName: string, roleConfirmations: EntityRoleConfirmation[],
): Promise<ProjectSummary[]> {
  const res = await fetch(`${BASE}/workbook/${workbookId}/classify-schema`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      workbook_id: workbookId, schema_name: schemaName, role_confirmations: roleConfirmations,
    }),
  });
  return handle<ProjectSummary[]>(res);
}

export async function queryProject(
  workbookId: string, project: string, message: string,
): Promise<ChatQueryResponse> {
  const res = await fetch(`${BASE}/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ workbook_id: workbookId, project, message }),
  });
  return handle<ChatQueryResponse>(res);
}

export async function generateDts(
  workbookId: string, project: string, query: string,
): Promise<GenerateDtsResponse> {
  const res = await fetch(`${BASE}/generate-dts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ workbook_id: workbookId, project, query }),
  });
  return handle<GenerateDtsResponse>(res);
}

export function downloadDtsUrl(workbookId: string, project: string): string {
  return `${BASE}/download-dts/${workbookId}/${project}`;
}

export function exportRecordsUrl(workbookId: string, project?: string): string {
  return project
    ? `${BASE}/workbook/${workbookId}/export-records/${project}`
    : `${BASE}/workbook/${workbookId}/export-records`;
}

export async function batchUpdateDts(
  workbookId: string, project: string, files: File[], outputFilenames: string[],
  useAiFallback: boolean = false,
): Promise<DtsBatchUpdateResponse> {
  const form = new FormData();
  form.append("project", project);
  form.append("output_filenames", JSON.stringify(outputFilenames));
  form.append("use_ai_fallback", String(useAiFallback));
  for (const f of files) form.append("files", f);
  const res = await fetch(`${BASE}/workbook/${workbookId}/dts-update`, {
    method: "POST",
    body: form,
  });
  return handle<DtsBatchUpdateResponse>(res);
}

/** Triggers a client-side download of in-memory text content under a
 * given filename - needed because updated/remainder DTS content comes
 * back as JSON strings, not a server-streamed file. */
export function downloadTextAsFile(filename: string, content: string, mimeType: string = "text/plain"): void {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export function exportMdUrl(workbookId: string, project: string): string {
  return `${BASE}/workbook/${workbookId}/export-md/${encodeURIComponent(project)}`;
}

export async function fetchPeripheralSummary(
  workbookId: string, project: string, keyword: string,
): Promise<{ mdText: string; matchedCount: number }> {
  const res = await fetch(
    `${BASE}/workbook/${workbookId}/peripheral/${encodeURIComponent(project)}/summary?keyword=${encodeURIComponent(keyword)}`,
  );
  if (!res.ok) throw new Error(await res.text());
  const mdText = await res.text();
  const matchedCount = Number(res.headers.get("X-Matched-Count") ?? "0");
  return { mdText, matchedCount };
}

export async function fetchPeripheralDts(
  workbookId: string, project: string, keyword: string,
): Promise<GenerateDtsResponse> {
  const res = await fetch(
    `${BASE}/workbook/${workbookId}/peripheral/${encodeURIComponent(project)}/dts?keyword=${encodeURIComponent(keyword)}`,
  );
  return handle<GenerateDtsResponse>(res);
}

export async function usbToolProjects(workbookId: string): Promise<{ projects: string[] }> {
  const res = await fetch(`${BASE}/workbook/${workbookId}/usb-tool/projects`);
  return handle(res);
}

export interface UsbToolRunOptions {
  project: string;
  pattern?: "auto" | "controller" | "regulator";
  vbusPin?: string[];
  insertAfter?: string;
  noUpdate?: boolean;
  aiAdvice?: boolean;
  aiPropose?: boolean;
  roles?: string[];
  includePins?: string[];
  excludePins?: string[];
  templateFiles?: File[];
  aiSuggest?: boolean;
  levelSource?: string;
  kernel?: string;
  kernelBase?: string;
  facts?: string;
  aiDraftFile?: File | null;
  docsFiles?: File[];
  dtsFile?: File | null;
  overlayFile?: File | null;
}

export async function usbToolRun(workbookId: string, opts: UsbToolRunOptions): Promise<UsbToolRunResponse> {
  const form = new FormData();
  form.append("project", opts.project);
  form.append("pattern", opts.pattern ?? "auto");
  (opts.vbusPin ?? []).forEach((v) => form.append("vbus_pin", v));
  if (opts.insertAfter) form.append("insert_after", opts.insertAfter);
  form.append("no_update", String(opts.noUpdate ?? false));
  form.append("ai_advice", String(opts.aiAdvice ?? false));
  form.append("ai_propose", String(opts.aiPropose ?? false));
  (opts.roles ?? []).forEach((v) => form.append("roles", v));
  (opts.includePins ?? []).forEach((v) => form.append("include_pins", v));
  (opts.excludePins ?? []).forEach((v) => form.append("exclude_pins", v));
  (opts.templateFiles ?? []).forEach((f) => form.append("templates", f));
  form.append("ai_suggest", String(opts.aiSuggest ?? false));
  form.append("level_source", opts.levelSource ?? "convention");
  if (opts.kernel && opts.kernel.trim()) form.append("kernel", opts.kernel.trim());
  if (opts.kernelBase && opts.kernelBase.trim()) form.append("kernel_base", opts.kernelBase.trim());
  form.append("facts", opts.facts ?? "");
  if (opts.aiDraftFile) form.append("ai_draft", opts.aiDraftFile);
  (opts.docsFiles ?? []).forEach((f) => form.append("docs", f));
  if (opts.dtsFile) form.append("dts", opts.dtsFile);
  if (opts.overlayFile) form.append("overlay", opts.overlayFile);
  const res = await fetch(`${BASE}/workbook/${workbookId}/usb-tool/update-or-generate`, {
    method: "POST", body: form,
  });
  return handle<UsbToolRunResponse>(res);
}

export async function usbToolVerify(
  workbookId: string, project: string, dtsFile: File, existingFiles: File[] = [], allPins = false,
): Promise<UsbToolVerifyResponse> {
  const form = new FormData();
  form.append("project", project);
  form.append("all_pins", String(allPins));
  form.append("dts", dtsFile);
  existingFiles.forEach((f) => form.append("existing", f));
  const res = await fetch(`${BASE}/workbook/${workbookId}/usb-tool/verify`, {
    method: "POST", body: form,
  });
  return handle<UsbToolVerifyResponse>(res);
}

export interface AiStatus {
  configured: boolean;
  deployment: string | null;
  features: string[];
  key_present: boolean;
}

export async function aiStatus(): Promise<AiStatus> {
  const res = await fetch(`${BASE}/ai/status`);
  return handle<AiStatus>(res);
}

export interface EthToolOptions {
  project: string;
  families: string[];
  includePins?: string[];
  excludePins?: string[];
  aiCheck?: boolean;
  kernel?: string;
  kernelBase?: string;
  templateFiles?: File[];
}

export async function ethToolRun(workbookId: string, o: EthToolOptions): Promise<EthToolResponse> {
  const form = new FormData();
  form.append("project", o.project);
  o.families.forEach((f) => form.append("families", f));
  (o.includePins ?? []).forEach((v) => form.append("include_pins", v));
  (o.excludePins ?? []).forEach((v) => form.append("exclude_pins", v));
  form.append("ai_check", String(o.aiCheck ?? false));
  if (o.kernel && o.kernel.trim()) form.append("kernel", o.kernel.trim());
  if (o.kernelBase && o.kernelBase.trim()) form.append("kernel_base", o.kernelBase.trim());
  (o.templateFiles ?? []).forEach((f) => form.append("templates", f));
  const res = await fetch(`${BASE}/workbook/${workbookId}/eth-tool/generate`, { method: "POST", body: form });
  return handle<EthToolResponse>(res);
}

export async function spiToolRun(workbookId: string, o: EthToolOptions): Promise<SpiToolResponse> {
  const form = new FormData();
  form.append("project", o.project);
  o.families.forEach((f) => form.append("families", f));
  (o.includePins ?? []).forEach((v) => form.append("include_pins", v));
  (o.excludePins ?? []).forEach((v) => form.append("exclude_pins", v));
  form.append("ai_check", String(o.aiCheck ?? false));
  if (o.kernel && o.kernel.trim()) form.append("kernel", o.kernel.trim());
  if (o.kernelBase && o.kernelBase.trim()) form.append("kernel_base", o.kernelBase.trim());
  (o.templateFiles ?? []).forEach((f) => form.append("templates", f));
  const res = await fetch(`${BASE}/workbook/${workbookId}/spi-tool/generate`, { method: "POST", body: form });
  return handle<SpiToolResponse>(res);
}

export async function uartToolRun(workbookId: string, o: EthToolOptions): Promise<UartToolResponse> {
  const form = new FormData();
  form.append("project", o.project);
  o.families.forEach((f) => form.append("families", f));
  (o.includePins ?? []).forEach((v) => form.append("include_pins", v));
  (o.excludePins ?? []).forEach((v) => form.append("exclude_pins", v));
  form.append("ai_check", String(o.aiCheck ?? false));
  if (o.kernel && o.kernel.trim()) form.append("kernel", o.kernel.trim());
  if (o.kernelBase && o.kernelBase.trim()) form.append("kernel_base", o.kernelBase.trim());
  (o.templateFiles ?? []).forEach((f) => form.append("templates", f));
  const res = await fetch(`${BASE}/workbook/${workbookId}/uart-tool/generate`, { method: "POST", body: form });
  return handle<UartToolResponse>(res);
}

export async function i2cToolRun(workbookId: string, o: EthToolOptions): Promise<I2cToolResponse> {
  const form = new FormData();
  form.append("project", o.project);
  o.families.forEach((f) => form.append("families", f));
  (o.includePins ?? []).forEach((v) => form.append("include_pins", v));
  (o.excludePins ?? []).forEach((v) => form.append("exclude_pins", v));
  form.append("ai_check", String(o.aiCheck ?? false));
  if (o.kernel && o.kernel.trim()) form.append("kernel", o.kernel.trim());
  if (o.kernelBase && o.kernelBase.trim()) form.append("kernel_base", o.kernelBase.trim());
  (o.templateFiles ?? []).forEach((f) => form.append("templates", f));
  const res = await fetch(`${BASE}/workbook/${workbookId}/i2c-tool/generate`, { method: "POST", body: form });
  return handle<I2cToolResponse>(res);
}

export interface CombinedOptions {
  project: string;
  peripherals: string[];
  usbPattern?: string;
  kernel?: string;
  kernelBase?: string;
  templateFiles?: File[];
}

export async function combinedRun(workbookId: string, o: CombinedOptions): Promise<CombinedToolResponse> {
  const form = new FormData();
  form.append("project", o.project);
  o.peripherals.forEach((p) => form.append("peripherals", p));
  form.append("usb_pattern", o.usbPattern || "auto");
  if (o.kernel && o.kernel.trim()) form.append("kernel", o.kernel.trim());
  if (o.kernelBase && o.kernelBase.trim()) form.append("kernel_base", o.kernelBase.trim());
  (o.templateFiles ?? []).forEach((f) => form.append("templates", f));
  const res = await fetch(`${BASE}/workbook/${workbookId}/combined/generate`, { method: "POST", body: form });
  return handle<CombinedToolResponse>(res);
}

export async function genericPreviewRun(workbookId: string, project: string, aiDraft: boolean): Promise<GenericPreviewResponse> {
  const form = new FormData();
  form.append("project", project);
  form.append("ai_draft", String(aiDraft));
  const res = await fetch(`${BASE}/workbook/${workbookId}/generic-tool/generate`, { method: "POST", body: form });
  return handle<GenericPreviewResponse>(res);
}
