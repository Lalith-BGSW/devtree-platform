"""Kernel-version -> pin-state FORMAT ("dynamic template").

The device-tree syntax is stable across kernels; what moves is the CONVENTION: e.g. the Qualcomm TLMM pin states were
nested  `label: label { mux { pins..; function..; }; config { .. }; };`  up to v6.1 and are flat
`label: label-name-state { pins..; function..; };`  from v6.2 (measured on sa8155p-adp.dts, see kernel_profiles.json).

resolve(version, soc) never invents a format:
  1. fetch a reference file of that kernel tag (<raw_base>/<tag>/<path>) and LEARN its style with dts_style.learn_style
     (only format is learned - never pins, labels, includes or the copyright header);
  2. if it cannot be fetched, use the table of MEASURED rules in kernel_profiles.json;
  3. if the version is outside every measured range, change nothing and say so.
A template file supplied by the user always wins over all of this (the caller decides)."""
import json
import os
import re
import ssl
import urllib.error
import urllib.request

from app import dts_style

_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kernel_profiles.json")

VENDOR_CAVEAT = ("a kernel VERSION describes the mainline conventions. A vendor BSP kernel (msm-5.4 and similar) can use a "
                 "different format at the same version number - if your project's accepted files look different, give them as --template.")


def parse_version(text):
    """'6.6', 'v6.6.30', 'linux-5.15', '6.7-rc2' -> {'major','minor','tag','shown','note'} or None."""
    m = re.fullmatch(r"\s*(?:linux[-_ ]?)?v?(\d{1,2})\.(\d{1,3})(?:\.(\d{1,3}))?(?:\.y)?(-rc\d+)?\s*", str(text or ""), re.I)
    if not m:
        return None
    major, minor, patch, rc = int(m.group(1)), int(m.group(2)), m.group(3), (m.group(4) or "")
    note = (f"point release {major}.{minor}.{patch} has no tag in the mainline tree: the v{major}.{minor} tag is used"
            if patch else "")
    return {"major": major, "minor": minor, "tag": f"v{major}.{minor}{rc}", "shown": f"{major}.{minor}{'.' + patch if patch else ''}{rc}",
            "note": note}


def _load():
    with open(_DATA, encoding="utf-8") as fh:
        return json.load(fh)


def _ver(s):
    a, b = str(s).split(".")[:2]
    return int(a), int(b)


def _http_get(url):
    """(status, text). Python first (honours HTTPS_PROXY and LLM_CA_BUNDLE); on a proxy login demand (407) or any network
    failure, PowerShell with the Windows login (same route as the AI gateway). Raises OSError when nothing answers."""
    from app import llm_client
    cfg = llm_client.config()
    cfg["timeout"] = min(float(cfg.get("timeout") or 30), 30.0)
    if cfg["transport"] != "powershell":
        try:
            ctx = ssl.create_default_context(cafile=cfg["ca_bundle"]) if cfg["ca_bundle"] else None
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "devtree-platform"}),
                                        timeout=cfg["timeout"], context=ctx) as r:
                return (getattr(r, "status", None) or 200), r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code != 407:
                return e.code, ""
        except (urllib.error.URLError, TimeoutError, ssl.SSLError, OSError):
            pass
    if llm_client._powershell_exe():
        try:
            st, body, _ = llm_client._post_powershell(cfg, url, {}, b"", method="GET")
            return st, body
        except llm_client._Unreachable as e:
            raise OSError(str(e)) from None
    raise OSError("no network route to " + url.split("/")[2])


