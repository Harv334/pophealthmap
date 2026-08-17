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
"""

from __future__ import annotations

import json
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


def read_borough_gj() -> dict:
    text = BOROUGHS.read_text(encoding="utf-8")
    marker = "var BOROUGH_GJ = "
    start = text.index(marker) + len(marker)
    return json.loads(text[start : text.rindex("}") + 1])


def main() -> int:
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
