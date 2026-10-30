export interface ColumnMeta {
  index: number;
  excel_letter: string;
  group_label: string | null;
  field_label: string | null;
  base_field: string | null;
  entity: string | null;
  slug: string | null;
  category: string | null;
  is_hidden: boolean;
  known: boolean;
}

export interface EntitySummary {
  name: string;
  role: "soc" | "project" | "subgroup" | "unknown";
  columns: string[];
  column_count: number;
  role_confirmed: boolean;
  suggestion_reason?: string | null;
}

export interface EntityRoleConfirmation {
  entity: string;
  role: "soc" | "project" | "subgroup" | "ignore";
}

export interface WorkbookAnalysis {
  workbook_id: string;
  sheet_name: string;
  total_columns: number;
  hidden_columns: number;
  header_row: number;
  data_start_row: number;
  entities: EntitySummary[];
  columns: ColumnMeta[];
  schema_match: boolean;
  schema_issues: string[];
  matched_schema_name: string | null;
  needs_classification: boolean;
}

export interface ProjectSummary {
  project: string;
  soc: string;
  record_count: number;
  fields: string[];
}

export interface NormalizedRecord {
  project: string;
  soc: string;
  row_index: number;
  fields: Record<string, unknown>;
}

export interface ValidationIssue {
  severity: "error" | "warning";
  rule: string;
  project: string;
  message: string;
  row_index: number | null;
}

export interface ChatQueryResponse {
  intent: Record<string, unknown>;
  matched_records: NormalizedRecord[];
  total_matches: number;
  answer: string;
}

export interface GenerateDtsResponse {
  project: string;
  dts_text: string;
  used_records: NormalizedRecord[];
  validation: ValidationIssue[];
  review_report: string;
}

export interface PinChangeOut {
  pin: string;
  field: string;
  old_value: string | null;
  new_value: string;
}

export interface NodeUpdateOut {
  label: string;
  changed: boolean;
  changes: PinChangeOut[];
  unmatched_pins: string[];
  structural_issue: string | null;
  review_note: string | null;
  ai_assisted: boolean;
  ai_advisory: string | null;
}

export interface DtsUpdateFileResult {
  original_filename: string;
  output_filename: string;
  updated_text: string;
  format_role: string;
  recognized_pin_state_count: number;
  unrecognized_pin_state_count: number;
  format_summary: string;
  format_note: string;
  ai_fallback_requested: boolean;
  ai_candidate_count: number;
  ai_extraction_failure_count: number;
  ai_fallback_status: string;
  node_results: NodeUpdateOut[];
  changed_node_count: number;
  unmatched_node_count: number;
  structural_issue_count: number;
}

export interface DtsBatchUpdateResponse {
  project: string;
  files: DtsUpdateFileResult[];
  remainder_dts_text: string;
  remainder_pin_count: number;
  total_excel_signal_pins: number;
  total_covered_pins: number;
  ambiguous_pin_count?: number;
  review_report: string;
}

export interface UsbToolSummary {
  updated: number;
  consumer_nodes: number;
  regulators: number;
  new_states: number;
  other_gpio: number;
  unsupported_controller: number;
  unconfirmed_controller: number;
  needs_review: number;
}

export interface AiProposal {
  status: "clean" | "needs_attention" | "unavailable" | "failed" | "empty";
  reason?: string;
  error?: string | null;
  text?: string;
  final_text?: string;
  final_combined_text?: string | null;
  template_assumed?: boolean;
  template_pattern?: string;
  template_source?: string;
  template_same_soc?: boolean;
  open_items?: string[];
  open_item_kinds?: string[];
  ai_note?: string | null;
  combined_changes?: number;
  facts_report?: { n: number; fact: string; status: string; note: string }[];
  facts_extracted?: string[];
  facts_rejected?: number;
  facts_note?: string | null;
  rounds?: { round: number; source: string; problems: number }[];
  verification?: { wrong: number; ambiguous: number; unverifiable: number; ok: number; dtc: string; changed_verified_lines: number };
  open_findings?: string[];
  unsupported?: { line: number; kind: string; token: string }[];
  unverifiable_items?: string[];
  diff?: string;
  usage?: { calls: number; prompt_tokens: number; completion_tokens: number };
}

export interface AiRoleRow { pin: string; tail: string; role: string; quote: string; reason: string; status: string; why: string; role_arg: string }
export interface AiAuditRow { pin: string; verdict: string; quote: string; reason: string; status: string; why: string }
export interface AiSuggest {
  roles: { status: string; rows: AiRoleRow[]; note?: string; candidates?: { pin: string; tail: string }[] };
  audit: { status: string; rows: AiAuditRow[]; pads: { pin: string }[]; note?: string; candidates?: { pin: string }[] };
}

export interface AiCheckRow { pin: string; type: string; quote_a: string; quote_b: string; issue: string; status: string; why: string }
export interface AiCheck { status: "none" | "ok" | "unavailable" | "error"; rows: AiCheckRow[]; checked: number; note: string }

