from __future__ import annotations
import base64
import json
import os
import tempfile
import shutil

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, Response

from app import dts_style
from app.models import (
    WorkbookAnalysis, ProjectSummary,
    ChatQueryRequest, ChatQueryResponse, GenerateDtsRequest, GenerateDtsResponse,
    DtsBatchUpdateResponse, DtsUpdateFileResult, NodeUpdateOut, PinChangeOut,
    ValidationIssue, NormalizedRecord,
    ConfirmSchemaRequest,
)
from app.md_exporter import build_project_markdown
from app.peripheral_extractor import (
    resolve_gen5_signal_slug, filter_records_by_gen5_substring, build_peripheral_summary_markdown,
    related_group_mates, build_related_markdown, _resolve_field_slugs_for_summary,
)
from app.parsers.workbook_parser import parse_workbook, build_analysis, WorkbookParseError
from app.parsers.normalizer import normalize, extract_vocabulary
from app.schema import format_data_dictionary
from app.schema_registry import (
    match_known_schema, confirm_new_schema, list_known_schemas, update_value_decode_map,
)
from app.ai_value_decoder import suggest_value_field, ai_decode_values
from app.rag.index import rag_store
from app.rag.llm import parse_intent, explain
from app.generators.dts_generator import generate_dts, build_pin_index, remainder_records
from app.generators.dts_updater import update_dts_file
from app.review_report import build_generation_review_report, build_update_review_report
from app.validators.rules import run_validation
from app.store import WorkbookSession, put, require
from app import llm_client

import sys as _sys
_TOOL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tool")
if _TOOL_DIR not in _sys.path:
    _sys.path.insert(0, _TOOL_DIR)
import eth_dts_tool as _eth_tool          # Ethernet counterpart of the USB engine
import spi_dts_tool as _spi_tool
import generic_dts_tool as _generic_tool      # opt-in non-Qualcomm preview
import uart_dts_tool as _uart_tool        # UART counterpart
import i2c_dts_tool as _i2c_tool          # I2C counterpart
import combined_dts_tool as _combined_tool   # USB + Ethernet + SPI merged
import usb_dts_tool as _usb_tool          # the same engine the CLI and the Copilot agent use - no logic duplicated
import verify_dts as _usb_verify


app = FastAPI(title="PinMux -> Device Tree Platform")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten before any real deployment
    allow_methods=["*"],
    allow_headers=["*"],
)


# Rendered once at import time - same for every workbook since the schema
# is fixed (see app/schema.py). Fed into every LLM call for grounded
# explanations instead of guessing from field names.
DATA_DICTIONARY = format_data_dictionary()


def _ambiguous_to_validation_issues(project: str, ambiguous: list[NormalizedRecord]
                                     ) -> list[ValidationIssue]:
    """Converts generate_dts()'s ambiguous-record list into visible
    validation warnings, so they surface in the same UI panel as every
    other validation issue rather than needing a separate reporting path.
    Per classify_pin()'s contract, these are rows with NO usable
    Selected Interface or PinMuxed Name value - not a recognized
    non-signal marker, just genuinely no data to decide from."""
    issues = []
    for rec in ambiguous:
        issues.append(ValidationIssue(
            severity="warning",
            rule="ambiguous_classification",
            project=project,
            message=(f"Row {rec.row_index}: no usable Selected Interface or "
                     f"PinMuxed Name value - cannot determine if this is a "
                     f"real signal pin or a non-signal row. Not included in "
                     f"generated DTS; needs manual review."),
            row_index=rec.row_index,
        ))
    return issues


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/ai/status")
def ai_status():
    """Whether the LLM gateway is configured, and which features may use it. Never returns the key."""
    return llm_client.status()


@app.post("/api/workbook/upload", response_model=WorkbookAnalysis)
async def upload_workbook(file: UploadFile = File(...)):
    if not file.filename.lower().endswith((".xlsm", ".xlsx")):
        raise HTTPException(400, "Please upload a .xlsm or .xlsx workbook.")

    suffix = os.path.splitext(file.filename)[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        parsed = parse_workbook(tmp_path)
    except WorkbookParseError as e:
        os.unlink(tmp_path)
        raise HTTPException(400, str(e))

    # Keep the original bytes on disk for this session's lifetime (moved, not
    # copied, from the temp upload) - the USB tool bridge endpoints re-read
    # the real workbook by this path rather than asking for a second upload.
    uploads_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "uploads")
    os.makedirs(uploads_dir, exist_ok=True)
    excel_path = os.path.join(uploads_dir, f"{parsed.workbook_id}{suffix}")
    shutil.move(tmp_path, excel_path)

    analysis = build_analysis(parsed)
    session = WorkbookSession(parsed=parsed, excel_path=excel_path)
    if analysis.matched_schema_name is not None:
        # Exact match against an already-confirmed schema - resolve the
        # entities immediately so /build can use them without needing to
        # re-derive anything. Unmatched files leave these unset; /build
        # will reject with a clear message pointing at /classify-schema.
        matched = next(s for s in list_known_schemas() if s.name == analysis.matched_schema_name)
        session.soc_entity = matched.soc_entity
        session.project_entities = list(matched.project_entities)
        session.subgroup_entities = list(matched.subgroup_entities)
    put(parsed.workbook_id, session)
    return analysis


def _do_build(workbook_id: str, session: WorkbookSession) -> list[ProjectSummary]:
    """Shared build logic - normalizes and indexes using whatever
    soc_entity/project_entities/subgroup_entities are currently resolved
    on the session (either from an exact schema match at upload time, or
    freshly confirmed via /classify-schema). Used by both /build and
    /classify-schema so there's exactly one code path for this, not two
    that could drift apart."""
    if not session.soc_entity or not session.project_entities:
        raise HTTPException(
            400,
            "This workbook's structure hasn't been resolved yet. Either it "
            "didn't match any known schema at upload (check schema_issues / "
            "needs_classification in the upload response) - classify it via "
            "POST /classify-schema first - or upload it again.",
        )

    records = normalize(
        session.parsed,
        soc_entity=session.soc_entity,
        project_entities=session.project_entities,
        soc_subgroup_entities=session.subgroup_entities,
    )

    # A file whose columns don't match the resolved entities at all
    # normalizes to zero records for every project - silently "succeeding"
    # with an empty, unusable session would be worse than refusing
    # outright, since every feature downstream (search, generation,
    # validation) would then quietly find nothing with no indication why.
    # Confirmed real case: uploading a workbook with different SoC/project
    # column names normalizes to record_count=0 for both projects with no
    # error at all before this check existed.
    if not records:
        raise HTTPException(
            422,
            f"This file's columns don't match the resolved schema "
            f"(SoC={session.soc_entity!r}, projects={session.project_entities!r}) "
            f"at all - normalizing it produced zero records for every "
            f"project. If this was freshly classified, double-check the "
            f"role confirmations; otherwise re-upload the correct workbook.",
        )

    session.records_by_project = {}
    session.validation_by_project = {}

    summaries: list[ProjectSummary] = []
    for project in session.project_entities:
        proj_records = [r for r in records if r.project == project]
        session.records_by_project[project] = proj_records
        session.validation_by_project[project] = run_validation(proj_records)
        session.vocabulary_by_project[project] = extract_vocabulary(proj_records)
        rag_store.index_records(workbook_id, project, proj_records)

        field_names: set[str] = set()
        for r in proj_records:
            field_names.update(r.fields.keys())

        summaries.append(ProjectSummary(
            project=project,
            soc=session.soc_entity,
            record_count=len(proj_records),
            fields=sorted(field_names),
        ))
    return summaries


