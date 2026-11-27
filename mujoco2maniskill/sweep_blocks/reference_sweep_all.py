"""The sweeping reference on every one of RoboDojo's 55 saved layouts -> out/robodojo_sweep/reference_sweep.json"""
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
HERE = Path(__file__).resolve().parents[1]
ks = sorted(int(f.stem.split("_")[-1]) for f in (HERE / "assets" / "robodojo_sweep" / "layouts").glob("sweep_blocks_[0-9]*.json"))


def one(k):
    r = subprocess.run([sys.executable, "-I", str(HERE / "sweep_blocks" / "reference_sweep.py"), str(k), "--quiet"], capture_output=True, text=True)
    l = next((x for x in r.stdout.splitlines() if x.startswith("REFERENCE ")), None)
    return json.loads(l[10:]) if l else dict(layout=k, solved=False, error=r.stderr[-200:])


with ThreadPoolExecutor(int(sys.argv[1]) if len(sys.argv) > 1 else 10) as ex:
    res = list(ex.map(one, ks))
(HERE / "out" / "robodojo_sweep" / "reference_sweep.json").write_text(json.dumps(res, indent=1))
print("swept: solved", sum(bool(x.get("solved")) for x in res), "of", len(res), "| layouts:", [x["layout"] for x in res if x.get("solved")],
      "| cubes in:", sum(x.get("cubes_in") or 0 for x in res), "of", sum(x.get("cubes") or 0 for x in res))
