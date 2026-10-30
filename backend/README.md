# PinMux → Device Tree Platform

Turns a PinMux Excel workbook into Linux Device Tree (`.dts`/`.dtsi`)
data. Everything that ends up in a DTS file is deterministic Python. An optional LLM (Bosch Model Farm) can EXPLAIN
the pins the tool refused to generate - it can never write or change any generated value. Two workflows:

1. **Generate from scratch** - build a DTS file in a reference file's
   style, from Excel data alone.
2. **Update existing reference files** - upload real DTS/DTSI files, patch
   only the values that disagree with current Excel data (leaving
   everything else byte-identical), save each under a new filename, and
   generate a "remainder" file for any Excel-defined pin not covered by
   any of the uploaded files.

## Verified status, scope and limits (read this first)

**Verified**
- Deterministic: every value comes from a workbook cell; no LLM generates or decides anything. The optional AI
  advisory is explanation-only and is tested to leave the generated DTS byte-identical.
- 15 backend test scripts and 35 USB-tool checks pass from a fresh extract, including a token-for-token
  reproduction of the accepted Suzuki Gerrit change and a real `dtc` syntax compile of the generated output
  for CCS20, POC_NESSY2, Honda and Suzuki (including the patched real pinmux files).
- Accounting: for every project tried (CCS20, POC_NESSY2, Honda, Suzuki, Iveco B1, A0_sample) each
  USB-matched pin lands in exactly one category - nothing is silently dropped.

**Not verified / not claimed**
- `dtc` here checks syntax and label consistency only (base-tree labels are stubbed). Nothing has been built
  against a real kernel tree, so device-tree *bindings* are unchecked.
- Consumer wiring (`&usbN` blocks, regulators) rests on two accepted/reference examples. With no existing DTS and
  no `--pattern`, both shapes are previewed in the combined view and neither is written to the output.
- Output levels (output-high/low) are never derived from Excel; they are left as NEEDS REVIEW.

**What generalises, and what was learned from one project.** Works across projects and SoCs of the same workbook family
(tested: CCS20, POC_NESSY2 = SA8155P; Honda, Suzuki = SA6155P; Iveco = SA7255P; TCC8050 fails closed). Pin classification, values,
verification, shared / boot-strap pins and the non-TLMM fail-closed rule do not depend on a project. Three things come from
the reference examples and are labelled so: (1) role names - the built-in keywords (OC, PWR_EN, VBUS) are the Gen5 naming seen so
far; a project that names them differently (Iveco: USB_FAULT_B, 5V0S_USB_EXTERNAL_EN) is taught with `--role SIGNAL=oc|vbus`
(or the "Signal roles" field) - until then those pins still get nodes, just not wired; (2) the `dwc3@a600000` / `usb2_phy0`
block is SA8155P-only (CCS20) and is emitted for no other SoC; (3) `qcom,default-mode-host` on usb0, 5 V regulators and the
output-level convention come from the two accepted examples (CCS20, Suzuki) - tagged as inferences, not read from the Excel.

