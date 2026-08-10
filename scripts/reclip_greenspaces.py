#!/usr/bin/env python3
"""Re-clip greenspaces.geojson to the dashboard's NW London footprint + 200m buffer.

Source-of-truth for the NWL boundary is the `BOROUGH_GJ` constant inlined in
index.html — that's what the dashboard actually renders. Reading from there
(rather than data/boundaries/boroughs.geojson, which may be stale or include
Camden) guarantees the clip can never disagree with what's on screen.

Pipeline:
    1. Pull `const BOROUGH_GJ = {...};` from index.html
    2. Union the borough polygons; project to BNG (EPSG:27700)
    3. Buffer the union outward by 200m and project back to WGS84
    4. For each greenspace polygon: drop if it doesn't intersect the
       buffered footprint; otherwise clip to the buffered footprint
    5. Drop any clipped fragments smaller than 25 m² (post-clip slivers)

Output:
    greenspaces.geojson  — overwritten in place

Reports counts before/after, plus a sanity check listing any clipped
fragments that look like Camden leftovers (centroid east of -0.10°).

Run:
    python scripts/reclip_greenspaces.py
"""
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INDEX_HTML  = REPO / 'index.html'
GREENSPACES = REPO / 'greenspaces.geojson'
BUFFER_M    = 200


def main():
    try:
        from shapely.geometry import shape, mapping, Point
        from shapely.ops import unary_union, transform
        import pyproj
    except ImportError as e:
        raise SystemExit(
            f"Missing dep ({e.name}). Run: pip install shapely pyproj"
        )

    if not INDEX_HTML.exists():
        raise SystemExit(f"Cannot find {INDEX_HTML}")
    if not GREENSPACES.exists():
        raise SystemExit(f"Cannot find {GREENSPACES}")

    print(f"reading BOROUGH_GJ from {INDEX_HTML.name}")
    html = INDEX_HTML.read_text(encoding='utf-8')
    m = re.search(r'const BOROUGH_GJ\s*=\s*(\{.*?\});', html, re.DOTALL)
    if not m:
        raise SystemExit("Could not find inline BOROUGH_GJ in index.html. " "Map data moved to data/map/*.js in Phase 3.1; read it with map_data.read_blob() instead of parsing index.html.")
    bg = json.loads(m.group(1))
    feats = bg.get('features', [])
    print(f"  {len(feats)} borough polygons in BOROUGH_GJ")
    for f in feats:
        nm = (f.get('properties') or {}).get('name', '?')
        print(f"    · {nm}")
    if any('camden' in ((f.get('properties') or {}).get('name', '').lower())
           for f in feats):
        print("  ⚠️  Camden is still in BOROUGH_GJ — fix index.html first.")

    nwl_polys = [shape(f['geometry']) for f in feats]
    nwl_wgs = unary_union(nwl_polys)

    # Project WGS84 → BNG (metres) → buffer → back to WGS84
    to_bng = pyproj.Transformer.from_crs(4326, 27700, always_xy=True).transform
    to_wgs = pyproj.Transformer.from_crs(27700, 4326, always_xy=True).transform
    nwl_bng = transform(to_bng, nwl_wgs)
    nwl_buffered = transform(to_wgs, nwl_bng.buffer(BUFFER_M))
    print(f"  buffered NWL footprint bounds: "
          f"{[round(x, 4) for x in nwl_buffered.bounds]}")

    print(f"\nloading {GREENSPACES.name}")
    with open(GREENSPACES, encoding='utf-8') as f:
        gs = json.load(f)
    in_n = len(gs['features'])
    print(f"  {in_n} input features")

    kept = []
    dropped_outside    = 0
    dropped_sliver     = 0
    dropped_invalid    = 0
    suspicious_camden  = 0

    for feat in gs['features']:
        try:
            g = shape(feat['geometry'])
        except Exception:
            dropped_invalid += 1
            continue
        if not g.is_valid:
            g = g.buffer(0)
        if not g.intersects(nwl_buffered):
            dropped_outside += 1
            continue
        clipped = g.intersection(nwl_buffered)
        if clipped.is_empty:
            dropped_outside += 1
            continue
        # Drop sliver fragments after clipping
        try:
            clipped_bng = transform(to_bng, clipped)
            if clipped_bng.area < 25:
                dropped_sliver += 1
                continue
        except Exception:
            pass

        # Sanity-check: any feature whose centroid sits east of -0.10° AND
        # north of 51.53° is plausibly a Camden leftover. Flag for review;
        # don't auto-drop in case it's a legitimate Westminster park.
        c = clipped.centroid
        if c.x > -0.10 and c.y > 51.53:
            suspicious_camden += 1

        kept.append({
            'type': 'Feature',
            'properties': feat.get('properties') or {},
            'geometry': mapping(clipped),
        })

    print(f"\nresult:")
    print(f"  kept:                {len(kept)}")
    print(f"  dropped outside:     {dropped_outside}")
    print(f"  dropped slivers:     {dropped_sliver}")
    print(f"  dropped invalid:     {dropped_invalid}")
    print(f"  suspicious Camden-leftovers (east of -0.10° + north of 51.53°): "
          f"{suspicious_camden}")

    out = {'type': 'FeatureCollection', 'features': kept}
    GREENSPACES.write_text(
        json.dumps(out, ensure_ascii=False, allow_nan=False),
        encoding='utf-8',
    )
    print(f"\nwrote {GREENSPACES} ({GREENSPACES.stat().st_size:,} bytes)")


if __name__ == '__main__':
    main()
