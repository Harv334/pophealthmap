#\!/usr/bin/env python3
"""
build_greenspaces.py
====================

One-shot builder: converts the OS Open Greenspace TQ tile shapefile into a
NW-London-clipped, simplified GeoJSON that the map toggles on/off.

Input:
    data/environment/opgrsp_essh_tq.zip  (OS Open Greenspace ESRI Shape, TQ tile)

Clip:
    Unions all 1,313 embedded LSOA polygons (extracted from index.html) to
    build the true NW London footprint, then buffers by 200 m so parks that
    straddle the borough boundary stay whole. Polygons outside are dropped.

Output:
    greenspaces.geojson (repo root). Features carry: id, function, name,
    aliases. Simplified at 1.5 m in EPSG:27700 before reprojection.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent
ZIP_PATH = REPO / "data" / "environment" / "opgrsp_essh_tq.zip"
HTML_PATH = REPO / "index.html"
OUT_PATH = REPO / "greenspaces.geojson"

FOOTPRINT_BUFFER_M = 200.0
SIMPLIFY_M = 1.5
DROP_FUNCTIONS = {"Tennis Court", "Bowling Green"}


def _extract_lsoa_imd(html: str) -> dict:
    """Extract the embedded LSOA_IMD GeoJSON object from index.html."""
    m = re.search(r"const\s+LSOA_IMD\s*=\s*(\{)", html)
    if not m:
        sys.exit("Could not find 'const LSOA_IMD = {' in index.html")
    start = m.end() - 1
    depth = 0
    i = start
    in_str = False
    esc = False
    while i < len(html):
        c = html[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(html[start:i + 1])
        i += 1
    sys.exit("Failed to parse LSOA_IMD object (brace mismatch)")


def main() -> None:
    try:
        import geopandas as gpd
        from shapely.geometry import shape
        from shapely.ops import unary_union
    except ImportError:
        sys.exit(
            "geopandas + shapely required. Install with:\n"
            "  pip install geopandas shapely pyproj --break-system-packages"
        )

    if not ZIP_PATH.exists():
        sys.exit(f"{ZIP_PATH} not found")
    if not HTML_PATH.exists():
        sys.exit(f"{HTML_PATH} not found")

    print(f"Reading LSOA_IMD from {HTML_PATH.name} ...", flush=True)
    html = HTML_PATH.read_text(encoding="utf-8")
    gj = _extract_lsoa_imd(html)
    lsoa_geoms = [shape(f["geometry"]) for f in gj.get("features", []) if f.get("geometry")]
    print(f"  {len(lsoa_geoms):,} LSOA polygons loaded", flush=True)

    # Reproject first (vectorised), then union in 27700. Faster + numerically
    # stable. buffer(0) cleans tiny self-intersections that trip up unary_union.
    lsoa_27700 = gpd.GeoSeries(lsoa_geoms, crs="EPSG:4326").to_crs("EPSG:27700").buffer(0)
    print(f"  reprojected to EPSG:27700; dissolving ...", flush=True)
    lsoa_union_27700 = unary_union(list(lsoa_27700.geometry))
    footprint_27700 = lsoa_union_27700.buffer(FOOTPRINT_BUFFER_M)
    bbox_27700 = footprint_27700.envelope
    print(f"  footprint area: {footprint_27700.area / 1e6:.1f} km2", flush=True)

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        print(f"Unzipping {ZIP_PATH.name} ...", flush=True)
        with zipfile.ZipFile(ZIP_PATH) as z:
            for name in z.namelist():
                if "GreenspaceSite" in name:
                    z.extract(name, tdp)
        shp = next(tdp.rglob("TQ_GreenspaceSite.shp"), None)
        if shp is None:
            sys.exit("TQ_GreenspaceSite.shp not found in zip")
        gdf = gpd.read_file(shp)
    print(f"  Loaded {len(gdf):,} features, CRS={gdf.crs}", flush=True)

    print(f"Clipping to NW London footprint (+{FOOTPRINT_BUFFER_M:.0f} m buffer) ...", flush=True)
    before = len(gdf)
    gdf = gdf[gdf.intersects(bbox_27700)].copy()
    print(f"  {len(gdf):,} features pass bbox prefilter (from {before:,})", flush=True)
    gdf = gdf[gdf.intersects(footprint_27700)].copy()
    print(f"  {len(gdf):,} features intersect NW London footprint", flush=True)

    if "function" in gdf.columns and DROP_FUNCTIONS:
        before = len(gdf)
        gdf = gdf[~gdf["function"].isin(DROP_FUNCTIONS)].copy()
        print(f"  Dropped {before - len(gdf):,} features in {sorted(DROP_FUNCTIONS)}", flush=True)

    print(f"Simplifying at {SIMPLIFY_M:.1f} m tolerance ...", flush=True)
    gdf["geometry"] = gdf.geometry.simplify(SIMPLIFY_M, preserve_topology=True)
    gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notna()].copy()

    print("Reprojecting to EPSG:4326 ...", flush=True)
    gdf = gdf.to_crs("EPSG:4326")

    wanted = [c for c in ("id", "function", "distName1", "distName2", "distName3", "distName4") if c in gdf.columns]
    gdf = gdf[wanted + ["geometry"]].copy()

    def _primary(row):
        for c in ("distName1", "distName2", "distName3", "distName4"):
            v = row.get(c) if c in row else None
            if v and str(v).strip():
                return str(v).strip()
        return ""

    def _aliases(row):
        seen = []
        for c in ("distName1", "distName2", "distName3", "distName4"):
            v = row.get(c) if c in row else None
            if v and str(v).strip():
                s = str(v).strip()
                if s not in seen:
                    seen.append(s)
        return " / ".join(seen[1:]) if len(seen) > 1 else ""

    gdf["name"] = gdf.apply(_primary, axis=1)
    gdf["aliases"] = gdf.apply(_aliases, axis=1)
    keep = ["id", "function", "name", "aliases", "geometry"]
    gdf = gdf[[c for c in keep if c in gdf.columns]].copy()

    if "function" in gdf.columns:
        print("Category breakdown:", flush=True)
        for cat, n in gdf["function"].value_counts().items():
            print(f"  {n:>5}  {cat}", flush=True)

    print(f"Building features ...", flush=True)
    feats = []
    for _, row in gdf.iterrows():
        geom = row.geometry.__geo_interface__ if row.geometry is not None else None
        if geom is None:
            continue
        props = {
            "id": row.get("id", ""),
            "function": row.get("function", ""),
            "name": row.get("name", ""),
        }
        if row.get("aliases"):
            props["aliases"] = row["aliases"]
        feats.append({"type": "Feature", "properties": props, "geometry": geom})

    payload = json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":"))
    print(f"  {len(feats):,} features; payload {len(payload):,} bytes", flush=True)

    print(f"Writing {OUT_PATH} ...", flush=True)
    with open(OUT_PATH, "wb") as fh:
        fh.write(payload.encode("utf-8"))
        fh.flush()
        os.fsync(fh.fileno())
    size_kb = OUT_PATH.stat().st_size / 1024
    print(f"  wrote {size_kb:,.0f} KB", flush=True)


if __name__ == "__main__":
    main()
