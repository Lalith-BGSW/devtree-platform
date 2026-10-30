# USB DTS report - CCS20

- Workbook: `n.xlsx`  |  Target DTS: `(none - generated from scratch)`
- Output: `usb_generated.dtsi`
- USB-matched pins: **21** (match mode: both)  |  oc/vbus: 3  |  other GPIOs: 2  |  PHY pads: 12  |  unsupported controller: 0  |  controller unconfirmed: 0  |  needs review: 4
- VBUS/OC pattern: **unconfirmed** (no existing VBUS/OC convention found in the supplied files, and no --pattern given - safe TLMM pin states may be generated; ambiguous or unsupported-controller pins are excluded and listed in the report. Re-run with --pattern controller or --pattern regulator to also get the consumer wiring (&usb0/&usb1 blocks or regulator-fixed nodes).)
- Detected controller instances: USB20 -> &usb1, USB30 -> &usb0

## Consumer nodes / new states

- &usb0 (USB30): electrical config generated above (section 2), but NOT wired into any consumer - pattern unconfirmed; re-run with --pattern controller or --pattern regulator to get this instance's &usb consumer block.
- &usb1 (USB20): electrical config generated above (section 2), but NOT wired into any consumer - pattern unconfirmed; re-run with --pattern controller or --pattern regulator to get this instance's &usb consumer block.
- 6 new pinctrl state(s) inserted in a new &tlmm block (none existed)

## Other USB-related GPIOs - real signal, role not auto-assigned (nothing withheld)

Single-purpose, TLMM-controlled pins whose signal doesn't match a known role keyword (OC/PWR_EN/VBUS). Unlike NEEDS REVIEW below, these are NOT ambiguous - each has exactly one real signal - so their real Excel values are included either as an update to their existing node or as a newly generated one. What's still a human decision: which consumer (if any) should reference the label, and for an output pin, its level. Split below by whether "HUB" appears in the pin's own Gen5 Signal Name / Interface Group - a real, separate sub-system (I2C-attached hub IC) from the USB controller itself.


**Without hub (pure USB controller)**

| Pin | Label | Status | Gen5 Signal Name | Excel remark |
|---|---|---|---|---|
| GPIO_38 | sa8155_cfg_usb_phy_port_select | new node generated | SA8155_USB_CFG:USB_PHY_PORT_SELECT | - |

**With hub (I2C-attached hub IC)**

| Pin | Label | Status | Gen5 Signal Name | Excel remark |
|---|---|---|---|---|
| GPIO_113 | cpu_usbhub_ctrl_rst | new node generated | CPU_USBHUB_CTRL_1V8:RST | mounting option only for Japan subPCB |

## NEEDS REVIEW - not generated (never guessed)


**Without hub**

| Pin | Gen5 Signal Name | Why | Excel remark | Schematic net (from the workbook) |
|---|---|---|---|---|
| GPIO_136 | SCC_CPU_BOOT_1V8:BOOT_MODE0, SA8155SIP_BOOTCFG_1V8:FORCE_USB | Boot-strap only - read at reset, not a runtime USB signal (excluded from runtime DTS) | - | D5700(K37) z28.z33.D5700_K37_SCC_CPU_BOOT_1V8_BOOT_MODE0_P0,MP5961=MP(1) ENDPNT,R6025(1) SCC_CPU_BOOT_1V8:BOOT_MODE0,R8673(2) z34.z37.SIGN00173,D8634(4) CPU_DBG_CTRL_3V3:BOOT_MODE0,MP9962=MP(1) ENDPNT,X9960=QSH-030-01-L-D-A-K-TR(27) ENDPNT,R8757(2) z34.SCC_CPU |

**With hub**

