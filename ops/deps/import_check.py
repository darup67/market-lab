"""Import every installed distribution's top-level modules in a fresh subprocess each.
Catches broken native libs (e.g. libomp vanishing with /opt/homebrew, Sep 2026).
Usage: <venv>/bin/python import_check.py [--json]   exit 1 if any import fails."""
import importlib.metadata as md, json, subprocess, sys

SKIP = {"pip", "setuptools", "wheel", "_distutils_hack", "pkg_resources", "tests", "test", "docs", "examples",
        "benchmarks", "scripts", "__pycache__"}
OPTIONAL = {"ray", "fastai", "vowpalwabbit", "imodels", "tabpfn", "skl2onnx", "onnxruntime"}   # known-absent extras

def tops(dist):
    names = set()
    try:
        txt = dist.read_text("top_level.txt")
        if txt: names.update(n.strip() for n in txt.split() if n.strip())
    except Exception:
        pass
    if not names:
        for f in dist.files or []:
            p = str(f).split("/")[0]
            if p.endswith(".py"): names.add(p[:-3])
            elif "." not in p and not p.endswith((".dist-info", ".egg-info", ".data")): names.add(p)
    return {n for n in names if n and not n.startswith("_") and n not in SKIP and n not in OPTIONAL}

fails = []
mods = sorted({(d.metadata["Name"], m) for d in md.distributions() for m in tops(d)})
for dist, mod in mods:
    r = subprocess.run([sys.executable, "-c", f"import {mod}"], capture_output=True, text=True, timeout=180)
    if r.returncode:
        err = (r.stderr.strip().splitlines() or ["?"])[-1][:200]
        fails.append({"dist": dist, "module": mod, "error": err})
if "--json" in sys.argv:
    print(json.dumps({"checked": len(mods), "fails": fails}))
else:
    print(f"checked {len(mods)} modules, {len(fails)} failed")
    for f in fails: print(f"  FAIL {f['dist']} ({f['module']}): {f['error']}")
sys.exit(1 if fails else 0)
