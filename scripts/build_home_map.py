"""Build the home page's map of England: every ward, coloured by deprivation,
with the nine regions drawn over it to choose from.

The home page (data/map/home.js) is the first thing a visitor sees, before
any region's data has loaded, so its map has to be small. This takes each
published region's wards and LSOA deciles, the same files the region maps
use, and packs them the way the loading screen's map is packed
(build_loader_map.py): each ward simplified for a map of the whole country,
quantised to a box of BOX units, delta encoded and written as varints in a
base64 alphabet. Each ward carries one decile, the average of its LSOAs'
IMD 2025 deciles. Each region is the union of its wards, with its islands,
and the figures its card gives: how many LAs, wards and LSOAs it has, and
what share of its LSOAs are in the most deprived 10% and 20% in England.

Projected on the British National Grid (pyproj), so England has its usual
shape. Reads London from data/map/ and every other region from
data/regions/<slug>/map/, where the deploy unpacks the region packs, and
writes data/map/home_map.js. A region whose pack is not there is left out,
and the page lists it without a map.

    py scripts/build_home_map.py

It runs in the site deploy after the region packs are unpacked
(jekyll-gh-pages.yml), so the home map always matches the regions published
with it. The committed copy is what a plain checkout and the tests use.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import date
from pathlib import Path

from pyproj import Transformer
from shapely.geometry import Polygon, shape
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from build_loader_map import ALPHABET, encode_varints, rdp  # noqa: E402

OUT = ROOT / "data" / "map" / "home_map.js"

# The regions in the order the home page lists them, north to south, with
# London where the region menu has it. Their slugs match index.html's REGIONS.
REGIONS = [
    ("north-east", "North East"),
    ("north-west", "North West"),
    ("yorkshire-and-the-humber", "Yorkshire and The Humber"),
    ("east-midlands", "East Midlands"),
    ("west-midlands", "West Midlands"),
    ("east-of-england", "East of England"),
    ("london", "London"),
    ("south-east", "South East"),
    ("south-west", "South West"),
]

# England is about 650 km from north to south. BOX units across the longer
# side is a unit of about 540 m: finer than a pixel for the map at the size
# the page draws it, and coarse enough to keep the file small.
BOX = 1200
# Metres. A ward edge moves by at most this, a little under a unit.
TOL_WARD = 450
# A region's outline is drawn over the wards, a little smoother.
TOL_REGION = 600
# Islands smaller than this (square metres) are left out of a region's
# outline; the Isle of Wight, Anglesey-sized islands and the larger estuary
# islands stay.
MIN_PART_M2 = 4_000_000

TO_BNG = Transformer.from_crs("EPSG:4326", "EPSG:27700", always_xy=True)


def read_js(path: Path, name: str):
    text = path.read_text(encoding="utf-8")
    marker = f"var {name} = "
    start = text.index(marker) + len(marker)
    end = max(text.rfind("}"), text.rfind("]")) + 1
    return json.loads(text[start:end])


def region_dir(slug: str) -> Path:
    return ROOT / "data" / "map" if slug == "london" else ROOT / "data" / "regions" / slug / "map"


def to_bng(ring: list) -> list:
    xs, ys = TO_BNG.transform([p[0] for p in ring], [p[1] for p in ring])
    return list(zip(xs, ys))


def parts(geometry: dict) -> list:
    """Each polygon's outer ring, largest first."""
    if not geometry:
        return []
    if geometry["type"] == "Polygon":
        rings = [geometry["coordinates"][0]]
    elif geometry["type"] == "MultiPolygon":
        rings = [poly[0] for poly in geometry["coordinates"]]
    else:
        return []
    return sorted((r for r in rings if len(r) >= 4), key=len, reverse=True)


def pack_ring(ring_bng: list, tol: float, project) -> str | None:
    pts, last = [], None
    for pt in rdp(ring_bng, tol):
        xy = project(pt)
        if xy != last:
            pts.append(xy)
            last = xy
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts.pop()
    if len(pts) < 3:
        return None
    vals = [len(pts), pts[0][0], pts[0][1]]
    for i in range(1, len(pts)):
        vals += [pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]]
    return encode_varints(vals)