@app.post("/api/workbook/{workbook_id}/build", response_model=list[ProjectSummary])
def build_workbook(workbook_id: str):
    """Builds the normalized/indexed data model. Works immediately if the
    file matched a known schema at upload (soc/project/subgroup entities
    were already resolved then) - no manual classification needed, same
    as the original fixed-schema design. For a file that didn't match
    anything known, this returns a clear 400 pointing at /classify-schema
    instead of silently building nothing."""
    try:
        session = require(workbook_id)
    except KeyError:
        raise HTTPException(404, "Unknown workbook_id - upload it again.")
    return _do_build(workbook_id, session)


@app.post("/api/workbook/{workbook_id}/classify-schema", response_model=list[ProjectSummary])
def classify_schema(workbook_id: str, req: ConfirmSchemaRequest):
    """Confirms entity roles for a workbook that didn't match any known
    schema, registers it as a NEW known schema (so the same structure is
    recognized automatically next time - no re-confirmation needed), and
    immediately builds using it. Requires exactly one 'soc' role and at
    least one 'project' role among the confirmations; 'subgroup' and
    'ignore' are both optional. Nothing here is guessed - every role was
    either exactly matched against an existing schema (see /upload) or is
    being explicitly confirmed by a human right now."""
    try:
        session = require(workbook_id)
    except KeyError:
        raise HTTPException(404, "Unknown workbook_id - upload it again.")

    role_by_entity = {c.entity: c.role for c in req.role_confirmations}
    soc_entities = [e for e, r in role_by_entity.items() if r == "soc"]
    project_entities = [e for e, r in role_by_entity.items() if r == "project"]
    subgroup_entities = [e for e, r in role_by_entity.items() if r == "subgroup"]

    if len(soc_entities) != 1:
        raise HTTPException(400, f"Exactly one entity must be confirmed as 'soc' - got {len(soc_entities)}.")
    if not project_entities:
        raise HTTPException(400, "At least one entity must be confirmed as 'project'.")

    soc_entity = soc_entities[0]

    # Build columns_by_label from the session's parsed columns, using
    # the CONFIRMED role for category assignment (generic per-role
    # defaults, since a brand-new schema has no hand-written per-field
    # descriptions yet - those can be refined later without affecting
    # correctness, since descriptions only feed LLM grounding text).
    #
    # Every labeled column must end up in known_columns, not just the
    # ones tied to a real (non-"ignore") role - match_known_schema()
    # compares against ALL of a file's labeled columns (see
    # build_analysis()), so any column silently left out here would
    # make this exact file fail to match its own just-confirmed schema
    # on the next upload, forcing reclassification every single time.
    # Confirmed real case: a workbook has structural marker columns
    # ("_PARA_ROW"/"_END_SB"-style boundary/legend markers) with a
    # label but no detected entity at all - these aren't "ignored" by a
    # human choice, they simply don't belong to any entity. The original
    # hand-authored SA8155P schema already handles this by recording
    # them with entity="" and category="marker" (see app/schema.py's
    # _MARKER_COLUMNS) - normalize() naturally excludes them from every
    # real project's records since an empty entity matches nothing.
    # Mirrored here for BOTH that no-entity case and for a real entity a
    # human explicitly marks "ignore": either way the column still gets
    # remembered as belonging to this schema (so the file matches next
    # time), it just carries no parsing meaning.
    category_by_role = {
        "soc": "identity", "project": "project_config", "subgroup": "pinmux_register",
    }
    columns_by_label: dict[str, tuple[str, str, str]] = {}
    for col in session.parsed.columns:
        if not col.field_label:
            continue
        role = role_by_entity.get(col.entity) if col.entity else None
        if col.entity and col.base_field and role not in (None, "ignore"):
            columns_by_label[col.field_label] = (col.entity, col.base_field, category_by_role[role])
        else:
            # No entity detected, or a real entity the human marked "ignore" -
            # remembered as a marker so this exact file still self-matches.
            columns_by_label[col.field_label] = ("", col.field_label, "marker")

    schema = confirm_new_schema(
        name=req.schema_name, soc_entity=soc_entity,
        project_entities=project_entities, subgroup_entities=subgroup_entities,
        columns_by_label=columns_by_label,
    )

    # Write the now-confirmed slug/category/known back onto the session's
    # actual parsed columns - normalize() filters on col.slug being set
    # (see app/parsers/normalizer.py), and the unmatched-schema branch of
    # build_analysis() deliberately never wrote these (they were only
    # SUGGESTIONS then, not yet confirmed). Mirrors exactly what the
    # matched-schema branch does for an already-known file.
    for col in session.parsed.columns:
        known_def = schema.known_columns.get(col.field_label) if col.field_label else None
        if known_def:
            col.base_field = known_def.base_field
            col.entity = known_def.entity
            col.slug = known_def.slug
            col.category = known_def.category
            col.known = True

    session.soc_entity = schema.soc_entity
    session.project_entities = list(schema.project_entities)
    session.subgroup_entities = list(schema.subgroup_entities)

    summaries = _do_build(workbook_id, session)

    if req.use_ai_value_decoding:
        # Opt-in AI-assisted value decoding (see app/ai_value_decoder.py):
        # for each concept (bias/drive_strength/direction), find the most
        # likely field by name, sample its DISTINCT real values from the
        # just-built records, and ask the LLM to map each to the correct
        # directive ONCE - not per-row. Safe no-op without GROQ_API_KEY
        # (ai_decode_values returns None, nothing gets configured, every
        # pin just falls back to NEEDS REVIEW same as if this flag were
        # off - identical to every other AI-assisted feature in this app).
        all_project_records = [
            r for proj in session.project_entities
            for r in session.records_by_project.get(proj, [])
        ]
        slug_to_base_field: dict[str, str] = {}
        for col in session.parsed.columns:
            if col.known and col.slug and col.base_field and col.entity in session.project_entities:
                slug_to_base_field[col.slug] = col.base_field

        for concept in ("bias", "drive_strength", "direction"):
            field_slug = suggest_value_field(slug_to_base_field, concept)
            if field_slug is None:
                continue
            distinct_values = sorted({
                str(r.fields[field_slug]) for r in all_project_records
                if r.fields.get(field_slug) not in (None, "")
            })
            if not distinct_values:
                continue
            field_display = slug_to_base_field[field_slug]
            value_map = ai_decode_values(field_display, distinct_values, concept)
            if value_map:
                update_value_decode_map(schema.soc_entity, concept, field_slug, value_map)

    return summaries


