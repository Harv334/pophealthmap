#!/usr/bin/env python3
"""Write the list of files a region pack serves into its meta/manifest.json.

The page reads it ("files", paths relative to the region's folder, such as
"ward_core.json" or "map/pcn.json") and does not request a file the list
leaves out, so a region without London's greenspaces, PCNs or output areas
shows no 404s in the console and loses no round trip on a missing
ward_core.json. A manifest with no list (London's) leaves every request as
it was.

Run last, once every file in the pack has been written: build-region.yml
runs it before packing, and build_england.py calls write_files() on the
England view.

    py scripts/write_pack_files.py data/regions/north-east [ENTRY ...]

With ENTRY names (files or folders inside the region's folder, as packed),
only those are listed, so the list matches the pack exactly.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Written by the pipeline beside the served files but never served.
SKIP_SUFFIXES = (".parquet", ".py", ".md", ".tmp", ".csv")


def served_files(root: Path, entries: list[str] | None = None) -> list[str]:
    out = []
    if entries:
        paths = []
        for e in entries:
            q = root / e
            paths += sorted(q.rglob("*")) if q.is_dir() else [q]
    else:
        paths = sorted(root.rglob("*"))
    for p in paths:
        if not p.is_file() or p.suffix in SKIP_SUFFIXES or p.name.startswith("."):
            continue
        rel = p.relative_to(root).as_posix()
        if rel.split("/")[0] in ("boundaries", "outcomes", "healthcare"):
            continue
        out.append(rel)
    return out


def write_files(root: Path, entries: list[str] | None = None) -> int:
    root = Path(root)
    mpath = root / "meta" / "manifest.json"
    try:
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}
    files = served_files(root, entries)
    if "meta/manifest.json" not in files:
        files.append("meta/manifest.json")
    manifest["files"] = sorted(files)
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return len(files)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: write_pack_files.py <region folder> [ENTRY ...]", file=sys.stderr)
        return 2
    n = write_files(Path(argv[1]), argv[2:] or None)
    print(f"write_pack_files: {n} files listed in {argv[1]}/meta/manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
