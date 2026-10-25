"""scripts/check_region_pack.py on synthetic packs: what stops a region publishing.

    python3 tests/pipeline/test_region_pack.py
"""
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_region_pack as crp  # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        fails.append(name)


def pack(d, sources=None, method="counts", stats_keys=("imd_score", "ft_93283"), wards=None, gaps=None):
    d = pathlib.Path(d)
    (d / "meta").mkdir(parents=True, exist_ok=True)
    (d / "map").mkdir(parents=True, exist_ok=True)
    (d / "meta" / "manifest.json").write_text(json.dumps({"sources": sources or {
        "census": {"status": "ok"}, "fingertips_msoa": {"status": "ok"}, "custom": {"status": "skipped"}}}))
    st = {"stats": {k: {} for k in stats_keys}}
    if method:
        st["census_method"] = method
    if gaps:
        st["known_gaps"] = gaps
    (d / "map" / "indicators.js").write_text("// x\nvar PH_IND_STATS = " + json.dumps(st) + ";\n")
    w = wards if wards is not None else {f"W{i}": {"indicators": {"imd_score": i, "ft_93283": 70 + i}} for i in range(50)}
    (d / "ward_data.json").write_text(json.dumps({"wards": w, "metadata": {}}))
    return d


with tempfile.TemporaryDirectory() as t:
    t = pathlib.Path(t)
    london = t / "london"
    (london / "data" / "map").mkdir(parents=True)
    (london / "data" / "map" / "indicators.js").write_text(
        "var PH_IND_STATS = " + json.dumps({"census_method": "counts",
                                            "stats": {"imd_score": {}, "ft_93283": {}, "ft_93250": {}, "ptal_score": {}}}) + ";")

    good = pack(t / "good", stats_keys=("imd_score", "ft_93283", "ft_93250"))
    check("a healthy pack passes", crp.check(good, "good", None, london) == [], json.dumps(crp.check(good, "good", None, london)))

    bad = pack(t / "failed", sources={"census": {"status": "failed", "error": "Nomis 503"},
                                      "fingertips_msoa": {"status": "partial", "notes": "6 kept from last run"}},
               stats_keys=("imd_score", "ft_93283", "ft_93250"))
    p = crp.check(bad, "failed")
    check("a failed source stops it", any("census is failed: Nomis 503" in x for x in p), json.dumps(p))
    check("a partial source stops it", any("fingertips_msoa is partial" in x for x in p), json.dumps(p))
    check("an allowed source does not", not any("census" in x for x in crp.check(bad, "failed", allow_sources=("census",))))

    old = pack(t / "old", method=None, stats_keys=("imd_score", "ft_93283", "ft_93250"))
    p = crp.check(old, "old")
    check("a pack without census_method 'counts' is stopped", any("census_method" in x for x in p), json.dumps(p))

    thin = pack(t / "thin", stats_keys=("imd_score", "ft_93283"))
    p = crp.check(thin, "thin", None, london)
    check("a Local Health series London has is required", any("ft_93250" in x for x in p) and not any("ptal" in x for x in p), json.dumps(p))
    listed = pack(t / "listed", stats_keys=("imd_score", "ft_93283"), gaps=["ft_93250"])
    check("unless it is listed as a known gap", crp.check(listed, "listed", None, london) == [])

    # London gains low birth weight from a source added later
    # (fingertips_msoa_extra). A pack built before that source existed is not
    # held back for lacking it; a pack built since must have it or list it.
    (london / "data" / "outcomes").mkdir(parents=True)
    (london / "data" / "outcomes" / "fingertips_msoa_extra_checks.json").write_text(json.dumps(
        {"series": {"93092": {"ships": True, "kind": "publisher"}, "93234": {"ships": False, "kind": "publisher"}}}))
    (london / "data" / "map" / "indicators.js").write_text(
        "var PH_IND_STATS = " + json.dumps({"census_method": "counts",
                                            "stats": {"imd_score": {}, "ft_93283": {}, "ft_93250": {}, "ft_93092": {}}}) + ";")
    before = pack(t / "before", stats_keys=("imd_score", "ft_93283", "ft_93250"))
    check("a pack built before the new source is not held back for its series",
          crp.check(before, "before", None, london) == [], json.dumps(crp.check(before, "before", None, london)))
    since = pack(t / "since", sources={"census": {"status": "ok"}, "fingertips_msoa_extra": {"status": "ok"}},
                 stats_keys=("imd_score", "ft_93283", "ft_93250"))
    p = crp.check(since, "since", None, london)
    check("a pack built since must have the series", any("ft_93092" in x for x in p), json.dumps(p))
    gap = pack(t / "gap", sources={"census": {"status": "ok"}, "fingertips_msoa_extra": {"status": "skipped"}},
               stats_keys=("imd_score", "ft_93283", "ft_93250"), gaps=["ft_93092"])
    check("or list it as a known gap, and a skipped source passes", crp.check(gap, "gap", None, london) == [],
          json.dumps(crp.check(gap, "gap", None, london)))
    (london / "data" / "map" / "indicators.js").write_text(
        "var PH_IND_STATS = " + json.dumps({"census_method": "counts",
                                            "stats": {"imd_score": {}, "ft_93283": {}, "ft_93250": {}, "ptal_score": {}}}) + ";")

    prev = pack(t / "prev", stats_keys=("imd_score", "ft_93283", "ft_93250"),
                wards={f"W{i}": {"indicators": {"imd_score": i, "ft_93283": 1, "ft_93250": 2}} for i in range(50)})
    p = crp.check(good, "good", prev, london)
    check("an indicator that would disappear stops it", any("ft_93250" in x and "disappear" in x for x in p), json.dumps(p))
    sparse = pack(t / "sparse", stats_keys=("imd_score", "ft_93283", "ft_93250"),
                  wards={f"W{i}": {"indicators": {"imd_score": i, "ft_93283": (1 if i < 30 else None), "ft_93250": 2}} for i in range(50)})
    p = crp.check(sparse, "sparse", prev, london)
    check("losing more than 10% of ward values stops it", any("150 values to 130" in x for x in p), json.dumps(p))
    check("the same pack again passes against itself", crp.check(prev, "prev", prev, london) == [])
    check("the command exits 1 on a problem and 0 without",
          crp.main([str(old)]) == 1 and crp.main([str(good), "--london", str(london)]) == 0)

print()
print(("FAILED: " + "; ".join(fails)) if fails else "all checks passed")
raise SystemExit(1 if fails else 0)
