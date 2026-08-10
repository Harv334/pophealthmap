"""
Read the map data globals that index.html loads.

These used to be inline `const X = {...};` literals inside index.html, and
several scripts pulled them back out with a regex. They now live in
data/map/*.js as `var X = {...};`, so those regexes find nothing. Import from
here instead of parsing index.html.

    from map_data import read_blob
    lsoa_imd = read_blob("LSOA_IMD")
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
MAP_DIR = REPO_ROOT / "data" / "map"

# Must match MAP_BLOBS in fetch_all_data.py and the <script src> tags in
# index.html. Kept as a plain dict so this module has no import cost.
BLOBS = {
    "GJ":         "wards.js",
    "GPS":        "gp_practices.js",
    "HOSP":       "hospitals.js",
    "LSOA_IMD":   "lsoa_imd.js",
    "BOROUGH_GJ": "boroughs.js",
}


def blob_path(name: str) -> Path:
    if name not in BLOBS:
        raise KeyError(f"unknown map blob {name!r}; known: {', '.join(sorted(BLOBS))}")
    return MAP_DIR / BLOBS[name]


def read_blob(name: str):
    """Parse data/map/<file>.js and return the assigned value."""
    path = blob_path(name)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. It is generated or committed map data; see "
            f"data/map/ and fetch_all_data.py export_map_blobs()."
        )
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^var\s+(\w+)\s*=\s*(.+);\s*$", text, re.M | re.S)
    if not m:
        raise ValueError(f"{path}: expected a single `var NAME = <json>;` assignment")
    if m.group(1) != name:
        raise ValueError(f"{path}: assigns {m.group(1)}, expected {name}")
    return json.loads(m.group(2))


def write_blob(name: str, payload, description: str = "") -> Path:
    """Write a blob back in the same form the loader expects."""
    path = blob_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    header = f"// {description}\n" if description else ""
    path.write_text(
        f"{header}// Loaded by index.html as a classic script before the main block,\n"
        f"// so this global is defined by the time any code reads it.\n"
        f"var {name} = {body};\n", encoding="utf-8")
    return path
