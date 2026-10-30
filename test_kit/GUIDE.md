# Test kit - check every option without real data

**To test in the web page, use the ready-made files in `test_kit/upload_these/`** (one per option, with `README.txt` saying what each should do).

Everything here is **fabricated**: `demo_workbook.xlsx` has 10 invented pins, so the right answer for each is known in advance.
Nothing needs the real workbook, the network or the AI gateway (the kernel check uses GitHub if it can reach it and the built-in table if not).

## The pins in the demo workbook (project `CCS20`)

| Pin | Signal | Workbook says | What the tool must do |
|---|---|---|---|
| GPIO_10 | USB0_OC_B | input, pull-up, 4 mA | USB over-current state: `input-enable`, `bias-pull-up`, `<4>` |
| GPIO_11 | USB0_PWR_EN | output, no pull, 4 mA | VBUS enable: level `output-low` **flagged** NEEDS REVIEW (inferred, not in Excel) |
| GPIO_12 | USB1_OC_B | input, pull-up, 4 mA | same as GPIO_10 |
| GPIO_13 | USB1_PWR_EN | output, no pull, 4 mA | same as GPIO_11 |
| GPIO_14 | USBHUB HUB_RST | output, no pull, 8 mA | generated, **no** level, NEEDS REVIEW |
| GPIO_15 | BOOTCFG FORCE_USB | input, pull-down, 2 mA | **not** generated (boot-strap, read at reset) unless you include it |
| GPIO_20 / 21 | BT UART TX / RX | out no-pull / in pull-up, 2 mA | not in a USB run; in the generic generator: two **separate** nodes (settings differ) |
| GPIO_22 / 23 | camera I2C SDA / SCL | out, no pull, 4 mA | not in a USB run; generic generator: **one** shared node (settings identical) |

## 1. One command - runs all the checks

Windows (PowerShell), from the folder that contains `backend` and `test_kit`:

    cd backend
    .\.venv\Scripts\python.exe ..\test_kit\run_checks.py

(Linux/macOS: `python3 test_kit/run_checks.py`.)  It prints one `PASS` / `FAIL` line per check, about 40 in total, and ends with
`ALL CHECKS PASSED`. Every file it generated is kept in `test_kit/_out/<scenario>/` so you can open them: `files/*_pinmux.dtsi`,
`files/*_overlay.dts`, `report.md`, `log.txt`.

What each group of checks proves:

| Group | Option tested | You should see |
|---|---|---|
| 1 Baseline | none | the 5 USB pins, correct values, no guessed levels, 2 regulators, verifier 0 wrong |
| 2 Templates | `--template` (A: 4 spaces + flat `-state`; B: tabs + nested) | the **layout** follows the template, the **values are identical** to the baseline, none of the template's own pins (gpio90/91/95/96) appear |
| 3 Kernel | `--kernel 6.6` / `5.15` | 6.6 gives flat `-state` nodes, 5.15 nested mux/config, same values; a template beats `--kernel`; `--kernel banana` is refused |
| 4 Decisions | `--level-source`, `--exclude-pin`, `--include-pin`, `--pattern controller` | `none` writes no level; `include-pin GPIO_15` generates the boot-strap pin with its Excel values; `controller` gives `oc-gpio` / `vbus-out-gpio` instead of regulators |
| 5 Update / check | `--dts`, `--no-update` | the 2 deliberate mistakes in `inputs/existing_with_errors.dts` are found (gpio10 drive 16 to 4, bias pull-down to pull-up), the correct node is untouched, a second run changes nothing, check-only does not rewrite the file |
| 6 Generic generator | every peripheral | each pin exactly once, each with its own values, output pins left as NEEDS REVIEW |

If a check fails, open `test_kit/_out/<scenario>/log.txt` and `report.md` for it, and send me the `FAIL` line.

## 2. The same tests in the web page (before / after)

After the second and every later run, the USB result starts with a box **"Compared with the previous run"**. It lists the options of both runs and says in one line whether the pin values changed or only the layout did, with the exact lines that differ, the verifier counts and the open decisions. So: generate once with no options, change one option, generate again, read the box.

Start the app as usual, upload `test_kit/demo_workbook.xlsx`, pick project `CCS20`, then:

1. **USB generator, nothing in Advanced options** - expect 5 pin states, 2 regulators, 2 `&usbN` nodes and 3 decisions listed (levels for gpio11/13 inferred, gpio14 level missing, over-current pins not wired in).
2. **Format of the output files** -> attach `test_kit/templates/template_A_spaces_flat.dtsi` and generate again - the files should change from tabs/nested to 4 spaces/flat. Pin values must not change. "Format" in the result should say your template.
3. Remove the template, type kernel `6.6`, generate - flat nodes; type `5.15` - nested.
4. **Your decisions** -> Output levels = "None" - the `output-low` lines disappear.
5. **Update or check a DTS** panel (top of the page) -> drop `test_kit/inputs/existing_with_errors.dts`:
   - **Check only** - WRONG: 2 (gpio10 drive-strength and bias), CONFIRMED OK for gpio12.
   - **Update 1 File** - download it: the two values are corrected, comments show old -> new.
6. **Generate a draft Device Tree** (query `full`) - all 10 pins, UART in two nodes, I2C in one.

## 3. AI options (needs the Bosch Model Farm gateway; skip if it is not reachable)

* Set AI assistance to **Hybrid**, put the line from `test_kit/inputs/facts.txt` (`gpio11 boots output-low per the schematic`) in "Facts you know".
* Expect: gpio11 gets `output-low` with "source: FACT 1" and its NEEDS REVIEW goes away; **every other value is unchanged**; the table "Where the AI was used in this run" shows the call count; the verifier still says 0 wrong.
* With no facts, documents or draft the "complete the DTS" step is skipped (0 calls) - that is correct, not a fault.
* **Every Hybrid run also makes one AI data-check call.** On the demo workbook it must report **GPIO_12**: the remark says "External pull-down required" but the pull cell says PULL_UP. It appears in the box "AI check of your workbook data" and in the report. The generated values do not change.

## 4. What this kit cannot prove

* **`dtc` compile** - needs your BSP's kernel tree. Run your normal DTS build on the generated files; that is the one real proof.
* **Vendor BSP format** - a kernel version describes mainline. For your BSP, give an accepted DTS as the template.
* **Real boot levels, the `&usb1` VBUS pin, the sub-PCB variant** - hardware facts only you can state.