@app.get("/api/workbook/{workbook_id}/projects", response_model=list[ProjectSummary])
def list_projects(workbook_id: str):
    try:
        session = require(workbook_id)
    except KeyError:
        raise HTTPException(404, "Unknown workbook_id.")
    summaries = []
    for project, records in session.records_by_project.items():
        field_names: set[str] = set()
        for r in records:
            field_names.update(r.fields.keys())
        summaries.append(ProjectSummary(
            project=project, soc=session.soc_entity or "",
            record_count=len(records), fields=sorted(field_names),
        ))
    return summaries


@app.get("/api/workbook/{workbook_id}/export-records/{project}")
def export_records(workbook_id: str, project: str):
    """Downloads the fully normalized, schema-verified records for one
    project as standalone JSON - a portable snapshot of exactly what
    this pipeline extracted from the workbook, independent of the
    running app. Includes EVERY normalized row (real signal pins AND
    non-signal markers like AGND/DNC alike) - the non-signal filtering
    that happens for DTS generation is a separate, later step
    (classify_pin(), see app/constants.py), so this export is the raw
    extracted dataset, not just what became DTS content.

    This is NOT used by the app itself for anything - AI features here
    already work directly against the live in-memory records, which are
    always at least as fresh and don't need a JSON round-trip. This
    export exists purely as a standalone artifact: for review, for
    handing to someone without running the app, or for feeding into a
    different tool - see the person's own stated use case before this
    was built."""
    try:
        session = require(workbook_id)
    except KeyError:
        raise HTTPException(404, "Unknown workbook_id.")
    if project not in session.records_by_project:
        raise HTTPException(404, f"Unknown project {project!r} - has it been built yet?")

    records = session.records_by_project[project]
    payload = {
        "soc": session.soc_entity,
        "project": project,
        "record_count": len(records),
        "records": [
            {"row_index": r.row_index, "project": r.project, "soc": r.soc, "fields": r.fields}
            for r in records
        ],
    }
    body = json.dumps(payload, indent=2, default=str)
    return Response(
        content=body, media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{project.lower()}_records.json"'},
    )


@app.get("/api/workbook/{workbook_id}/export-records", response_model=None)
def export_all_records(workbook_id: str):
    """Same as export_records() but for every built project in one file -
    convenient when you want the whole workbook's extracted data as a
    single portable snapshot rather than one download per project."""
    try:
        session = require(workbook_id)
    except KeyError:
        raise HTTPException(404, "Unknown workbook_id.")
    if not session.records_by_project:
        raise HTTPException(400, "No projects have been built yet.")

    payload = {
        "soc": session.soc_entity,
        "projects": {
            project: {
                "record_count": len(records),
                "records": [
                    {"row_index": r.row_index, "project": r.project, "soc": r.soc, "fields": r.fields}
                    for r in records
                ],
            }
            for project, records in session.records_by_project.items()
        },
    }
    body = json.dumps(payload, indent=2, default=str)
    return Response(
        content=body, media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="workbook_records.json"'},
    )



@app.post("/api/query", response_model=ChatQueryResponse)
def query(req: ChatQueryRequest):
    try:
        session = require(req.workbook_id)
    except KeyError:
        raise HTTPException(404, "Unknown workbook_id.")
    if req.project not in session.records_by_project:
        raise HTTPException(400, f"Project '{req.project}' has not been classified/indexed yet.")

    intent = parse_intent(
        req.message, data_dictionary=DATA_DICTIONARY,
        vocabulary=session.vocabulary_by_project.get(req.project),
    )
    # Full scan over EVERY row in the project when keywords are present
    # (limit=None) - k only matters for the open-ended fallback ranking.
    # This is what makes "show all X" actually exhaustive on a 2,000+ row
    # real workbook instead of silently missing rows outside a fixed top-k.
    all_matches = rag_store.search(
        req.workbook_id, req.project, req.message, k=15,
        keyword_filter=intent.get("keywords") or None,
        limit=None,
    )
    total_matches = len(all_matches)

    # Still cap what's SHOWN and sent to the LLM (chat UI legibility,
    # prompt size) - but total_matches above tells the truth about how
    # many rows actually matched, and the explanation prompt is told the
    # real count so it doesn't imply completeness it doesn't have.
    DISPLAY_CAP = 60
    matched = all_matches[:DISPLAY_CAP]

    records_text = "\n".join(
        f"- row {r.row_index}: " + ", ".join(f"{k}={v}" for k, v in r.fields.items())
        for r in matched
    )
    if total_matches > DISPLAY_CAP:
        records_text = (
            f"[{total_matches} rows matched in total; showing the first {DISPLAY_CAP} below. "
            f"Mention in your answer that there are more than shown.]\n" + records_text
        )

    # If a DTS batch-update was run for this project, surface its results
    # as extra grounding - lets the chat answer "what changed in file X"/
    # "how many pins are still uncovered" accurately instead of only
    # knowing about raw Excel rows.
    extra_context = ""
    if session.dts_update_history and session.dts_update_history.get("project") == req.project:
        h = session.dts_update_history
        extra_context = (
            "\n\n=== Most recent DTS batch-update run for this project ===\n"
            f"Files processed: {len(h['files'])}\n"
        )
        for f in h["files"]:
            extra_context += (
                f"- {f['output_filename']}: {f['changed_nodes']} of {f['total_nodes']} "
                f"node(s) updated, {f['nodes_with_unmatched_pins']} node(s) had pins "
                f"not found in Excel\n"
            )
        extra_context += (
            f"Remainder (Excel pins not in any uploaded file): {h['remainder_pin_count']} pins\n"
            f"Total Excel signal pins: {h['total_excel_signal_pins']}, "
            f"covered by uploaded files: {h['total_covered_pins']}\n"
        )

    answer = explain(req.message, records_text + extra_context, data_dictionary=DATA_DICTIONARY)

    return ChatQueryResponse(
        intent=intent, matched_records=matched, total_matches=total_matches, answer=answer,
    )