**The general keyword generator ("Extract Pins by Signal")** follows the same fail-closed rule: a pin whose register is not a
`TLMM_GPIO_CFG...` one (SA7255P's SAIL pins) is listed in a NOT GENERATED comment and the review report, never emitted into
`&tlmm`. It also lists pins of the same Interface Group that the keyword missed (e.g. an IMU's chip-select when searching "spi") as
suggestions to confirm - never auto-included, because the same group also holds unrelated buses.

**What was removed from the USB side (it added noise, not output).** The quick-ask keyword box; the old `usb-consumer` endpoint, its models
and client code (the USB engine replaces them); the Extract panel's USB consumer section and its USB "Generate DTS" button; the
AI advisory in Hybrid mode (it stays in Explain mode); and the report sections that only said "none" - "Updated in existing file" with
no file, the "(none in file)" verification table, and empty unsupported-controller / cross-sheet-conflict headings. A section appears
only when it has something to say.

**What Hybrid mode assumes, and where that lives.** With no project file and no `--pattern`, Hybrid completes the output in the style of
the accepted example for the SAME SoC, from `backend/app/reference_templates.json` (SA8155P -> controller style, the CCS20 example;
SA6155P -> regulator style, the Suzuki change; any other SoC -> the default, reported as weaker evidence). To teach it a new accepted
example, edit that file - no code change. Output levels default to `convention` (a VBUS enable is OFF at boot, flagged; other output pins stay
NEEDS REVIEW); `excel` (the workbook's ResetValue, a register default that is "low" on almost every pin) and `none` are explicit choices in
Advanced options. The AI is called only when it has something to complete (facts, documents or a draft); with none it is skipped and the
report says so.

**Template-driven output files.** The USB output is written as separate files by role - `<project>_usb_pinmux.dtsi` (the `&tlmm`
pin states) and `<project>_usb_overlay.dts` (the `&usbN` consumers and regulators) - in the FORMAT of the templates you give
(`--template FILE`, repeatable; "Template DTS files" in Advanced options): indentation, `label: node` style, `mux{}`/`config{}` or flat
nodes, blank-line habits, line endings, the copyright header and `/dts-v1/; /plugin/;`. Only format is learned. No pin, label or
`#include` of a template is ever copied (the includes are listed in the report for a person to decide), and a restyled pin node is
parsed back and must mean exactly what the verified output meant - otherwise that file keeps the default format and says why. The
written files are verified again by the independent verifier, and they are made from the hybrid result only when it is clean. With
no template the files use the default (vendor BSP) format; with an existing DTS (`--dts`) that file is its own template.

**Format by Linux kernel version (no template available).** `--kernel 6.6` (field "Linux kernel version" in Advanced options) makes the
pin-state format follow that kernel. Measured on `sa8155p-adp.dts` of the mainline tree: v5.15 and v6.1 use nested
`label: label { mux { pins; function; } config {..} }`; v6.2 up to v6.12 use flat `label: label-name-state { pins; function; .. }`.
The tool fetches that tag's reference file (listed per SoC in `app/kernel_profiles.json`; `--kernel-reference PATH` names another;
`KERNEL_RAW_BASE` points at an internal mirror; the PowerShell/Windows-login route is used when the proxy asks for it) and learns only
its FORMAT - never its pins, labels, includes or copyright header. If the network is unreachable it uses the table of measured rules in
the same JSON file; for a version outside the measured range (5.15 - 6.12) it keeps the default format and says so - nothing is guessed.
A `--template` always wins. These are mainline conventions: a vendor BSP kernel (msm-5.4 and similar) can differ at the same version, so
the report says where the format came from. The overlay file has no kernel-version convention and keeps the template / default format.

**Vendor kernels and provenance.** A version number alone does not identify a vendor BSP's conventions. The report therefore always starts the
"Output files and format" section with `Format source:` - your template, a kernel reference (fetched), the built-in table, or the built-in
default ("NOT confirmed to match your BSP"). To follow a vendor tree, give `--kernel-base URL` (or env `KERNEL_RAW_BASE`, or the "Vendor kernel
tree" field): files are read as `URL/<tag>/<path>`. If a template and a kernel are both given, the template wins and the report says when its
layout differs from the kernel's. The only source that proves your project's format is an accepted file of your project (`--template`).
`scripts/run_all_tests.py` runs every test and prints PASSED / SKIPPED (customer sample not present) / FAILED.

**Result at a glance.** The card above the generated DTS is counted from the FINAL files (pin states, `&usbN` nodes, regulators, NEEDS REVIEW
lines, verifier result, format source, what the AI did) - never from an intermediate step. Its status is green only when nothing is left to
decide; otherwise it says how many decisions remain. The Python-only step's own counts sit in a collapsed detail. Every tab has a one-line
caption saying what it is.

The card also lists **the three parts of an accepted change** - 1. Regulators (`regulator-fixed`, root nodes), 2. Pin states (`&tlmm`), 3. USB
nodes (`&usbN`) - each with its count, the file it is in, a Copy button, and, when a part is absent, the reason (e.g. controller style has no
regulators because it wires oc-gpio / vbus-out-gpio on `&usbN` directly). In the overlay file the regulators come before the `&usbN` nodes.

**DTS Updater additions (merged from a second AI session).** The generic updater now keeps each file's own indentation, keeps a `default`/
`sleep`-state node unchanged (with a review note, since the workbook has no state-specific data), keeps an existing `function` when Excel only
gives an unverified candidate, shows per file how many pin states its format checker recognised, and - only if you tick the box and
`node_extractor` is on - lets the AI inspect unrecognised nodes as read-only advice. The template engine also understands
`pinmux{}/pinconf{}` layouts, TLMM controllers declared inside a SoC node, and comments inside nodes; a template with pin-state blocks it
cannot read is ignored (stated) rather than half-learned, and a fetched kernel file in an unfamiliar layout gives "unrecognized" instead of a guessed format.

Open items are split into **decisions** (a person must answer: a missing VBUS-enable pin, output levels, cross-sheet variants) and **for
information** (what was deliberately not generated and why: boot-strap pins, shared I2C pins, the hub IC node...). The card counts both;
the headline only counts decisions.

**Where the AI is used (and the ledger).** Six features can call the LLM: Explain flagged pins, Complete the DTS from facts /
documents / a draft, Suggest roles + audit missed pins, and Cross-check your workbook data for contradictions (all USB; ON by default; the data check runs on every Hybrid run and only reports - it never changes a value), plus Decode unknown cell values, Read unrecognised
DTS formats (generic; OFF unless listed in `LLM_FEATURES`, each falling back to its non-AI behaviour). Every USB
report with an AI mode on ends with "Where the AI was used in this run": each feature's switch, what happened, the gateway's real calls
and tokens, and the effect on the DTS (e.g. "0 lines of the final file differ from Python-only"). The AI never changes a verified line, so
that effect is usually nil by design - the table makes that visible instead of implied.

**Signal-name vocabulary (data, not code): `backend/app/usb_role_keywords.json`.** The words that make a signal an over-current
pin (`OC`, `FAULT`, `OVERCURRENT`, ...) or a VBUS / port power enable (`PWR_EN`, `VBUS`, `EXTERNAL_EN`, ...), as regular expressions
matched against the part of the Gen5 Signal Name after the colon. Edit the file to teach a project's naming - no code change, no new
pack. A signal that matches neither stays an "other GPIO"; in Hybrid mode the AI is then asked about it. The verifier reads the same
file. Whatever matches is still checked against the workbook's direction (over-current = input, VBUS enable = output). When the NAME and
the DIRECTION contradict each other (e.g. `EXTERNAL_EN` listed as an input), neither the AI nor Python can tell which is wrong, so it
is a CONFLICT for a person: stated in the report, in the open items and as a `NEEDS REVIEW` on the pin itself, and in the AI
suggestions a separate "I know the board: use it anyway" button - never a silent rejection and never an automatic acceptance.

**AI suggestions (Hybrid mode, `--ai-suggest`) - for what Python cannot decide, always confirmed by a person.**
(1) *Signal roles*: a signal that is really an over-current pin or a VBUS enable but is named in a way Python does not know
(e.g. `USB_FAULT_B`) is generated yet not wired. When its controller instance still lacks that role, the AI proposes `oc | vbus | other`
with a quote of the signal name. (2) *Missed pins*: Python lists USB-looking pads and supplies that were not matched (no pinctrl impact)
and asks the AI about the few unmatched GPIOs that mention USB. Python validates every answer - the pin is one it sent, the quote occurs
literally in the workbook text, a role fits the workbook's own direction (over-current = input, VBUS enable = output), and one controller
slot is never given to two pins - and nothing is applied: an accepted suggestion becomes a button (`--role SIGNAL=oc`,
`--include-pin GPIO_N`) and Python then writes every value from the Excel. A role you set yourself is checked against the direction too
(a warning, never a block). Needs `usb_suggest` in `LLM_FEATURES` (it is in the default).

**How "works in all cases" is checked.** `backend/scripts/usb_cases_test.py` runs EVERY project of every workbook available
(samples/, plus any listed in `USB_CASE_WORKBOOKS`) through the same pipeline with an AI that misbehaves on purpose - an invented
regulator inside `&tlmm`, an unfilled `FACT <n>` placeholder, an invented hub with a compatible string and an address, a node with no
checkable specifics - and asserts invariants that name no pin, project or SoC: every matched pin is accounted for; no pin number
appears that is not the project's own; Python's output is never changed; every Python state survives; nothing the AI invented stays
active; a "pattern assumed" banner appears only when the template really added content; the final file compiles. A new project is
just another row.

**Scope**
- USB only, and only Qualcomm TLMM pins (`TLMM_GPIO_CFG...`). Pins on another controller (e.g. SA7255P `SAIL_CFG`)
  or on a workbook with no register-setting data (e.g. TCC8050) are listed with their Excel values and NOT emitted.
- Of the pins matched for USB, only single-purpose TLMM GPIOs become nodes. Shared pins (e.g. CCS20 `GPIO_92`,
  `GPIO_23/24`), boot-strap pins and PHY/supply pads are listed in the report with the reason - by design.
- Hub / no-hub: pins are split into "with hub" / "without hub" by the word HUB in the workbook's own signal or
  interface-group text. Hardware-variant wiring is only reported (a USB-named sheet that disagrees with the
  Database sheet - seen in 1 of the 4 workbooks tried); the tool does not choose a variant for you.

**Before you share this outside the team**
`backend/samples/` contains real customer workbooks and DTS files (Nissan, Honda/Suzuki). The tests need them;
remove or replace them before the project leaves the team.

## Status

Built and verified end-to-end against the real files for this project
(`Nissan_CCS20_SA8155PSIP_PinMux.xlsx` and
`sa8155p-oem-aivi2-ccs20-common-pinmux.dtsi`) - not just synthetic data.
Every claim below has a corresponding test script or was confirmed via
live `curl` calls against a running server.

### The big architectural decision: hardcoded schema, not guessed

Early versions dynamically detected the workbook's structure (which
columns belong to the SoC vs. which project) via keyword heuristics. Real
testing showed this could miscount columns with no way to catch itself.
Since every workbook of this type has an identical, confirmed-fixed
structure, the schema is now hardcoded (`app/schema.py`) - all 124 real
columns, verified byte-for-byte against the actual file. Upload now
*validates* against this known schema (and clearly flags a mismatch)
rather than guessing. No more manual classification step.

### Real bugs found and fixed along the way

Each of these only surfaced from testing against the actual real files,
not synthetic data - listed here because they're the kind of thing that
would silently produce wrong output if reintroduced:

- **Pin identifier**: `ID (SA8155P)` is a row-counter, not a pin name -
  excluded from identifier candidates; ball name/ID matching now handles
  the workbook's multiple chip-revision variants (SA8155SIP/SA8195SIP/
  SA8155SoC/SiPV1/PRE-SiPV2).
- **63.5% of rows aren't real signal pins.** `NC`/`AGND`/`DNC`/`VDD*`
  rail labels are deliberately-populated non-signal markers, not blank
  data - excluded from DTS generation (`app/constants.py`).
- **Duplicate-pin validation used the wrong key.** `PinMuxed Name` like
  `AGND` intentionally repeats on hundreds of different physical balls -
  validation now keys on the physical ball ID instead.
- **Search matched theoretical capability lists**, not actual pin
  assignment - e.g. "bluetooth uart" pulled in SPI/audio/Ethernet rows
  because the SoC's `Interfaces` capability field mentioned "UART" among
  a dozen options. Search now scopes to fields that reflect real
  assignment only.
- **RAG silently truncated results** even after keyword filtering had
  already narrowed them - fixed so filtered queries do a full scan of
  every row in the project, not a fixed top-k slice.
- **Real `Direction` values are `"0: IN"`/`"1: OUT"`**, not plain
  `"in"`/`"out"` - the DTS generator's direction-bucketing (which splits
  UART TX/RTS from RX/CTS into separate nodes, matching the real
  reference file's pattern) initially missed this and merged everything
  into one node.
- **Malformed DTS output** (`function = "gpio"` for PCIe/SD/TDM pins) -
  fixed to always derive the function name from the workbook's own
  interface value, never a hardcoded fallback, regardless of whether a
  reference file happened to contain a matching example.

## Architecture

```
PinMux Workbook (.xlsm/.xlsx)
        |
Workbook Parser          (openpyxl; reads visible + hidden columns;
        |                 validates against the hardcoded schema)
        |
Normalizer                (per-project NormalizedRecords: SoC + project
        |                  fields merged; legend/lookup rows filtered)
        |
RAG Index                  (lexical by default, zero dependencies; upgrades
        |                   automatically to embeddings if numpy +
        |                   sentence-transformers happen to be installed)
        |
Validation Engine            (duplicate pins, missing interface, UART
        |                     completeness, mandatory fields)
        |
   +----+----+
   |         |
Generate   Update existing reference files
(from      (parse -> match pins to Excel -> patch only what
 scratch)   disagrees -> remainder file for uncovered pins)
   |         |
   +----+----+
        |
.dts / .dtsi output, matching the real reference file's structural
style (&tlmm { label: label { mux{}; config{}; }; }; ...)
```

The optional LLM layer (`app/llm_client.py`, `app/usb_ai_advisor.py`) sits beside this pipeline, never inside it:
the files that produce DTS text (`app/generators/*`, `tool/usb_dts_tool.py`) take no input from it. See
"AI advisory" below.

## Project layout

```
backend/
  app/                          the engine (shared by the web app, the CLI and the Copilot agent)
    main.py                      FastAPI app - HTTP endpoints, incl. /api/workbook/{id}/usb-tool/*
    models.py, store.py          schemas; in-memory sessions (recovered from data/uploads after a restart)
    schema.py, schema_registry.py  hardcoded SA8155P schema + confirmed-schema registry (data/confirmed_schemas.json)
    parsers/                     workbook_parser, normalizer, dts_parser (existing .dts -> editable nodes)
    generators/                  dts_generator (from scratch), dts_updater (patch existing files)
    usb_consumer_generator.py    USB pin categorization (oc/vbus/other/PHY/unsupported controller/needs review)
    peripheral_extractor.py, md_exporter.py, review_report.py, validators/, rag/, templates/
    llm_client.py                Bosch Model Farm client (stdlib only); inert until a key is configured
    usb_ai_advisor.py            explains flagged pins; its answers are checked against the workbook evidence
    ai_value_decoder.py, parsers/ai_node_extractor.py   older opt-in LLM helpers (off by default)
  tool/
    usb_dts_tool.py              the USB engine: check / update / generate  (CLI + run(args))
    verify_dts.py                independent checker for ANY DTS against the workbook
    test_usb_dts_tool.py         35 end-to-end checks incl. a real `dtc` compile (skipped if dtc is absent)
  scripts/                       22 backend test scripts (incl. LLM client + advisor tests against a local mock
                                 gateway) + the synthetic-workbook generator
  samples/                       real workbooks + DTS files the tests run against (see "Before you share this")
frontend/src/components/         UploadPanel, AnalysisPanel, Workspace, DtsUpdatePanel,
                                 PeripheralExtractPanel, UsbToolPanel, BgaGrid
.github/agents/                  Copilot agent that runs tool/usb_dts_tool.py (never writes DTS itself)
```

## Running it locally

**Backend**
```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env     # no configuration needed - see .env.example
uvicorn app.main:app --reload --port 8000
```

Windows (PowerShell):
```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   REM no configuration needed
uvicorn app.main:app --reload --port 8000
```

`requirements.txt` only lists dependencies with prebuilt wheels
everywhere - no C/C++ compiler needed. RAG search is pure-Python lexical
matching by default; uncomment the `numpy`/`sentence-transformers` lines
at the bottom for embedding-based search (optional, skippable).

**Frontend**
```bash
cd frontend
npm install
npm run dev              # http://localhost:5173, proxies /api to :8000
```

## AI advisory (optional)

**What it does.** For the pins the tool deliberately did NOT generate (shared pins, boot-strap pins, pins on an
unsupported/unconfirmed controller) it asks the model to explain the situation in plain language and list what
the hardware owner must confirm. The result is a separate report section ("AI advisory - explanation only").

**What it cannot do.** It cannot change the DTS: the output is byte-identical with and without it (tested, also
through the web API). Its answers are untrusted input: every quoted piece of evidence must literally occur in
the evidence that was sent, and any specific it adds that is not in that evidence - an I2C address, a
`vendor,model` compatible string, output-high/low, a GPIO flag - is removed and counted in the report.

**Hybrid mode: Python -> AI -> Python (the recommended AI mode).** Each does what it is best at, and the result is ONE
final file:
1. *Python* extracts the workbook, classifies every USB pin, and generates the verified nodes (values straight from Excel).
2. *AI, first reading:* it can read documents you supply (datasheet notes, schematic text, a change description; `.txt`/`.md`)
   and extract facts, each with a verbatim quote. *Python* keeps a fact only if its quote literally occurs in the document and
   every specific in the fact (address, compatible, level) is inside that quote.
1b. *Python, template completion:* with no reference file and no chosen pattern, hybrid mode completes the output with the
   ACCEPTED-CHANGE template (the Suzuki change: regulator-fixed VBUS regulators, `&usbN` consumers, output levels by its
   "regulator off at boot" convention) by running the same tool with `--pattern regulator` internally. The Python-only
   output is not changed, every assumed line is tagged, and the report starts with a PATTERN ASSUMED banner. Choosing a
   pattern yourself, or attaching the project's DTS, always wins over the template.
   If the AI is unavailable, this Python completion and your level facts are still delivered.
2b. *Python again, for the simple facts:* a fact that names one pin and states its boot level ("gpio82 boots output-low")
   is applied by Python itself - a mechanical edit, not something to trust a model with (one ignored it in a real run).
   "Reset is active low" is never read as a level. Every fact you give is then accounted for in the report: applied by
   Python / used by the AI / NOT used (with what is usually missing, e.g. the I2C controller label) / NOT applied.
3. *AI, then writing:* it completes what the workbook cannot state - from the verified nodes, the flagged pins' evidence, the
   accepted document facts and any facts you typed. It can also CORRECT a draft you give it (Copilot's, a colleague's).
4. *Python again:* the existing verifier checks every line against the Excel and sends exact findings back for repair (max 2
   rounds); verified lines the AI changed or dropped are caught; `dtc` must compile it.
5. *Python demotes the rest:* any AI-added line whose I2C address, compatible string, output level, GPIO flag/number or
   base-tree label is in none of the verified output / evidence / facts becomes a `// UNVERIFIED` comment (a whole node if the
   flagged line opens one) and is listed. The merged file is verified and compiled again.
An output level is only accepted for the pin a fact names (a fact for gpio82 does not license gpio113); an unbacked level is
demoted and the NEEDS REVIEW marker is put back. The result is also rendered as a **Hybrid Combined View** (`<combined>.hybrid.dts`):
changed nodes replaced in place and tagged, anything the AI added in a separate "4. AI-ADDED AND VERIFIED" section - the Python
Combined View itself is never edited.
The final file (`<out>.hybrid.dtsi`; "Hybrid final output" in the UI) therefore has only two kinds of active lines: Python-generated
and AI-added-and-verified. Python's own output (`--out`) is never changed. CLI: `--ai-propose`, `--facts`, `--docs`, `--ai-draft`;
web: AI assistance -> "Hybrid". "Nothing omitted, nothing wrong" means exactly this: every pin is accounted for, every value is from the Excel, the template or
a fact you gave, and everything the output cannot state is in a "Still needs a decision" list with the one action that
resolves it (e.g. CCS20's usb1 VBUS: the candidate pin, its schematic net, and `--vbus-pin`). It does not mean every value
is known - output levels and the BSP's wiring style are not in the Excel.
What the AI cannot do: know hardware facts nobody gave it - those stay NEEDS REVIEW. Tested against
a mock gateway, including that disabling demotion, the source guard, or the document-quote check makes the tests fail.

**Data sent.** Per flagged pin: signal names, mux options, interface group, the imported schematic net trace and
the Excel remark. No workbook rows, no DTS text. For hybrid mode it also sends this run's USB nodes, your facts/draft, and the text of any documents you supply. Other features (`chat`, `value_decoder`, `node_extractor`) send
more and stay OFF unless you add them to `LLM_FEATURES`.

**Set up** (Bosch Model Farm). Copy `backend/.env.example` to `backend/.env` (gitignored - never commit or share the
real key) and set at least `LLM_API_KEY` and `LLM_DEPLOYMENT`. Then, on your network:

    cd backend
    python -m app.llm_client check        # one tiny call; prints the exact problem if the path/key/proxy is wrong

Use it: CLI `--ai-advice`, or the checkbox in the web panel (disabled until configured). Restart the backend after
editing `.env`.

**Verified here / not verified here.** The request shape follows the Model Farm reference and is tested against a
local mock gateway (path, api-version, both auth headers, GPT-5 parameters, 401/403/404/429, unreachable host, key
never in an error). It has NOT been run against the real gateway from the development environment - `check` is that
test.

## Using the "update reference files" workflow

1. Upload and build the workbook as usual.
2. In the workspace, use the **Update Reference DTS Files** panel: drag
   in one or more existing `.dts`/`.dtsi` files.
3. Each file gets an editable output filename (defaults to the original
   name - change it before running if you want).
4. Click **Update** - the app parses each file, matches its pins against
   current Excel data, and patches only the values that actually differ
   (with an inline `/* UPDATED from Excel: field 'old' -> 'new' */`
   comment). Nodes with no Excel match, or that don't match the
   recognized shape at all, are left completely untouched.
5. Download each updated file under its chosen name, plus a **remainder**
   file covering every Excel-defined signal pin that didn't appear in any
   of the uploaded files.

This is deliberately conservative: a node is only edited when its pins
cleanly match Excel data; anything ambiguous is left alone rather than
risk corrupting a real reference file. `dts_update_test.py` exercises
this against exact-match, partial-match, no-match, and
parser-doesn't-recognize-this-shape cases.

## Calibrating to your real workbook

Since the schema is hardcoded now, most of the earlier "does this
generalize" uncertainty is resolved - confirmed via `schema_test.py`
built directly from `app/schema.py`'s column definitions, and via live
testing against the real file. Remaining open items:

1. **Shadow/backup fields** (`PinMuxSHDW0`, `SelectedSHDW`) aren't used
   by the normalizer or generator - semantics unconfirmed without seeing
   real values in those specific columns.
2. **Sleep/idle DTS states** - the real reference file shows some pins
   have both an `_active` and `_sleep` configuration (same pins, muxed
   differently for low-power). The workbook has no distinct "sleep
   config" column, so only the primary/active state is generated -
   documented gap, not silently dropped.
3. **Compare projects / impact analysis** (original spec's "Future
   Enhancements") - not built. The data model supports it (both projects
   normalized and queryable) but there's no dedicated endpoint/UI.

## The USB engine - one tested implementation, three ways in

USB got dedicated, hardened treatment beyond the general schema-driven
pipeline above: per-project VBUS/OC convention detection (never
defaults - stops and asks if it can't tell), minimal in-place patching
of existing pinctrl nodes, cross-sheet consistency checks (a
USB-named sheet like `USBHUB` disagreeing with the `Database` sheet
about which GPIO does what), and a companion verifier that checks
*any* DTS - yours, a colleague's, an AI's - against the workbook,
keeping WRONG / AMBIGUOUS / UNVERIFIABLE strictly apart.

This logic lives in exactly one place, `backend/tool/usb_dts_tool.py`
and `backend/tool/verify_dts.py`, each structured as a `run(args)`
function callable directly (not just via `argparse`). Three interfaces
call that same function - none of them reimplement any of the logic:

1. **CLI** - `python backend/tool/usb_dts_tool.py --excel <wb> --project
   <NAME> --dts <existing.dtsi> --out result.dtsi --report report.md`
   (see `backend/tool/README.md` for the full flag reference: `--pattern`,
   `--vbus-pin`, `--resolve`, `--overlay`/`--insert-after`, `--combined`,
   `--inventory`). `verify_dts.py` takes the same `--excel`/`--project`
   plus `--dts <file-to-check>` and optional `--existing <project-file>`
   for duplicate-definition detection.
2. **Web UI / API** - a "USB DTS Tool" panel in the same Workspace screen
   as every other feature, reusing the workbook you already uploaded (no
   second upload of the Excel file - only an existing DTS/overlay to
   check-or-update is a fresh upload, since those were never part of the
   workbook upload to begin with). Backed by
   `GET /api/workbook/{workbook_id}/usb-tool/projects`,
   `POST /api/workbook/{workbook_id}/usb-tool/update-or-generate`,
   `POST /api/workbook/{workbook_id}/usb-tool/verify` - JSON responses
   with the same `out_text`/`report_text`/`summary` fields the CLI
   prints. Because the workbook's file path is stable for the session
   (not a fresh temp file per call), the parsed-workbook cache works
   here too - a second run against the same session is fast.
3. **Copilot agent** - `.github/agents/universal-usb-dts-generator.agent.md`
   runs the CLI form and reports its output; it's instructed never to
   write DTS by hand and to stop (not improvise) if the engine files
   are missing from the workspace.

Real workbooks (`Nissan_CCS20_SA8155PSIP_PinMux.xlsx`,
`Honda_database_v2.xlsx`) and their real project DTS files live in
`backend/samples/` for trying any of the three paths immediately.
`python backend/tool/test_usb_dts_tool.py` runs 21 checks against
that real data, including a token-by-token reproduction of an actually
accepted Gerrit change - not synthetic fixtures.

**Known limitation:** the "Check a DTS" panel only checks against the
project's own USB pins by default; the CLI's `--all-pins`/`--existing`
flags (checking every pin, or flagging duplicate definitions against
other real project files) aren't yet exposed in the UI - use the CLI
directly for those two cases.

## What's deliberately simple (v1 scope)

- **In-memory store** (`app/store.py`) - one process. After a restart a session is rebuilt from the
  saved workbook when its schema is known; swap for Redis/Postgres if you need more.
- **RAG index** - flat in-memory search per project. Fine at real-file
  scale (a few thousand rows); swap for FAISS/Chroma if that changes.
- **DTS node-type recognition** covers uart/i2c/spi/gpio explicitly, plus
  a data-driven fallback (function name derived from the workbook's own
  interface value) for everything else - PCIe/SD/TDM/audio pins generate
  correctly-named but not deeply-modeled nodes.

## Generic generator (full pinmux draft) - how it writes nodes

Python only; the AI never writes a line. It is a starting point for a new project - the everyday jobs are **Update an existing DTS** and **Check an existing DTS** (both are at the top of the page).

- **One node per signal.** A plain GPIO (reset, enable, interrupt) gets its own node named after its Gen5 signal (`CPU_USBHUB_CTRL_1V8:RST` -> `cpu_usbhub_ctrl_rst`; a boot-strap `BOOTCFG` name is never preferred).
- **Pins share a node only when every setting matches** (function, drive strength, bias, direction). A bus (QUP / TDM / PCIe ...) keeps its pins together only while they match. `render_node` refuses a node whose pins differ, so a first-pin value can never be applied to the rest.
- **Output levels are never guessed.** The Excel says OUT, not high or low; the node carries `NEEDS REVIEW: output pin ...` until a person states the level. The updater keeps an existing `output-high` / `output-low`.
- **Pads that are not TLMM pins are not written into `&tlmm`** (DSI, CSI, USB PHY, UFS, DP, JTAG, PCIe lanes, SSC ...). They are listed in a comment. Judged only when the workbook gives its GPIO pins `TLMM_GPIO_CFG` registers.
- Checked by `scripts/generator_values_test.py` (synthetic records, plus the Honda workbook when present: every GPIO compared with its own workbook values).


## Test kit (no real data needed)

`test_kit/` has a fabricated demo workbook, template and existing-DTS samples and `run_checks.py`, which exercises every option and checks the result against known answers. See `test_kit/GUIDE.md`.

## Ethernet (RGMII / RMII / MDIO / EMAC / SGMII)

Tab **Generate Ethernet files**, or `python backend/tool/eth_dts_tool.py --excel board.xlsx --project CCS20 --kernel 6.6 --out-dir out/`.

- A pin is Ethernet only when the workbook's **selected** function says so (`selected_interface` / `pinmuxed_name`). The `interfaces` capability list is never used.
- Filters: RGMII, RMII/MII, MDIO/MDC, EMAC, SGMII (SGMII is SerDes: a note, never a pin state). CLI: `--family rgmii --family mdio ...`.
- Selection uses the **Selected Interface** cell alone. Names, remarks and capability columns never add a pin. A pin you want anyway (PHY reset, interrupt) can be added with `--include-pin GPIO_N` (muxed as gpio).
- Function names come from `backend/app/eth_profiles.json` (SA8155P table read from Linux v6.6 `pinctrl-sm8150.c`). The workbook's mux value is compared with it; a disagreement is marked NEEDS REVIEW. Another SoC: add an entry to that file; without one only RGMII/RMII are generated, marked for review.
- Output: `<project>_ethernet_pinmux.dtsi` and `<project>_ethernet_overlay.dts` (`&ethernet` with `pinctrl-0`), in your template's format, else the kernel's (`--kernel`), else the default. phy-mode, PHY address, PHY reset and PHY-vs-switch are never guessed: they are NEEDS REVIEW.
- The finished files are read again by an independent checker (`eth_generator.verify_text`). AI (optional): a data check only; it never writes into the files.
- Test: `python backend/scripts/eth_generator_test.py`.

### Before using Ethernet on a new workbook: discover how it writes Ethernet

Workbooks differ per project and SoC. Run the read-only scan first:

`python backend/tool/eth_discover.py --excel board.xlsx --out eth_discovery.md`

It lists every column that contains an Ethernet word (with samples and whether the column looks like a capability list), each Ethernet row with its key cells, and what the current rules would do with it. Use it to see which column holds the real selected function in that workbook.

Verified on real workbooks (Nissan CCS20 / POC_NESSY2, Iveco CDC, Honda GenID, Suzuki DAX): only the Nissan projects select Ethernet on TLMM pins (RGMII on 12-13 pins; the workbook's mux values match the SA8155P pin table on all of them). In the other workbooks Ethernet appears only as an alternate function (Interfaces / PortN columns), as SGMII SerDes lanes, or on SAIL pins, so the tool correctly reports "no Ethernet pin is selected". The selected function's mux value is `regsetting<N>` where N is the 'PinMux Setting' cell.


### Ethernet: board-wired control lines (second rule)
Rule 1 is unchanged: a pin is Ethernet when its **Selected Interface** says so. Rule 2 adds the PHY's own control lines that the workbook sets as
`GPIO` / `WUP_GPIO` but the board wires to Ethernet: its **Gen5 signal** carries the word ETH
(e.g. group `CPU_ETH`, signal `CPU_ETH_CTRL_1V8:MDIO` / `:RST_B` / `:INT_B`). They are generated as `gpio` pin states, marked "board-wired control line",
with drive/bias from the workbook. A group-only or signal-only match (e.g. a USB "PHY" pin) is not enough. Turn it off with `--no-board-wiring`.
Real workbooks: Nissan POC_NESSY2 gains MDIO (gpio59), reset (gpio79) and interrupt (gpio124). Iveco B1/B2 have only SGMII0 SerDes lanes (no pinctrl).

### Ethernet: all evidence, and how the AI helps (third rule)
Python weighs every column, but only two kinds of evidence create a pin state: **Selected Interface** says Ethernet (rule 1), or the **Gen5 signal**
carries ETH on a pin set as GPIO / WUP_GPIO (rule 2). Everything else is only **suggested** in "Left out", with the column and value as evidence and an **Add** button:
- `likely` = Interface Group says ETH, or two live columns mention Ethernet, no note says "not in use", and Gen5 does not name another function;
- `possible` = everything else (one mention, an old-import column `old_*`/`cmp_*`, a "not in use" note, or a Gen5 name for another function).
Power-rail names (`PWR:..._ETH`) and the capability columns (`Interfaces`, `PortN`, `InstanceN`) are ignored. Pins with no TLMM number (e.g. SAIL) are only counted in a note.

With **AI data check** on (`--ai-check`) one extra call (`app/eth_ai.py`) reads each suggested pin's own cells and says `ethernet_gpio` / `not_ethernet` / `unclear`
with a role (mdio, mdc, reset, interrupt ...) and a verbatim quote, and quotes **board facts written in words** (phy-mode, clock delay, PHY address, reset, link
partner, speed). Python verifies every quote, drops a fact whose value is not inside its quote, and caps the AI at `unclear` when a note says "not in use / spare /
reserved". The AI never changes a file: you press Add, and Python generates the pin from the Excel. Test: `scripts/eth_ai_test.py`.

## SPI (master / slave)

`python backend/tool/spi_dts_tool.py --excel board.xlsx --project CCS20 --kernel 6.6 --out-dir out/` (also the **Generate SPI files** tab).

- **Which pins:** only those whose *Selected Interface* is SPI (`SPI-M_QUPn[_SEk]` = master, `SPI-S_QUPn` = slave). The `Interfaces`, `PortN`, `InstanceN` and `RegSetting` columns list what a ball *could* do and are never used to pick pins. Pin role (MISO / MOSI / SCLK / CS0..n) comes from the pin's own name, then the Gen5 signal tail.
- **Function name:** from the SoC pin table in `backend/app/spi_profiles.json` (read from the Linux pinctrl driver): `qupN` for the selected QUP, or the `qup_lK` function the workbook's own mux value points at (native chip selects 1..n often sit on `qup_l*`; flagged for confirmation). The workbook mux value is `PinMux Setting = Qk` -> `RegSettingQUPk = TLMM_GPIO_CFGn(5:2)=v`; a disagreement with the table is written as NEEDS REVIEW, never resolved silently. A SoC without a table gets the workbook's QUP name as function, every pin marked for review.
- **Verified tables:** SA8155P / SM8150 only. SA6155P and SA7255P have no table yet (function names `qupN` / `qupN_seK` are the workbook's, marked for review) - add them to `spi_profiles.json` from the SoC's `pinctrl-*.c`.
- **Consumers:** one `&spiN` per QUP (`status`, `pinctrl-0` with that engine's pins; `spi-slave` for slave engines) and the `&qupv3_id_X` wrapper to enable. SPI devices (compatible, chip select, frequency, mode) are not in the pin data and stay as review comments.
- **Board-wired control lines:** a pin whose Gen5 signal says SPI (`..._SPI_...:SPI_M_REQ`, a software chip select) but whose Selected Interface is `GPIO` / `WUP_GPIO` is generated as a `gpio` pin state ("wired") and put in the controller of the same Gen5 group and mode (`SPI_S_` -> slave, `SPI_M_` -> master). With no match it stays unassigned and is listed as a decision. `--no-board-wiring` switches this off.
- **Direction:** a Direction cell that contradicts the role (e.g. master SCLK = IN) is reported as a review note; function pins do not use Direction.
- **Not TLMM:** other pin controllers (e.g. Telechips GPSB on TCC8050) have no GPIO number in the data: a note, no pin state.
- **Check:** an independent reader re-parses the finished files against the workbook and the table (function on that pin, one engine per controller, every selected pin present, drive / bias). The optional AI data check reads each pin's cells for contradictions and never changes the files.
- **Not covered yet:** grouped (rx/tx) and multi-state (default/sleep) formats, same as USB / Ethernet; `dtc` has never been run on the output.

SPI also cross-checks the **Gen5 signal** against **Selected Interface**: a pin selected as SPI whose Gen5 signal names another bus (UART, I2C, CAN ...) is still generated from Selected Interface but flagged NEEDS REVIEW; a pin whose Gen5 signal says SPI while a non-GPIO, non-SPI function is selected is not generated and appears under "Left out" with the reason. On the four sample workbooks the two columns agree.

### SPI: AI (optional, suggestions only)

`--ai-check` / the AI switch on the SPI tab does two things, neither writes into the files: (1) the data check of the generated pins (contradicting remarks, Direction vs role); (2) `backend/app/spi_ai.py` reads the **Left out** candidates (pins not selected as SPI that carry SPI wording in another live column, e.g. a software chip select or a REQ line) and answers `spi_line` / `not_spi` / `unclear` with a verbatim quote, and quotes board facts written in words (SPI mode, max frequency, device, chip select) for the `&spiN` device nodes. Python rejects any quote not in the cells, any fact value not inside its quote, any verdict capped by a "not in use / spare" note or by a Gen5 signal that names another bus; the Add button turns an accepted candidate into `include_pins`, generated from the Excel. Candidate tiers: "likely" only when the Interface Group says SPI (or Gen5 is empty and two live columns agree); wording like "could be used as SPI" and capability columns are ignored; reference-only wording is "possible".

Tests never use your real gateway: `scripts/run_all_tests.py` removes LLM_* / FARM_* / proxy variables from the test processes and sets `PINMUX_NO_DOTENV=1`, so a filled-in `backend/.env` cannot make the mock-gateway tests fail (HTTP 403 from the real gateway). To run a single test script by hand with a real `.env` present, set `PINMUX_NO_DOTENV=1` first.

SPI board-wired lines: when no selected pin shares the line's exact Gen5 group, it is tied to the engine whose Gen5 groups start with the same two name parts (e.g. `CPU_SBR_...`) if that points at exactly one engine; the pin state then carries a NEEDS REVIEW note. Two or more candidate engines: not guessed, listed as a decision.
