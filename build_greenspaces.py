#!/usr/bin/env python3
"""
build_greenspaces.py
====================

One-shot builder: converts the OS Open Greenspace TQ tile into the
greenspaces.geojson that the map's "Parks and green spaces" layer draws and
the printable ward and LSOA reports list.

Input:
    data/environment/opgrsp_essh_tq.zip  (OS Open Greenspace ESRI Shape, TQ tile)
    data/boundaries/wards.geojson        (the region's wards, for the footprint)

Clip:
    The union of the region's ward boundaries, buffered by 200 m so parks
    that straddle the edge stay whole. Sites outside are dropped. This used
    to clip to the old North West London footprint, read out of index.html,
    so the layer and the reports had no parks east of about Regent's Park.

Output:
    greenspaces.geojson (repo root). Features carry: id, function, name and
    aliases. Simplified at 1.5 m in EPSG:27700 before reprojection, with
    coordinates to five decimal places (about a metre) and no height.

Needs shapely and pyproj, which the main pipeline already uses
(requirements.txt). The shapefile and its table are read directly, so
geopandas is not needed.

Run:
    python3 build_greenspaces.py
"""
from __future__ import annotations

import json
import os
import struct
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent
ZIP_PATH = REPO / "data" / "environment" / "opgrsp_essh_tq.zip"
WARDS_PATH = REPO / "data" / "boundaries" / "wards.geojson"
OUT_PATH = REPO / "greenspaces.geojson"

FOOTPRINT_BUFFER_M = 200.0
SIMPLIFY_M = 1.5
DROP_FUNCTIONS = {"Tennis Court", "Bowling Green"}
PLACES = 5


def _read_dbf(raw: bytes) -> list[dict]:
    """Records of a dBase III table, as dicts of stripped strings."""
    n = struct.unpack("<I", raw[4:8])[0]
    hdr_len = struct.unpack("<H", raw[8:10])[0]
    rec_len = struct.unpack("<H", raw[10:12])[0]
    fields, pos = [], 32
    while raw[pos] != 0x0D:
        fd = raw[pos:pos + 32]
        fields.append((fd[:11].split(b"\0")[0].decode("ascii"), fd[16]))
        pos += 32
    out = []
    for i in range(n):
        rec = raw[hdr_len + i * rec_len: hdr_len + (i + 1) * rec_len]
        if rec[:1] == b"*":          # deleted
            out.append(None)
            continue
        row, off = {}, 1
        for name, size in fields:
            row[name] = rec[off:off + size].decode("utf-8", "replace").strip()
            off += size
        out.append(row)
    return out


def _signed_area(ring) -> float:
    a = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        a += x1 * y2 - x2 * y1
    return a / 2.0


def _read_polygons(raw: bytes):
    """Yield each record of a Polygon or PolygonZ shapefile as a list of
    rings ([(x, y), ...]); None for a null shape."""
    from shapely.geometry import Polygon, MultiPolygon
    pos = 100
    while pos < len(raw):
        _num, clen = struct.unpack(">ii", raw[pos:pos + 8])
        body = raw[pos + 8: pos + 8 + clen * 2]
        pos += 8 + clen * 2
        stype = struct.unpack("<i", body[:4])[0]
        if stype == 0:
            yield None
            continue
        if stype not in (5, 15, 25):
            raise SystemExit(f"unexpected shape type {stype}")
        nparts, npts = struct.unpack("<ii", body[36:44])
        parts = list(struct.unpack(f"<{nparts}i", body[44:44 + 4 * nparts]))
        p0 = 44 + 4 * nparts
        xy = struct.unpack(f"<{2 * npts}d", body[p0:p0 + 16 * npts])
        pts = list(zip(xy[0::2], xy[1::2]))
        rings = [pts[a:b] for a, b in zip(parts, parts[1:] + [npts])]
        # Outer rings run clockwise in a shapefile (negative signed area),
        # holes anticlockwise. Each hole goes with the outer ring holding it.
        outers = [r for r in rings if _signed_area(r) < 0] or rings[:1]
        holes = [r for r in rings if r not in outers]
        polys = []
        for o in outers:
            po = Polygon(o)
            mine = [h for h in holes if po.contains(Polygon(h).representative_point())]
            polys.append(Polygon(o, mine))
        g = polys[0] if len(polys) == 1 else MultiPolygon(polys)
        yield g if g.is_valid else g.buffer(0)