@app.post("/api/generate-dts", response_model=GenerateDtsResponse)
def generate_dts_endpoint(req: GenerateDtsRequest):
    try:
        session = require(req.workbook_id)
    except KeyError:
        raise HTTPException(404, "Unknown workbook_id.")
    if req.project not in session.records_by_project:
        raise HTTPException(400, f"Project '{req.project}' has not been classified/indexed yet.")

    all_records = session.records_by_project[req.project]

    if req.query.strip().lower() in ("full", "all", "everything", ""):
        used_records = all_records
    else:
        intent = parse_intent(
            req.query, data_dictionary=DATA_DICTIONARY,
            vocabulary=session.vocabulary_by_project.get(req.project),
        )
        # limit=None - DTS generation MUST include every matching row, not
        # a top-k slice. Dropping a row here means a real pin silently
        # missing from the generated device tree.
        used_records = rag_store.search(
            req.workbook_id, req.project, req.query, k=50,
            keyword_filter=intent.get("keywords") or None,
            limit=None,
        )

    dts_text, ambiguous, review_entries = generate_dts(
        req.project, session.soc_entity or "unknown_soc", used_records)
    validation = run_validation(used_records)
    validation += _ambiguous_to_validation_issues(req.project, ambiguous)

    review_report = build_generation_review_report(
        req.project, session.soc_entity or "unknown_soc", review_entries, len(ambiguous))

    return GenerateDtsResponse(
        project=req.project, dts_text=dts_text,
        used_records=used_records, validation=validation,
        review_report=review_report,
    )


@app.get("/api/download-dts/{workbook_id}/{project}", response_class=PlainTextResponse)
def download_dts(workbook_id: str, project: str):
    try:
        session = require(workbook_id)
    except KeyError:
        raise HTTPException(404, "Unknown workbook_id.")
    if project not in session.records_by_project:
        raise HTTPException(404, "Unknown project.")
    dts_text, _ambiguous, _review = generate_dts(project, session.soc_entity or "unknown_soc",
                                                  session.records_by_project[project])
    return PlainTextResponse(
        content=dts_text,
        headers={"Content-Disposition": f'attachment; filename="{project.lower()}.dtsi"'},
    )


@app.post("/api/workbook/{workbook_id}/dts-update", response_model=DtsBatchUpdateResponse)
async def dts_batch_update(
    workbook_id: str,
    project: str = Form(...),
    output_filenames: str = Form(...),  # JSON array of strings, same order as files
    files: list[UploadFile] = File(...),
    use_ai_fallback: bool = Form(False),
):
    """Batch-updates a set of existing DTS files against the Excel
    workbook (source of truth for content), per the confirmed policy:
    disagreements get overwritten with an old->new comment, partial pin
    matches update what matched and flag what didn't, and a pin counts as
    'covered' just by appearing in any uploaded file. Also returns a
    'remainder' DTS covering every Excel signal pin not found in any of
    the uploaded files.

    use_ai_fallback (opt-in, default False): for node text the strict
    parser doesn't recognize (a different format than the one confirmed
    shape), additionally try AI-assisted reading of what's already
    there and report how it compares to Excel via an inserted advisory
    comment - the node's own text is never rewritten by this path.
    Requires GROQ_API_KEY; produces no additional output without one."""
    try:
        session = require(workbook_id)
    except KeyError:
        raise HTTPException(404, "Unknown workbook_id - upload it again.")
    if project not in session.records_by_project:
        raise HTTPException(400, f"Project '{project}' has not been built yet.")

    try:
        filenames = json.loads(output_filenames)
        if not isinstance(filenames, list) or not all(isinstance(f, str) for f in filenames):
            raise ValueError
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(400, "output_filenames must be a JSON array of strings.")
    if len(filenames) != len(files):
        raise HTTPException(
            400, f"output_filenames length ({len(filenames)}) must match number of files ({len(files)}).",
        )

    records = session.records_by_project[project]
    pin_index = build_pin_index(records)

    file_results: list[DtsUpdateFileResult] = []
    all_covered: set[str] = set()

    for upload, out_name in zip(files, filenames):
        raw_bytes = await upload.read()
        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            text = raw_bytes.decode("utf-8", errors="replace")

        style = dts_style.learn_style(text, upload.filename or out_name)
        if style.unrecognized_pin_states:
            format_note = (
                f"Partial format recognition: {style.recognized_pin_states} pin-state block(s) recognized and "
                f"{style.unrecognized_pin_states} not recognized. Excel updates still apply only to nodes the updater understands."
            )
        elif style.recognized_pin_states:
            format_note = (
                f"Recognized {style.recognized_pin_states} pin-state block(s). This is a structure check only; "
                "Excel updates still apply only to nodes the updater understands."
            )
        elif style.role == "unknown":
            format_note = "DTS structure not recognized for format analysis; unmatched content is left untouched."
        else:
            format_note = "No TLMM pin-state blocks found; pin-state format coverage does not apply to this file."

        result = update_dts_file(text, pin_index, use_ai_fallback=use_ai_fallback)
        all_covered |= result.covered_pins

        node_results_out = [
            NodeUpdateOut(
                label=nr.label, changed=nr.changed,
                changes=[
                    PinChangeOut(pin=c.pin, field=c.field, old_value=c.old_value, new_value=c.new_value)
                    for c in nr.changes
                ],
                unmatched_pins=nr.unmatched_pins,
                structural_issue=nr.structural_issue,
                review_note=nr.review_note,
                ai_assisted=nr.ai_assisted,
                ai_advisory=nr.ai_advisory,
            )
            for nr in result.node_results
        ]
        file_results.append(DtsUpdateFileResult(
            original_filename=upload.filename or "unknown",
            output_filename=out_name,
            updated_text=result.updated_text,
            format_role=style.role,
            recognized_pin_state_count=style.recognized_pin_states,
            unrecognized_pin_state_count=style.unrecognized_pin_states,
            format_summary=style.summary(),
            format_note=format_note,
            ai_fallback_requested=result.ai_fallback_requested,
            ai_candidate_count=result.ai_candidate_count,
            ai_extraction_failure_count=result.ai_extraction_failure_count,
            ai_fallback_status=result.ai_fallback_status,
            node_results=node_results_out,
            changed_node_count=sum(1 for nr in result.node_results if nr.changed),
            unmatched_node_count=sum(1 for nr in result.node_results if nr.unmatched_pins),
            structural_issue_count=sum(1 for nr in result.node_results if nr.structural_issue),
        ))

    remainder_recs = remainder_records(records, all_covered)
    remainder_text, remainder_ambiguous, _remainder_review_entries = generate_dts(
        project, session.soc_entity or "unknown_soc", remainder_recs)

    # Stored for grounded chat Q&A about this run (e.g. "what changed in
    # file X", "how many pins are still uncovered").
    session.dts_update_history = {
        "project": project,
        "files": [
            {
                "output_filename": r.output_filename,
                "changed_nodes": r.changed_node_count,
                "nodes_with_unmatched_pins": r.unmatched_node_count,
                "total_nodes": len(r.node_results),
            }
            for r in file_results
        ],
        "remainder_pin_count": len(remainder_recs),
        "total_excel_signal_pins": len(pin_index),
        "total_covered_pins": len(all_covered & set(pin_index.keys())),
    }

    return DtsBatchUpdateResponse(
        project=project,
        files=file_results,
        remainder_dts_text=remainder_text,
        remainder_pin_count=len(remainder_recs),
        total_excel_signal_pins=len(pin_index),
        total_covered_pins=len(all_covered & set(pin_index.keys())),
        ambiguous_pin_count=len(remainder_ambiguous),
        review_report=build_update_review_report(
            project, [f.model_dump() for f in file_results]),
    )


