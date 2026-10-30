FILES TO UPLOAD (all made up - no real data)
Upload 00 as the workbook, choose project CCS20, then use one file per field below. Generate once with nothing set, then add ONE file
and generate again: the "Compared with the previous run" box shows the difference.

Field in the USB section                         File                                     What you should see
-----------------------------------------------  ---------------------------------------  ----------------------------------------------------------
(workbook upload)                                00_workbook_demo_workbook.xlsx           5 pin states, 2 regulators, 2 &usbN nodes, 3 open decisions
Format > Template DTS files                      01_template_A_spaces_flat.dtsi           layout becomes 4 spaces + flat "-state" nodes; values identical
Format > Template DTS files                      01_template_B_tabs_nested.dtsi           tabs + nested mux/config; values identical. Do not attach A and B together.
Format > Linux kernel version (no template)      type 6.6, then 5.15                      6.6 flat nodes, 5.15 nested; values identical
Existing files > Existing DTS to update          02_existing_dts_with_2_mistakes.dts      2 values corrected in gpio10: drive 16 -> 4, bias pull-down -> pull-up; gpio12 untouched
Existing files > Overlay .dts                    03_overlay_for_regulator_style.dts       (needs Pattern = regulator) type  emrst  in "Insert regulators after node":
                                                                                          the 2 regulators land right after the emrst node, the rest of the overlay is kept
Your decisions > Output levels                   choose "None"                            the output-low lines on gpio11 / gpio13 disappear
Your decisions > Shared VBUS pins                (leave empty for the demo)               -
Inputs for the AI (Hybrid mode, needs gateway)   04_document_schematic_notes.txt          facts with verbatim quotes about gpio11 and gpio14 levels
Inputs for the AI > Facts                        paste the 2 lines of 05_facts.txt        gpio11 and gpio14 -> output-low (the same as the schematic note in 04); everything else unchanged; the "decisions" list then names only gpio13
Inputs for the AI > A draft to correct           06_draft_to_correct.dts                  its wrong drive-strength <16> and bias-pull-up on gpio11 are caught and corrected

The AI rows (04, 05, 06) need the Bosch Model Farm gateway; they could not be tried here. Everything else was run and checked.
Expected answers for every pin: see ../GUIDE.md.

AI data check (Hybrid mode, needs gateway): no file needed. On 00_workbook_demo_workbook.xlsx it should flag GPIO_12 (remark "External pull-down required" vs pull cell PULL_UP).
