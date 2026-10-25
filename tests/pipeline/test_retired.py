"""The indicators taken off the map in October 2026 (RETIRED_KEYS): left out of
every file the pipeline writes, of the Custom area counts, and not a reason to
stop a region pack that no longer has them. index.html's own list matches.

    python3 tests/pipeline/test_retired.py
"""
import importlib.util
import json
import pathlib
import re
import sys
import tempfile

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1].parent
spec = importlib.util.spec_from_file_location("p", ROOT / "fetch_all_data.py")
p = importlib.util.module_from_spec(spec); sys.modules["p"] = p; spec.loader.exec_module(p)
sys.path.insert(0, str(ROOT / "scripts"))
import check_region_pack as crp  # noqa: E402

FAILS = []


def check(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + ("" if cond else f"   {detail}"))
    if not cond:
        FAILS.append(label)


R = p.RETIRED_KEYS
print("=== the list ===")
check("24 keys, one of each pair that repeated another", len(R) == 24, str(len(R)))
check("OHID's LA child poverty stays until DWP's ward figures come", "ft_child_poverty_low_income" not in R)
check("the kept half of each pair is not on it",
      not R & {"census_white_pct", "imd_decile", "imd_score", "adult_skills_score", "no2_ugm3", "pm25_ugm3",
               "census_english_hh_none_pct", "claimant_rate_pct", "crime_rate_per_1000", "idaci_pct",
               "census_hh_deprived_2plus_pct", "ft_93107", "fuel_poverty_pct", "census_over65_pct",
               "ft_93283", "hle_female", "saie_total_income", "saie_net_ahc", "samhi_index"})

print("=== the files the map reads ===")
wards = {"E05000001": {"name": "A", "indicators": {"imd_score": 30.1, "imd_decile_mean": 2.4, "sape_population": 9000,
                                                   "census_population": 8800, "crime_total": 400}}}
flat = {"E01000001": {"imd_decile": 2, "idaci_decile": 3, "idaci_pct": 31.0, "pm10_ugm3": 18.0, "pm25_ugm3": 9.0}}
la = {"E09000002": {"ft_life_expectancy_male": 77.8, "ft_smoking_prevalence_adults": 11.0,
                    "ft_notes": {"ft_life_expectancy_male": "x", "ft_smoking_prevalence_adults": "y"}, "ft_from": {}}}
p.drop_retired(wards, nested=True); p.drop_retired(flat); p.drop_retired(la)
check("a ward keeps its other figures and loses the retired ones",
      wards["E05000001"]["indicators"] == {"imd_score": 30.1, "census_population": 8800}, str(wards))
check("an LSOA keeps its own decile and loses the IDACI decile and PM10",
      flat["E01000001"] == {"imd_decile": 2, "idaci_pct": 31.0, "pm25_ugm3": 9.0}, str(flat))
check("an LA loses the LA life expectancy and its note, and keeps the rest",
      la["E09000002"] == {"ft_smoking_prevalence_adults": 11.0, "ft_notes": {"ft_smoking_prevalence_adults": "y"},
                          "ft_from": {}}, str(la))

msoa = {"E02000001": {"saie_net_bhc": 30000, "saie_net_bhc_lci": 28000, "saie_net_bhc_uci": 32000,
                       "saie_net_ahc": 25000, "saie_net_ahc_lci": 23000, "ft_93097": 4.1, "ft_93097_vs": "H"}}
ward = {"E05000002": {"indicators": {"ft_93097": 3.0, "ft_93250": 101.0}, "cover": {"ft_93097": 85, "ft_93250": 85}}}
p.drop_retired(msoa); p.drop_retired(ward, nested=True)
check("an MSOA loses a retired figure's 95% limits and OHID's mark, and keeps the kept income's limits",
      msoa["E02000001"] == {"saie_net_ahc": 25000, "saie_net_ahc_lci": 23000}, str(msoa))
check("a ward loses a retired figure's coverage and keeps the rest",
      ward["E05000002"] == {"indicators": {"ft_93250": 101.0}, "cover": {"ft_93250": 85}}, str(ward))

counts = pd.DataFrame({"LSOA21CD": ["E01000001"],
                       "census_white_pct__num": [50], "census_white_pct__den": [100],
                       "census_non_white_pct__num": [50], "census_non_white_pct__den": [100]})
cc = p.lsoa_census_counts(counts, ["E01000001"])
check("Custom area's Census counts leave out Non-White %", cc.get("keys") == ["census_white_pct"]
      and cc["rows"]["E01000001"] == [50, 100], str(cc))
src = (ROOT / "fetch_all_data.py").read_text(encoding="utf-8")
check("the output area files take their keys past the same list",
      'keys = [c for c in frame.columns if c.startswith("census_") and c not in RETIRED_KEYS]' in src)
check("export_all clears all four payloads before writing them",
      re.search(r'drop_retired\(ward_data\.get\("wards", \{\}\), nested=True\)\s+for recs in \(lsoa_data, msoa_data, boro_data\):\s+drop_retired\(recs\)', src) is not None)
check("and the ward file's note of how each IoD figure was weighted",
      re.search(r'drop_retired\(recs\)\s+weights = \(ward_data\.get\("metadata"\) or \{\}\)\.get\("iod_weights"\)', src) is not None)

print("=== a region pack built since ===")
with tempfile.TemporaryDirectory() as t:
    def pack(d, ind):
        d = pathlib.Path(t) / d
        (d / "meta").mkdir(parents=True); (d / "map").mkdir()
        (d / "meta" / "manifest.json").write_text(json.dumps({"sources": {"census": {"status": "ok"}}}))
        (d / "map" / "indicators.js").write_text("var PH_IND_STATS = " + json.dumps({"census_method": "counts", "stats": {}}) + ";\n")
        (d / "ward_data.json").write_text(json.dumps({"wards": {f"W{i}": {"indicators": dict(ind)} for i in range(40)}}))
        return d
    keep = {f"k{i}": i for i in range(8)}
    old = pack("old", dict(keep, **{k: 1 for k in sorted(R)[:12]}))
    new = pack("new", keep)
    probs = crp.check(new, scope="x", previous=old)
    check("a pack rebuilt without the retired keys is not stopped for losing them, or for having fewer values",
          probs == [], str(probs))
    gone = pack("gone", {k: v for k, v in keep.items() if k != "k0"})
    probs = crp.check(gone, scope="x", previous=old)
    check("but losing any other indicator still stops it", any("k0" in x for x in probs), str(probs))

print("=== index.html ===")
html = (ROOT / "index.html").read_text(encoding="utf-8")
m = re.search(r"const PH_RETIRED = new Set\(\[(.*?)\]\);", html, re.S)
page = set(re.findall(r"'([a-z0-9_]+)'", m.group(1))) if m else set()
check("the page's list is the pipeline's, for region packs built before", page == set(R),
      f"page only: {sorted(page - set(R))}; pipeline only: {sorted(set(R) - page)}")
left = [k for k in sorted(R) if re.search(r'<option value="%s"' % k, html) or re.search(r"^\s*%s\s*:" % k, html, re.M)]
check("no picker option or registry entry is left for any of them", not left, str(left))

print(f"\n{'All checks passed' if not FAILS else f'{len(FAILS)} failed'}")
sys.exit(1 if FAILS else 0)