def _round(coords):
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], PLACES), round(coords[1], PLACES)]
    return [_round(c) for c in coords]


def main() -> None:
    try:
        from shapely.geometry import shape, mapping
        from shapely.ops import unary_union, transform
        from pyproj import Transformer
    except ImportError:
        sys.exit("shapely and pyproj are needed: pip install -r requirements.txt")
    for p in (ZIP_PATH, WARDS_PATH):
        if not p.exists():
            sys.exit(f"{p} not found")

    to_bng = Transformer.from_crs("EPSG:4326", "EPSG:27700", always_xy=True).transform
    to_wgs = Transformer.from_crs("EPSG:27700", "EPSG:4326", always_xy=True).transform

    wards = json.loads(WARDS_PATH.read_text(encoding="utf-8"))["features"]
    print(f"Footprint from {len(wards):,} wards in {WARDS_PATH.name} ...", flush=True)
    ward_geoms = [transform(to_bng, shape(f["geometry"])).buffer(0) for f in wards if f.get("geometry")]
    footprint = unary_union(ward_geoms).buffer(FOOTPRINT_BUFFER_M)
    print(f"  footprint area: {footprint.area / 1e6:,.1f} km2", flush=True)

    with zipfile.ZipFile(ZIP_PATH) as z:
        shp_name = next(n for n in z.namelist() if n.endswith("GreenspaceSite.shp"))
        dbf_name = shp_name[:-4] + ".dbf"
        shp_raw, dbf_raw = z.read(shp_name), z.read(dbf_name)
    rows = _read_dbf(dbf_raw)
    print(f"  {len(rows):,} sites in {Path(shp_name).name}", flush=True)

    from shapely.prepared import prep
    fp = prep(footprint)
    feats, dropped = [], 0
    for row, geom in zip(rows, _read_polygons(shp_raw)):
        if row is None or geom is None or geom.is_empty:
            continue
        if row.get("function") in DROP_FUNCTIONS:
            dropped += 1
            continue
        if not fp.intersects(geom):
            continue
        g = geom.simplify(SIMPLIFY_M, preserve_topology=True)
        if g.is_empty:
            continue
        names = []
        for c in ("distName1", "distName2", "distName3", "distName4"):
            v = (row.get(c) or "").strip()
            if v and v not in names:
                names.append(v)
        props = {"id": row.get("id", ""), "function": row.get("function", ""),
                 "name": names[0] if names else ""}
        if len(names) > 1:
            props["aliases"] = " / ".join(names[1:])
        gj = mapping(transform(to_wgs, g))
        feats.append({"type": "Feature", "properties": props,
                      "geometry": {"type": gj["type"], "coordinates": _round(gj["coordinates"])}})
    print(f"  {len(feats):,} sites inside the footprint ({dropped:,} tennis courts and bowling greens left out)", flush=True)

    counts: dict = {}
    for f in feats:
        counts[f["properties"]["function"]] = counts.get(f["properties"]["function"], 0) + 1
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {v:>6,}  {k}", flush=True)

    payload = json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":"))
    tmp = OUT_PATH.with_suffix(".geojson.tmp")
    with open(tmp, "wb") as fh:
        fh.write(payload.encode("utf-8"))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, OUT_PATH)
    print(f"Wrote {OUT_PATH.name}: {OUT_PATH.stat().st_size / 1024:,.0f} KB", flush=True)


if __name__ == "__main__":
    main()