def resolve(version, soc=None, reference=None, fetch=None, base=None):
    """-> {'style': Style|None, 'kind': 'fetched'|'table'|'none'|'unrecognized', 'version', 'notes': [..], 'source'}.
    reference: optional path inside the kernel tree that overrides the SoC's list. fetch: injectable (tests).
    base: where the kernel tree is served from (default: the mainline raw URL in kernel_profiles.json, or env KERNEL_RAW_BASE). Point it at
    a VENDOR tree (your company's git mirror, raw-file URL, <base>/<tag>/<path>) to learn the format of that tree instead of mainline."""
    v = parse_version(version)
    if not v:
        raise ValueError(f"--kernel {version!r}: expected a version like 6.6, 5.15 or v6.1.20")
    fetch = fetch or _http_get          # looked up at call time so a test can replace _http_get
    data = _load()
    notes = [v["note"]] if v["note"] else []
    custom = bool(base or os.environ.get("KERNEL_RAW_BASE"))
    base = (base or os.environ.get("KERNEL_RAW_BASE") or data["raw_base"]).rstrip("/")
    paths = [reference] if reference else list(data.get("soc_reference", {}).get((soc or "").upper(), []))
    tried, unrecognized = [], []
    for p in paths:
        url = f"{base}/{v['tag']}/{p.lstrip('/')}"
        try:
            st, text = fetch(url)
        except OSError as e:
            tried.append(f"{p}: not reachable ({e})")
            break                                    # the network is the problem, not this path: do not hammer it
        if st != 200 or not text.strip():
            tried.append(f"{p}: HTTP {st} at {v['tag']}")
            continue
        style = dts_style.learn_style(text, os.path.basename(p), role="pinmux")
        if style.unrecognized_pin_states:       # an unfamiliar layout: never substitute an older measured format for it
            unrecognized.append(p)
            continue
        if not style.recognized_pin_states:
            tried.append(f"{p}: has no pin-state nodes at {v['tag']}")
            continue
        style.source = (f"Linux {v['tag']} {p} (fetched from {base.split('//')[-1].split('/')[0]}"
                        f"{' - a tree you pointed at, not mainline' if custom else ' - mainline'})")
        style.header, style.skipped_includes, style.preamble = "", [], []   # a kernel file's copyright header / includes are never copied
        return {"style": style, "kind": "fetched", "version": v["shown"], "notes": notes + ([] if custom else [VENDOR_CAVEAT]), "source": style.source}
    if unrecognized and not custom:
        # The mainline reference file is exactly the file the table was MEASURED on: its pin states use sub-grouped / mux-only
        # layouts the learner does not parse, so the measured rule is the better source than refusing. A vendor tree
        # (custom base) never gets this: it is a different file the table says nothing about.
        t0 = data["table"]
        for r in t0["rules"]:
            if _ver(r["min"]) <= (v["major"], v["minor"]) <= _ver(r["max"]):
                style = dts_style.Style(role="pinmux", learned=True, layout=r["layout"], name_style=r["name_style"],
                                        source=f"built-in table for Linux {v['shown']} (measured on {t0['measured_on']}; the fetched "
                                               f"file's own layout is not fully parsable: {r['evidence']})")
                return {"style": style, "kind": "table", "version": v["shown"],
                        "notes": notes + ["the fetched mainline file was read but its pin-state layout is not fully parsable; the measured rule "
                                          "for that same file was used instead"] + [VENDOR_CAVEAT], "source": style.source}
    if unrecognized:
        notes.append("the fetched DTS has pin-state nodes in an unrecognized format "
                     f"({', '.join(unrecognized)}); no older measured format was substituted. "
                     "Provide an accepted DTS template or add a tested parser for this layout.")
        return {"style": None, "kind": "unrecognized", "version": v["shown"], "notes": notes + ([] if custom else [VENDOR_CAVEAT]),
                "source": f"Linux {v['tag']} fetched reference ({', '.join(unrecognized)})"}
    if tried:
        notes.append("kernel reference not used - " + "; ".join(tried))
    elif not paths:
        notes.append(f"no reference file is listed for SoC {soc or '(unknown)'} in kernel_profiles.json (--kernel-reference PATH names one)")
    t = data["table"]
    for r in t["rules"]:
        if _ver(r["min"]) <= (v["major"], v["minor"]) <= _ver(r["max"]):
            style = dts_style.Style(role="pinmux", learned=True, layout=r["layout"], name_style=r["name_style"],
                                    source=f"built-in table for Linux {v['shown']} (measured on {t['measured_on']}, not fetched: {r['evidence']})")
            if soc and (soc.upper() not in data.get("soc_reference", {})):
                notes.append(f"the table was measured on {t['measured_on']}; SoC {soc} was not measured - the kernel's own file for it may differ")
            return {"style": style, "kind": "table", "version": v["shown"], "notes": notes + [VENDOR_CAVEAT], "source": style.source}
    lo, hi = t["rules"][0]["min"], t["rules"][-1]["max"]
    notes.append(f"Linux {v['shown']} is outside the measured range {lo} - {hi} and could not be fetched: the default format is kept")
    return {"style": None, "kind": "none", "version": v["shown"], "notes": notes + [VENDOR_CAVEAT], "source": ""}
