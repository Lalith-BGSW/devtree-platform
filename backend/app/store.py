"""
In-memory store keyed by workbook_id. Fine for local/dev use (matches the
scope of this handover: one engineer, one workbook session at a time).

Swap for Redis/Postgres later if this needs to survive process restarts or
serve multiple concurrent users - the interface below is the seam to do
that behind without touching the API layer.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from app.models import NormalizedRecord, ValidationIssue
from app.parsers.workbook_parser import ParsedWorkbook


@dataclass
class WorkbookSession:
    parsed: ParsedWorkbook
    # Absolute path to the ORIGINAL uploaded workbook bytes, kept on disk for
    # the lifetime of this session (see app/main.py's upload handler) so
    # features that need to re-read the real file - the USB tool bridge
    # endpoints - can do so without asking the user to upload it a second
    # time. Every other feature works from `parsed`/`records_by_project`
    # alone and never touches this.
    excel_path: str | None = None
    soc_entity: str | None = None
    project_entities: list[str] = field(default_factory=list)
    subgroup_entities: list[str] = field(default_factory=list)
    records_by_project: dict[str, list[NormalizedRecord]] = field(default_factory=dict)
    validation_by_project: dict[str, list[ValidationIssue]] = field(default_factory=dict)
    # {project: {field_slug: [distinct real values seen]}} - built at
    # /build time, fed into LLM intent parsing so it knows the file's
    # actual vocabulary (e.g. "BTWLAN" as the real Bluetooth abbreviation)
    # instead of guessing from natural-language terms alone.
    vocabulary_by_project: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    # Summary of the most recent DTS batch-update run, if any - fed into
    # chat grounding so questions like "what changed in file X" or "how
    # many pins are still uncovered" can be answered accurately.
    dts_update_history: dict | None = None


_sessions: dict[str, WorkbookSession] = {}


def _try_recover_session(workbook_id: str) -> WorkbookSession | None:
    """If the server was restarted or reloaded, auto-recover the session
    from the preserved workbook in data/uploads so the user doesn't have
    to re-upload. Re-parses and re-matches the file, then defers to the
    SAME _do_build() the /build and /classify-schema endpoints use (a
    deferred import - app.main already imports from this module at load
    time, so importing back from here has to happen inside the function,
    not at module level, to avoid a circular import) rather than
    reimplementing a subset of it: an earlier version of this function
    called normalize() directly and stopped there, which skipped
    _do_build()'s zero-records safety check (silently "succeeding" with
    an unusable session instead of raising a clear error) and never
    populated validation_by_project, vocabulary_by_project, or the search
    index - a recovered session's "Ask the Workbook" panel would silently
    return no results for every query, with no indication why."""
    import glob
    import os
    uploads_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "uploads")
    pattern = os.path.join(uploads_dir, f"{workbook_id}.*")
    matches = glob.glob(pattern)
    if not matches:
        return None
    excel_path = matches[0]
    try:
        from app.parsers.workbook_parser import parse_workbook, build_analysis
        from app.schema_registry import list_known_schemas
        parsed = parse_workbook(excel_path)
        analysis = build_analysis(parsed)
        if analysis.matched_schema_name is None:
            return None  # can't safely re-resolve an unconfirmed schema without a human
        matched = next((s for s in list_known_schemas() if s.name == analysis.matched_schema_name), None)
        if matched is None:
            return None
        session = WorkbookSession(parsed=parsed, excel_path=excel_path)
        session.soc_entity = matched.soc_entity
        session.project_entities = list(matched.project_entities)
        session.subgroup_entities = list(matched.subgroup_entities)
        _sessions[workbook_id] = session
        from app.main import _do_build   # deferred: app.main imports this module at load time
        _do_build(workbook_id, session)  # same safety check + indexing every normal build gets
        return session
    except Exception:
        _sessions.pop(workbook_id, None)  # don't leave a half-initialized session behind
        return None


def put(workbook_id: str, session: WorkbookSession) -> None:
    _sessions[workbook_id] = session


def get(workbook_id: str) -> WorkbookSession | None:
    session = _sessions.get(workbook_id)
    if session is None:
        session = _try_recover_session(workbook_id)
    return session


def require(workbook_id: str) -> WorkbookSession:
    session = get(workbook_id)
    if session is None:
        raise KeyError(f"Unknown workbook_id: {workbook_id}")
    return session
