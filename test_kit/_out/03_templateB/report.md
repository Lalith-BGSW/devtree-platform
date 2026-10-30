# USB DTS report - CCS20

- Workbook: `demo_workbook.xlsx`  |  Target DTS: `(none - generated from scratch)`
- Output: `C:\Users\MOL2COB\Downloads\devtree-platform_unified_new\test_kit\_out\03_templateB\result.dtsi`
- USB-matched pins: **6** (match mode: both)  |  oc/vbus: 4  |  other GPIOs: 1  |  PHY pads: 0  |  unsupported controller: 0  |  controller unconfirmed: 0  |  needs review: 1
- VBUS/OC pattern: **regulator** (explicitly requested (--pattern regulator))
- Overlay output: `..\test_kit\_out\03_templateB\usb_vbus_regulators.dts`
- Detected controller instances: USB20_0 -> &usb0, USB20_1 -> &usb1
- WARNING: RegSetting not resolvable for 5 pin(s) (no usable register-setting data for them in this workbook) - 0 show a GPIO function in their own Port/Instance data, 5 were judged by ball name only. See the sections below.

## Consumer nodes / new states

- usb0_vbus_regulator (USB20_0): generated from GPIO_11 (GPIO_ACTIVE_HIGH, enable-active-high)
- &usb0 (USB20_0): consumer generated (status, vbus_dwc3-supply, pinctrl-0)
- USB20_0: over-current pin GPIO_10 (GPIO_ACTIVE_LOW): its pinctrl state (usbv2_0_fault_detect) is generated from the Excel but NOT referenced by the consumer - no confirmed over-current wiring for the regulator pattern.
- usb1_vbus_regulator (USB20_1): generated from GPIO_13 (GPIO_ACTIVE_HIGH, enable-active-high)
- &usb1 (USB20_1): consumer generated (status, vbus_dwc3-supply, pinctrl-0; qcom,default-mode-host left as NEEDS REVIEW)
- USB20_1: over-current pin GPIO_12 (GPIO_ACTIVE_LOW): its pinctrl state (usbv2_1_fault_detect) is generated from the Excel but NOT referenced by the consumer - no confirmed over-current wiring for the regulator pattern.
- 5 new pinctrl state(s) inserted in a new &tlmm block (none existed)
- regulators/consumers written to ..\test_kit\_out\03_templateB\usb_vbus_regulators.dts as a snippet - place SECTION 1 inside the correct parent node (use --overlay + --insert-after).

## Other USB-related GPIOs - real signal, role not auto-assigned (nothing withheld)

Single-purpose, TLMM-controlled pins whose signal doesn't match a known role keyword (OC/PWR_EN/VBUS). Unlike NEEDS REVIEW below, these are NOT ambiguous - each has exactly one real signal - so their real Excel values are included either as an update to their existing node or as a newly generated one. What's still a human decision: which consumer (if any) should reference the label, and for an output pin, its level. Split below by whether "HUB" appears in the pin's own Gen5 Signal Name / Interface Group - a real, separate sub-system (I2C-attached hub IC) from the USB controller itself.


**Without hub (pure USB controller)**

None.

**With hub (I2C-attached hub IC)**

| Pin | Label | Status | Gen5 Signal Name | Excel remark |
|---|---|---|---|---|
| GPIO_14 | cpu_usbhub_ctrl_hub_rst | new node generated | CPU_USBHUB_CTRL_1V8:HUB_RST | - |

## NEEDS REVIEW - not generated (never guessed)


**Without hub**

| Pin | Gen5 Signal Name | Why | Excel remark | Schematic net (from the workbook) |
|---|---|---|---|---|
| GPIO_15 | SA6150_BOOTCFG_1V8:FORCE_USB | Boot-strap only - read at reset, not a runtime USB signal (excluded from runtime DTS) | PD: FORCED_USB pin | - |

**With hub**

None.

## PHY / differential pads - not TLMM, excluded from pinctrl

None.

## Flagged inferences (present in the output, marked NEEDS REVIEW)

| State | Pin | Level | Basis |
|---|---|---|---|
| usbv2_0_power_enable | gpio11 | output-low | pin starts in the regulator's OFF state - consistent with the 2 accepted examples (an active-low enable -> output-high, an active-high enable -> output-low); Excel has no level column |
| usbv2_1_power_enable | gpio13 | output-low | pin starts in the regulator's OFF state - consistent with the 2 accepted examples (an active-low enable -> output-high, an active-high enable -> output-low); Excel has no level column |

## Not confirmed by any data source (deliberately absent from output)

- Any USB hub IC `compatible` string / I2C address, and any hub reset/port-select property names: nothing in the workbook or existing DTS defines them.
- `debounce-delay-ms`, `qcom,default-mode-host` and the `dwc3` child for instances other than usb0.
- Whether an `&usbN` label maps to a given controller in your base devicetree (instances are numbered usb0, usb1, ... in detection order).

## Validation

- Structure OK (braces balanced, no duplicate labels).
- dtc not installed here - run your normal DTS build to compile-check.

## Output files and format

Format source: **your template (accepted by your project)**.

| File | Role | Format it follows |
|---|---|---|
| `ccs20_usb_pinmux.dtsi` | pinmux | learned from the template 'template_B_tabs_nested.dtsi' - tabs, 'label: node', mux{}/config{} pin states, 2 state(s) analyzed, blank line between nodes: yes, blank line inside a node: yes, LF line endings, copyright header copied |
| `ccs20_usb_overlay.dts` | overlay | default format (the vendor BSP style of the samples: tabs, mux{} / config{}, 'label: label') - tabs, 'label: node', LF line endings |

- Written to `..\test_kit\_out\03_templateB\files`. Values are identical to the verified output: every restyled pin node was parsed back and compared (label, pins, function, properties) - a file that did not match would have kept the default format.
- The independent verifier read the files again: 0 wrong | 0 ambiguous | 0 unverifiable | 9 confirmed OK.
- no overlay template was given: the overlay file uses the default format
