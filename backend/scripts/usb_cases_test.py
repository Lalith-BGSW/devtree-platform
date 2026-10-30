"""
CASE MATRIX: the same pipeline over EVERY project in every workbook available, checked against invariants that name no pin,
no project and no SoC. Nothing here is specific to a case; a new project is just another row.

Workbooks: the ones in samples/, plus any listed in the environment variable USB_CASE_WORKBOOKS (os.pathsep-separated; IVECO_XL and
TCC_XL are also picked up). The AI is a mock that MISBEHAVES on purpose (invented regulator inside &tlmm, an unfilled placeholder,
an invented hub with a compatible string and an address, a level for a pin nobody mentioned), because that is what a real
model did in a real run.
"""
import json, os, re, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, ROOT); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(ROOT, "tool"))
import sys as _s, os as _o; _s.path.insert(0, _o.path.join(_o.path.dirname(_o.path.abspath(__file__)), '..', 'scripts')); import _samples; _samples.need(__file__)
from _mock_llm import MockLLM
import usb_dts_tool as T
from app import dts_style as D

books = [os.path.join(ROOT, "samples", "Nissan_CCS20_SA8155PSIP_PinMux.xlsx"), os.path.join(ROOT, "samples", "Honda_database_v2.xlsx")]
for var in ("IVECO_XL", "TCC_XL"):
    if os.environ.get(var):
        books.append(os.environ[var])
books += [b for b in os.environ.get("USB_CASE_WORKBOOKS", "").split(os.pathsep) if b]
books = [b for b in dict.fromkeys(books) if os.path.exists(b)]

mock = MockLLM(); base = mock.start()
tmp = tempfile.mkdtemp()
JUNK = '''
&tlmm {
	vbus_regulator: regulator-fixed {
		compatible = "regulator-fixed";
		/* NEEDS REVIEW: FACT <n> not applied - missing regulator name */
	};
};
&unknown_bus {
	hub@2d {
		compatible = "vendor,hubchip";
		reg = <0x2d>;
	};
};
'''
# Junk that exactly ONE guard can catch, so disabling that guard is visible:
ONLY_PLACEHOLDER = "\n/* NEEDS REVIEW: FACT <n> not applied - missing something */\n"          # a comment: no other guard looks at comments
ONLY_NEW_NODE = "\n&tlmm {\n\tinvented_node: invented_node {\n\t\tstatus = \"okay\";\n\t};\n};\n"   # no hex, compatible, label, level or flag in it
def verified_of(body):
    m = re.search(r"VERIFIED_DTS:\n```dts\n(.*?)\n```", body["messages"][1]["content"], re.S)
    return (m.group(1) + "\n") if m else ""
def configure(on=True):
    for k in [k for k in os.environ if k.startswith("LLM_")]:
        del os.environ[k]
    mock.requests.clear(); mock.script.clear()
    if on:
        os.environ.update(LLM_BASE_URL=base, LLM_API_KEY="k", LLM_DEPLOYMENT="d", LLM_TIMEOUT="5")
        mock.reply = lambda b: "```dts\n" + verified_of(b) + JUNK + ONLY_PLACEHOLDER + ONLY_NEW_NODE + "```"
class NS:
    def __init__(s, **k):
        d = dict(project=None, dts=None, out=None, report=None, pattern="auto", overlay=None, insert_after=None, vbus_pin=[], role=[],
                 inventory=None, combined=None, no_cache=False, no_update=False, keyword="usb", match="both", list_projects=False,
                 ai_advice=False, ai_max_pins=12, excel=None, ai_propose=False, facts="", ai_draft=None, ai_proposal_out=None, docs=[])
        d.update(k); s.__dict__.update(d)
OLD_VOCAB = os.path.join(tmp, "old_vocab.json")
json.dump({"oc": ["(^|_)OC(_|$)"], "vbus": ["PWR_EN", "VBUS"]}, open(OLD_VOCAB, "w"))


def mis_suggest(body):
    """Answers the role / audit prompts as a careless model would: every pin gets a role, every GPIO is 'USB', plus an invented pin."""
    user = body["messages"][1]["content"]
    items = json.loads(user.split(":\n", 1)[1])
    if user.startswith("SIGNALS"):
        sug = [{"pin": it["pin"], "role": ("oc" if i % 2 == 0 else "vbus"), "quote": it["signal"].split(":")[-1], "reason": "x"} for i, it in enumerate(items)]
        sug.append({"pin": "GPIO_99999", "role": "oc", "quote": "x", "reason": "x"})
        return json.dumps({"suggestions": sug})
    ver = [{"pin": it["pin"], "verdict": "usb_gpio", "quote": it["text"][:12], "reason": "x"} for it in items]
    ver.append({"pin": "GPIO_99999", "verdict": "usb_gpio", "quote": "x", "reason": "x"})
    return json.dumps({"verdicts": ver})


