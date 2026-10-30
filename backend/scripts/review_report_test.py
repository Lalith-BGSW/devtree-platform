"""
Tests the review-report feature end-to-end THROUGH the actual FastAPI
endpoints (via TestClient), not just the underlying functions directly -
deliberately, because that's exactly where two real crash bugs were
hiding that no other test caught:
  1. GenerateDtsResponse/DtsBatchUpdateResponse had no review_report
     field - Pydantic v2 silently DROPS unknown kwargs rather than
     raising, so the report was being computed and thrown away on every
     single call, with no error to reveal it.
  2. build_generation_review_report was called in main.py but never
     imported - a NameError waiting to happen on every /generate-dts call.
  3. The remainder-DTS call inside /dts-update unpacked generate_dts()'s
     return as 2 values after it became a 3-tuple - a real ValueError on
     every /dts-update call.
  4. The exact same 2-value unpack bug existed independently in TWO of
     the agent's tools (generate_dts_from_scratch, generate_remainder_dts).

All four are fixed; this test locks them in by actually exercising the
endpoints, the way a real client would.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import sys as _s, os as _o; _s.path.insert(0, _o.path.join(_o.path.dirname(_o.path.abspath(__file__)), '..', 'scripts')); import _samples; _samples.need(__file__)

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

WORKBOOK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "samples", "Nissan_CCS20_SA8155PSIP_PinMux.xlsx")
SAMPLE_DTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "samples", "ccs20-pinmux.dtsi")

print("=== Upload and build ===")
with open(WORKBOOK_PATH, "rb") as f:
    resp = client.post("/api/workbook/upload", files={"file": ("wb.xlsx", f, "application/octet-stream")})
assert resp.status_code == 200, resp.text
workbook_id = resp.json()["workbook_id"]

resp = client.post(f"/api/workbook/{workbook_id}/build")
assert resp.status_code == 200, resp.text
print("OK")

print("\n=== /api/generate-dts must not crash, must include review_report ===")
resp = client.post("/api/generate-dts", json={
    "workbook_id": workbook_id, "project": "CCS20", "query": "full",
})
assert resp.status_code == 200, f"CRASHED: {resp.status_code} {resp.text}"
data = resp.json()
assert "review_report" in data, "review_report field missing from response"
assert len(data["review_report"]) > 0, "review_report is empty"
assert "node(s) need review" in data["review_report"]
print(f"OK: review_report present, {len(data['review_report'])} chars")
print(data["review_report"][:300])

print("\n=== /api/workbook/{id}/dts-update must not crash, must include review_report ===")
with open(SAMPLE_DTS_PATH, "rb") as f:
    resp = client.post(
        f"/api/workbook/{workbook_id}/dts-update",
        data={"project": "CCS20", "output_filenames": '["test_output.dtsi"]', "use_ai_fallback": "false"},
        files={"files": ("sample.dtsi", f, "text/plain")},
    )
assert resp.status_code == 200, f"CRASHED: {resp.status_code} {resp.text}"
data = resp.json()
assert "review_report" in data, "review_report field missing from update response"
assert len(data["review_report"]) > 0
assert "Values updated" in data["review_report"] or "node(s) updated" in data["review_report"]
print(f"OK: review_report present, {len(data['review_report'])} chars")
print(data["review_report"][:500])

print("\nREVIEW REPORT END-TO-END TEST PASSED")
