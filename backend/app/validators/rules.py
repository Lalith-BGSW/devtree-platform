"""
Deterministic validation rules over NormalizedRecords. No LLM involved -
these are plain Python checks, matching the spec's validation list:
  - duplicate GPIO/ball assignment within a project
  - missing interface (pin exists but interface field empty)
  - UART completeness (TX present without RX, or vice versa)
  - project consistency (mandatory fields missing)

Field lookups are done via a small set of candidate slugs, since the exact
slug depends on the real workbook's column names (e.g. "ball_id" vs "gpio").
This keeps the rules usable without hardcoding to one specific workbook
revision.
"""

from __future__ import annotations
from app.models import NormalizedRecord, ValidationIssue
from app.constants import is_non_signal_value

# Exact slug matches checked first; "id" is deliberately excluded - on the
# real workbook that's a row-sequence number (1, 2, 3...), not a pin/ball
# identifier, so including it would make the duplicate-pin check meaningless
# and silently mislabel rows. Substring candidates handle the real
# workbook's chip-revision-prefixed variants (sa8155sip_ball_name,
# sa8195sip_ball_name, sipv1_ball_name, etc.) without hardcoding each one.
_PIN_ID_EXACT_CANDIDATES = ("pinmuxed_name", "gpio", "pin_id", "pin_name")
_PIN_ID_SUBSTRING_CANDIDATES = ("ball_name", "ball_id")
_INTERFACE_CANDIDATES = ("selected_interface", "interface", "interface_group")
_SIGNAL_CANDIDATES = ("gen5_signal_name", "signal", "signal_name", "pinmuxed_name")
_MANDATORY_DEFAULT = ("selected_interface",)


def _first_present(fields: dict, candidates: tuple[str, ...]) -> str | None:
    for key in candidates:
        if key in fields and fields[key] not in (None, ""):
            return str(fields[key])
    return None


def _first_pin_identifier(fields: dict) -> str | None:
    """Best DISPLAY name for a pin - prefers the project's assigned net
    label (e.g. "GPIO43") since that's what an engineer recognizes. NOT
    safe to use for uniqueness checks: on the real workbook, labels like
    "GND"/"AGND"/"DNC" are the PinMuxed Name on hundreds of different
    physical balls by design - see _unique_ball_key() below for that.

    The ball_name/ball_id fallback (only reached when PinMuxed Name
    itself is empty) skips any value that's itself a non-signal marker
    before accepting it - the workbook has five ball-name variant columns
    (one per chip package revision) and they can genuinely disagree about
    a given row (e.g. sa8155sip_ball_name='DNC' while
    sa8155soc_ball_name='REFGEN_REXT0' for the same real signal pin -
    confirmed real case). Taking whichever came first in column order
    without checking its value could return 'DNC' for a real pin."""
    exact = _first_present(fields, _PIN_ID_EXACT_CANDIDATES)
    if exact is not None:
        return exact
    for key, val in fields.items():
        if val in (None, ""):
            continue
        if any(sub in key for sub in _PIN_ID_SUBSTRING_CANDIDATES) and not is_non_signal_value(val):
            return str(val)
    # every variant was itself a marker (or absent) - still return
    # something rather than nothing
    for key, val in fields.items():
        if val in (None, ""):
            continue
        if any(sub in key for sub in _PIN_ID_SUBSTRING_CANDIDATES):
            return str(val)
    return None


def _unique_ball_key(fields: dict) -> str | None:
    """Best UNIQUENESS key for a physical pin - prefers a ball_id field
    (the BGA grid coordinate, e.g. "AF12") over ball_name, since ball_id is
    unique per physical location WITHIN A SINGLE PACKAGE VARIANT's own
    coordinate grid. Critically, it is NOT comparable ACROSS variants: the
    workbook has five ball_id columns (one per chip package revision -
    SA8155SIP/SA8195SIP/SA8155SoC/SiPV1/PRE-SiPV2), and two completely
    different physical balls on two different packages can coincidentally
    share the same coordinate label. Confirmed real case: row 133's
    SA8155SIP Ball ID is 'E35' (a real WUP_GPIO pin); row 271's SA8155SoC
    Ball ID is ALSO 'E35' (an unrelated SP_ARI_POWER_ALARM pin, on a
    different package entirely) - comparing the raw strings without
    tracking which column they came from flagged this as a duplicate,
    when it's actually two unrelated balls on two different packages that
    just happen to share a coordinate label. The key is prefixed with the
    source column name specifically to prevent this: 'sa8155sip_ball_id:
    E35' and 'sa8155soc_ball_id:E35' are correctly treated as different
    keys, even though the coordinate text is identical."""
    for key, val in fields.items():
        if val in (None, ""):
            continue
        if "ball_id" in key:
            return f"{key}:{val}"
    for key, val in fields.items():
        if val in (None, ""):
            continue
        if "ball_name" in key:
            return f"{key}:{val}"
    return None