def active(text):
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("//"))
def states(text):
    return set(re.findall(r"^\t(\w+): \1 \{", text, re.M))
def pin_numbers(text):
    t = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return set(re.findall(r"gpio(\d+)", t)) | set(re.findall(r"&tlmm\s+(\d+)", t))

rows = 0
print(f"{'workbook':34} {'project':20} {'matched':>7} {'states':>6} {'junk demoted':>12}")
for xl in books:
    for proj in T.list_projects(xl):
        parsed, recs, soc = T.load_records(xl, proj)
        project_pins = {T.gpio_num(r) for r in recs} - {None}
        tag = re.sub(r"\W", "_", proj)
        configure(on=False)
        try:
            py = T.run(NS(excel=xl, project=proj, out=f"{tmp}/{tag}.dtsi", report=f"{tmp}/{tag}.md"))
        except SystemExit as e:
            assert "No pins matched" in str(e), f"{proj}: {e}"
            print(f"{os.path.basename(xl)[:34]:34} {proj:20} {'-':>7}   (no USB pins in this project: stops with a clear message)")
            continue
        rep = py.report_text
        counts = [int(x) for x in re.search(r"USB-matched pins: \*\*(\d+)\*\*.*?oc/vbus: (\d+).*?other GPIOs: (\d+).*?PHY pads: (\d+).*?unsupported controller: (\d+).*?"
                                              r"controller unconfirmed: (\d+).*?needs review: (\d+)", rep, re.S).groups()]
        assert counts[0] == sum(counts[1:]), f"{proj}: pins unaccounted for: {counts}"                          # 1. every matched pin lands somewhere
        assert pin_numbers(py.out_text) <= project_pins, f"{proj}: pins in the output that are not this project's"   # 2. nothing foreign leaks in
        if py.files:                                                      # 2b. the written FILES: same values, verified again, nothing foreign
            assert not py.bundle_verification["wrong"], f"{proj}: the written files fail the independent verifier"
            assert pin_numbers("".join(f["text"] for f in py.files)) <= project_pins, f"{proj}: foreign pins in the written files"
            _sig = lambda tx: {k: v for b in D.top_level_blocks(D._norm(tx)) if b["header"].startswith("&tlmm")
                               for k, v in D.signature(D.parse_pin_nodes(D._inner(b["text"]))[0]).items()}
            assert _sig(py.files[0]["text"]) == _sig(py.out_text), f"{proj}: the pinmux file means something different from the verified output"
            assert all(os.path.exists(os.path.join(py.files_dir, f["name"])) for f in py.files)
        configure(on=True)
        hy = T.run(NS(excel=xl, project=proj, out=f"{tmp}/{tag}_h.dtsi", report=f"{tmp}/{tag}_h.md", ai_propose=True, combined=f"{tmp}/{tag}_c.dts",
                      facts="context: board note"))      # a context fact engages the (misbehaving) AI; without one it is skipped
        ap = hy.ai_proposal
        assert ap and ap["status"] != "unavailable", proj
        fin = ap["final_text"]; act = active(fin)
        assert hy.out_text == py.out_text, f"{proj}: the Python-only output changed"                             # 3. AI never touches Python's output
        assert states(py.out_text) <= states(fin) | states(ap.get("python_only_text", "")), f"{proj}: a Python state was lost"
        assert "vendor,hubchip" not in act and "0x2d" not in act and "&unknown_bus" not in act, f"{proj}: invented hub is ACTIVE"   # 4. invention demoted
        assert not re.search(r"^\s*vbus_regulator\s*:", act, re.M) and "<n>" not in act, f"{proj}: junk node/placeholder is ACTIVE"
        assert "FACT <n>" not in act, f"{proj}: an unfilled placeholder is ACTIVE (only the placeholder filter catches this one)"
        assert "invented_node" not in act, f"{proj}: an invented node with no specifics is ACTIVE (only the new-node guard catches this one)"
        assert ap["placeholders_removed"] >= 1
        tlmm_body = re.findall(r"^&tlmm \{(.*?)^\};", act, re.S | re.M)
        assert not any("regulator-fixed" in b for b in tlmm_body), f"{proj}: regulator inside &tlmm"
        assert pin_numbers(act) <= project_pins, f"{proj}: foreign pin numbers in the final output: {pin_numbers(act) - project_pins}"
        assert ap["template_assumed"] == bool(re.search(r"compatible = \"regulator-fixed\"|^\s*(oc-gpio|vbus-out-gpio) = ", act, re.M)), f"{proj}: banner without content (or content without banner)"
        assert T.run_dtc(fin)[0] in (True, None), f"{proj}: the final file does not compile"
        if ap["open_items"] and "No over-current or VBUS-enable pin was recognized" in ap["open_items"][0]:
            assert "Signals the tool could not classify:" in ap["open_items"][0]                                 # 5. says WHICH signals, not a canned example
        # AI SUGGESTIONS (roles + audit) from a careless model: contained by Python, never changing the output
        # run under a vocabulary that lacks FAULT / EXTERNAL_EN so that projects with such names HAVE signals for the AI to classify
        os.environ["USB_ROLE_KEYWORDS"] = OLD_VOCAB
        try:
            configure(on=True); mock.reply = mis_suggest
            base_old = T.run(NS(excel=xl, project=proj, out=f"{tmp}/{tag}_b.dtsi", report=f"{tmp}/{tag}_b.md"))
            sg_run = T.run(NS(excel=xl, project=proj, out=f"{tmp}/{tag}_s.dtsi", report=f"{tmp}/{tag}_s.md", ai_suggest=True))
        finally:
            del os.environ["USB_ROLE_KEYWORDS"]
        sg = sg_run.ai_suggest
        assert sg_run.out_text == base_old.out_text, f"{proj}: suggestions changed the Python output"
        cand_roles = {c["pin"]: c for c in sg["roles"].get("candidates", [])}
        for r in sg["roles"]["rows"]:
            assert r["pin"] != "GPIO_99999" or r["status"] == "rejected", f"{proj}: an invented pin was accepted"
            if r["status"] == "conflict":
                c = cand_roles[r["pin"]]
                d = c["direction"].upper()
                contra = (r["role"] == "oc" and "OUT" in d and not re.search(r"\bIN\b", d)) or (r["role"] == "vbus" and re.search(r"\bIN\b", d) and "OUT" not in d)
                assert contra and r["role_arg"], f"{proj}: a 'conflict' that does not contradict the workbook"
            if r["status"] == "accepted":
                c = cand_roles[r["pin"]]
                d = c["direction"].upper()
                assert r["role"] in c["free_roles"], f"{proj}: a taken slot was offered"
                assert not (r["role"] == "oc" and "OUT" in d and not re.search(r"\bIN\b", d)), f"{proj}: an OUTPUT offered as over-current"
                assert not (r["role"] == "vbus" and re.search(r"\bIN\b", d) and "OUT" not in d), f"{proj}: an INPUT offered as VBUS enable"
                assert r["role_arg"] == f"{c['tail'].upper()}={r['role']}"
        slots = [(r["instance"], r["role"]) for r in sg["roles"]["rows"] if r["status"] in ("accepted", "conflict")]
        assert len(slots) == len(set(slots)), f"{proj}: two pins offered for one controller slot"
        cand_aud = {c["pin"]: c for c in sg["audit"].get("candidates", [])}
        for r in sg["audit"]["rows"]:
            assert r["pin"] != "GPIO_99999" or r["status"] == "rejected", f"{proj}: an invented pin was accepted in the audit"
            if r["status"] == "include":
                assert re.sub(r"\s+", "", r["quote"]).lower() in re.sub(r"\s+", "", cand_aud[r["pin"]]["text"]).lower(), f"{proj}: unquoted audit verdict accepted"
        configure(on=False)
        no_ai = T.run(NS(excel=xl, project=proj, out=f"{tmp}/{tag}_n.dtsi", report=f"{tmp}/{tag}_n.md", ai_propose=True, facts="x"))
        assert no_ai.out_text == py.out_text                                                                      # 6. no AI -> still works
        rows += 1
        print(f"{os.path.basename(xl)[:34]:34} {proj:20} {counts[0]:>7} {len(states(py.out_text)):>6} {len(ap['unsupported']):>12}")
assert rows >= 4, "expected at least the 4 sample projects"
mock.stop()
print(f"\nUSB CASES TEST PASSED ({rows} projects, no case-specific logic)")