@app.get("/api/workbook/{workbook_id}/export-md/{project}", response_class=PlainTextResponse)
def export_md(workbook_id: str, project: str):
    """Downloads a Markdown PinMux reference table for one project."""
    try:
        session = require(workbook_id)
    except KeyError:
        raise HTTPException(404, "Unknown workbook_id.")
    if project not in session.records_by_project:
        raise HTTPException(404, f"Unknown project {project!r} - has it been built yet?")

    md_text, unresolved = build_project_markdown(
        project, session.soc_entity, session.records_by_project[project], session.parsed.columns,
    )
    headers = {"Content-Disposition": f'attachment; filename="{project.lower().replace(" ", "_")}_database_export.md"'}
    if unresolved:
        headers["X-Unresolved-Fields"] = ",".join(unresolved)
    return PlainTextResponse(content=md_text, headers=headers)


@app.get("/api/workbook/{workbook_id}/peripheral/{project}/summary", response_class=PlainTextResponse)
def peripheral_summary(workbook_id: str, project: str, keyword: str):
    """Stage 1: filters this project's records by a case-insensitive
    substring match against Gen5 Signal Name, returns the matched pins
    as a Markdown table for quick scanning."""
    try:
        session = require(workbook_id)
    except KeyError:
        raise HTTPException(404, "Unknown workbook_id.")
    if project not in session.records_by_project:
        raise HTTPException(404, f"Unknown project {project!r} - has it been built yet?")

    gen5_slug = resolve_gen5_signal_slug(project, session.soc_entity, session.parsed.columns)
    if not gen5_slug:
        raise HTTPException(422, "Could not find a Gen5 Signal Name column for this project.")

    matched = filter_records_by_gen5_substring(session.records_by_project[project], keyword, gen5_slug)
    md_text = build_peripheral_summary_markdown(
        keyword, project, matched, gen5_slug, session.parsed.columns, session.soc_entity,
    )
    # Pins of the same Interface Group the keyword missed (e.g. an IMU's chip-select when searching "spi"): shown for a
    # person to confirm - never added to the DTS automatically.
    ig_slug = _resolve_field_slugs_for_summary(project, session.soc_entity, session.parsed.columns).get("interface_group")
    mates = related_group_mates(session.records_by_project[project], matched, ig_slug)
    if mates:
        md_text += "\n\n" + build_related_markdown(mates, gen5_slug, ig_slug, keyword)
    return PlainTextResponse(content=md_text, headers={"X-Matched-Count": str(len(matched)),
                                                       "X-Related-Count": str(len(mates))})


@app.get("/api/workbook/{workbook_id}/peripheral/{project}/dts", response_model=GenerateDtsResponse)
def peripheral_dts(workbook_id: str, project: str, keyword: str):
    """Stage 2: generates DTS scoped to ONLY the pins matching the
    same Gen5 Signal Name substring filter used in the summary stage."""
    try:
        session = require(workbook_id)
    except KeyError:
        raise HTTPException(404, "Unknown workbook_id.")
    if project not in session.records_by_project:
        raise HTTPException(404, f"Unknown project {project!r} - has it been built yet?")

    gen5_slug = resolve_gen5_signal_slug(project, session.soc_entity, session.parsed.columns)
    if not gen5_slug:
        raise HTTPException(422, "Could not find a Gen5 Signal Name column for this project.")

    matched = filter_records_by_gen5_substring(session.records_by_project[project], keyword, gen5_slug)
    if not matched:
        raise HTTPException(404, f"No pins matched {keyword!r} against Gen5 Signal Name.")

    dts_text, ambiguous, review_entries = generate_dts(project, session.soc_entity or "unknown_soc", matched)
    validation = run_validation(matched)
    validation += _ambiguous_to_validation_issues(project, ambiguous)
    review_report = build_generation_review_report(
        project, session.soc_entity or "unknown_soc", review_entries, len(ambiguous))

    return GenerateDtsResponse(
        project=project, dts_text=dts_text,
        used_records=matched, validation=validation,
        review_report=review_report,
    )


# ---------------------------------------------------------------------------
# USB tool bridge - calls the SAME run(args) functions the CLI
# (tool/usb_dts_tool.py, tool/verify_dts.py) and the Copilot agent use. No
# logic is reimplemented here. Reuses the workbook already uploaded via
# /api/workbook/upload (session.excel_path) - the person never uploads the
# same file twice; only an existing DTS/overlay to check-or-update is a
# fresh upload here, since those aren't part of the workbook upload at all.
# ---------------------------------------------------------------------------

