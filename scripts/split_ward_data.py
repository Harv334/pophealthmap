#!/usr/bin/env python3
"""Split ward_data.json into what the first paint needs and what it does not.

ward_data.json is 2.4 MB, 420 KB over the wire, and the map cannot draw a
single ward until all of it has arrived. Roughly 30% of it is the Fingertips
block: fifty ft_* series per ward. The opening view reads none of them. They
are only reached when somebody picks one of the Fingertips indicators out of
the menu, opens a ward's full profile, or goes into Query, Compare or Data
export.

So the file is written twice, cut on the one boundary that is a real seam
rather than a convenient one:

    data/ward_core.json   name, borough, Core20 flag, the per-category crime
                          breakdown, and the census, IMD, crime, transport,
                          fuel and claimant indicators. This is what the
                          loading screen waits for.
    data/ward_rest.json   the 50 ft_* series, fetched in the background once
                          the map is up.

ward_data.json itself is left alone. It is what the pipeline writes and what
every other consumer reads, and having the split derive from it rather than
replace it means a refresh cannot leave the two halves describing different
weeks.

Run after any refresh, before deploying. The workflow does this.
"""

import gzip
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "ward_data.json"
OUT_DIR = ROOT / "data"
CORE = OUT_DIR / "ward_core.json"
REST = OUT_DIR / "ward_rest.json"

# The seam. Everything a first paint can reach stays on the near side of it.
#
# Not a hand-listed set of keys: a list would have to be edited every time the
# pipeline adds a series, and the failure mode of forgetting is a blank figure
# in the panel rather than an error, which is the kind that ships. The prefix
# is the source, and the source is the thing that decides whether the opening
# view reads it.
DEFERRED_PREFIX = "ft_"

# crime_by_category is NOT deferred, though it looks like it should be: it is
# 23.6 KB gz and no panel reads it directly.
#
# It is read once, at load time, by injectCrimeCategories() in index.html,
# which flattens it into three ward indicators that ARE selectable from the
# overlay menu on first paint: crime_violence_12mo, crime_asb_12mo and
# crime_theft_12mo. Those three are derived on the client and exist nowhere in
# this file, so deferring their only source does not delay them, it removes
# them: the menu would offer three ordinary crime overlays that paint a blank
# map until the second fetch lands.
#
# 23.6 KB of the 188 KB is the price of the seam staying honest. Everything
# left on the far side is genuinely unreachable until somebody asks for it.
DEFERRED_FIELDS = ()


def gz_kb(obj):
    return len(gzip.compress(json.dumps(obj, separators=(",", ":")).encode())) / 1024


def main():
    if not SRC.exists():
        sys.exit(f"missing {SRC}. Run the pipeline first.")

    data = json.loads(SRC.read_text(encoding="utf-8"))
    wards = data.get("wards")
    if not isinstance(wards, dict) or not wards:
        sys.exit("ward_data.json has no 'wards' object. Refusing to write a split of nothing.")

    core_wards, rest_wards = {}, {}
    n_core_inds = n_rest_inds = 0

    for code, w in wards.items():
        inds = w.get("indicators") or {}
        core = {k: v for k, v in w.items() if k not in ("indicators",) + DEFERRED_FIELDS}
        core["indicators"] = {k: v for k, v in inds.items() if not k.startswith(DEFERRED_PREFIX)}
        core_wards[code] = core
        n_core_inds = max(n_core_inds, len(core["indicators"]))

        rest = {"indicators": {k: v for k, v in inds.items() if k.startswith(DEFERRED_PREFIX)}}
        for f in DEFERRED_FIELDS:
            if w.get(f):
                rest[f] = w[f]
        rest_wards[code] = rest
        n_rest_inds = max(n_rest_inds, len(rest["indicators"]))

    # Nothing may be lost between the two halves. Checked rather than assumed,
    # because the way this fails is a figure quietly missing from one ward.
    for code, w in wards.items():
        before = set((w.get("indicators") or {}).keys())
        after = set(core_wards[code]["indicators"]) | set(rest_wards[code]["indicators"])
        if before != after:
            sys.exit(f"{code}: {len(before - after)} indicators would be lost by the split")

    core_doc = {"wards": core_wards, "metadata": data.get("metadata")}
    rest_doc = {"wards": rest_wards}

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for path, doc in ((CORE, core_doc), (REST, rest_doc)):
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
        shutil.move(str(tmp), str(path))

    whole = gz_kb(data)
    print(f"wards          {len(wards)}")
    print(f"indicators     {n_core_inds} in core, {n_rest_inds} deferred")
    print(f"ward_data.json {whole:6.1f} KB gz   (left in place, unchanged)")
    print(f"ward_core.json {gz_kb(core_doc):6.1f} KB gz   <- the loading screen waits for this")
    print(f"ward_rest.json {gz_kb(rest_doc):6.1f} KB gz   <- fetched once the map is up")
    print(f"first paint     {whole - gz_kb(core_doc):6.1f} KB gz lighter")


if __name__ == "__main__":
    main()
