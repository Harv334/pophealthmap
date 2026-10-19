"""The LA-to-region index in index.html matches scopes/, and reads as people type.

Run: python3 tests/pipeline/test_la_regions.py
"""
import importlib.util, json, pathlib, re, sys

REPO = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("build_la_regions", REPO / "scripts" / "build_la_regions.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

failed = 0
def ok(cond, name, detail=""):
    global failed
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  -- " + str(detail)))
    if not cond:
        failed += 1

index = mod.build_index()
n_scopes = sum(len(json.loads(f.read_text(encoding="utf-8"))["lads"]) for f in (REPO / "scopes").glob("*.json"))
ok(sum(len(v) for v in index.values()) == n_scopes, "every LA in scopes/ is in the index once", n_scopes)
ok(len(index) == 9 and "london" in index and "north-east" in index, "all nine regions", sorted(index))
ok("Leeds" in index["yorkshire-and-the-humber"] and "Manchester" in index["north-west"]
   and "Newcastle upon Tyne" in index["north-east"], "Leeds, Manchester and Newcastle are on their regions' maps")
ok(mod.display_name("Bristol, City of") == "Bristol" and mod.display_name("Herefordshire, County of") == "Herefordshire"
   and mod.display_name("City of London") == "City of London", "ONS's inverted names are written as people type them")

html = (REPO / "index.html").read_text(encoding="utf-8")
ok(mod.splice(html, mod.render(index)) == html, "index.html carries the current index (run scripts/build_la_regions.py)")
m = re.search(r"const PH_LA_REGIONS = (\{.*?\});", html)
shipped = json.loads(m.group(1)) if m else {}
ok(len(m.group(0)) < 8000 if m else False, "the index is small enough to ship in the page", len(m.group(0)) if m else 0)
ok(set(shipped) == set(index), "the shipped index names the same regions", sorted(shipped))

sys.exit(1 if failed else 0)