class _UsbNS:
    """A plain object with the same attributes build_argparser().parse_args() would set -
    usb_dts_tool.run()/verify_dts.run() only ever read attributes, never call argparse itself."""
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _usb_temp_dir() -> str:
    return tempfile.mkdtemp(prefix="usbtool_")


async def _save_upload(upload: UploadFile, dest_dir: str) -> str:
    path = os.path.join(dest_dir, upload.filename or "upload.bin")
    with open(path, "wb") as f:
        f.write(await upload.read())
    return path


@app.get("/api/workbook/{workbook_id}/usb-tool/projects")
def usb_tool_projects(workbook_id: str):
    """Project names for the already-uploaded workbook - no re-read, no re-upload; this is exactly
    session.project_entities, resolved once at /build time."""
    session = require(workbook_id)
    if not session.excel_path:
        raise HTTPException(409, "This workbook session has no stored file (uploaded before this "
                                 "feature existed) - upload it again.")
    return {"projects": session.project_entities}


@app.post("/api/workbook/{workbook_id}/usb-tool/update-or-generate")
async def usb_tool_update_or_generate(
    workbook_id: str,
    project: str = Form(...),
    pattern: str = Form("auto"),
    vbus_pin: list[str] = Form(default_factory=list),
    keyword: str = Form("usb"),
    match: str = Form("both"),
    no_update: bool = Form(False),
    ai_advice: bool = Form(False),
    ai_max_pins: int = Form(12),
    ai_propose: bool = Form(False),
    roles: list[str] = Form(default_factory=list),
    include_pins: list[str] = Form(default_factory=list),
    exclude_pins: list[str] = Form(default_factory=list),
    ai_suggest: bool = Form(False),
    level_source: str = Form("convention"),
    kernel: str = Form(""),
    kernel_base: str = Form(""),
    facts: str = Form(""),
    ai_draft: UploadFile | None = File(None),
    docs: list[UploadFile] = File(default_factory=list),
    templates: list[UploadFile] = File(default_factory=list),
    dts: UploadFile | None = File(None),
    overlay: UploadFile | None = File(None),
    insert_after: str | None = Form(None),
):
    """Checks and updates what's already in the DTS from the SAME workbook already uploaded for this
    session, generates what's missing, and never guesses at anything ambiguous - identical behaviour
    to the CLI and the Copilot agent, reached from the web UI you're already using."""
    session = require(workbook_id)
    if not session.excel_path:
        raise HTTPException(409, "This workbook session has no stored file (uploaded before this "
                                 "feature existed) - upload it again.")
    d = _usb_temp_dir()
    try:
        dts_path = await _save_upload(dts, d) if dts is not None else None
        overlay_path = await _save_upload(overlay, d) if overlay is not None else None
        draft_path = await _save_upload(ai_draft, d) if ai_draft is not None else None
        docs_paths = [await _save_upload(f, d) for f in docs]
        template_paths = [await _save_upload(f, d) for f in templates]
        ns = _UsbNS(
            excel=session.excel_path, project=project, dts=dts_path,
            out=os.path.join(d, "result.dtsi"), report=os.path.join(d, "report.md"),
            pattern=pattern, overlay=overlay_path, insert_after=insert_after,
            vbus_pin=vbus_pin, inventory=os.path.join(d, "inventory.md"),
            combined=os.path.join(d, "combined.dts"), no_cache=False, no_update=no_update,
            keyword=keyword, match=match, list_projects=False,
            ai_advice=ai_advice, ai_max_pins=max(1, min(ai_max_pins, 30)),
            ai_propose=ai_propose, role=roles, include_pin=include_pins, exclude_pin=exclude_pins, template=template_paths, out_dir=os.path.join(d, "files"), ai_suggest=ai_suggest, level_source=level_source, kernel=(kernel.strip() or None), kernel_reference=None, kernel_base=(kernel_base.strip() or None), facts=facts, ai_draft=draft_path, docs=docs_paths, ai_proposal_out=os.path.join(d, "hybrid.dtsi"),
        )
        res = _usb_tool.run(ns)
        overlay_text = (open(res.overlay_path, encoding="utf-8", errors="replace").read()
                        if res.overlay_path and os.path.exists(res.overlay_path) else None)
        inventory_text = (open(res.inventory_path, encoding="utf-8", errors="replace").read()
                          if res.inventory_path and os.path.exists(res.inventory_path) else None)
        combined_text = (open(res.combined_path, encoding="utf-8", errors="replace").read()
                         if res.combined_path and os.path.exists(res.combined_path) else None)
        return {
            "out_text": res.out_text, "report_text": res.report_text,
            "overlay_text": overlay_text, "inventory_text": inventory_text,
            "combined_text": combined_text, "pattern": res.pattern,
            "pattern_evidence": res.pattern_evidence, "summary": res.summary,
            "validation_problems": res.validation_problems, "dtc_result": res.dtc_result,
            "exit_code": res.exit_code, "roles_applied": res.roles_applied, "included_pins": res.included_pins, "excluded_pins": res.excluded_pins, "sheet_conflict_pins": res.sheet_conflict_pins, "role_warnings": res.role_warnings, "ai_suggest": res.ai_suggest, "ai_check": res.ai_check, "ai_ledger": res.ai_ledger,
            "files": res.files, "bundle_notes": res.bundle_notes, "kernel_resolution": res.kernel_resolution, "overview": res.overview,
            "bundle_zip_b64": (base64.b64encode(dts_style.zip_bytes(res.files)).decode("ascii") if res.files else None),
            "ai_proposal": ({k: v for k, v in res.ai_proposal.items() if k not in ("path", "python_only_text")} if res.ai_proposal else None),
        }
    except SystemExit as e:
        raise HTTPException(400, str(e))
    finally:
        shutil.rmtree(d, ignore_errors=True)


@app.get("/api/eth-tool/families")
def eth_tool_families():
    from app import eth_generator
    return {"families": eth_generator.families()}


