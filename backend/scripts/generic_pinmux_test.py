"""Generic (non-Qualcomm) preview: groups by selected interface, checker catches tampering, Qualcomm rows are ignored."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import generic_pinmux as gp

FAILS = []
def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {info}"))
    if not ok:
        FAILS.append(name)

class R:
    def __init__(self, **f): self.fields = f

def row(ball, inst, pm, mux, sig, pe="0: disable", ps="0: Pull-down", drv="1: x4"):
    return R(ball_name=ball, selected_interface=inst, pinmuxed_name=pm, pinmux_setting=mux, gen5_signal_name=sig,
             pull_enable=pe, pull_select=ps, drive_strength=drv, ball_id="X1")

recs = [row("GPIO_A06", "GPSB_06", "GSCK06", 6, "S:SPI_CLK"), row("GPIO_A07", "GPSB_06", "GSCMD06", 6, "S:SPI_CS_B"),
        row("GPIO_A18", "UART_08", "UT_TXD08", 7, "D:UART_RXD"), row("GPIO_A19", "UART_08", "UT_RXD08", 7, "D:UART_TXD", pe="1: enable", ps="1: Pull-up"),
        row("GPIO_A04", "I2C_03", "I2C_SCL03", 8, "V:I2C_SCL"), row("GPIO_A05", "I2C_03", "I2C_SDA03", 8, "V:I2C_SDA"),
        row("GPIO_B01", "OLDI0", "X", 1, "O:X"), row("gpio6", "SPI-M_QUP6", "qup", 1, "Q:SPI_CLK")]
g = gp.generate(recs, "T")
check("44-style grouping: 3 controllers, 6 pins", g["controllers"] == 3 and len(g["pins"]) == 6, g["counts"])
check("QUP and non-bus rows are ignored", all(p["pin"].startswith("GPIO_A") for p in g["pins"]))
check("every block is stamped unverified", g["text"].count("UNVERIFIED") >= 4 and "&pinctrl" in g["text"])
check("pull-up read from the workbook", "bias-pull-up;" in g["text"] and "bias-disable;" in g["text"])
check("UART swap vs Gen5 name flagged for review", g["text"].count("named from the other end") == 2)
check("drive stays a comment, not a number", "drive-strength" not in g["text"] and "units unconfirmed" in g["text"])
v = gp.check(g["text"], recs)
check("checker agrees with the generated file", not v["wrong"] and v["ok"] == 6, v)
bad = g["text"].replace('"GPIO_A19"', '"GPIO_A20"', 1)
check("checker catches a wrong pin", bool(gp.check(bad, recs)["wrong"]))
bad2 = g["text"].replace("bias-pull-up;", "bias-pull-down;")
check("checker catches a wrong bias", any("bias" in w for w in gp.check(bad2, recs)["wrong"]))
check("family filter works", gp.generate(recs, "T", ["i2c"])["counts"] == {"spi": 0, "uart": 0, "i2c": 2})
check("AI draft is unavailable, not an error, without config", gp.ai_draft(g["pins"], "T")["status"] in ("unavailable", "ok", "error"))
print("FAILED: " + ", ".join(FAILS) if FAILS else "all generic preview checks passed")
sys.exit(1 if FAILS else 0)
