#!/usr/bin/env python3
"""
Check a region's built or published files before they go on the site.

A region build used to publish whatever it produced. fetch_all_data.py marks a
source that failed, or came back thin, as "failed" or "partial" in the
manifest and still exits 0, so a Fingertips or Nomis outage on refresh day
would have stripped indicators from that region (and from England, which is
built from the regions) for a month with nothing failing. And the packs built
before the Census counts method (17 October 2026) were still being served.

Checks, each an error unless noted:

  1. No source in meta/manifest.json is "failed" or "partial".
  2. map/indicators.js says census_method "counts", the method London uses.
  3. Every Local Health series London has (ft_ and a number, in London's
     data/map/indicators.js) is in this region's too, unless it is listed as a
     known gap, in "known_gaps" in scopes/<scope>.json or in indicators.js.
     A series from a source the region's pack was built before (its manifest
     has no record of that source, NEWER_SOURCES) is not required of it
     either: the pack cannot hold it until it is rebuilt, and its rebuild is
     checked in full.
  4. Against the pack it would replace (--previous): no indicator key that pack
     had has gone, and ward_data.json holds at least 90% as many values. The
     indicators taken off the map (RETIRED_KEYS in fetch_all_data.py) are left
     out of both counts, as a rebuilt pack no longer has them.

Usage:
  scripts/check_region_pack.py DIR [--scope NAME] [--previous DIR]
                               [--london ROOT] [--allow-sources a,b]

DIR is data/regions/<scope> after a build, or an unpacked region pack. Prints
each problem and exits 1 if there are any.
"""
from __future__ import annotations

import argparse
import ast
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOCAL_HEALTH = re.compile(r"^ft_\d+$")
MIN_SHARE = 0.9
# Sources added after packs were first built, and the file in London's build
# that names the Local Health series each one makes ("series", keyed by id).
NEWER_SOURCES = {
    "fingertips_msoa_extra": "data/outcomes/fingertips_msoa_extra_checks.json",
}


def newer_series(london: pathlib.Path, sources: dict) -> set:
    """ft_ keys London's newer sources make, for each such source this pack's
    manifest does not record (it was built before the source existed)."""
    out: set = set()
    for name, rel in NEWER_SOURCES.items():
        if name in (sources or {}):
            continue
        f = london / rel
        try:
            series = (json.loads(f.read_text(encoding="utf-8")) or {}).get("series") or {}
        except (OSError, ValueError):
            continue
        out |= {f"ft_{i}" for i in series}
    return out


def retired_keys() -> frozenset:
    """RETIRED_KEYS from fetch_all_data.py, read without importing it."""
    try:
        tree = ast.parse((ROOT / "fetch_all_data.py").read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return frozenset()
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "RETIRED_KEYS" for t in node.targets):
            v = node.value.args[0] if isinstance(node.value, ast.Call) and node.value.args else node.value
            return frozenset(ast.literal_eval(v))
    return frozenset()


RETIRED = retired_keys()


def read_stats(path: pathlib.Path) -> dict | None:
    """PH_IND_STATS from an indicators.js, or None if there is none."""
    if not path.exists():
        return None
    t = path.read_text(encoding="utf-8")
    i = t.find("var PH_IND_STATS = ")
    if i < 0:
        return None
    body = t[i + len("var PH_IND_STATS = "):]
    body = body[: body.rfind(";")] if ";" in body else body
    try:
        return json.loads(body)
    except ValueError:
        return None


def ward_values(path: pathlib.Path) -> tuple[set, int]:
    """The indicator keys in a ward_data.json, and how many values it holds,
    leaving out the retired ones."""
    if not path.exists():
        return set(), 0
    data = json.loads(path.read_text(encoding="utf-8"))
    keys, n = set(), 0
    for w in (data.get("wards") or {}).values():
        for k, v in ((w or {}).get("indicators") or {}).items():
            if k in RETIRED:
                continue
            keys.add(k)
            if v is not None and v != "":
                n += 1
    return keys, n


def check(pack: pathlib.Path, scope: str = "", previous: pathlib.Path | None = None,
          london: pathlib.Path | None = None, allow_sources=()) -> list[str]:
    problems: list[str] = []
    scope = scope or pack.name

    manifest = pack / "meta" / "manifest.json"
    m: dict = {}
    if not manifest.exists():
        problems.append("no meta/manifest.json")
    else:
        m = json.loads(manifest.read_text(encoding="utf-8"))
        for name, rec in sorted((m.get("sources") or {}).items()):
            status = (rec or {}).get("status", "")
            if status in ("failed", "partial") and name not in allow_sources:
                why = (rec.get("error") or rec.get("notes") or "").strip()[:200]
                problems.append(f"source {name} is {status}" + (f": {why}" if why else ""))

    stats = read_stats(pack / "map" / "indicators.js")
    if stats is None:
        problems.append("no PH_IND_STATS in map/indicators.js")
        stats = {}
    elif stats.get("census_method") != "counts":
        problems.append(f"census_method is {stats.get('census_method')!r}, not 'counts': "
                        "the pack predates the Census counts method and needs rebuilding")

    gaps = set(stats.get("known_gaps") or [])
    scope_file = ROOT / "scopes" / f"{scope}.json"
    if scope_file.exists():
        gaps |= set(json.loads(scope_file.read_text(encoding="utf-8")).get("known_gaps") or [])

    if london is not None:
        ls = read_stats(london / "data" / "map" / "indicators.js") or {}
        want = {k for k in (ls.get("stats") or {}) if LOCAL_HEALTH.match(k)}
        have = set((stats.get("stats") or {}).keys())
        missing = sorted(want - have - gaps - newer_series(london, m.get("sources") or {}))
        if missing:
            problems.append(f"{len(missing)} Local Health series London has are missing and not "
                            f"listed as known gaps: {', '.join(missing)}")

    if previous is not None and (previous / "ward_data.json").exists():
        old_keys, old_n = ward_values(previous / "ward_data.json")
        new_keys, new_n = ward_values(pack / "ward_data.json")
        lost = sorted(old_keys - new_keys)
        if lost:
            problems.append(f"{len(lost)} indicator(s) the published pack has would disappear: "
                            + ", ".join(lost))
        if old_n and new_n < old_n * MIN_SHARE:
            problems.append(f"ward_data.json would fall from {old_n:,} values to {new_n:,} "
                            f"({new_n / old_n:.0%}), more than a {1 - MIN_SHARE:.0%} drop")
    return problems


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("pack", type=pathlib.Path)
    p.add_argument("--scope", default="")
    p.add_argument("--previous", type=pathlib.Path)
    p.add_argument("--london", type=pathlib.Path,
                   help="London's site root, to check the Local Health series against")
    p.add_argument("--allow-sources", default="",
                   help="comma-separated sources whose failed or partial status is accepted")
    a = p.parse_args(argv)
    allow = tuple(s for s in a.allow_sources.split(",") if s)
    problems = check(a.pack, a.scope, a.previous, a.london, allow)
    name = a.scope or a.pack.name
    if problems:
        for x in problems:
            print(f"::error::region {name}: {x}")
        return 1
    print(f"region {name}: pack checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
