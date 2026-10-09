"""Build the five London ICB boundaries by dissolving the borough outlines.

An Integrated Care Board footprint in London is exactly a union of whole
boroughs, so there is nothing to download: the geometry is already in
data/map/boroughs.js and the only new information is which borough belongs to
which board. Deriving it rather than fetching it means the two can never
disagree about where a borough edge is, and it keeps the boundary in step
automatically when the borough source is refreshed.

The membership below is hardcoded, and it is the part to check if this is ever
pointed at a different footprint or a reorganisation happens. Two things guard
it: North West London is cross-checked against the eight boroughs the project
already defines as its NWL scope, and the script refuses to write anything
unless all 33 London local authorities are assigned to exactly one board.

ONS codes are deliberately not included. The membership is well established
and checkable by eye; the nine-digit ICB codes are not, and a wrong one
written into the map would be worse than none at all.

Run when the borough boundaries change:

    py scripts/build_icb_boundaries.py

Outside London (a region build, see build-region.yml) the membership is not
typed here. It is read from ONS's own local authority to ICB lookup, found on
the ONS ArcGIS server the same way scripts/make_scopes.py finds its lookup,
and the boards are dissolved from the region's council outlines:

    py scripts/build_icb_boundaries.py --region north-east

That writes icbs.json and icb_lookup.json into data/regions/<slug>/map/. A
board that reaches past the region (North East and North Cumbria does) is
drawn for the part inside it, and its council count says "in" the region.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from shapely.geometry import mapping, shape
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parent.parent
BOROUGHS = ROOT / "data" / "map" / "boroughs.js"
OUT = ROOT / "data" / "map" / "icbs.json"
LOOKUP = ROOT / "data" / "map" / "icb_lookup.json"

# The eight the rest of this project calls North West London. Kept as its own
# list even though it is no longer a board of its own: it is still the scope
# the pipeline and CLAUDE.md are written around, and it is the cross-check that
# the board below is composed of the right boroughs.
NWL = [
    "Brent", "Ealing", "Hammersmith and Fulham", "Harrow",
    "Hillingdon", "Hounslow", "Kensington and Chelsea", "Westminster",
]

# The five boroughs that were North Central London before the merger.
NCL = ["Barnet", "Camden", "Enfield", "Haringey", "Islington"]

# Four boards, not five. North West London and North Central London are now a
# single board, NHS West and North London ICB, so they are composed here rather
# than drawn as two regions that happen to touch: a merged board has one
# outline, and the boundary that used to run between them is gone.
ICBS: dict[str, list[str]] = {
    "West and North London": NWL + NCL,
    "North East London": [
        "Barking and Dagenham", "City of London", "Hackney", "Havering",
        "Newham", "Redbridge", "Tower Hamlets", "Waltham Forest",
    ],
    "South East London": [
        "Bexley", "Bromley", "Greenwich", "Lambeth", "Lewisham", "Southwark",
    ],
    "South West London": [
        "Croydon", "Kingston upon Thames", "Merton",
        "Richmond upon Thames", "Sutton", "Wandsworth",
    ],
}

# Four hues chosen for the pairs that actually touch. The ring of adjacencies
# is WNL-NE, NE-SE, SE-SW, SW-WNL, so the four are spread around the wheel in
# that order and every neighbouring pair sits a quarter turn apart. Saturated
# enough to survive being drawn at low opacity over a basemap, in either theme.
#
# The teal that used to be here went with the merger. It existed because a pale
# violet and a pale blue were indistinguishable along the old North West to
# North Central boundary, which is a boundary that no longer exists.
COLOURS = {
    "West and North London": "#8E44AD",  # violet
    "North East London":     "#C2701C",  # amber
    "South East London":     "#2E7D32",  # green
    "South West London":     "#C0392B",  # red
}


def read_borough_gj(path: Path = BOROUGHS) -> dict:
    text = path.read_text(encoding="utf-8")
    marker = "var BOROUGH_GJ = "
    start = text.index(marker) + len(marker)
    return json.loads(text[start : text.rindex("}") + 1])


ARCGIS = "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services"

# Hues for the region boards, assigned so no two boards that touch share one.
# The London four first, then four more of the same weight.
REGION_PALETTE = ["#8E44AD", "#C2701C", "#2E7D32", "#C0392B",
                  "#1F6FB2", "#B8860B", "#00838F", "#AD1457"]


def ons_lad_to_icb() -> tuple[str, dict, dict]:
    """(service, {LAD code: ICB code}, {ICB code: ICB name}) from the newest
    ONS lookup that carries both a local authority and an ICB column. An
    LSOA level lookup is reduced to its authorities by majority."""
    import requests
    r = requests.get(ARCGIS, params={"f": "json"}, timeout=60)
    r.raise_for_status()
    best = None
    for svc in r.json().get("services", []):
        name = svc.get("name", "")
        icb = re.search(r"ICB(\d\d)", name)
        if (svc.get("type") != "FeatureServer" or not icb or "LAD" not in name
                or not re.search(r"_LU(_v\d+)?$", name)):
            continue
        v = re.search(r"_v(\d+)$", name)
        key = (int(icb.group(1)), int(v.group(1)) if v else 0)
        if best is None or key > best[0]:
            best = (key, name)
    if not best:
        raise SystemExit("no LAD to ICB lookup found on the ONS ArcGIS server")
    service = best[1]
    url = f"{ARCGIS}/{service}/FeatureServer/0/query"
    rows, offset = [], 0
    while True:
        r = requests.get(url, params={"where": "1=1", "outFields": "*", "f": "json",
                                      "returnGeometry": "false",
                                      "resultOffset": offset, "resultRecordCount": 2000},
                         timeout=120)
        r.raise_for_status()
        feats = r.json().get("features", [])
        rows += [f["attributes"] for f in feats]
        if len(feats) < 2000:
            break
        offset += len(feats)
    votes: dict = {}
    names: dict = {}
    for row in rows:
        lad = next((v for k, v in row.items() if re.fullmatch(r"LAD\d\dCD", k, re.I)), None)
        icb = next((v for k, v in row.items() if re.fullmatch(r"ICB\d\dCD", k, re.I)), None)
        inm = next((v for k, v in row.items() if re.fullmatch(r"ICB\d\dNM", k, re.I)), None)
        if lad and icb:
            votes.setdefault(lad, {}).setdefault(icb, 0)
            votes[lad][icb] += 1
            if inm:
                names[icb] = inm
    lad_icb = {lad: max(c, key=c.get) for lad, c in votes.items()}
    print(f"{service}: {len(lad_icb)} authorities in {len(set(lad_icb.values()))} boards")
    return service, lad_icb, names


def short_icb_name(name: str) -> str:
    n = re.sub(r"^NHS\s+", "", name.strip())
    n = re.sub(r"\s+(Integrated Care Board|ICB)$", "", n)
    return n


def region_main(slug: str) -> int:
    map_dir = ROOT / "data" / "regions" / slug / "map"
    gj = read_borough_gj(map_dir / "boroughs.js")
    by_code = {}
    for feat in gj.get("features", []):
        p = feat.get("properties") or {}
        code = next((v for k, v in p.items() if re.fullmatch(r"LAD\d\dCD", k)), None)
        if code and p.get("name"):
            by_code[code] = feat
    print(f"read {len(by_code)} council outlines for {slug}")

    service, lad_icb, names = ons_lad_to_icb()
    missing = sorted(c for c in by_code if c not in lad_icb)
    if missing:
        # An authority newer than the lookup. Better no layer than a board map
        # with a council-shaped hole in it.
        raise SystemExit(f"no ICB in {service} for {missing}")

    members: dict = {}
    for code, feat in by_code.items():
        members.setdefault(lad_icb[code], []).append(feat)
    whole = {icb: sum(1 for v in lad_icb.values() if v == icb) for icb in members}

    shapes = {}
    for icb, feats in members.items():
        merged = unary_union([shape(f["geometry"]).buffer(0) for f in feats])
        pieces = list(merged.geoms) if merged.geom_type == "MultiPolygon" else [merged]
        total = sum(p.area for p in pieces)
        kept = [p for p in pieces if p.area / total >= 0.001]
        shapes[icb] = unary_union(kept) if len(kept) > 1 else kept[0]

    # Greedy colouring, most-neighboured board first, so boards that touch
    # never share a hue.
    order = sorted(members, key=lambda i: short_icb_name(names.get(i, i)))
    touch = {i: {j for j in members if j != i and shapes[i].buffer(1e-4).intersects(shapes[j])}
             for i in members}
    colour = {}
    for i in sorted(members, key=lambda i: -len(touch[i])):
        used = {colour[j] for j in touch[i] if j in colour}
        colour[i] = next((c for c in REGION_PALETTE if c not in used), REGION_PALETTE[0])

    features, labels, counts, colours, lad_to, partial = [], {}, {}, {}, {}, []
    for icb in order:
        short = short_icb_name(names.get(icb, icb))
        boroughs = sorted(f["properties"]["name"] for f in members[icb])
        part = len(boroughs) < whole[icb]
        label = f"NHS {short} ICB"
        labels[short], counts[short], colours[short] = label, len(boroughs), colour[icb]
        if part:
            partial.append(short)
        for b in boroughs:
            lad_to[b] = short
        features.append({
            "type": "Feature",
            "properties": {"name": short, "label": label, "code": icb,
                           "boroughs": boroughs, "n_boroughs": len(boroughs),
                           "colour": colour[icb], "partial": part},
            "geometry": mapping(shapes[icb]),
        })
        print(f"  {short:40s} {len(boroughs):>2} councils{' (part)' if part else ''}")

    out = map_dir / "icbs.json"
    out.write_text(json.dumps({
        "_comment": (f"Integrated Care Boards in this region, dissolved from the "
                     f"council outlines in boroughs.js by scripts/build_icb_boundaries.py "
                     f"--region {slug}, membership from ONS {service}. Do not hand edit."),
        "type": "FeatureCollection", "features": features,
    }, separators=(",", ":"), ensure_ascii=False) + "\n", encoding="utf-8")
    (map_dir / "icb_lookup.json").write_text(json.dumps({
        "_comment": f"Council to Integrated Care Board, from ONS {service}. Generated alongside icbs.json.",
        "order": [short_icb_name(names.get(i, i)) for i in order],
        "colours": colours, "labels": labels, "counts": counts, "lad_to_icb": lad_to,
        # Boards that reach beyond the region; counts above are this region's.
        "partial": partial,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)} ({len(features)} boards)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", help="region slug; boards from the ONS lookup")
    args = ap.parse_args()
    if args.region:
        return region_main(args.region)
    if not BOROUGHS.exists():
        print(f"missing {BOROUGHS}", file=sys.stderr)
        return 1

    gj = read_borough_gj()
    by_name = {}
    for feat in gj.get("features", []):
        name = (feat.get("properties") or {}).get("name")
        if name:
            by_name[name] = feat

    print(f"read {len(by_name)} borough outlines")

    # Refuse to write a partial map. Every borough in exactly one board, and
    # every named borough actually present in the geometry.
    assigned = [b for members in ICBS.values() for b in members]
    duplicates = {b for b in assigned if assigned.count(b) > 1}
    missing_geom = [b for b in assigned if b not in by_name]
    unassigned = sorted(set(by_name) - set(assigned))

    problems = []
    if duplicates:
        problems.append(f"borough in more than one board: {sorted(duplicates)}")
    if missing_geom:
        problems.append(f"no geometry for: {missing_geom}")
    if unassigned:
        problems.append(f"borough in no board: {unassigned}")
    if len(assigned) != len(by_name):
        problems.append(f"{len(assigned)} assigned against {len(by_name)} boroughs")
    # The project's own NWL scope has to sit whole inside one board, or the
    # eight boroughs the pipeline is built around have been split across two.
    stray = [b for b in NWL if b not in ICBS["West and North London"]]
    if stray:
        problems.append(f"NWL boroughs outside West and North London: {stray}")
    if problems:
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1

    features, dropped_total = [], 0
    for name, members in ICBS.items():
        # buffer(0) first: the generalised borough outlines can carry tiny
        # self-touching artefacts that make a union invalid, and a dissolve
        # over an invalid polygon silently drops parts of it.
        parts = [shape(by_name[b]["geometry"]).buffer(0) for b in members]
        merged = unary_union(parts)

        # An ICB footprint in London is contiguous, so anything the dissolve
        # leaves detached is a sliver where two generalised borough edges did
        # not quite meet. Measured, they run to a few hundred square metres
        # against footprints of tens of square kilometres. The threshold is
        # three orders of magnitude above the largest sliver seen and far
        # below anything that could be real territory.
        pieces = list(merged.geoms) if merged.geom_type == "MultiPolygon" else [merged]
        total = sum(p.area for p in pieces)
        kept = [p for p in pieces if p.area / total >= 0.001]
        dropped = len(pieces) - len(kept)
        dropped_total += dropped
        merged = unary_union(kept) if len(kept) > 1 else kept[0]

        features.append({
            "type": "Feature",
            "properties": {
                "name": name,
                "label": f"NHS {name} ICB",
                "boroughs": members,
                "n_boroughs": len(members),
                "colour": COLOURS[name],
            },
            "geometry": mapping(merged),
        })
        note = f", {dropped} sliver{'s' if dropped != 1 else ''} dropped" if dropped else ""
        print(f"  {name:22s} {len(members):>2} boroughs -> {merged.geom_type}{note}")

    if dropped_total:
        print(f"\n{dropped_total} dissolve slivers dropped in total")

    # JSON rather than a JS global, because this layer is off by default and is
    # fetched on demand like the MSOA boundaries, not spliced in ahead of the
    # map. See ensureIcbLayer in index.html.
    payload = {
        "_comment": (
            "The five London Integrated Care Board footprints, dissolved from "
            "the borough outlines in boroughs.js by "
            "scripts/build_icb_boundaries.py. Do not hand edit: the geometry "
            "has to stay identical to the borough edges it is built from."
        ),
        "type": "FeatureCollection",
        "features": features,
    }
    OUT.write_text(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    kb = OUT.stat().st_size / 1024
    print(f"\nwrote {OUT.relative_to(ROOT)} ({kb:,.0f} kB, {len(features)} boards)")

    # The membership on its own, without any geometry. Focus by ICB has to know
    # which boroughs belong to a board before anybody has asked to see the
    # regions drawn, and 1 kB at startup is a great deal cheaper than 61.
    lookup = {
        "_comment": (
            "Borough to Integrated Care Board. Generated by "
            "scripts/build_icb_boundaries.py alongside icbs.json, from the same "
            "membership, so the focus control and the drawn regions can never "
            "disagree."
        ),
        "order": list(ICBS.keys()),
        "colours": COLOURS,
        # The full board name, so the focus control and the map tooltip say the
        # same thing and neither has to build it out of a short name.
        "labels": {name: f"NHS {name} ICB" for name in ICBS},
        "counts": {name: len(members) for name, members in ICBS.items()},
        "lad_to_icb": {b: name for name, members in ICBS.items() for b in members},
    }
    LOOKUP.write_text(
        json.dumps(lookup, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"wrote {LOOKUP.relative_to(ROOT)} "
          f"({LOOKUP.stat().st_size:,} bytes, {len(lookup['lad_to_icb'])} boroughs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
