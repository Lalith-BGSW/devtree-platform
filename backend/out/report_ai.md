# USB DTS report - CCS20

- Workbook: `Nissan_CCS20_SA8155PSIP_PinMux.xlsm`  |  Target DTS: `(none - generated from scratch)`
- Output: `out\result_ai.dtsi`
- USB-matched pins: **21** (match mode: both)  |  oc/vbus: 3  |  other GPIOs: 2  |  PHY pads: 12  |  unsupported controller: 0  |  controller unconfirmed: 0  |  needs review: 4
- VBUS/OC pattern: **unconfirmed** (no existing VBUS/OC convention found in the supplied files, and no --pattern given - safe TLMM pin states may be generated; ambiguous or unsupported-controller pins are excluded and listed in the report. Re-run with --pattern controller or --pattern regulator to also get the consumer wiring (&usb0/&usb1 blocks or regulator-fixed nodes).)
- Detected controller instances: USB20 -> &usb1, USB30 -> &usb0

## Updated in existing file

No existing file given.

## Consumer nodes / new states

- &usb0 (USB30): electrical config generated above (section 2), but NOT wired into any consumer - pattern unconfirmed; re-run with --pattern controller or --pattern regulator to get this instance's &usb consumer block.
- &usb1 (USB20): electrical config generated above (section 2), but NOT wired into any consumer - pattern unconfirmed; re-run with --pattern controller or --pattern regulator to get this instance's &usb consumer block.
- 5 new pinctrl state(s) inserted in a new &tlmm block (none existed)

## Verification: every USB-related TLMM GPIO, existing file as it was BEFORE this run

| Pin | Existing node | Status | Difference |
|---|---|---|---|
| GPIO_81 | (none in file) | - | - |
| GPIO_102 | (none in file) | - | - |
| GPIO_82 | (none in file) | - | - |
| GPIO_38 | (none in file) | - | - |
| GPIO_113 | (none in file) | - | - |
| GPIO_23 | (none in file) | - | - |
| GPIO_24 | (none in file) | - | - |
| GPIO_92 | (none in file) | - | - |
| GPIO_136 | (none in file) | - | - |

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

## USB pins on an unsupported pin controller - not generated

None.

## NEEDS REVIEW - not generated (never guessed)


**Without hub**

| Pin | Gen5 Signal Name | Why | Excel remark | Schematic net (from the workbook) |
|---|---|---|---|---|
| GPIO_136 | SCC_CPU_BOOT_1V8:BOOT_MODE0, SA8155SIP_BOOTCFG_1V8:FORCE_USB | Boot-strap only - read at reset, not a runtime USB signal (excluded from runtime DTS) | - | PinMux4 |

**With hub**

| Pin | Gen5 Signal Name | Why | Excel remark | Schematic net (from the workbook) |
|---|---|---|---|---|
| GPIO_23 | CPU_SUBPCB_I2C_1V8:I2C_SDA, CPU_A2B_I2C_1V8:I2C_SDA, CPU_USBHUB_SMI2C_1V8:SMB_DATA, CPU_VO_RSE_I2C_1V8:I2C_SDA | Shared I2C bus pin - 4 devices on one bus (CPU_SUBPCB_I2C, CPU_A2B_I2C, CPU_USBHUB_SMI2C, CPU_VO_RSE_I2C); it is configured by the I2C controller's pinctrl, not by a USB node. Multi-purpose pin - 4 distinct signal roles on this ball | - | PinMux1 |
| GPIO_24 | CPU_SUBPCB_I2C_1V8:I2C_SCL, CPU_A2B_I2C_1V8:I2C_SCL, CPU_USBHUB_SMI2C_1V8:SMB_CLK, CPU_VO_RSE_I2C_1V8:I2C_SCL | Shared I2C bus pin - 4 devices on one bus (CPU_SUBPCB_I2C, CPU_A2B_I2C, CPU_USBHUB_SMI2C, CPU_VO_RSE_I2C); it is configured by the I2C controller's pinctrl, not by a USB node. Multi-purpose pin - 4 distinct signal roles on this ball | - | PinMux1 |
| GPIO_92 | CPU_USBHUB_CTRL_1V8:USB_HUB_I2C_EN, CPU_USB20_CTRL_1V8:USB_PWR_EN | Multi-purpose pin - 2 distinct signal roles on this ball | - | PinMux3 |

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

## AI advisory - explanation only, NOT used to generate or change anything

_AI-generated. Treat as a reading aid, not as a fact. Every quote below was checked to occur in the workbook evidence; any specific the AI added that is not in that evidence was removed._

| Pin | Situation | Summary | Open questions for the hardware owner | Evidence quoted |
|---|---|---|---|---|
| GPIO_23 | shared_i2c_bus | GPIO_23 is a shared I2C bus pin used by four devices on one bus. It serves multiple roles as an I2C data line for different interfaces. | What are the specific I2C addresses for the devices on the shared bus? / Is there a specific priority or order for the devices on the I2C bus? / Are there any additional configurations required for the I2C controller? | Shared I2C bus pin - 4 devices on one bus (CPU_SUBPCB_I2C, CPU_A2B_I2C, CPU_USBHUB_SMI2C, CPU_VO_RSE_I2C) / Multi-purpose pin - 4 distinct signal roles on this ball |
| GPIO_24 | shared_i2c_bus | GPIO_24 is a shared I2C bus pin used by four devices on one bus. It serves multiple roles as an I2C clock signal for different interfaces. | What are the specific I2C addresses for the devices on the shared bus? / Is there a specific priority or order for the devices on the I2C bus? / What is the expected behavior if multiple devices attempt to communicate simultaneously? | Shared I2C bus pin - 4 devices on one bus (CPU_SUBPCB_I2C, CPU_A2B_I2C, CPU_USBHUB_SMI2C, CPU_VO_RSE_I2C) / Multi-purpose pin - 4 distinct signal roles on this ball |
| GPIO_92 | shared_gpio_net | GPIO_92 serves multiple roles, including USB_HUB_I2C_EN and USB_PWR_EN. It is a multi-purpose pin with distinct signal roles. | What are the specific use cases for each signal role? / Is there a priority or condition for selecting between the roles? / Are there any constraints on using GPIO_92 for both roles simultaneously? | Multi-purpose pin - 2 distinct signal roles on this ball / CPU_USBHUB_CTRL_1V8:USB_HUB_I2C_EN / CPU_USB20_CTRL_1V8:USB_PWR_EN |
| GPIO_136 | boot_strap_only | GPIO_136 is used for boot configuration and is only read at reset, not during runtime. It is associated with signals related to boot modes and USB force settings. | What specific boot modes are configured using this pin? / Are there any runtime functions associated with GPIO_136? / Is there a need for this pin to be included in the device tree despite being boot-strap only? | Boot-strap only - read at reset, not a runtime USB signal (excluded from runtime DTS) / SCC_CPU_BOOT_1V8:BOOT_MODE0 / SA8155SIP_BOOTCFG_1V8:FORCE_USB |

Model/deployment: askbosch-prod-farm-openai-gpt-4o-mini-2024-07-18 | calls: 4 | tokens: 2847 in / 701 out | statements removed by the guard: 0