export interface UsbToolRunResponse {
  ai_check?: AiCheck | null;
  ai_suggest?: AiSuggest | null;
  included_pins?: string[];
  excluded_pins?: string[];
  sheet_conflict_pins?: string[];
  role_warnings?: string[];
  files?: { name: string; role: string; text: string; style_source: string; style_summary: string; note: string }[];
  bundle_notes?: string[];
  overview?: {
    status: "complete" | "needs_decisions" | "problems"; headline: string;
    parts: { key: "regulators" | "pin_states" | "usb_nodes"; title: string; what: string; count: number; file: string | null; text: string; why_not: string | null }[];
    pin_states: number; consumers: string[]; regulators: number;
    review_lines: number; open_decisions: number; open_info: number; wiring: string; files: string[]; format_source: string[];
    verifier: { wrong: number; ambiguous: number; unverifiable: number; ok: number } | null;
    ai: { changed_output: boolean; ran: string[]; calls: number; tokens: number };
  } | null;
  kernel_resolution?: { version: string; kind: "fetched" | "table" | "none"; source: string; notes: string[] } | null;
  bundle_zip_b64?: string | null;
  ai_ledger?: { feature: string; label: string; switch: string; status: string; calls: number; tokens: number; effect: string }[];
  ai_proposal?: AiProposal | null;
  roles_applied?: string[];
  out_text: string;
  report_text: string;
  overlay_text: string | null;
  inventory_text: string | null;
  combined_text: string | null;
  pattern: string;
  pattern_evidence: string;
  summary: UsbToolSummary;
  validation_problems: string[];
  dtc_result: string | null;
  exit_code: number;
}

export interface UsbToolVerifyCounts {
  wrong: number;
  ambiguous: number;
  unverifiable: number;
  ok: number;
}

export interface UsbToolVerifyResponse {
  report_text: string;
  exit_code: number;
  counts: UsbToolVerifyCounts;
}

export interface EthPin { pin: string; gpio: string; label: string; function: string; mux: number | null; table_mux: number | null; signal: string; family: string; status: string; selected_as: string; drive: string | null; bias: string | null }
export interface SpiPin extends EthPin { role: string; instance: string | null; n: number | null }
export type SpiToolResponse = Omit<EthToolResponse, "pins"> & { pins: SpiPin[] };
export type UartToolResponse = Omit<EthToolResponse, "pins"> & { pins: SpiPin[] };
export type I2cToolResponse = Omit<EthToolResponse, "pins"> & { pins: SpiPin[] };
export interface EthLeftOut {
  pin: string; signal: string; family: string; reason: string;
  gpio?: string; tier?: "likely" | "possible"; evidence?: { column: string; value: string; stale: boolean }[];
  ai?: { verdict: "ethernet_gpio" | "not_ethernet" | "spi_line" | "not_spi" | "unclear"; role: string; quote: string; reason: string; note: string };
}
export interface EthToolResponse {
  ai_facts?: { pin: string; topic: string; value: string; quote: string }[];
  overview: {
    status: "complete" | "needs_decisions" | "problems" | "nothing"; headline: string; pin_states: number; left_out: number; review_lines: number;
    open_decisions: number; families: Record<string, number>; enabled: string[]; files: string[]; controller: string | null; soc_table: string | null;
    verifier: { wrong: number; ambiguous: number; unverifiable: number; ok: number };
  };
  pins: EthPin[];
  left_out: EthLeftOut[];
  items: { kind: "decision" | "info"; text: string }[];
  counts: Record<string, number>;
  files: { name: string; role: string; text: string; style_source: string; style_summary: string; note: string }[];
  bundle_notes: string[];
  kernel_resolution?: { version: string; kind: string; source: string; notes: string[] } | null;
  verification: { wrong: string[]; ambiguous: string[]; unverifiable: string[]; ok: number };
  validation_problems: string[];
  dtc_result: string | null;
  report_text: string;
  column_hits?: Record<string, number>;
  ai_check?: AiCheck | null;
  ai_ledger?: { feature: string; label: string; switch: string; status: string; calls: number; tokens: number; effect: string }[];
  soc: string | null;
  soc_known: boolean;
  bundle_zip_b64?: string | null;
}

export interface CombinedToolResponse {
  overview: { status: "complete" | "needs_decisions" | "problems" | "nothing"; headline: string; pin_states: number; files: string[] };
  peripherals: { peripheral: string; status: string; headline: string; pin_states: number; files: string[]; notes: string[] }[];
  decisions: { peripheral: string; text: string }[];
  left_out: { peripheral: string; pin: string; signal: string; reason: string; tier?: string | null }[];
  conflicts: string[];
  notes: string[];
  sections: { peripheral: string; pin_states: number }[];
  files: { name: string; role: string; text: string }[];
  report_text: string;
  bundle_zip_b64?: string | null;
}

export interface GenericPreviewResponse {
  overview: { status: string; headline: string; pin_states: number; review_lines: number; unverified: boolean; files: string[]; verifier: { wrong: number; ok: number; checked: number } };
  pins: { pin: string; ball_id: string; signal: string; instance: string; mux: number | string | null; bias: string | null; drive: string | null; notes: string[] }[];
  items: { kind: string; text: string }[];
  files: { name: string; text: string }[];
  verification: { wrong: string[]; ok: number; checked: number } | null;
  ai_draft: { status: string; text: string; note?: string; calls: number } | null;
  report_text: string;
  soc: string | null;
}
