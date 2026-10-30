"""
Core data models for the PinMux -> Device Tree pipeline.

The workbook's schema (which columns exist, which entity/project each
belongs to) is now HARDCODED in app/schema.py, per the user's
confirmation that every workbook of this type has identical structure.
ColumnMeta still carries per-column metadata (position, visibility, the
schema definition it matched), but entity/role assignment comes from
schema.KNOWN_COLUMNS, not from runtime guessing.
"""

from __future__ import annotations
from typing import Optional, Any
from pydantic import BaseModel, Field


class ColumnMeta(BaseModel):
    """Everything the parser knows about one worksheet column."""
    index: int  # 0-based column index
    excel_letter: str  # e.g. "E"
    group_label: Optional[str] = None  # row 2 value (may be None if merged/blank)
    field_label: Optional[str] = None  # row 3 raw value, e.g. "Ball ID (SA8155P)"
    base_field: Optional[str] = None  # "Ball ID" (label with entity suffix stripped)
    entity: Optional[str] = None  # "SA8155P" - from the known schema, not guessed
    slug: Optional[str] = None  # "ball_id" - normalized key used in records
    category: Optional[str] = None  # identity | electrical | pinmux_register | project_config | marker
    is_hidden: bool = False
    known: bool = True  # False if this column's label wasn't found in the known schema


class EntitySummary(BaseModel):
    """One entity (SoC / project / sub-group) and its role. When the file
    matched a known schema exactly, role is CONFIRMED (role_confirmed=True,
    from that schema - not a guess). When it didn't match anything known,
    role is a SUGGESTION (role_confirmed=False) from structural/keyword
    heuristics - never applied to parsing until a human confirms it via
    the classify-new-schema endpoint."""
    name: str
    role: str  # "soc" | "project" | "subgroup" | "unknown"
    columns: list[str]  # base_field names belonging to this entity
    column_count: int
    role_confirmed: bool = True
    suggestion_reason: Optional[str] = None


class WorkbookAnalysis(BaseModel):
    """Returned right after upload: what was parsed, and whether it
    matches a known schema."""
    workbook_id: str
    sheet_name: str
    total_columns: int
    hidden_columns: int
    header_row: int
    data_start_row: int
    entities: list[EntitySummary]
    columns: list[ColumnMeta]
    schema_match: bool  # True only if it exactly matched an ALREADY-CONFIRMED known schema
    schema_issues: list[str] = Field(default_factory=list)
    matched_schema_name: Optional[str] = None  # which known schema matched, if any
    needs_classification: bool = False  # True if this is a genuinely new structure - entities above are SUGGESTIONS, must be confirmed via /classify-schema before /build will work


class EntityRoleConfirmation(BaseModel):
    entity: str
    role: str  # "soc" | "project" | "subgroup" | "ignore"


class ConfirmSchemaRequest(BaseModel):
    workbook_id: str
    schema_name: str  # human-readable name for this newly confirmed schema, e.g. "SA9000X (ProjectAlpha)"
    role_confirmations: list[EntityRoleConfirmation]  # exactly one "soc" entry required; 1+ "project" entries required; "subgroup"/"ignore" for the rest
    use_ai_value_decoding: bool = False  # opt-in: after building, try AI-assisted bias/drive-strength/direction value decoding for fields that look likely by name (see app/ai_value_decoder.py). No LLM is configured in this deployment, so this is currently always a safe no-op regardless of value.


class NormalizedRecord(BaseModel):
    project: str
    soc: str
    row_index: int
    fields: dict[str, Any]  # slugified field -> value, merged SoC + project fields


class ProjectSummary(BaseModel):
    project: str
    soc: str
    record_count: int
    fields: list[str]


class ValidationIssue(BaseModel):
    severity: str  # "error" | "warning"
    rule: str
    project: str
    message: str
    row_index: Optional[int] = None


class ChatQueryRequest(BaseModel):
    workbook_id: str
    project: str
    message: str


class ChatQueryResponse(BaseModel):
    intent: dict[str, Any]
    matched_records: list[NormalizedRecord]
    total_matches: int  # total rows found by the full scan, before any display cap
    answer: str


class GenerateDtsRequest(BaseModel):
    workbook_id: str
    project: str
    query: str  # e.g. "Bluetooth UART" or "full" for whole-project generation


class GenerateDtsResponse(BaseModel):
    project: str
    dts_text: str
    used_records: list[NormalizedRecord]
    validation: list[ValidationIssue]
    review_report: str = ""


class DtsUpdateFileRequest(BaseModel):
    original_filename: str
    output_filename: str  # user-provided name for the updated output


class PinChangeOut(BaseModel):
    pin: str
    field: str
    old_value: str | None
    new_value: str


class NodeUpdateOut(BaseModel):
    label: str
    changed: bool
    changes: list[PinChangeOut] = Field(default_factory=list)
    unmatched_pins: list[str] = Field(default_factory=list)
    structural_issue: str | None = None
    review_note: str | None = None
    ai_assisted: bool = False
    ai_advisory: str | None = None


class DtsUpdateFileResult(BaseModel):
    original_filename: str
    output_filename: str
    updated_text: str
    format_role: str = "unknown"
    recognized_pin_state_count: int = 0
    unrecognized_pin_state_count: int = 0
    format_summary: str = ""
    format_note: str = ""
    ai_fallback_requested: bool = False
    ai_candidate_count: int = 0
    ai_extraction_failure_count: int = 0
    ai_fallback_status: str = ""
    node_results: list[NodeUpdateOut]
    changed_node_count: int
    unmatched_node_count: int
    structural_issue_count: int = 0


class DtsBatchUpdateResponse(BaseModel):
    project: str
    files: list[DtsUpdateFileResult]
    remainder_dts_text: str
    remainder_pin_count: int
    total_excel_signal_pins: int
    total_covered_pins: int
    ambiguous_pin_count: int = 0
    review_report: str = ""