def run_validation(records: list[NormalizedRecord],
                    mandatory_fields: tuple[str, ...] = _MANDATORY_DEFAULT
                    ) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not records:
        return issues

    project = records[0].project

    # --- duplicate pin/ball assignment ---
    # Uses the physical ball_id/ball_name (see _unique_ball_key), NOT the
    # display pin name - "AGND"/"GND"/"DNC"/"NC" legitimately repeat across
    # hundreds of different physical balls on the real workbook, and
    # flagging every repeat as an "error" would drown out real duplicates
    # in noise. Non-signal rows are skipped from this check entirely.
    seen: dict[str, int] = {}
    for rec in records:
        display_pin = _first_pin_identifier(rec.fields)
        iface = _first_present(rec.fields, _INTERFACE_CANDIDATES)
        if is_non_signal_value(display_pin) or is_non_signal_value(iface):
            continue
        key = _unique_ball_key(rec.fields)
        if key is None:
            continue
        if key in seen:
            # The key is prefixed with its source column (e.g.
            # "sa8155sip_ball_id:E35") to prevent cross-package
            # coincidences from colliding - strip that prefix for the
            # human-facing message, which should show the plain
            # coordinate, not the internal comparison key.
            display_ball = key.split(":", 1)[-1]
            issues.append(ValidationIssue(
                severity="error",
                rule="duplicate_pin_assignment",
                project=project,
                message=f"Ball '{display_ball}' (pin '{display_pin}') is assigned on more "
                        f"than one row (rows {seen[key]} and {rec.row_index}).",
                row_index=rec.row_index,
            ))
        else:
            seen[key] = rec.row_index

    # --- missing interface ---
    for rec in records:
        pin = _first_pin_identifier(rec.fields)
        iface = _first_present(rec.fields, _INTERFACE_CANDIDATES)
        if pin is not None and iface is None:
            issues.append(ValidationIssue(
                severity="warning",
                rule="missing_interface",
                project=project,
                message=f"Pin/GPIO '{pin}' has no interface assigned.",
                row_index=rec.row_index,
            ))

    # --- UART TX/RX completeness (heuristic: signal name contains TX/RX) ---
    tx_signals = []
    rx_signals = []
    for rec in records:
        iface = (_first_present(rec.fields, _INTERFACE_CANDIDATES) or "").lower()
        signal = (_first_present(rec.fields, _SIGNAL_CANDIDATES) or "")
        if "uart" not in iface:
            continue
        su = signal.upper()
        if "TX" in su:
            tx_signals.append((rec.row_index, signal))
        if "RX" in su:
            rx_signals.append((rec.row_index, signal))
    if tx_signals and not rx_signals:
        issues.append(ValidationIssue(
            severity="error",
            rule="uart_incomplete",
            project=project,
            message=f"UART TX signal(s) found ({', '.join(s for _, s in tx_signals)}) "
                    f"but no matching RX signal.",
        ))
    elif rx_signals and not tx_signals:
        issues.append(ValidationIssue(
            severity="error",
            rule="uart_incomplete",
            project=project,
            message=f"UART RX signal(s) found ({', '.join(s for _, s in rx_signals)}) "
                    f"but no matching TX signal.",
        ))

    # --- mandatory field checks ---
    for rec in records:
        missing = [f for f in mandatory_fields if f not in rec.fields or rec.fields[f] in (None, "")]
        if missing:
            issues.append(ValidationIssue(
                severity="warning",
                rule="missing_mandatory_field",
                project=project,
                message=f"Row {rec.row_index} is missing mandatory field(s): {', '.join(missing)}.",
                row_index=rec.row_index,
            ))

    return issues
