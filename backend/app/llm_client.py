"""
Minimal chat-completions client for an Azure-OpenAI-style gateway (built for Bosch's GenAI platform,
https://aoai-farm.bosch-temp.com). Standard library only - no new dependency.

Everything is configuration, because the exact call shape is not something this code can verify from
outside the network. Put values in backend/.env (never in code, never in a zip - see .env.example):

    LLM_API_KEY          the subscription key (sent as the header named by LLM_KEY_HEADER)
    LLM_DEPLOYMENT       model/deployment name on the platform
    LLM_BASE_URL         default https://aoai-farm.bosch-temp.com/api   (the BMF base URL includes /api)
    LLM_API_VERSION      default 2025-04-01-preview
    LLM_CHAT_PATH        default /openai/deployments/{deployment}/chat/completions
    LLM_KEY_HEADER       default genaiplatform-farm-subscription-key
    LLM_AUTH_MODE        both (default, confirmed working against the real gateway) | all | azure | key_header | bearer.
                         both       = custom key header + `Authorization: Bearer` (platform docs)
                         all        = both + `api-key` (what the Azure OpenAI SDK sends)   azure = custom key header + api-key
                         key_header / bearer = just that one
    Existing Model Farm .env files work too: FARM_API_KEY, FARM_DEPLOYMENT_NAME, FARM_BASE_URL and FARM_API_VERSION are
    accepted as aliases (the LLM_* names win if both are set).
    LLM_EXTRA_HEADERS    optional JSON object of additional headers (e.g. a subscription id header)
    LLM_SEND_MODEL       "1" (default) puts "model" in the request body, as the BMF examples do; "0" to omit
    LLM_TIMEOUT          seconds, default 30
    LLM_CA_BUNDLE        path to a corporate CA bundle if TLS verification fails behind a proxy
    LLM_TRANSPORT        auto (default) | python | powershell. A corporate proxy that wants your Windows login (HTTP 407,
                         NTLM/Kerberos) cannot be answered by Python's built-in client. With auto, on Windows, the client
                         switches to PowerShell (which uses your Windows login) the first time it sees a 407.
    LLM_PROXY            http://host:port of the corporate proxy, if this machine cannot resolve the gateway's
                         name itself (browsers on such networks use a proxy script; Python cannot read one).
                         Basic-auth form http://user:pass@host:port works; NTLM/Kerberos proxies do not.
    LLM_FEATURES         comma list of features allowed to call the LLM. Default: usb_advice,usb_propose,usb_suggest
                         (the flagged-pin explainer, which sends only signal names / mux options /
                         net traces). Others: chat, value_decoder, node_extractor - these send
                         workbook rows or DTS text, so they are off unless you enable them.

get_client(feature) returns None unless the key + deployment are set AND the feature is enabled; every
caller already has a deterministic fallback for None. Errors never include the key.

Check your setup:   python -m app.llm_client check
"""

from __future__ import annotations

import json
import base64
import os
import re
import shutil
import ssl
import sys
import time
import urllib.error
import urllib.request
from types import SimpleNamespace

DEFAULTS = {
    "LLM_BASE_URL": "https://aoai-farm.bosch-temp.com/api",
    "LLM_API_VERSION": "2025-04-01-preview",
    "LLM_AUTH_MODE": "both",
    "LLM_TRANSPORT": "auto",
    "LLM_SEND_MODEL": "1",
    "LLM_CHAT_PATH": "/openai/deployments/{deployment}/chat/completions",
    "LLM_KEY_HEADER": "genaiplatform-farm-subscription-key",
    "LLM_TIMEOUT": "30",
    "LLM_FEATURES": "usb_advice,usb_propose,usb_suggest",
}

usage_totals = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}


class LLMError(Exception):
    """A failed LLM call. The message is safe to show: it never contains the key or request headers."""


_proxy_cache: dict = {}


