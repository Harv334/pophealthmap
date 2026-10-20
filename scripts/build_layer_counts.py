"""Count the records in each lazily loaded layer, ahead of loading it.

The point layers and the LSOA boundaries are fetched on demand now, which left
the counts beside them blank until the reader ticked the box. Blank was honest
but unhelpful: "1,737" beside Pharmacies is the number somebody wants before
deciding whether to switch the layer on, not after.

So the counts are measured here, from the committed payloads, and written to a
small file the map reads at startup. The whole thing is well under a kilobyte
against the 13.6 MB it saves loading.

The counting rules mirror the loaders in index.html exactly, and they have to.
A number here that disagrees with the number the layer shows once loaded would
be worse than no number at all:

  - the six point layers count records with a truthy lat and lng, which is the
    `if (!p.lat || !p.lng) return;` guard in each load function,
  - VCSE counts what it can pin, `sc === 'explicit' && hq && lat != null &&
    lng != null`, the region-wide charities based in the region that only the
    Directory lists (`sc === 'london_wide'`, same test otherwise), and the
    declared total, which is vcseCountsOf in index.html,
  - CICs report every record, which is what wireCICs writes,
  - greenspaces and LSOAs are feature collections, so it is the feature count.

Run after any data refresh, and check the numbers against the console lines
the loaders print:

    py scripts/build_layer_counts.py
    py scripts/build_layer_counts.py --region north-east

With --region the payloads are read from data/regions/<id>/ (where map/ and
meta/ sit directly under the region's folder) and the counts are written to
data/regions/<id>/meta/layer_counts.json. build-region.yml runs it for each
region pack, and build_england.py calls count_layers() on the England view.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "meta" / "layer_counts.json"


def load(name: str):
    path = ROOT / name
    if not path.exists():
        print(f"  {name}: missing, skipped", file=sys.stderr)
        return None
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def mappable(rows) -> int:
    """The `if (!p.lat || !p.lng) return;` guard the six point loaders share.
    Falsy rather than None, so a 0 counts as missing exactly as it does there."""
    if not isinstance(rows, list):
        return 0
    return sum(1 for r in rows if isinstance(r, dict) and r.get("lat") and r.get("lng"))


def features(gj) -> int:
    if isinstance(gj, dict):
        return len(gj.get("features") or [])
    if isinstance(gj, list):
        return len(gj)
    return 0


def read_js_const(name: str, var: str):
    """Parse one of the data/map/*.js files, which are JS assignments rather
    than JSON."""
    path = ROOT / name
    if not path.exists():
        print(f"  {name}: missing, skipped", file=sys.stderr)
        return None
    text = path.read_text(encoding="utf-8")
    marker = f"var {var} = "
    if marker not in text:
        return None
    start = text.index(marker) + len(marker)
    return json.loads(text[start : text.rindex("}") + 1])


def count_layers(root: Path | None = None, region: bool = False) -> dict[str, int]:
    """The counts for one site root: the repository (London) or a region's
    folder, where map/ sits directly under it rather than under data/."""
    global ROOT
    saved = ROOT
    if root is not None:
        ROOT = Path(root)
    try:
        return _count(region)
    finally:
        ROOT = saved


def _count(region: bool) -> dict[str, int]:
    mp = "map/" if region else "data/map/"
    counts: dict[str, int] = {}

    print("point layers (records with a usable lat and lng):")
    for key, filename in (
        ("dental", "dental_practices.json"),
        ("pharmacy", "pharmacies.json"),
    ):
        rows = load(filename)
        if rows is None:
            continue
        counts[key] = mappable(rows)
        print(f"  {key:10s} {counts[key]:>7,}  of {len(rows):>7,} in the file")

    vcse = load("vcse_data.json")
    if vcse is not None:
        pinned = sum(
            1
            for r in vcse
            if isinstance(r, dict)
            and r.get("sc") == "explicit"
            and r.get("hq")
            and r.get("lat") is not None
            and r.get("lng") is not None
            and not r.get("np")
        )
        # Based here but not pinned: a council office for an address, or a
        # charity that only holds land (np, see charity_no_pin).
        nopin = sum(
            1
            for r in vcse
            if isinstance(r, dict)
            and r.get("sc") == "explicit"
            and r.get("hq")
            and r.get("lat") is not None
            and r.get("lng") is not None
            and r.get("np")
        )
        wide = sum(
            1
            for r in vcse
            if isinstance(r, dict)
            and r.get("sc") == "london_wide"
            and r.get("hq")
            and r.get("lat") is not None
            and r.get("lng") is not None
        )
        counts["vcse"] = pinned
        counts["vcse_wide"] = wide
        counts["vcse_nopin"] = nopin
        counts["vcse_total"] = len(vcse)
        print(f"\nVCSE:      {pinned:>7,} mappable, {wide:,} region-wide in the Directory, "
              f"of {len(vcse):,} declared")

    grn = load("greenspaces.geojson")
    if grn is not None:
        counts["greenspace"] = features(grn)
        print(f"greenspace {counts['greenspace']:>7,}")

    # MSOA boundaries, so the layer row has a count before the lazy MSOA
    # payload is fetched (it is only fetched once MSOAs are used).
    msoa = load(mp + "msoa_boundaries.json")
    if msoa is not None:
        counts["msoa"] = features(msoa)
        print(f"MSOAs:     {counts['msoa']:>7,}")

    icb = load(mp + "icbs.json")
    if icb is not None:
        counts["icb"] = features(icb)
        print(f"ICBs:      {counts['icb']:>7,}")

    # LSOA total, and the Core20 subset. The Core20 caption under that toggle
    # can only be counted from the deciles, so without this it stayed blank for
    # anybody who never opened an LSOA.
    lsoa = read_js_const(mp + "lsoa_imd.js", "LSOA_IMD")
    if lsoa:
        feats = lsoa.get("features") or []
        counts["lsoa"] = len(feats)
        counts["core20"] = sum(
            1
            for f in feats
            if (f.get("properties") or {}).get("imd_decile") in (1, 2)
        )
        print(f"LSOAs:     {counts['lsoa']:>7,}")
        print(f"Core20:    {counts['core20']:>7,}  (IMD deciles 1 and 2)")
    return counts


def write_counts(counts: dict[str, int], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_comment": (
            "Generated by scripts/build_layer_counts.py. Record counts for the "
            "layers the map loads on demand, so their counts can be shown "
            "before the payload is fetched. Re-run after a data refresh."
        ),
        "counts": counts,
    }
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {out}, {out.stat().st_size} bytes")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Count the lazily loaded layers.")
    ap.add_argument("--region", help="a region id under data/regions/ (default: London at the root)")
    args = ap.parse_args(argv)
    if args.region:
        root = ROOT / "data" / "regions" / args.region
        if not root.is_dir():
            print(f"no region folder at {root}", file=sys.stderr)
            return 1
        write_counts(count_layers(root, region=True), root / "meta" / "layer_counts.json")
    else:
        write_counts(count_layers(), OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