@app.post("/api/workbook/{workbook_id}/eth-tool/generate")
async def eth_tool_generate(
    workbook_id: str,
    project: str = Form(...),
    families: list[str] = Form(default_factory=list),
    include_pins: list[str] = Form(default_factory=list),
    exclude_pins: list[str] = Form(default_factory=list),
    ai_check: bool = Form(False),
    kernel: str = Form(""),
    kernel_base: str = Form(""),
    templates: list[UploadFile] = File(default_factory=list),
):
    """Ethernet pin states and the &ethernet consumer from the workbook already uploaded for this session (RGMII / RMII / MDIO / EMAC / SGMII)."""
    session = require(workbook_id)
    if not session.excel_path:
        raise HTTPException(409, "This workbook session has no stored file (uploaded before this feature existed) - upload it again.")
    d = _usb_temp_dir()
    try:
        template_paths = [await _save_upload(f, d) for f in templates]
        ns = _UsbNS(excel=session.excel_path, project=project, list_projects=False, family=families or None,
                    include_pin=include_pins, exclude_pin=exclude_pins, ai_check=ai_check, template=template_paths,
                    kernel=(kernel.strip() or None), kernel_base=(kernel_base.strip() or None), out_dir=os.path.join(d, "files"))
        res = _eth_tool.run(ns)
        return {
            "overview": res.overview, "pins": res.pins, "left_out": res.left_out, "items": res.items, "counts": res.counts,
            "files": res.files, "bundle_notes": res.bundle_notes, "kernel_resolution": res.kernel_resolution,
            "verification": res.verification, "validation_problems": res.validation_problems, "dtc_result": res.dtc_result,
            "report_text": res.report_text, "column_hits": res.column_hits, "ai_check": res.ai_check, "ai_facts": res.ai_facts, "ai_ledger": res.ai_ledger, "soc": res.soc, "soc_known": res.soc_known,
            "bundle_zip_b64": (base64.b64encode(dts_style.zip_bytes(res.files)).decode("ascii") if res.files else None),
        }
    except SystemExit as e:
        raise HTTPException(400, str(e))
    finally:
        shutil.rmtree(d, ignore_errors=True)


@app.get("/api/spi-tool/families")
def spi_tool_families():
    from app import spi_generator
    return {"families": spi_generator.families()}


@app.post("/api/workbook/{workbook_id}/spi-tool/generate")
async def spi_tool_generate(
    workbook_id: str,
    project: str = Form(...),
    families: list[str] = Form(default_factory=list),
    include_pins: list[str] = Form(default_factory=list),
    exclude_pins: list[str] = Form(default_factory=list),
    ai_check: bool = Form(False),
    kernel: str = Form(""),
    kernel_base: str = Form(""),
    templates: list[UploadFile] = File(default_factory=list),
):
    """SPI pin states and the &spiN consumers from the workbook already uploaded for this session (master / slave)."""
    session = require(workbook_id)
    if not session.excel_path:
        raise HTTPException(409, "This workbook session has no stored file (uploaded before this feature existed) - upload it again.")
    d = _usb_temp_dir()
    try:
        template_paths = [await _save_upload(f, d) for f in templates]
        ns = _UsbNS(excel=session.excel_path, project=project, list_projects=False, family=families or None,
                    include_pin=include_pins, exclude_pin=exclude_pins, ai_check=ai_check, template=template_paths,
                    kernel=(kernel.strip() or None), kernel_base=(kernel_base.strip() or None), out_dir=os.path.join(d, "files"))
        res = _spi_tool.run(ns)
        return {
            "overview": res.overview, "pins": res.pins, "left_out": res.left_out, "items": res.items, "counts": res.counts,
            "files": res.files, "bundle_notes": res.bundle_notes, "kernel_resolution": res.kernel_resolution,
            "verification": res.verification, "validation_problems": res.validation_problems, "dtc_result": res.dtc_result,
            "report_text": res.report_text, "column_hits": res.column_hits, "ai_check": res.ai_check, "ai_facts": res.ai_facts, "ai_ledger": res.ai_ledger, "soc": res.soc, "soc_known": res.soc_known,
            "bundle_zip_b64": (base64.b64encode(dts_style.zip_bytes(res.files)).decode("ascii") if res.files else None),
        }
    except SystemExit as e:
        raise HTTPException(400, str(e))
    finally:
        shutil.rmtree(d, ignore_errors=True)


@app.post("/api/workbook/{workbook_id}/generic-tool/generate")
async def generic_tool_generate(
    workbook_id: str,
    project: str = Form(...),
    families: list[str] = Form(default_factory=list),
    ai_draft: bool = Form(False),
):
    """Opt-in, UNVERIFIED preview for non-Qualcomm workbooks (e.g. Telechips): pins as the workbook names them. Not used by any other engine."""
    session = require(workbook_id)
    if not session.excel_path:
        raise HTTPException(409, "This workbook session has no stored file (uploaded before this feature existed) - upload it again.")
    try:
        ns = _UsbNS(excel=session.excel_path, project=project, list_projects=False, family=families or None, ai_draft=ai_draft, out_dir=None)
        res = _generic_tool.run(ns)
        return {"overview": res.overview, "pins": res.pins, "items": res.items, "counts": res.counts, "files": res.files,
                "verification": res.verification, "ai_draft": res.ai_draft, "report_text": res.report_text, "soc": res.soc}
    except SystemExit as e:
        raise HTTPException(400, str(e))


@app.get("/api/uart-tool/families")
def uart_tool_families():
    from app import uart_generator
    return {"families": uart_generator.families()}


@app.post("/api/workbook/{workbook_id}/uart-tool/generate")
async def uart_tool_generate(
    workbook_id: str,
    project: str = Form(...),
    families: list[str] = Form(default_factory=list),
    include_pins: list[str] = Form(default_factory=list),
    exclude_pins: list[str] = Form(default_factory=list),
    ai_check: bool = Form(False),
    kernel: str = Form(""),
    kernel_base: str = Form(""),
    templates: list[UploadFile] = File(default_factory=list),
):
    """UART pin states and the &uartN consumers from the workbook already uploaded for this session (UART / HSUART)."""
    session = require(workbook_id)
    if not session.excel_path:
        raise HTTPException(409, "This workbook session has no stored file (uploaded before this feature existed) - upload it again.")
    d = _usb_temp_dir()
    try:
        template_paths = [await _save_upload(f, d) for f in templates]
        ns = _UsbNS(excel=session.excel_path, project=project, list_projects=False, family=families or None,
                    include_pin=include_pins, exclude_pin=exclude_pins, ai_check=ai_check, template=template_paths,
                    kernel=(kernel.strip() or None), kernel_base=(kernel_base.strip() or None), out_dir=os.path.join(d, "files"))
        res = _uart_tool.run(ns)
        return {
            "overview": res.overview, "pins": res.pins, "left_out": res.left_out, "items": res.items, "counts": res.counts,
            "files": res.files, "bundle_notes": res.bundle_notes, "kernel_resolution": res.kernel_resolution,
            "verification": res.verification, "validation_problems": res.validation_problems, "dtc_result": res.dtc_result,
            "report_text": res.report_text, "column_hits": res.column_hits, "ai_check": res.ai_check, "ai_facts": res.ai_facts, "ai_ledger": res.ai_ledger, "soc": res.soc, "soc_known": res.soc_known,
            "bundle_zip_b64": (base64.b64encode(dts_style.zip_bytes(res.files)).decode("ascii") if res.files else None),
        }
    except SystemExit as e:
        raise HTTPException(400, str(e))
    finally:
        shutil.rmtree(d, ignore_errors=True)


