"""The home page's map of England (scripts/build_home_map.py).

Each published region's wards, simplified and packed, each with the average
IMD decile of its LSOAs; each region's outline, label point and figures (its
LAs, wards and LSOAs, and the shares of its LSOAs in the most deprived 10% and
20% in England). A region with no pack here is left out, not drawn empty.

Offline: two small regions of square wards, written where the script reads
London (data/map) and a region pack (data/regions/<slug>/map).

    python3 tests/pipeline/test_home_map.py
"""
import json
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import build_home_map as H  # noqa: E402
from build_loader_map import ALPHABET  # noqa: E402

FAILS = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"\n          {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(label)


IDX = {c: i for i, c in enumerate(ALPHABET)}


def decode(s):
    out, i = [], 0
    while i < len(s):
        shift = val = 0
        while True:
            c = IDX[s[i]]
            i += 1
            val |= (c & 31) << shift
            shift += 5
            if not c & 32:
                break
        out.append((val >> 1) ^ -(val & 1))
    return out


def square(lon, lat, d=0.02):
    return {"type": "Polygon", "coordinates": [[[lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat + d], [lon, lat]]]}


tmp = pathlib.Path(tempfile.mkdtemp())
dirs = {"london": tmp / "data" / "map", "north-east": tmp / "data" / "regions" / "north-east" / "map"}
places = {"london": (-0.15, 51.5, "Camden"), "north-east": (-1.6, 54.95, "Gateshead")}
for slug, d in dirs.items():
    d.mkdir(parents=True)
    lon, lat, la = places[slug]
    wards = [{"type": "Feature", "properties": {"WD24CD": f"{slug}-w{i}", "WD24NM": f"W{i}", "LAD": la},
              "geometry": square(lon + 0.02 * i, lat)} for i in range(3)]
    (d / "wards.js").write_text("var GJ = " + json.dumps({"type": "FeatureCollection", "features": wards}) + ";")
    # Ward 0: deciles 1 and 2 (average 1.5, so 2); ward 1: 9 and 10 (10); ward 2: none.
    lsoas = [{"type": "Feature", "properties": {"code": f"{slug}-l{j}", "ward_code": f"{slug}-w{w}", "imd_decile": dec},
              "geometry": None} for j, (w, dec) in enumerate([(0, 1), (0, 2), (1, 9), (1, 10)])]
    (d / "lsoa_imd.js").write_text("var LSOA_IMD = " + json.dumps({"type": "FeatureCollection", "features": lsoas}) + ";")

H.region_dir = lambda slug: dirs.get(slug, tmp / "nowhere" / slug)
H.OUT = tmp / "home_map.js"
H.ROOT = tmp
rc = H.main()
check("it builds", rc == 0)
text = H.OUT.read_text(encoding="utf-8")
data = json.loads(text[text.index("= ") + 2:text.rstrip().rindex(";")])
regs = {r["id"]: r for r in data["regions"]}
check("the regions with a pack are on the map, and the others are left out",
      sorted(regs) == ["london", "north-east"], str(sorted(regs)))
check("in the home page's order, north to south", [r["id"] for r in data["regions"]] == ["north-east", "london"])
ne = regs["north-east"]
check("a region's counts: LAs, wards and LSOAs", (ne["las"], ne["wards"], ne["lsoas"]) == (1, 3, 4), str(ne))
check("the shares of its LSOAs in the most deprived 10% and 20%", (ne["d10"], ne["d20"]) == (25.0, 50.0), str(ne))
check("its outline is packed, with a label point inside the map",
      ne["n"] >= 1 and ne["p"] and 0 <= ne["lx"] <= data["w"] and 0 <= ne["ly"] <= data["h"], str(ne))
w = data["wards"]
check("every ward with a shape is packed, with a decile and a region each",
      w["n"] == 6 and len(w["d"]) == 6 and len(w["g"]) == 6, json.dumps({k: w[k] for k in ("n", "d", "g")}))
# Ward 0's LSOAs are deciles 1 and 2 (1.5 rounds up to 2, C), ward 1's 9 and
# 10 (9.5 to 10, K), and ward 2 has none (0, A).
check("a ward's decile is its LSOAs' average, halves rounded up, and 0 with none", w["d"] == "CKACKA", w["d"])
vals = decode(w["p"])
n = vals[0]
pts = [(vals[1], vals[2])]
for k in range(1, n):
    pts.append((pts[-1][0] + vals[1 + 2 * k], pts[-1][1] + vals[2 + 2 * k]))
check("the first ward decodes to a ring inside the map's box",
      n >= 3 and all(0 <= x <= data["w"] and 0 <= y <= data["h"] for x, y in pts), str(pts))
check("the map is about BOX units on its longer side", max(data["w"], data["h"]) == H.BOX, f"{data['w']}x{data['h']}")
check("England's north is at the top: the North East's label is above London's", ne["ly"] < regs["london"]["ly"])

print(f"\n{'All checks passed' if not FAILS else f'{len(FAILS)} failed'}")
sys.exit(1 if FAILS else 0)
