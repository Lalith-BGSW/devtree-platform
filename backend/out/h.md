# USB DTS report - CCS20

- Workbook: `Nissan_CCS20_SA8155PSIP_PinMux.xlsm`  |  Target DTS: `(none - generated from scratch)`
- Output: `out\h.dtsi`
- USB-matched pins: **21** (match mode: both)  |  oc/vbus: 3  |  other GPIOs: 2  |  PHY pads: 12  |  unsupported controller: 0  |  controller unconfirmed: 0  |  needs review: 4
- VBUS/OC pattern: **controller** (explicitly requested (--pattern controller))
- Detected controller instances: USB20 -> &usb1, USB30 -> &usb0

## Updated in existing file

No existing file given.

## Consumer nodes / new states

- &usb0 (USB30): generated
- &usb1 (USB20): generated - template lines only confirmed for the reference instance left as NEEDS REVIEW
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
| GPIO_136 | SCC_CPU_BOOT_1V8:BOOT_MODE0, SA8155SIP_BOOTCFG_1V8:FORCE_USB | Boot-strap only - read at reset, not a runtime USB signal (excluded from runtime DTS) | - | D5700(K37) z28.z33.D5700_K37_SCC_CPU_BOOT_1V8_BOOT_MODE0_P0,MP5961=MP(1) ENDPNT,R6025(1) SCC_CPU_BOOT_1V8:BOOT_MODE0,R8673(2) z34.z37.SIGN00173,D8634(4) CPU_DBG_CTRL_3V3:BOOT_MODE0,MP9962=MP(1) ENDPNT,X9960=QSH-030-01-L-D-A-K-TR(27) ENDPNT,R8757(2) z34.SCC_CPU |

**With hub**

| Pin | Gen5 Signal Name | Why | Excel remark | Schematic net (from the workbook) |
|---|---|---|---|---|
| GPIO_23 | CPU_SUBPCB_I2C_1V8:I2C_SDA, CPU_A2B_I2C_1V8:I2C_SDA, CPU_USBHUB_SMI2C_1V8:SMB_DATA, CPU_VO_RSE_I2C_1V8:I2C_SDA | Shared I2C bus pin - 4 devices on one bus (CPU_SUBPCB_I2C, CPU_A2B_I2C, CPU_USBHUB_SMI2C, CPU_VO_RSE_I2C); it is configured by the I2C controller's pinctrl, not by a USB node. Multi-purpose pin - 4 distinct signal roles on this ball | - | D5700(AJ20) CPU_SUBPCB_I2C_1V8:I2C_SDA,MP8207=MP(1) ENDPNT,X8190=245656060009829+(54) ENDPNT |
| GPIO_24 | CPU_SUBPCB_I2C_1V8:I2C_SCL, CPU_A2B_I2C_1V8:I2C_SCL, CPU_USBHUB_SMI2C_1V8:SMB_CLK, CPU_VO_RSE_I2C_1V8:I2C_SCL | Shared I2C bus pin - 4 devices on one bus (CPU_SUBPCB_I2C, CPU_A2B_I2C, CPU_USBHUB_SMI2C, CPU_VO_RSE_I2C); it is configured by the I2C controller's pinctrl, not by a USB node. Multi-purpose pin - 4 distinct signal roles on this ball | - | D5700(AL20) CPU_SUBPCB_I2C_1V8:I2C_SCL,MP8206=MP(1) ENDPNT,X8190=245656060009829+(52) ENDPNT |
| GPIO_92 | CPU_USBHUB_CTRL_1V8:USB_HUB_I2C_EN, CPU_USB20_CTRL_1V8:USB_PWR_EN | Multi-purpose pin - 2 distinct signal roles on this ball | - | D5700(AB17) CPU_USBHUB_CTRL_1V8:USB_HUB_I2C_EN,MP8191=MP(1) ENDPNT,X8190=245656060009829+(12) ENDPNT |

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

## Facts extracted from your documents (AI read them, Python verified each quote)

2 fact(s) accepted, 0 rejected (the quote was not literally in the document, or the fact contained a specific its quote did not).
- `hub_notes.md`: 133 characters read, the AI returned 2 fact(s) -> 2 accepted, 0 rejected

1. The USB hub is a microchip,usb2514. (from hub_notes.md: "The USB hub is a microchip,usb2514 attached to the QUP18 I2C bus (node label &i2c18).")
2. Its I2C address is 0x2d. (from hub_notes.md: "Its I2C address is 0x2d.")

## Hybrid output - Python-verified nodes plus the AI additions that passed every check

**Status: CLEAN - the final file passed every automatic check.** Final file written to `out\h.hybrid.dtsi`.

Every active line in the final file is either generated by Python or an AI addition that passed Python's checks: values against the Excel, verified lines compared with the tool's output, `dtc` compile, and a source check on anything the AI added. AI lines that failed the source check are kept as `// UNVERIFIED` comments - visible, not active. The Python-only output above is unchanged.

- Rounds: AI #1: 0 problem(s)
- Verifier on the final draft: 0 wrong | 0 ambiguous | 7 unverifiable | 8 confirmed OK
- Verified lines changed or dropped by the AI: 0
- dtc not installed here - run your normal DTS build to compile-check.
- Model usage: 1 call(s), 3074 in / 823 out tokens

**Kept as comments (not active) - the AI added a specific that is not in the verified output, the evidence or your facts:**

| Line | What | Value |
|---|---|---|
| 94 | compatible-style string | `"qcom,i2c-qup"` |
| 95 | I2C address / hex value | `0x0` |
| 95 | I2C address / hex value | `0x0` |
| 95 | I2C address / hex value | `0x0` |
| 95 | I2C address / hex value | `0x0` |

**Cannot be verified from the workbook (needs your confirmation):**
- line 94: compatible = "qcom,i2c-qup": chip/IP identity - not in the workbook; needs a datasheet or existing node
- line 100: compatible = "microchip,usb2514": chip/IP identity - not in the workbook; needs a datasheet or existing node
- line 95: reg = <0x0 0x0 0x0 0x0>: address - not in the workbook
- line 101: reg = <0x2d>: address - not in the workbook
- line 92: unit address @0: not in the workbook
- line 78: usb-phy = <&usb2_phy0>, <&usb_nop_phy>: board/PHY wiring fact - not in the workbook; check against a real reference
- line 79: maximum-speed = "high-speed": board/PHY wiring fact - not in the workbook; check against a real reference

<details><summary>Diff: Python-only output -> hybrid final</summary>

```diff
--- python_only_output.dtsi
+++ hybrid_final.dtsi
@@ -22,3 +22,3 @@
 			drive-strength = <10>;
-			/* NEEDS REVIEW: output pin - add output-high or output-low per hardware design (level is not derivable from Excel) */
+			output-high; /* source: FACT 1 */
 			bias-disable;
@@ -91,2 +91,14 @@
 	 * add from a real reference if this instance needs them. */
+
+	i2c18: i2c@0 {
+// UNVERIFIED [compatible-style string "qcom,i2c-qup" is not in the workbook, the verified output or your facts]: compatible = "qcom,i2c-qup";
+// UNVERIFIED [I2C address / hex value 0x0 is not in the workbook, the verified output or your facts]: reg = <0x0 0x0 0x0 0x0>; /* NEEDS REVIEW: missing reg property */
+		#address-cells = <1>;
+		#size-cells = <0>;
+
+		hub@2d {
+			compatible = "microchip,usb2514";
+			reg = <0x2d>; /* source: FACT 2 */
+		};
+	}
 };
```

</details>
