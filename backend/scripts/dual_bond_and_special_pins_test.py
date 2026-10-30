"""Dual-bond alias balls must not create a second declaration of a TLMM pin; TLMM special pins are named correctly."""
import os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.join(HERE, "..")
sys.path.insert(0, ROOT)
from app.models import NormalizedRecord
from app.generators import dts_generator as G
F = []
def check(n, c):
    print(("PASS " if c else "FAIL ") + n)
    if not c: F.append(n)
def rec(row, ball, reg, **kw):
    f = {"soc_ball_name": ball, "pinmuxed_name": "DP_HOT_PLUG_DETECT", "selected_interface": "DP", "interface_group": "VO",
         "regsetting1": reg}; f.update(kw)
    return NormalizedRecord(project="P", soc="SA8155P", row_index=row, fields=f)
a = rec(1, "GPIO_10", "TLMM_GPIO_CFG10(1:0)", direction="0: IN", io_gpio_pull="1: PULL_DOWN", io_drv_strength="4: DRV_10_MA")
b = rec(2, "DP0_HPD", "TLMM_GPIO_CFG10(1:0)", io_drv_strength="4: DRV_10_MA")
check("alias resolves to gpio10", G._to_dts_pin_name(G._first_pin_identifier(b.fields)) == "gpio10")
t, _, _ = G.generate_dts("P", "SA8155P", [b, a])
check("pin declared once", len(re.findall(r'pins = "gpio10"', t)) == 2 and "dp0hpd" not in t)
check("keeps the row with settings", "bias-pull-down" in t)
sd = NormalizedRecord(project="P", soc="SA8155P", row_index=3, fields={"soc_ball_name": "SDC2_CLK", "pinmuxed_name": "SDC2_CLK",
     "selected_interface": "SD2", "interface_group": "SD2"})
t, _, _ = G.generate_dts("P", "SA8155P", [a, sd])
check("special pin named", 'sdc2clk -> pins = "sdc2_clk"' in t)
t, _, _ = G.generate_dts("P", "SA6155P", [a, sd])
check("other SoC family not mapped", "sdc2_clk" not in t)
sys.exit(1 if F else 0)
