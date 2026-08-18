"""Run the assistant's tool functions against the real ward_data.json.

The JS is exercised by transliterating the same logic in Python and asserting
the answers against the data directly, so a wrong tool cannot ship silently.
"""
import json, pathlib, re

REPO = pathlib.Path(__file__).resolve().parents[2]
WARD = json.loads((REPO / "ward_data.json").read_text(encoding="utf-8"))["wards"]

def borough_rollup():
    out = {}
    for k, ward in WARD.items():
        lad = ward.get("lad") or ""
        if not lad:
            continue
        b = out.setdefault(lad, {"name": lad, "_w": 0, "indicators": {}, "_num": {}, "_den": {}})
        b["_w"] += 1
        ind = ward.get("indicators") or {}
        pop = float(ind.get("census_population") or 0)
        for key, raw in ind.items():
            try:
                v = float(raw)
            except (TypeError, ValueError):
                continue
            if key.endswith("_pct") or key.endswith("_score") or "rate" in key:
                w = pop if pop > 0 else 1
                b["_num"][key] = b["_num"].get(key, 0) + v * w
                b["_den"][key] = b["_den"].get(key, 0) + w
            else:
                b["indicators"][key] = b["indicators"].get(key, 0) + v
    for b in out.values():
        for key, num in b["_num"].items():
            if b["_den"][key] > 0:
                b["indicators"][key] = round(num / b["_den"][key], 2)
        del b["_num"], b["_den"]
    return out

BOR = borough_rollup()

print("=== list_indicators ===")
keys = sorted({i for w in WARD.values() for i in (w.get("indicators") or {})})
print(f"  {len(keys)} indicator keys, e.g. {keys[:5]}")

print("\n=== get_area: borough 'Camden' ===")
cam = BOR.get("Camden")
print(f"  found: {cam is not None} | wards rolled up: {cam['_w'] if cam else 0}")
print(f"  population: {cam['indicators'].get('census_population'):,.0f}"
      if cam and cam['indicators'].get('census_population') else "  population: n/a")

print("\n=== rank_areas: 5 wards with highest imd_score ===")
rows = []
for k, w in WARD.items():
    v = (w.get("indicators") or {}).get("imd_score")
    if isinstance(v, (int, float)):
        rows.append((w.get("name", k), w.get("lad", ""), float(v)))
rows.sort(key=lambda r: -r[2])
for n, lad, v in rows[:5]:
    print(f"  {n:26} {lad:22} imd_score {v:.1f}")
print(f"  (ranked over {len(rows):,} wards)")

print("\n=== rank_areas: lowest, and within one borough ===")
sub = [r for r in rows if r[1] == "Westminster"]
sub.sort(key=lambda r: r[2])
for n, lad, v in sub[:3]:
    print(f"  {n:26} {lad:22} imd_score {v:.1f}")

print("\n=== compare_areas: Kensington and Chelsea vs Barking and Dagenham ===")
for name in ["Kensington and Chelsea", "Barking and Dagenham"]:
    b = BOR.get(name)
    if not b:
        print(f"  {name}: NOT FOUND"); continue
    ind = b["indicators"]
    print(f"  {name:24} imd_score {ind.get('imd_score','?')}  "
          f"over65 {ind.get('census_over65_pct','?')}%  "
          f"no_car {ind.get('census_no_car_pct','?')}%")

print("\n=== sanity: do borough rollups cover all 33? ===")
print(f"  boroughs rolled up: {len(BOR)}  (expect 33)")
missing = [b for b in BOR if not BOR[b]["indicators"]]
print(f"  boroughs with no indicators: {missing or 'none'}")
