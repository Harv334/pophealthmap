#!/usr/bin/env python3
"""Write the published LSOA and MSOA figures in column form.

lsoa_data.json is one object per LSOA, each repeating the same sixty key
names: 8 MB to download and parse for the first Census indicator, most of it
the names. In column form, one list of codes and one list of values per
indicator, the same figures are about a fifth of the size, the form the
output area files already use.

The repository keeps the record form, which the pipeline, the region checks
and the other scripts read. Only the copies in the assembled site are
rewritten: scripts/build_site.sh runs this on _site, and the page
(_expandColumns in index.html) turns the columns back into the records its
lookups expect. A file already in column form is left as it is.

    {"format": "columns", "codes": [code, ...],
     "cols": {key: [value or null, ...], ...}}

A figure an area does not have is null in its column and left out when the
page rebuilds the record, so the rebuilt records match the originals exactly.

Usage: scripts/compact_tables.py OUT
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

NAMES = ("lsoa_data.json", "msoa_data.json")


def to_columns(records: dict) -> dict:
    codes = list(records)
    keys: list[str] = []
    seen = set()
    for code in codes:
        for k in records[code] or {}:
            if k not in seen:
                seen.add(k)
                keys.append(k)
    cols = {k: [(records[c] or {}).get(k) for c in codes] for k in keys}
    return {"format": "columns", "codes": codes, "cols": cols}


def from_columns(doc: dict) -> dict:
    """The page's _expandColumns, for the round-trip check."""
    out = {}
    cols = doc["cols"]
    for i, code in enumerate(doc["codes"]):
        out[code] = {k: v[i] for k, v in cols.items() if v[i] is not None}
    return out


def compact(path: Path) -> tuple[int, int] | None:
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(records, dict) or records.get("format") == "columns":
        return None
    if not all(isinstance(v, dict) and not any(isinstance(x, (dict, list)) for x in v.values())
               for v in records.values()):
        return None  # not flat records: leave it alone
    if any(x is None for v in records.values() for x in v.values()):
        return None  # a null would not survive the round trip
    doc = to_columns(records)
    if from_columns(doc) != records:
        raise SystemExit(f"compact_tables: {path} does not survive the round trip")
    before = path.stat().st_size
    path.write_text(json.dumps(doc, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    return before, path.stat().st_size


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: compact_tables.py OUT", file=sys.stderr)
        return 2
    out = Path(argv[1])
    folders = [out] + sorted(p for p in (out / "data" / "regions").glob("*") if p.is_dir())
    for d in folders:
        for name in NAMES:
            p = d / name
            if not p.is_file():
                continue
            # Never through a link to a file outside the site being built.
            if not p.resolve().is_relative_to(out.resolve()):
                print(f"compact_tables: {p} links outside {out}, left alone", file=sys.stderr)
                continue
            r = compact(p)
            if r:
                print(f"compact_tables: {p.relative_to(out)} {r[0] / 1e6:.1f} MB to {r[1] / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
