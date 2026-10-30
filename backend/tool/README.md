# USB DTS tool (tested engine)

This is the exact engine used by:
- `usb_dts_tool.py` / `verify_dts.py` from the command line
- the Copilot agent in `../../.github/agents/`
- the web UI, via the API endpoints in `app/main.py` under `/api/usb-tool/*`

All three call the same `run(args)` functions - nothing is reimplemented per-interface, so results are
identical whichever way you use it.

    python usb_dts_tool.py --excel ../samples/Nissan_CCS20_SA8155PSIP_PinMux.xlsx --project CCS20 \
        --dts ../samples/ccs20-pinmux.dtsi --out /tmp/result.dtsi --report /tmp/report.md

    python verify_dts.py --excel ../samples/Nissan_CCS20_SA8155PSIP_PinMux.xlsx --project CCS20 \
        --dts /tmp/result.dtsi --existing ../samples/ccs20-pinmux.dtsi --report /tmp/verify.md

    python test_usb_dts_tool.py      # 28 checks against real Nissan/Suzuki data - should end with
                                      # USB DTS TOOL TEST PASSED

See the project root README for the full flag reference, what each output file contains, and the API
endpoints exposed for the web UI.

    python usb_dts_tool.py ... --ai-advice     # optional: append an AI EXPLANATION of the flagged pins to the report
                                               # (needs backend/.env; never changes the DTS - see the root README)
