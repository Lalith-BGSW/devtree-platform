"""
Shared constants derived from analyzing the real workbook's header AND a
representative sample of its actual values (with explicit permission from
the user to read specific columns/rows for this purpose - see conversation
history; the full 2,358-row dataset itself was never exported or stored,
only aggregate value-distribution statistics).

Finding: on the real SA8155P SiP, the "PinMuxed Name" / "Selected
Interface" fields hold not just real signal names but also explicit
non-signal markers - 16% of the 2,358 real pin rows are "DNC" (Do Not
Connect) alone, and combined with "NC", "Power", and ground labels
("GND"/"AGND"), well over half the package's balls are not muxable signal
pins at all. These values are NOT missing/null - they are deliberately
populated labels - so a plain "is this field empty" check does not catch
them.

Extended after reviewing real "remainder" DTS output (pins not covered by
any manager-supplied reference file), which surfaced three more
non-signal patterns this list didn't originally catch:
  - Selected Interface as a literal numeric 0 (not text "DNC"/"NC") - an
    unconfigured-pin placeholder used on 48 real rows, paired with either
    a bare "0" PinMuxed Name or a power-rail name.
  - Voltage-regulator/reference pins (VREG*, VCOIN, LNBBCLK*) - not
    caught by the VDD*/VSYS* prefix check since they use different
    prefixes entirely.
  - "Unused <Interface>" as an explicit Interface Group value (e.g.
    "Unused PCIe") - a deliberate not-in-use marker, same intent as DNC
    but phrased differently.
"""

# Case-insensitive exact-match values that mean "this ball is not a real,
# muxable signal pin" - grounds, power rails, and explicit no-connects.
# Used to exclude rows from DTS pinctrl generation (a "full project" DTS
# should not contain a pinctrl node per ground ball) and from the
# duplicate-pin validation check (the same label legitimately repeats
# across hundreds of different physical balls by design, so flagging
# repeats of these values as "duplicate pin" errors is a false positive).
NON_SIGNAL_VALUES = {"dnc", "nc", "n/a", "na", "power", "gnd", "agnd", "vss", "vdd", "0", "0.0"}

# Prefixes checked via str.startswith() - covers rail/reference-clock
# naming families with many distinct suffixes rather than listing every
# one individually.
NON_SIGNAL_PREFIXES = ("vdd", "vsys", "vss", "gnd", "agnd", "vreg", "vcoin", "lnbbclk")


def is_non_signal_value(value) -> bool:
    if value is None:
        return False
    v = str(value).strip().lower()
    if v in NON_SIGNAL_VALUES:
        return True
    if v.startswith(NON_SIGNAL_PREFIXES):
        return True
    if v.startswith("unused") or "unused" in v:
        return True
    return False


def classify_pin(interface_value, pin_name, interface_group=None) -> str:
    """Classifies a row as "non_signal", "signal", or "ambiguous" -
    replacing a simple "is this word on my exclude list" check with a
    real three-way decision based on whether usable data exists at all.

    - "non_signal": interface_value, pin_name, or interface_group matches
      a CONFIRMED non-signal marker (verified against the full real
      dataset, not guessed from a handful of examples).
    - "signal": interface_value or pin_name has ANY real, non-marker
      value. Deliberately does not require recognizing the specific
      word/interface type - an unfamiliar real value is still real data,
      and should never be silently excluded just because the code
      doesn't have a name for it.
    - "ambiguous": NEITHER interface_value nor pin_name has any usable
      value - there is no data supporting a decision either way. This is
      the only case callers should flag for human review rather than
      silently including or excluding. It is never resolved by pattern-
      matching a growing list of "looks suspicious" words - that would
      just be hardcoding wearing a different hat, and would eventually
      misfire on some real value it happens to resemble.
    """
    if (is_non_signal_value(interface_value) or is_non_signal_value(pin_name)
            or is_non_signal_value(interface_group)):
        return "non_signal"
    if interface_value not in (None, "") or pin_name not in (None, ""):
        return "signal"
    return "ambiguous"
