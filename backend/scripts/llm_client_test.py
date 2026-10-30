"""
Tests app/llm_client.py against a LOCAL MOCK of the gateway (scripts/_mock_llm.py). What this proves: the
request is built the way the Bosch Model Farm reference documents it (path, api-version, headers, body),
reasoning-model parameters are handled, errors are readable, the key never leaks, and features are gated.
What it cannot prove: that the real gateway accepts the request - run `python -m app.llm_client check` on
your network for that.
"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from _mock_llm import MockLLM
from app import llm_client as L

KEY = "SECRET-KEY-123"
mock = MockLLM()
base = mock.start()

def setenv(**kw):
    for k in [k for k in os.environ if k.startswith("LLM_")]:
        del os.environ[k]
    for k, v in kw.items():
        os.environ[k] = v

def fresh(**kw):
    mock.requests.clear(); mock.script.clear()
    setenv(LLM_BASE_URL=base, LLM_API_KEY=KEY, LLM_DEPLOYMENT="askbosch-prod-farm-openai-gpt-4o-mini-2024-07-18", LLM_TIMEOUT="3", **kw)
    return L.get_client("usb_advice")

print("=== 1. Request shape matches the BMF reference ===")
c = fresh()
r = c.chat.completions.create(messages=[{"role": "user", "content": "hi"}], temperature=0.0, max_tokens=5)
q = mock.requests[0]
assert q["path"] == "/api/openai/deployments/askbosch-prod-farm-openai-gpt-4o-mini-2024-07-18/chat/completions?api-version=2025-04-01-preview", q["path"]
assert q["headers"]["genaiplatform-farm-subscription-key"] == KEY and q["headers"]["authorization"] == "Bearer " + KEY
assert "api-key" not in q["headers"], "default is the combination confirmed working on the real gateway: custom header + Bearer"
assert q["body"]["model"] == "askbosch-prod-farm-openai-gpt-4o-mini-2024-07-18" and q["body"]["temperature"] == 0.0
assert q["body"]["max_tokens"] == 5 and q["body"]["messages"][0]["content"] == "hi"
assert r.choices[0].message.content == "OK" and r.usage["prompt_tokens"] == 100
print("OK")

print("=== 2. Auth modes ===")
fresh(LLM_AUTH_MODE="key_header").chat.completions.create(messages=[], max_tokens=1)
assert "authorization" not in mock.requests[-1]["headers"] and "genaiplatform-farm-subscription-key" in mock.requests[-1]["headers"]
fresh(LLM_AUTH_MODE="all").chat.completions.create(messages=[], max_tokens=1)
h = mock.requests[-1]["headers"]
assert h["api-key"] == KEY and h["authorization"] == "Bearer " + KEY and h["genaiplatform-farm-subscription-key"] == KEY
fresh(LLM_AUTH_MODE="azure").chat.completions.create(messages=[], max_tokens=1)
h = mock.requests[-1]["headers"]
assert h["api-key"] == KEY and "authorization" not in h and h["genaiplatform-farm-subscription-key"] == KEY
fresh(LLM_AUTH_MODE="bearer").chat.completions.create(messages=[], max_tokens=1)
assert "genaiplatform-farm-subscription-key" not in mock.requests[-1]["headers"] and mock.requests[-1]["headers"]["authorization"] == "Bearer " + KEY
fresh(LLM_EXTRA_HEADERS='{"x-sub-id": "personal-test"}').chat.completions.create(messages=[], max_tokens=1)
assert mock.requests[-1]["headers"]["x-sub-id"] == "personal-test"
print("OK")

print("=== 3. GPT-5 / o-series deployments: no temperature, max_completion_tokens with headroom ===")
setenv(LLM_BASE_URL=base, LLM_API_KEY=KEY, LLM_DEPLOYMENT="gpt-5-nano-2025-08-07")
mock.requests.clear()
L.get_client("usb_advice").chat.completions.create(messages=[], temperature=0.0, max_tokens=600)
b = mock.requests[0]["body"]
assert "temperature" not in b and "max_tokens" not in b and b["max_completion_tokens"] >= 1500
assert "/deployments/gpt-5-nano-2025-08-07/chat/completions" in mock.requests[0]["path"]
print("OK")

print("=== 4. Errors are readable and NEVER contain the key ===")
for status, needle in ((401, "key rejected"), (403, "not allowed"), (404, "path not found")):
    c = fresh(); mock.script.append((status, {"error": "x " + KEY}, {}))
    try:
        c.chat.completions.create(messages=[], max_tokens=1); assert False
    except L.LLMError as e:
        assert needle in str(e) and KEY not in str(e), str(e)
        assert "gateway said:" in str(e) and "x ***" in str(e), "the gateway's own message is shown, with the key redacted"
c = fresh(); mock.script += [(429, {"error": "You are out of tokens for this month"}, {"Retry-After": "0"})] * 2
try:
    c.chat.completions.create(messages=[], max_tokens=1); assert False
except L.LLMError as e:
    assert "quota" in str(e) and "out of tokens for this month" in str(e) and KEY not in str(e), str(e)
print("OK")

print("=== 5. 429 is retried once (honours Retry-After) ===")
c = fresh(); mock.script.append((429, {"error": "slow down"}, {"Retry-After": "0"}))
assert c.chat.completions.create(messages=[], max_tokens=1).choices[0].message.content == "OK" and len(mock.requests) == 2
print("OK")

print("=== 6. Unreachable gateway -> LLMError, not a hang or a crash ===")
setenv(LLM_BASE_URL="http://127.0.0.1:9/api", LLM_API_KEY=KEY, LLM_DEPLOYMENT="d", LLM_TIMEOUT="2")
t0 = time.time()
try:
    L.get_client("usb_advice").chat.completions.create(messages=[], max_tokens=1); assert False
except L.LLMError as e:
    # a plain machine refuses the connection; a corporate Windows machine (proxy / firewall) times out instead - both must read the same way
    assert "could not reach" in str(e) and ("ConnectionRefusedError" in str(e) or "TimeoutError" in str(e) or "OSError" in str(e)) and "diagnose" in str(e) and KEY not in str(e)
assert time.time() - t0 < 15
print("OK")

print("=== 7. Feature gating and configuration ===")
fresh()
assert L.get_client("usb_advice") is not None
assert L.get_client("chat") is None and L.get_client("value_decoder") is None, "only usb_advice is on by default"
fresh(LLM_FEATURES="usb_advice,chat"); assert L.get_client("chat") is not None
setenv(LLM_BASE_URL=base, LLM_DEPLOYMENT="d"); assert L.get_client("usb_advice") is None and not L.is_configured()   # no key
setenv(LLM_BASE_URL=base, LLM_API_KEY=KEY); assert L.get_client("usb_advice") is None                                  # no deployment
print("OK")

print("=== 8. status() never exposes the key; usage is counted ===")
fresh(); s = L.status()
assert KEY not in str(s) and s["configured"] and s["key_present"] and s["auth_mode"] == "both"
before = L.usage_totals["calls"]
L.get_client("usb_advice").chat.completions.create(messages=[], max_tokens=1)
assert L.usage_totals["calls"] == before + 1
print("OK")

print("=== 9. `python -m app.llm_client check` (the live test you run on your network) ===")
import io, contextlib
def run_check():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = L._check()
    return rc, buf.getvalue()
fresh(); rc, out = run_check()
assert rc == 0 and "Call succeeded" in out and KEY not in out
fresh(); mock.script.append((401, {"error": "no"}, {}))
rc, out = run_check(); assert rc == 2 and "key rejected" in out and KEY not in out
setenv(LLM_BASE_URL=base); rc, out = run_check(); assert rc == 1 and "Not configured" in out
print("OK")

print("=== 10. `diagnose` pinpoints the failing step and sends no key ===")
fresh()
import io as _io, contextlib as _cl
def run_diag():
    buf = _io.StringIO()
    with _cl.redirect_stdout(buf):
        rc = L._diagnose()
    return rc, buf.getvalue()
fresh(); rc, out = run_diag()
assert "DNS lookup:               OK" in out and "Direct TCP connection:    OK" in out and ("WORKS" in out or "reachable, HTTP" in out) and rc == 0, out
assert KEY not in out and not any("authorization" in r["headers"] for r in mock.requests)
setenv(LLM_BASE_URL="http://127.0.0.1:9/api", LLM_API_KEY=KEY, LLM_DEPLOYMENT="d")
rc, out = run_diag()
assert rc == 3 and "Direct TCP connection:    FAILED" in out and "FAILED" in out.split("4. HTTP")[1] and KEY not in out, out
setenv(LLM_BASE_URL="http://no-such-host.invalid/api", LLM_API_KEY=KEY, LLM_DEPLOYMENT="d")
rc, out = run_diag()
assert "DNS lookup:               FAILED" in out and "cannot resolve the host name" in out and "LLM_PROXY=http" in out
print("OK")

print("=== 11. LLM_PROXY: a host this machine cannot resolve is reached through the proxy ===")
mock.requests.clear(); mock.script.clear()
setenv(LLM_BASE_URL="http://no-such-host.invalid/api", LLM_API_KEY=KEY, LLM_DEPLOYMENT="d", LLM_PROXY=base.rsplit("/api", 1)[0], LLM_TIMEOUT="3")
r = L.get_client("usb_advice").chat.completions.create(messages=[], max_tokens=1)
assert r.choices[0].message.content == "OK"
assert mock.requests[0]["path"].startswith("http://no-such-host.invalid/api/openai/deployments/d/chat/completions?api-version="), mock.requests[0]["path"]
assert L.status()["proxy_configured"] is True and base.rsplit("/api", 1)[0] not in str(L.status())
rc, out = run_diag()
assert rc == 0 and "LLM_PROXY (from .env): set" in out and "DNS lookup:               FAILED" in out and "normal; nothing to fix" in out and KEY not in out, out
print("OK")

print("=== 12. LLM_PROXY=auto uses the system (PAC-evaluated) proxy; the lookup is injection-safe ===")
orig = L._system_proxy_for
L._system_proxy_for = lambda url: base.rsplit("/api", 1)[0]          # what Windows would answer
mock.requests.clear(); mock.script.clear()
setenv(LLM_BASE_URL="http://no-such-host.invalid/api", LLM_API_KEY=KEY, LLM_DEPLOYMENT="d", LLM_PROXY="auto", LLM_TIMEOUT="3")
assert L.get_client("usb_advice").chat.completions.create(messages=[], max_tokens=1).choices[0].message.content == "OK"
assert mock.requests[0]["path"].startswith("http://no-such-host.invalid/api/openai/")
L._system_proxy_for = lambda url: None                                # Windows says: direct
assert L.config()["proxy"] == ""
L._system_proxy_for = orig
L._proxy_cache.clear()
assert L._system_proxy_for("https://x.example/api/; calc") is None and L._system_proxy_for("x$(whoami)") is None   # never reaches a shell
print("OK")

print("=== 13. PowerShell transport: for a proxy that wants the Windows login (HTTP 407) ===")
if L._powershell_exe():
    UNI = "drive 10 \u00b5A - 85 \u00b0C \u2014 ok"
    c = fresh(LLM_TRANSPORT="powershell")
    r = c.chat.completions.create(messages=[{"role": "user", "content": UNI}], max_tokens=5)
    q = mock.requests[-1]
    assert r.choices[0].message.content == "OK" and q["body"]["messages"][0]["content"] == UNI, "unicode must survive intact"
    assert q["headers"]["genaiplatform-farm-subscription-key"] == KEY and q["headers"]["authorization"] == "Bearer " + KEY
    assert q["headers"]["content-type"].startswith("application/json") and "powershell" in q["headers"]["user-agent"].lower()
    assert q["path"].startswith("/api/openai/deployments/") and q["body"]["model"] and "temperature" in q["body"]
    c = fresh(LLM_TRANSPORT="powershell"); mock.script.append((401, {"error": "bad " + KEY}, {}))
    try:
        c.chat.completions.create(messages=[], max_tokens=1); assert False
    except L.LLMError as e:
        assert "key rejected" in str(e) and "bad ***" in str(e) and KEY not in str(e), str(e)
    setenv(LLM_BASE_URL="http://127.0.0.1:9/api", LLM_API_KEY=KEY, LLM_DEPLOYMENT="d", LLM_TRANSPORT="powershell", LLM_TIMEOUT="5")
    try:
        L.get_client("usb_advice").chat.completions.create(messages=[], max_tokens=1); assert False
    except L.LLMError as e:
        assert "could not reach" in str(e) and "PowerShell" in str(e) and KEY not in str(e), str(e)
    mock.requests.clear(); mock.script.clear()
    setenv(LLM_BASE_URL="http://no-such-host.invalid/api", LLM_API_KEY=KEY, LLM_DEPLOYMENT="d", LLM_TRANSPORT="powershell",
           LLM_PROXY=base.rsplit("/api", 1)[0], LLM_TIMEOUT="8")
    assert L.get_client("usb_advice").chat.completions.create(messages=[], max_tokens=1).choices[0].message.content == "OK"
    assert mock.requests[-1]["path"].startswith("http://no-such-host.invalid/api/openai/"), "PowerShell honoured LLM_PROXY"
    # auto: a 407 from the proxy makes the client switch to PowerShell by itself (what happens on your Windows PC)
    real_win = L._is_windows
    L._is_windows = lambda: True; L._force_ps = False
    c = fresh(); mock.script.append((407, {"error": "proxy authentication required"}, {}))
    n0 = len(mock.requests)
    r = c.chat.completions.create(messages=[], max_tokens=1)
    assert r.choices[0].message.content == "OK" and len(mock.requests) - n0 == 2 and L._force_ps is True
    assert "powershell" not in mock.requests[-2]["headers"]["user-agent"].lower(), "first attempt is plain Python"
    assert "powershell" in mock.requests[-1]["headers"]["user-agent"].lower(), "the retry went through PowerShell"
    n1 = len(mock.requests); c.chat.completions.create(messages=[], max_tokens=1)
    assert len(mock.requests) - n1 == 1 and "powershell" in mock.requests[-1]["headers"]["user-agent"].lower(), "stays on PowerShell"
    # python-only mode: the 407 is an error with a useful hint
    L._force_ps = False
    c = fresh(LLM_TRANSPORT="python"); mock.script.append((407, {"error": "proxy authentication required"}, {}))
    try:
        c.chat.completions.create(messages=[], max_tokens=1); assert False
    except L.LLMError as e:
        assert "proxy rejected your login" in str(e) and KEY not in str(e)
    # diagnose: explains the 407 and proves the PowerShell route works
    L._force_ps = False
    fresh(); mock.script.append((407, {"error": "proxy authentication required"}, {}))
    rc, out = run_diag()
    assert "answered 407" in out and "5. Same request via PowerShell" in out and "WORKS" in out and "Nothing to fix" in out and rc == 0, out
    assert KEY not in out
    L._is_windows = real_win; L._force_ps = False
    print("OK")
else:
    print("(PowerShell not available here - transport test skipped)")

print("=== 14. An existing Model Farm .env (FARM_* names) works; LLM_* wins; copy-paste junk in the URL is dropped ===")
for k in [k for k in os.environ if k.startswith(("LLM_", "FARM_"))]:
    del os.environ[k]
os.environ.update(FARM_BASE_URL='https://aoai-farm.bosch-temp.com/api"]', FARM_API_KEY=KEY,
                  FARM_DEPLOYMENT_NAME="askbosch-prod-farm-openai-gpt-4o-mini-2024-07-18", FARM_API_VERSION="2023-12-01-preview")
c = L.config()
assert c["key"] == KEY and c["deployment"].endswith("gpt-4o-mini-2024-07-18") and c["api_version"] == "2023-12-01-preview"
assert c["base_url"] == "https://aoai-farm.bosch-temp.com/api", c["base_url"]
os.environ["LLM_API_VERSION"] = "2025-04-01-preview"
assert L.config()["api_version"] == "2025-04-01-preview", "LLM_* takes precedence over FARM_*"
os.environ["LLM_API_VERSION"] = ""
assert L.config()["api_version"] == "2023-12-01-preview", "an empty LLM_* value falls back, it does not blank the setting"
for k in [k for k in os.environ if k.startswith("FARM_")]:
    del os.environ[k]
print("OK")

mock.stop()
print("\nLLM CLIENT TEST PASSED")
