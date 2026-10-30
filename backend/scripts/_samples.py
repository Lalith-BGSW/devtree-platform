"""Tests that read the customer sample workbooks / DTS files in backend/samples.
Those files are customer data and are NOT shipped in code packs. need() makes such a test say so in one line and exit 0
('SKIPPED') instead of crashing with FileNotFoundError, so a run on a fresh checkout shows PASSED / SKIPPED / FAILED honestly.
A skipped test proves nothing: copy the samples into backend/samples and run again to get the real result."""
import os
import re
import sys

KNOWN = {"Nissan_CCS20_SA8155PSIP_PinMux.xlsx", "Honda_database_v2.xlsx", "ccs20-pinmux.dtsi", "suzuki-pinmux.dtsi",
         "suzuki-overlay.dts", "ai_draft_usb.dts", "ai_draft_regulator_ccs20.dts"}


def need(script_file):
    src = open(script_file, encoding="utf-8").read()
    names = sorted({n for n in re.findall(r"[\"']([^\"'/\\]+\.(?:xlsx|xlsm|dtsi|dts))[\"']", src) if n in KNOWN})
    samples = os.path.join(os.path.dirname(os.path.abspath(script_file)), "..", "samples")
    missing = [n for n in names if not os.path.exists(os.path.join(samples, n))]
    if missing:
        print(f"SKIPPED {os.path.basename(script_file)}: customer sample(s) not present in backend/samples: {', '.join(missing)}. "
              f"This proves nothing - copy them in and run again.")
        sys.exit(0)
