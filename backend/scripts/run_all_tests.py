"""Runs every backend test script and prints PASSED / SKIPPED (customer sample missing) / FAILED. Exit 1 if anything FAILED."""
import glob, os, subprocess, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
files = sorted(glob.glob(os.path.join(ROOT, "scripts", "*_test.py")) + [os.path.join(ROOT, "tool", "test_usb_dts_tool.py")])
bad = 0
# tests talk to a local mock gateway: they must never see the real key / proxy from backend/.env or from the shell
ENV = {k: v for k, v in os.environ.items() if not k.upper().startswith(("LLM_", "FARM_", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY"))}
ENV["PINMUX_NO_DOTENV"] = "1"
for f in files:
    p = subprocess.run([sys.executable, f], capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ROOT, env=ENV)
    out = (p.stdout + p.stderr)
    status = "FAILED" if p.returncode else ("SKIPPED" if "SKIPPED " in out.split("\n")[0:3].__str__() or out.lstrip().startswith("SKIPPED") else "PASSED")
    bad += status == "FAILED"
    print(f"{status:8} {os.path.basename(f)}" + (("   " + out.strip().splitlines()[-1][:150]) if status != "PASSED" else ""))
print(f"\n{len(files)} scripts, {bad} failed")
sys.exit(1 if bad else 0)