def _system_proxy_for(url: str):
    """Windows only: ask Windows which proxy the BROWSER would use for this URL, evaluating the PAC script the
    browser uses (Python cannot read PAC scripts itself). Returns e.g. 'http://proxy.example:8080', or None for
    a direct connection / any failure / non-Windows. Cached per URL (starting PowerShell takes about a second)."""
    if url in _proxy_cache:
        return _proxy_cache[url]
    found = None
    if sys.platform == "win32" and re.fullmatch(r"[A-Za-z0-9:/._-]+", url or ""):     # strict: it goes into a command line
        import subprocess
        ps = ("$u=[Uri]'" + url + "'; $r=[System.Net.WebRequest]::GetSystemWebProxy().GetProxy($u); "
              "if ($r.AbsoluteUri -ne $u.AbsoluteUri) { $r.AbsoluteUri }")
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                                 capture_output=True, text=True, timeout=25).stdout.strip()
            found = out.splitlines()[0].strip().rstrip("/") if out else None
        except Exception:
            found = None
    _proxy_cache[url] = found
    return found


def _load_env_file() -> None:
    """Read backend/.env (KEY=VALUE lines) into os.environ without overriding real environment variables."""
    if os.environ.get("PINMUX_NO_DOTENV"):          # set by scripts/run_all_tests.py: tests use their own mock gateway, never your real key
        return
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if not os.path.exists(path):
        return
    try:
        for line in open(path, encoding="utf-8").read().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except OSError:
        pass


ALIASES = {"LLM_API_KEY": "FARM_API_KEY", "LLM_DEPLOYMENT": "FARM_DEPLOYMENT_NAME",
           "LLM_BASE_URL": "FARM_BASE_URL", "LLM_API_VERSION": "FARM_API_VERSION"}


def config() -> dict:
    _load_env_file()
    g = lambda k: (os.environ.get(k) or os.environ.get(ALIASES.get(k, "_none_")) or DEFAULTS.get(k, "")).strip().strip("\"'")
    try:
        extra = json.loads(g("LLM_EXTRA_HEADERS") or "{}")
        if not isinstance(extra, dict):
            extra = {}
    except ValueError:
        extra = {}
    cfg = {
        # re.split drops copy-paste junk such as a trailing  "]  or  %22
        "base_url": re.split(r"[\"'\[\]\s]|%22", g("LLM_BASE_URL"))[0].rstrip("/"),
        "key": g("LLM_API_KEY"), "key_header": g("LLM_KEY_HEADER"),
        "deployment": g("LLM_DEPLOYMENT"), "api_version": g("LLM_API_VERSION"), "chat_path": g("LLM_CHAT_PATH"),
        "timeout": float(g("LLM_TIMEOUT") or 30), "extra_headers": {str(k): str(v) for k, v in extra.items()},
        "send_model": g("LLM_SEND_MODEL") in ("1", "true", "yes"), "ca_bundle": g("LLM_CA_BUNDLE"), "proxy": g("LLM_PROXY"),
        "transport": g("LLM_TRANSPORT").lower() if g("LLM_TRANSPORT").lower() in ("auto", "python", "powershell") else "auto",
        "auth_mode": g("LLM_AUTH_MODE") if g("LLM_AUTH_MODE") in ("all", "both", "azure", "key_header", "bearer") else "both",
        "features": {x.strip() for x in g("LLM_FEATURES").split(",") if x.strip()},
    }
    if cfg["proxy"].lower() == "auto":              # LLM_PROXY=auto: use the proxy Windows/the browser would use
        cfg["proxy"] = _system_proxy_for(cfg["base_url"] + "/") or ""
    return cfg


def is_configured() -> bool:
    c = config()
    return bool(c["key"] and c["deployment"])


def status() -> dict:
    """What the UI may show: never the key."""
    c = config()
    return {"configured": bool(c["key"] and c["deployment"]), "base_url": c["base_url"],
            "deployment": c["deployment"] or None, "api_version": c["api_version"],
            "key_header": c["key_header"], "auth_mode": c["auth_mode"], "features": sorted(c["features"]),
            "key_present": bool(c["key"]), "proxy_configured": bool(c["proxy"]), "transport": c["transport"]}


def get_model_name() -> str:
    return config()["deployment"] or "unconfigured"


def _reason(e: BaseException) -> str:
    """The underlying cause of a failed connection, e.g. 'SSLCertVerificationError: ... self-signed certificate
    in certificate chain' or 'gaierror: [Errno 11001] getaddrinfo failed'. Safe to show: the key is only ever
    in request headers, never in this text."""
    r = getattr(e, "reason", e)
    return f"{type(r).__name__}: {str(r)[:170]}"