def main() -> int:
    regions = []
    for slug, name in REGIONS:
        d = region_dir(slug)
        if not (d / "wards.js").exists() or not (d / "lsoa_imd.js").exists():
            print(f"  {name}: no pack here, left out of the map")
            continue
        wards = read_js(d / "wards.js", "GJ")["features"]
        lsoas = read_js(d / "lsoa_imd.js", "LSOA_IMD")["features"]
        regions.append((slug, name, wards, lsoas))
    if not regions:
        print("no region has wards and LSOAs here", file=sys.stderr)
        return 1

    # Every ward's rings in metres, and the extent of the whole map.
    projected = []
    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")
    for ri, (slug, name, wards, lsoas) in enumerate(regions):
        for f in wards:
            rings = [to_bng(r) for r in parts(f.get("geometry"))]
            if not rings:
                continue
            for r in rings:
                for x, y in r:
                    min_x, max_x = min(min_x, x), max(max_x, x)
                    min_y, max_y = min(min_y, y), max(max_y, y)
            projected.append((ri, f.get("properties") or {}, rings))
    scale = BOX / max(max_x - min_x, max_y - min_y)
    width, height = round((max_x - min_x) * scale), round((max_y - min_y) * scale)

    def project(pt):
        # The page's y grows downward; the grid's northing grows upward.
        return round((pt[0] - min_x) * scale), round((max_y - pt[1]) * scale)

    # One decile per ward: the average of its LSOAs' deciles, halves rounded
    # up (Python's round() takes 2.5 to 2).
    ward_dec = {}
    for slug, name, wards, lsoas in regions:
        for f in lsoas:
            p = f.get("properties") or {}
            if p.get("ward_code") and isinstance(p.get("imd_decile"), (int, float)):
                ward_dec.setdefault(p["ward_code"], []).append(int(p["imd_decile"]))
    ward_dec = {c: max(1, min(10, math.floor(sum(v) / len(v) + 0.5))) for c, v in ward_dec.items()}

    packed, deciles, region_of = [], [], []
    for ri, props, rings in projected:
        enc = pack_ring(rings[0], TOL_WARD, project)
        if not enc:
            continue
        packed.append(enc)
        deciles.append(ALPHABET[ward_dec.get(props.get("WD24CD", ""), 0)])
        region_of.append(ALPHABET[ri])

    out_regions = []
    for ri, (slug, name, wards, lsoas) in enumerate(regions):
        polys = []
        for f in wards:
            try:
                g = shape(f["geometry"])
            except Exception:
                continue
            for r in parts(f.get("geometry")):
                pg = Polygon(to_bng(r))
                polys.append(pg if pg.is_valid else pg.buffer(0))
        union = unary_union([p.buffer(30) for p in polys]).buffer(-30)
        geoms = list(union.geoms) if hasattr(union, "geoms") else [union]
        geoms = sorted((g for g in geoms if g.area >= MIN_PART_M2), key=lambda g: g.area, reverse=True)
        rings, n_rings = [], 0
        for g in geoms:
            enc = pack_ring(list(g.exterior.coords), TOL_REGION, project)
            if enc:
                rings.append(enc)
                n_rings += 1
        main_part = geoms[0]
        lp = main_part.representative_point()
        lx, ly = project((lp.x, lp.y))
        dec = [int(f["properties"]["imd_decile"]) for f in lsoas
               if isinstance((f.get("properties") or {}).get("imd_decile"), (int, float))]
        las = {(f.get("properties") or {}).get("LAD") for f in wards} - {None, ""}
        out_regions.append({
            "id": slug, "name": name,
            "las": len(las), "wards": len(wards), "lsoas": len(dec),
            # Shares of the region's LSOAs, in percent, one decimal.
            "d10": round(100 * sum(1 for v in dec if v == 1) / len(dec), 1) if dec else None,
            "d20": round(100 * sum(1 for v in dec if v <= 2) / len(dec), 1) if dec else None,
            "lx": lx, "ly": ly, "n": n_rings, "p": "".join(rings),
        })
        print(f"  {name}: {len(wards)} wards, {len(dec)} LSOAs, {len(las)} LAs, "
              f"{n_rings} outline part(s), most deprived 20%: {out_regions[-1]['d20']}%")

    data = {"built": date.today().isoformat(), "w": width, "h": height,
            "regions": out_regions,
            "wards": {"n": len(packed), "d": "".join(deciles), "g": "".join(region_of), "p": "".join(packed)}}
    OUT.write_text(
        "// The home page's map of England: each ward with its average IMD 2025 decile,\n"
        "// and the nine regions over it. Made by scripts/build_home_map.py from the\n"
        "// published regions; the deploy rebuilds it. Do not hand edit.\n"
        f"var PH_HOME_MAP = {json.dumps(data, separators=(',', ':'), ensure_ascii=False)};\n",
        encoding="utf-8")
    size = OUT.stat().st_size
    print(f"wrote {OUT.relative_to(ROOT)}: {len(packed)} wards, {len(out_regions)} regions, "
          f"{width}x{height}, {size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