| Pin | Gen5 Signal Name | Why | Excel remark | Schematic net (from the workbook) |
|---|---|---|---|---|
| GPIO_23 | CPU_SUBPCB_I2C_1V8:I2C_SDA, CPU_A2B_I2C_1V8:I2C_SDA, CPU_USBHUB_SMI2C_1V8:SMB_DATA, CPU_VO_RSE_I2C_1V8:I2C_SDA | Shared I2C bus pin - 4 devices on one bus (CPU_SUBPCB_I2C, CPU_A2B_I2C, CPU_USBHUB_SMI2C, CPU_VO_RSE_I2C); it is configured by the I2C controller's pinctrl (see the I2C tool), not by a USB node. Multi-purpose pin - 4 distinct signal roles on this ball | - | D5700(AJ20) CPU_SUBPCB_I2C_1V8:I2C_SDA,MP8207=MP(1) ENDPNT,X8190=245656060009829+(54) ENDPNT |
| GPIO_24 | CPU_SUBPCB_I2C_1V8:I2C_SCL, CPU_A2B_I2C_1V8:I2C_SCL, CPU_USBHUB_SMI2C_1V8:SMB_CLK, CPU_VO_RSE_I2C_1V8:I2C_SCL | Shared I2C bus pin - 4 devices on one bus (CPU_SUBPCB_I2C, CPU_A2B_I2C, CPU_USBHUB_SMI2C, CPU_VO_RSE_I2C); it is configured by the I2C controller's pinctrl (see the I2C tool), not by a USB node. Multi-purpose pin - 4 distinct signal roles on this ball | - | D5700(AL20) CPU_SUBPCB_I2C_1V8:I2C_SCL,MP8206=MP(1) ENDPNT,X8190=245656060009829+(52) ENDPNT |
| GPIO_92 | CPU_USBHUB_CTRL_1V8:USB_HUB_I2C_EN, CPU_USB20_CTRL_1V8:USB_PWR_EN | Multi-purpose pin - 2 distinct signal roles on this ball - the pin state cpu_usbhub_ctrl_usb_hub_i2c_en is generated once (shared); owner decided: CPU_USBHUB_CTRL_1V8:USB_HUB_I2C_EN | - | D5700(AB17) CPU_USBHUB_CTRL_1V8:USB_HUB_I2C_EN,MP8191=MP(1) ENDPNT,X8190=245656060009829+(12) ENDPNT |

## Cross-sheet conflicts - a USB sheet disagrees with the Database sheet

The Database sheet is what this tool reads. These USB-named sheets put the same signals on different GPIOs - usually a hardware/mounting variant (check the Remarks column). Nothing below was used to change the output; decide which variant applies.

| Sheet | Signal | GPIO (per sheet) | Database sheet says |
|---|---|---|---|
| USBHUB | CPU_USBHUB_CTRL_1V8:RST_B | GPIO_102 | Database sheet assigns it to: CPU_USB20_CTRL_1V8:USB_OC_B |
| USBHUB | CPU_USBHUB_CTRL_1V8:USB_PWR_EN | GPIO_113 | Database sheet assigns it to: CPU_USBHUB_CTRL_1V8:RST |
| USBHUB | CPU_USBHUB_CTRL_1V8:USB_OC_B | GPIO_123 | Database sheet assigns it to: no signal |

## PHY / differential pads - not TLMM, excluded from pinctrl

| Pad | Gen5 Signal Name |
|---|---|
| DP0_LN3_M | CPU_USB30:USB3_SSRX_N |
| DP0_LN3_P | CPU_USB30:USB3_SSRX_P |
| DP0_LN2_M | CPU_USB30:USB3_SSTX_N |
| DP0_LN2_P | CPU_USB30:USB3_SSTX_P |
| USB_A_HS_DM | CPU_USB30:USB2_DM |
| USB_A_HS_DP | CPU_USB30:USB2_DP |
| USB_2_HS_DM | CPU_USBHUB_UFP:USB_DM, CPU_USB20:USB_DM |
| USB_2_HS_DP | CPU_USBHUB_UFP:USB_DP, CPU_USB20:USB_DP |
| USB2_SS_RX_M | - |
| USB2_SS_RX_P | - |
| USB2_SS_TX_M | - |
| USB2_SS_TX_P | - |

## Not confirmed by any data source (deliberately absent from output)

- Any USB hub IC `compatible` string / I2C address, and any hub reset/port-select property names: nothing in the workbook or existing DTS defines them.
- `debounce-delay-ms`, `qcom,default-mode-host` and the `dwc3` child for instances other than usb0.
- Whether an `&usbN` label maps to a given controller in your base devicetree (instances are numbered usb0, usb1, ... in detection order).

## Validation

- Structure OK (braces balanced, no duplicate labels).
- dtc not installed here - run your normal DTS build to compile-check.

## Output files and format

Format source: **built-in default - NOT confirmed to match your BSP**. For a vendor BSP, give an accepted file of your project as `--template` (it is the only source that proves your project's format).

| File | Role | Format it follows |
|---|---|---|
| `ccs20_usb_pinmux.dtsi` | pinmux | default format (the vendor BSP style of the samples: tabs, mux{} / config{}, 'label: label') - tabs, 'label: node', mux{}/config{} pin states, blank line between nodes: yes, LF line endings |

- Written to `../../../../../../u1`. Values are identical to the verified output: every restyled pin node was parsed back and compared (label, pins, function, properties) - a file that did not match would have kept the default format.
- The independent verifier read the files again: 0 wrong | 0 ambiguous | 0 unverifiable | 6 confirmed OK.
- no overlay file: this output has no consumer or regulator blocks (the wiring style is unconfirmed - choose a pattern to get them)
