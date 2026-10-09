"""Write scopes/<region>.json for every English region from the ONS lookup.

A scope is the list of local authorities one pipeline run builds (see the
SCOPE block in fetch_all_data.py). London's was typed by hand and is left
alone; this writes the other eight from ONS's "Local Authority District to
Region" lookup, so nobody types three hundred codes.

The lookup is found rather than pinned: ONS republish it each year under a new
service name (LAD24_RGN24_EN_LU, LAD25_RGN25_EN_LU, ...), so this lists the
ONS ArcGIS services and takes the newest one with that shape.

Run where the network is open (the "Make region scopes" workflow does):
    python scripts/make_scopes.py            # write all eight
    python scripts/make_scopes.py --check    # compare with what is committed
"""
import argparse, json, re, sys
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[1]
ARCGIS = "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services"

REGIONS = {
    "E12000001": "north-east",
    "E12000002": "north-west",
    "E12000003": "yorkshire-and-the-humber",
    "E12000004": "east-midlands",
    "E12000005": "west-midlands",
    "E12000006": "east-of-england",
    "E12000007": "london",
    "E12000008": "south-east",
    "E12000009": "south-west",
}


def newest_lookup() -> str:
    r = requests.get(ARCGIS, params={"f": "json"}, timeout=60)
    r.raise_for_status()
    best = None
    for svc in r.json().get("services", []):
        m = re.fullmatch(r"LAD(\d\d)_RGN(\d\d)_EN_LU(?:_v(\d+))?", svc.get("name", ""))
        if m and svc.get("type") == "FeatureServer":
            key = (int(m.group(1)), int(m.group(3) or 0))
            if best is None or key > best[0]:
                best = (key, svc["name"])
    if not best:
        sys.exit("No LADyy_RGNyy_EN_LU service found on the ONS ArcGIS server.")
    return best[1]


def fetch_rows(service: str) -> list:
    url = f"{ARCGIS}/{service}/FeatureServer/0/query"
    rows, offset = [], 0
    while True:
        r = requests.get(url, params={"where": "1=1", "outFields": "*", "f": "json",
                                      "resultOffset": offset, "resultRecordCount": 1000},
                         timeout=60)
        r.raise_for_status()
        feats = r.json().get("features", [])
        rows += [f["attributes"] for f in feats]
        if len(feats) < 1000:
            return rows
        offset += len(feats)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="compare, do not write")
    args = ap.parse_args()

    service = newest_lookup()
    rows = fetch_rows(service)
    by_region: dict = {}
    names: dict = {}
    for row in rows:
        lad = next((v for k, v in row.items() if re.fullmatch(r"LAD\d\dCD", k)), None)
        lnm = next((v for k, v in row.items() if re.fullmatch(r"LAD\d\dNM", k)), None)
        rgn = next((v for k, v in row.items() if re.fullmatch(r"RGN\d\dCD", k)), None)
        rnm = next((v for k, v in row.items() if re.fullmatch(r"RGN\d\dNM", k)), None)
        if lad and rgn and lad.startswith("E"):
            by_region.setdefault(rgn, {})[lad] = lnm
            names[rgn] = rnm
    print(f"{service}: {sum(len(v) for v in by_region.values())} authorities in "
          f"{len(by_region)} regions")

    for code, slug in REGIONS.items():
        lads = by_region.get(code, {})
        if not lads:
            sys.exit(f"{code} ({slug}) not in {service}")
        path = REPO / "scopes" / f"{slug}.json"
        doc = {
            "name": names.get(code) or slug.replace("-", " ").title(),
            "region_code": code,
            "note": f"The {len(lads)} local authorities of {names.get(code) or slug} "
                    f"(ONS {code}), from {service}.",
            "lads": [[n, c] for c, n in sorted(lads.items())],
        }
        if path.exists():
            have = {c for _, c in json.loads(path.read_text(encoding="utf-8"))["lads"]}
            diff = have ^ set(lads)
            print(f"  {slug:26} {len(lads):3} authorities"
                  + (f"  DIFFERS from committed: {sorted(diff)}" if diff else "  matches committed"))
        else:
            print(f"  {slug:26} {len(lads):3} authorities  new")
        if args.check or slug == "london" or path.exists():
            continue   # London is curated; existing scopes are only checked
        path.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