def is_reasoning_deployment(deployment: str) -> bool:
    """GPT-5 family and o-series deployments reject `temperature`/`max_tokens` (see _Completions.create)."""
    return bool(re.search(r"(?:^|-)(?:gpt-5|o1|o3|o4)(?:[-.\d]|$)", (deployment or "").lower()))


def _opener(cfg: dict, ctx):
    """urllib opener honouring LLM_PROXY (if set) and the corporate CA bundle; otherwise the system/env proxies."""
    handlers = []
    if cfg.get("proxy"):
        handlers.append(urllib.request.ProxyHandler({"http": cfg["proxy"], "https": cfg["proxy"]}))
    handlers.append(urllib.request.HTTPSHandler(context=ctx))
    return urllib.request.build_opener(*handlers)


_force_ps = False        # set once a proxy answered 407: from then on this process sends via PowerShell


class _Unreachable(Exception):
    """The request never produced an HTTP answer (DNS, proxy, TLS, timeout...). Message = the underlying cause."""


def _is_windows() -> bool:
    return sys.platform == "win32"


def _powershell_exe():
    return shutil.which("powershell") or shutil.which("pwsh")


# Sends one HTTP request using the current Windows user's login for the proxy (-ProxyUseDefaultCredentials).
# Data arrives on stdin as ASCII-only JSON (so the console code page cannot corrupt it) and the script is passed
# as -EncodedCommand (so execution policy does not apply) - the key is never on a command line or in a file.
_PS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}
$cfg = [Console]::In.ReadToEnd() | ConvertFrom-Json
$h = @{}
foreach ($p in $cfg.headers.PSObject.Properties) { $h[$p.Name] = [string]$p.Value }
$req = @{ Uri = [string]$cfg.url; Method = [string]$cfg.method; UseBasicParsing = $true; TimeoutSec = [int]$cfg.timeout }
if ($h.Count -gt 0) { $req.Headers = $h }
if ($cfg.method -eq 'POST') { $req.Body = [Text.Encoding]::UTF8.GetBytes([string]$cfg.body); $req.ContentType = 'application/json' }
if ($cfg.proxy) { $req.Proxy = [string]$cfg.proxy; $req.ProxyUseDefaultCredentials = $true }
$out = @{ status = 0; body = ''; error = '' }
try {
  $r = Invoke-WebRequest @req
  $out.status = [int]$r.StatusCode
  if ($r.Content -is [byte[]]) { $out.body = [Text.Encoding]::UTF8.GetString($r.Content) } else { $out.body = [string]$r.Content }
} catch {
  $ex = $_.Exception
  if ($ex.Response) {
    $out.status = [int]$ex.Response.StatusCode
    if ($_.ErrorDetails -and $_.ErrorDetails.Message) { $out.body = [string]$_.ErrorDetails.Message }
    else { try { $sr = New-Object IO.StreamReader($ex.Response.GetResponseStream()); $out.body = $sr.ReadToEnd() } catch {} }
  } else { $out.error = [string]$ex.Message }
}
$out | ConvertTo-Json -Compress
"""


def _post_powershell(cfg: dict, url: str, headers: dict, payload: bytes, method: str = "POST"):
    """(status, body_text, None) via PowerShell, or raises _Unreachable."""
    import subprocess
    exe = _powershell_exe()
    if not exe:
        raise _Unreachable("PowerShell is not available on this machine")
    data = json.dumps({"url": url, "method": method, "timeout": int(cfg["timeout"]),
                       "headers": {k: v for k, v in headers.items() if k.lower() != "content-type"},
                       "body": payload.decode("utf-8") if payload else "",
                       "proxy": cfg.get("proxy") or (_system_proxy_for(url) or "")}, ensure_ascii=True)
    enc = base64.b64encode(_PS_SCRIPT.encode("utf-16-le")).decode("ascii")
    try:
        p = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-EncodedCommand", enc], input=data,
                           capture_output=True, text=True, encoding="utf-8", timeout=int(cfg["timeout"]) + 40)
    except (subprocess.TimeoutExpired, OSError) as e:
        raise _Unreachable(f"PowerShell: {type(e).__name__}") from None
    line = next((l.lstrip("\ufeff") for l in reversed(p.stdout.splitlines()) if l.lstrip("\ufeff").strip().startswith("{")), "")
    try:
        out = json.loads(line)
    except ValueError:
        raise _Unreachable("PowerShell returned no result: " + re.sub(r"\s+", " ", (p.stderr or p.stdout or ""))[:160]) from None
    if out.get("error"):
        raise _Unreachable("PowerShell: " + str(out["error"])[:200])
    return int(out.get("status") or 0), out.get("body") or "", None


def _can_use_powershell(cfg: dict) -> bool:
    return cfg["transport"] == "auto" and _is_windows() and bool(_powershell_exe())


def _post(cfg: dict, url: str, headers: dict, payload: bytes, opener):
    """One HTTP POST. Returns (status, text, retry_after); raises _Unreachable if there was no HTTP answer."""
    global _force_ps
    if cfg["transport"] == "powershell" or (_force_ps and _can_use_powershell(cfg)):
        return _post_powershell(cfg, url, headers, payload)
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with opener.open(req, timeout=cfg["timeout"]) as r:
            return r.status, r.read().decode("utf-8", "replace"), None
    except urllib.error.HTTPError as e:
        if e.code == 407 and _can_use_powershell(cfg):
            _force_ps = True        # the proxy wants the Windows login; Python cannot give it, PowerShell can
            return _post_powershell(cfg, url, headers, payload)
        try:
            text = e.read().decode("utf-8", "replace")
        except Exception:
            text = ""
        return e.code, text, e.headers.get("Retry-After")
    except (urllib.error.URLError, TimeoutError, ssl.SSLError, OSError) as e:
        if "407" in str(e) and _can_use_powershell(cfg):
            _force_ps = True
            return _post_powershell(cfg, url, headers, payload)
        raise _Unreachable(_reason(e)) from None


class _Completions:
    def __init__(self, cfg: dict):
        self._cfg = cfg

    def create(self, model=None, messages=None, temperature=0.0, max_tokens=None, **_ignored):
        cfg = self._cfg
        url = (cfg["base_url"] + cfg["chat_path"].format(deployment=cfg["deployment"])
               + "?api-version=" + cfg["api_version"])
        body = {"messages": messages or []}
        if is_reasoning_deployment(cfg["deployment"]):
            # GPT-5 / o-series: no temperature, max_completion_tokens instead of max_tokens, and part of that
            # budget is spent on hidden reasoning - so it needs headroom or the visible answer comes back empty.
            if max_tokens:
                body["max_completion_tokens"] = max(int(max_tokens) * 4, 1500)
        else:
            body["temperature"] = temperature
            if max_tokens:
                body["max_tokens"] = max_tokens
        if cfg["send_model"]:
            body["model"] = model or cfg["deployment"]
        headers = {"Content-Type": "application/json"}
        if cfg["auth_mode"] in ("all", "both", "azure", "key_header"):
            headers[cfg["key_header"]] = cfg["key"]
        if cfg["auth_mode"] in ("all", "both", "bearer"):
            headers["Authorization"] = "Bearer " + cfg["key"]
        if cfg["auth_mode"] in ("all", "azure"):
            headers["api-key"] = cfg["key"]
        headers.update(cfg["extra_headers"])
        payload = json.dumps(body).encode("utf-8")
        ctx = ssl.create_default_context(cafile=cfg["ca_bundle"]) if cfg["ca_bundle"] else None
        opener = _opener(cfg, ctx)
        data, last = None, "no attempt made"
        for attempt in (1, 2):
            try:
                status, text, retry_after = _post(cfg, url, headers, payload, opener)
            except _Unreachable as e:
                last = f"could not reach the LLM gateway ({e})"
                if attempt == 1:
                    time.sleep(1.0)
                    continue
                raise LLMError(last + " - run `python -m app.llm_client diagnose` to see which step fails "
                                      "(proxy, certificate, DNS or VPN)") from None
            if status == 200:
                try:
                    data = json.loads(text)
                except ValueError:
                    raise LLMError("the LLM gateway returned something that is not JSON") from None
                break
            last = f"HTTP {status} from the LLM gateway"
            if status in (429, 500, 502, 503, 504) and attempt == 1:
                try:
                    wait = min(float(retry_after or ""), 8.0)
                except (TypeError, ValueError):
                    wait = 1.5
                time.sleep(wait)
                continue
            hint = {401: " (key rejected - check LLM_API_KEY and LLM_KEY_HEADER)",
                    403: " (not allowed - check the key's subscription / deployment; the developer plan only "
                         "allows the deployments starred in the Model Farm reference)",
                    404: " (path not found - check LLM_DEPLOYMENT and LLM_CHAT_PATH)",
                    407: " (the proxy rejected your login - on Windows the tool retries through PowerShell "
                         "automatically; LLM_TRANSPORT=powershell forces it)",
                    429: " (rate limit, or this month's token quota is used up - see the Token Tracking dashboard)"
                    }.get(status, "")
            # The gateway's own explanation (e.g. its "out of tokens" hint). The key is redacted from it.
            said = re.sub(r"\s+", " ", (text or "").replace(cfg["key"], "***")).strip()[:300]
            raise LLMError(last + hint + (f" - gateway said: {said}" if said else "")) from None
        if data is None:
            raise LLMError(last)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise LLMError("the LLM gateway response had no choices[0].message.content") from None
        u = data.get("usage") or {}
        usage_totals["calls"] += 1
        usage_totals["prompt_tokens"] += int(u.get("prompt_tokens") or 0)
        usage_totals["completion_tokens"] += int(u.get("completion_tokens") or 0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content or ""))],
                               usage=u)


class _Client:
    def __init__(self, cfg: dict):
        self.chat = SimpleNamespace(completions=_Completions(cfg))


def get_client(feature: str = "usb_advice"):
    """None unless configured AND this feature is enabled in LLM_FEATURES (callers must handle None)."""
    c = config()
    if not (c["key"] and c["deployment"]) or feature not in c["features"]:
        return None
    return _Client(c)


def _check() -> int:
    s = status()
    print("LLM configuration (key never printed):")
    for k, v in s.items():
        print(f"  {k}: {v}")
    if not s["configured"]:
        print("\nNot configured: set LLM_API_KEY and LLM_DEPLOYMENT in backend/.env (see .env.example).")
        return 1
    try:
        client = _Client(config())
        r = client.chat.completions.create(messages=[{"role": "user", "content": "Reply with the single word OK."}],
                                           max_tokens=5)
        print("\nCall succeeded. Reply:", repr(r.choices[0].message.content.strip()), "| usage:", r.usage)
        return 0
    except LLMError as e:
        print("\nCall FAILED:", e)
        return 2


def _diagnose() -> int:
    """Walk the network path to the gateway one step at a time. Sends no key and no data - only name
    resolution, a TCP connect, a TLS handshake and one unauthenticated GET - so it can be pasted safely."""
    import socket
    from urllib.parse import urlparse
    c = config()
    u = urlparse(c["base_url"])
    host, port = u.hostname or "", u.port or (443 if u.scheme == "https" else 80)
    redact = lambda v: re.sub(r"//[^@/\s]+@", "//***@", str(v))
    print(f"Target: {c['base_url']}   (host {host}, port {port})")
    print("LLM_PROXY (from .env):", ("set" if c["proxy"] else "not set / resolved to direct")
          + ("  [auto = ask Windows]" if os.environ.get("LLM_PROXY", "").lower() == "auto" else ""))
    if sys.platform == "win32":
        sp = _system_proxy_for(c["base_url"] + "/")
        print("Windows says your browser would reach the gateway via:", redact(sp) if sp else "a direct connection (no proxy)")
        if sp and not c["proxy"]:
            print(f"   -> put this line in backend/.env:   LLM_PROXY=auto      (or  LLM_PROXY={redact(sp)} )")
    print("Proxy settings Python will use otherwise:", {k: redact(v) for k, v in urllib.request.getproxies().items()} or "none")
    if sys.platform == "win32":
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
            def val(n):
                try:
                    return winreg.QueryValueEx(k, n)[0]
                except OSError:
                    return None
            print("Windows (browser) proxy: ProxyEnable =", val("ProxyEnable"), "| ProxyServer =", redact(val("ProxyServer")),
                  "| AutoConfigURL (PAC script) =", val("AutoConfigURL"))
        except Exception:
            pass
    print("NO_PROXY:", os.environ.get("NO_PROXY") or "-", "\n")
    ctx = ssl.create_default_context(cafile=c["ca_bundle"]) if c["ca_bundle"] else ssl.create_default_context()
    failed = set()
    addrs = []
    try:
        addrs = sorted({a[4][0] for a in socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)})
        print("1. DNS lookup:               OK ->", ", ".join(addrs))
    except OSError as e:
        print("1. DNS lookup:               FAILED -", e); failed.add("dns")
    if addrs:
        try:
            socket.create_connection((addrs[0], port), timeout=6).close()
            print("2. Direct TCP connection:    OK")
        except OSError as e:
            print("2. Direct TCP connection:    FAILED -", e, "(expected if you must go through a proxy)"); failed.add("tcp")
    if u.scheme == "https" and "tcp" not in failed and addrs:
        try:
            with socket.create_connection((host, port), timeout=6) as raw, ctx.wrap_socket(raw, server_hostname=host) as tls:
                issuer = dict(x[0] for x in tls.getpeercert().get("issuer", ()))
                print("3. TLS certificate:          OK - issued by", issuer.get("organizationName") or issuer.get("commonName") or "?")
        except ssl.SSLCertVerificationError as e:
            print("3. TLS certificate:          NOT TRUSTED by Python -", e.verify_message); failed.add("tls")
        except OSError as e:
            print("3. TLS certificate:          FAILED -", e); failed.add("tls")
    ok, auth_needed = False, False
    try:
        with _opener(c, ctx).open(urllib.request.Request(c["base_url"] + "/", method="GET"), timeout=10) as r:
            print(f"4. HTTP request (via proxy): reachable, HTTP {r.status}")
            ok = True
    except urllib.error.HTTPError as e:
        if e.code == 407:
            print("4. HTTP request (via proxy): the proxy answered 407 - it wants your Windows login")
            auth_needed = True
        else:
            print(f"4. HTTP request (via proxy): the gateway answered HTTP {e.code} - the network path WORKS (an error code is fine here)")
            ok = True
    except (urllib.error.URLError, OSError) as e:
        print("4. HTTP request (via proxy): FAILED -", _reason(e))
        auth_needed = "407" in str(e)
    if not ok and auth_needed:
        if _powershell_exe():
            try:
                st, _b, _r = _post_powershell(c, c["base_url"] + "/", {}, b"", method="GET")
                print(f"5. Same request via PowerShell (uses your Windows login): the gateway answered HTTP {st} - WORKS")
                ok = True
                print("   -> The proxy needs your Windows login. Python alone cannot give it, so the tool automatically sends\n"
                      "      LLM calls through PowerShell (LLM_TRANSPORT=auto). Nothing to fix.")
            except _Unreachable as e:
                print("5. Same request via PowerShell: FAILED -", e)
        else:
            print("5. PowerShell was not found on this machine, so the Windows login cannot be used.")
    if not ok:
        failed.add("http")
    print()
    if not failed:
        print("Network path is fine. If `check` still fails, run it again and send me the exact message.")
    elif "http" not in failed:
        print("-> Some direct steps failed, but the real request works through the proxy path - that is normal; nothing to fix.")
    else:
        if "dns" in failed:
            print("-> This machine cannot resolve the host name. Two possibilities:\n"
                  "   a) you are not on the Bosch network / VPN (connect, then retry), or\n"
                  "   b) your browser works because a PROXY resolves names for it. Then find that proxy and set\n"
                  "      LLM_PROXY=auto in backend/.env (Windows picks the browser's proxy for you), or LLM_PROXY=http://<host>:<port>.")
        if "tls" in failed:
            print("-> A corporate proxy is re-signing HTTPS. Export your company root CA as a .pem file and set LLM_CA_BUNDLE=<path> in backend/.env.")
        if "dns" not in failed and "tls" not in failed:
            print("-> Python cannot get out. Set LLM_PROXY=http://<proxy-host>:<port> in backend/.env (the proxy your browser uses).")
    return 0 if "http" not in failed else 3


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    sys.exit(_check() if cmd == "check" else _diagnose() if cmd == "diagnose" else 0)
