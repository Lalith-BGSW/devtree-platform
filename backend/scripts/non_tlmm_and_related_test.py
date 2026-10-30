"""
Two guards for the GENERAL keyword generator (the one behind "Extract Pins by Signal"):
  1. A pin whose register is not a TLMM_GPIO_CFG one (e.g. SA7255P SAIL_CFG53) is listed, never emitted into &tlmm.
  2. Pins that share an Interface Group with the matched pins but were missed by the keyword are SUGGESTED, never auto-included.
Uses the bundled CCS20 workbook (SAIL rows are simulated by rewriting a register value) and, when IVECO_XL is set, the real Iveco one.
"""
import os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tool"))
import sys as _s, os as _o; _s.path.insert(0, _o.path.join(_o.path.dirname(_o.path.abspath(__file__)), '..', 'scripts')); import _samples; _samples.need(__file__)
import usb_dts_tool as T
from app.generators.dts_generator import generate_dts, non_tlmm_register
from app.peripheral_extractor import (resolve_gen5_signal_slug, filter_records_by_gen5_substring, related_group_mates,
                                      build_related_markdown, _resolve_field_slugs_for_summary)
from app.usb_consumer_generator import _ball_name

XL = os.path.join(ROOT, "samples", "Nissan_CCS20_SA8155PSIP_PinMux.xlsx")
parsed, recs, soc = T.load_records(XL, "CCS20")
by = {str(_ball_name(r)): r for r in recs}

print("=== 1. A non-TLMM register keeps the pin OUT of &tlmm and lists it ===")
tlmm_rec, off_rec = by["GPIO_82"], by["GPIO_81"].model_copy(deep=True)
for k, v in list(off_rec.fields.items()):
    if k.startswith("regsetting") and v:
        off_rec.fields[k] = "SAIL_CFG81(5:2)=0"
assert non_tlmm_register(tlmm_rec) is None and non_tlmm_register(off_rec) == "SAIL_CFG81(5:2)=0"
dts, amb, review = generate_dts("CCS20", soc, [tlmm_rec, off_rec])
node_part = dts.split("NOT GENERATED")[0]
assert 'pins = "gpio82"' in node_part and "gpio81" not in node_part and "sailio81" not in node_part, "off-controller pin must not be in a node"
assert "NOT GENERATED - 1 pin(s) on a pin controller other than TLMM" in dts and "GPIO_81  (SAIL_CFG81(5:2)=0)" in dts
assert any(e["node_name"].startswith("(not generated: pin controller is not TLMM)") and e["pins"] == ["GPIO_81"] for e in review), review
dts_ok, _a, review_ok = generate_dts("CCS20", soc, [tlmm_rec])
assert "NOT GENERATED" not in dts_ok and not any(e["node_name"].startswith("(not generated") for e in review_ok), "an all-TLMM selection is unchanged"
assert non_tlmm_register(type(tlmm_rec)(project="p", soc="s", row_index=0, fields={"ball_name": "X"})) is None, "no register data: left alone"
print("OK")

print("=== 2. Same-Interface-Group pins the keyword missed are SUGGESTED (not auto-included) ===")
g = resolve_gen5_signal_slug("CCS20", soc, parsed.columns)
ig = _resolve_field_slugs_for_summary("CCS20", soc, parsed.columns).get("interface_group")
matched = filter_records_by_gen5_substring(recs, "usb", g)
mates = related_group_mates(recs, matched, ig)
ids = {id(r) for r in matched}
norm = lambda v: re.sub(r"\s+", " ", str(v or "")).strip()
assert all(id(m) not in ids for m in mates), "never a matched row"
assert all(norm(m.fields.get(ig)) in {norm(r.fields.get(ig)) for r in matched} for m in mates), "always from a matched row's group"
assert related_group_mates(recs, matched, None) == [] and related_group_mates(recs, [], ig) == []
md = build_related_markdown(mates[:3], g, ig, "usb")
assert "NOT matched by `usb` (not in the DTS)" in md and md.count("\n| ") >= 3
print("OK")

print("=== 2b. The API's summary endpoint appends the related pins and reports their count ===")
from fastapi.testclient import TestClient
from app.main import app
c = TestClient(app)
wb = c.post("/api/workbook/upload", files={"file": ("w.xlsx", open(XL, "rb"))}).json()["workbook_id"]
assert c.post(f"/api/workbook/{wb}/build").status_code == 200
resp = c.get(f"/api/workbook/{wb}/peripheral/CCS20/summary", params={"keyword": "usb"})
assert resp.status_code == 200 and int(resp.headers["X-Related-Count"]) == len(mates), (resp.headers["X-Related-Count"], len(mates))
assert (len(mates) == 0) == ("### Related pins" not in resp.text)
dts_resp = c.get(f"/api/workbook/{wb}/peripheral/CCS20/dts", params={"keyword": "usb"})
assert dts_resp.status_code == 200 and "Related pins" not in dts_resp.json()["dts_text"], "suggestions never enter the DTS"
print("OK")

IVECO_XL = os.environ.get("IVECO_XL")
if IVECO_XL and os.path.exists(IVECO_XL):
    print("=== 3. Real Iveco B2, keyword spi: SAIL pins listed not emitted; IMU_CS_B suggested ===")
    p2, r2, soc2 = T.load_records(IVECO_XL, "Iveco CDC B2")
    g2 = resolve_gen5_signal_slug("Iveco CDC B2", soc2, p2.columns)
    ig2 = _resolve_field_slugs_for_summary("Iveco CDC B2", soc2, p2.columns).get("interface_group")
    m2 = filter_records_by_gen5_substring(r2, "spi", g2)
    assert len(m2) == 26
    d2, _a2, rv2 = generate_dts("Iveco CDC B2", soc2, m2)
    assert "sailio" not in d2.split("NOT GENERATED")[0] and "NOT GENERATED - 12 pin(s)" in d2, "the 12 SAIL pins are listed, not emitted"
    assert 'pins = "gpio10"' in d2 and "qupv3_se2_spi" in d2, "the 14 TLMM SPI pins are still generated"
    sug = {str(_ball_name(r)) for r in related_group_mates(r2, m2, ig2)}
    assert "GPIO_83" in sug, sug                      # IMU_CS_B: the pin Gemini included and the keyword missed
    print("OK")
else:
    print("(set IVECO_XL to run the real-Iveco check)")

print("\nNON-TLMM AND RELATED PINS TEST PASSED")