@app.get("/api/i2c-tool/families")
def i2c_tool_families():
    from app import i2c_generator
    return {"families": i2c_generator.families()}


@app.post("/api/workbook/{workbook_id}/i2c-tool/generate")
async def i2c_tool_generate(
    workbook_id: str,
    project: str = Form(...),
    families: list[str] = Form(default_factory=list),
    include_pins: list[str] = Form(default_factory=list),
    exclude_pins: list[str] = Form(default_factory=list),
    ai_check: bool = Form(False),
    kernel: str = Form(""),
    kernel_base: str = Form(""),
    templates: list[UploadFile] = File(default_factory=list),
):
    """I2C pin states and the &i2cN controllers from the workbook already uploaded for this session (I2C)."""
    session = require(workbook_id)
    if not session.excel_path:
        raise HTTPException(409, "This workbook session has no stored file (uploaded before this feature existed) - upload it again.")
    d = _usb_temp_dir()
    try:
        template_paths = [await _save_upload(f, d) for f in templates]
        ns = _UsbNS(excel=session.excel_path, project=project, list_projects=False, family=families or None,
                    include_pin=include_pins, exclude_pin=exclude_pins, ai_check=ai_check, template=template_paths,
                    kernel=(kernel.strip() or None), kernel_base=(kernel_base.strip() or None), out_dir=os.path.join(d, "files"))
        res = _i2c_tool.run(ns)
        return {
            "overview": res.overview, "pins": res.pins, "left_out": res.left_out, "items": res.items, "counts": res.counts,
            "files": res.files, "bundle_notes": res.bundle_notes, "kernel_resolution": res.kernel_resolution,
            "verification": res.verification, "validation_problems": res.validation_problems, "dtc_result": res.dtc_result,
            "report_text": res.report_text, "column_hits": res.column_hits, "ai_check": res.ai_check, "ai_facts": res.ai_facts, "ai_ledger": res.ai_ledger, "soc": res.soc, "soc_known": res.soc_known,
            "bundle_zip_b64": (base64.b64encode(dts_style.zip_bytes(res.files)).decode("ascii") if res.files else None),
        }
    except SystemExit as e:
        raise HTTPException(400, str(e))
    finally:
        shutil.rmtree(d, ignore_errors=True)


@app.post("/api/workbook/{workbook_id}/combined/generate")
async def combined_generate(
    workbook_id: str,
    project: str = Form(...),
    peripherals: list[str] = Form(default_factory=list),
    usb_pattern: str = Form("auto"),
    usb_vbus_pins: list[str] = Form(default_factory=list),
    kernel: str = Form(""),
    kernel_base: str = Form(""),
    templates: list[UploadFile] = File(default_factory=list),
):
    """ONE pin-state file and ONE consumer file for USB + Ethernet + SPI + UART + I2C of the workbook already uploaded: each engine generates and verifies its own
    part, then the parts are merged and checked against each other (same GPIO / label / controller twice, dangling pinctrl references)."""
    session = require(workbook_id)
    if not session.excel_path:
        raise HTTPException(409, "This workbook session has no stored file (uploaded before this feature existed) - upload it again.")
    d = _usb_temp_dir()
    try:
        template_paths = [await _save_upload(f, d) for f in templates]
        ns = _UsbNS(excel=session.excel_path, project=project, peripheral=peripherals or None, usb_pattern=usb_pattern if usb_pattern in ("auto", "controller", "regulator") else None,
                    usb_vbus_pin=usb_vbus_pins, kernel=(kernel.strip() or None), kernel_base=(kernel_base.strip() or None), template=template_paths,
                    out_dir=None)
        res = _combined_tool.run(ns)
        return {
            "overview": res.overview, "peripherals": res.peripherals, "conflicts": res.conflicts, "notes": res.notes, "sections": res.sections,
            "decisions": res.decisions, "left_out": res.left_out,
            "files": res.files, "report_text": res.report_text,
            "bundle_zip_b64": (base64.b64encode(dts_style.zip_bytes(res.files)).decode("ascii") if res.files else None),
        }
    except SystemExit as e:
        raise HTTPException(400, str(e))
    finally:
        shutil.rmtree(d, ignore_errors=True)


@app.post("/api/workbook/{workbook_id}/usb-tool/verify")
async def usb_tool_verify(
    workbook_id: str,
    project: str = Form(...),
    all_pins: bool = Form(False),
    dts: UploadFile = File(...),
    existing: list[UploadFile] = File(default_factory=list),
):
    """Checks any DTS - the tool's own output, a colleague's file, or an AI draft - against the SAME
    workbook already uploaded for this session, keeping WRONG / AMBIGUOUS / UNVERIFIABLE strictly
    apart. UNVERIFIABLE is never reported as correct."""
    session = require(workbook_id)
    if not session.excel_path:
        raise HTTPException(409, "This workbook session has no stored file (uploaded before this "
                                 "feature existed) - upload it again.")
    d = _usb_temp_dir()
    try:
        dts_path = await _save_upload(dts, d)
        existing_paths = [await _save_upload(f, d) for f in existing]
        ns = _UsbNS(excel=session.excel_path, project=project, dts=dts_path,
                    report=os.path.join(d, "verify.md"), existing=existing_paths,
                    no_cache=False, all_pins=all_pins)
        res = _usb_verify.run(ns)
        return {
            "report_text": res.report_text, "exit_code": res.exit_code,
            "counts": {"wrong": len(res.wrong), "ambiguous": len(res.ambiguous),
                      "unverifiable": len(res.unverifiable), "ok": len(res.ok)},
        }
    except SystemExit as e:
        raise HTTPException(400, str(e))
    finally:
        shutil.rmtree(d, ignore_errors=True)
