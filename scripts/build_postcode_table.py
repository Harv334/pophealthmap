#!/usr/bin/env python3
"""Every London postcode, with the figures published for the area it sits in.

A one-off extract, not part of the map. Nothing here is served by the site.

    python scripts/build_postcode_table.py                 # native metrics, readable
    python scripts/build_postcode_table.py --columns all   # every metric held
    python scripts/build_postcode_table.py --columns none  # geography and IMD only
    python scripts/build_postcode_table.py --terminated    # include retired postcodes

Writes three files beside the CSV, because a spreadsheet has nowhere to put a
caveat and one that travels separately does not travel:

    <name>.csv              the table
    <name>_dictionary.csv   every column, what it means, its source and year
    <name>_READ_ME.txt      the licence, and what these figures cannot say

## Where each column comes from

ONSPD supplies the geography: the postcode, its LSOA and its ward. The figures
come from this repo's own lsoa_data.json and ward_data.json, and the names from
lsoa_imd.js, so the output cannot disagree with the map about what London is or
what a ward is called.

The ward is ONSPD's, and that was measured rather than assumed. ONSPD Feb 2026
carries wd25cd where these wards are WD24, so an earlier build derived the ward
from the LSOA to avoid crossing two vintages. There is no gap to avoid: wd25cd
matched a known ward code for 180,982 of the 180,983 live London postcodes.
Deriving it from the LSOA instead was wrong for 13,869 postcodes, 7.66% of
London, because LSOAs cross ward boundaries and each is best-fitted to one
ward. The City of London was worst hit: its 25 wards share a handful of LSOAs,
so 20 of them came out with no postcodes at all while their postcodes were
labelled with a neighbouring ward.

Vintage matters on the LSOA too: lsoa21cd, never lsoa11cd. London had 4,835
LSOAs in 2011 and has 4,994 now.

"London" means "the postcode's LSOA is one of the 4,994 this project holds", so
no separate region filter can disagree with the map.

## Why the default is 46 metrics and not 60

lsoa_data.json holds 60 figures per LSOA, but only 46 are published at LSOA by
the body that produced them. The other 14 are computed onto the LSOA by this
pipeline from something that is not LSOA-shaped, and are excluded by default
because a postcode-level file is the wrong place to pass on an estimate as
though it were a measurement:

    air quality (3)     Defra's PCM model on a 1 km grid, sampled per LSOA
    green/blue (7)      Defra table is per Output Area; rolled up to LSOA here
    transport (4)       TfL stop and station points, measured from the LSOA
                        centroid, so it describes the centroid, not the area
    claimant_rate_pct   the count over a population denominator

Each was checked against the function in fetch_all_data.py that writes it,
and each call was rechecked by a second reader. Two results were not what the
metric name suggests: ptal_score IS native, because the GLA LSOA Atlas publishes
a ready-made per-LSOA column, and claimant_count is native while the rate beside
it is not. --columns all includes the excluded 14, suffixed _modelled.
"""

import argparse
import csv
import io
import json
import pathlib
import re
import sys
import zipfile

REPO = pathlib.Path(__file__).resolve().parent.parent
ONSPD_ZIP = REPO / ".cache" / "onspd" / "ONSPD_FEB_2026_UK.zip"
ONSPD_MEMBER = "Data/ONSPD_FEB_2026_UK.csv"

# key -> (column heading, source, year). Headings and sources are the map's own,
# read out of CATS and OV_META so the spreadsheet and the site call the same
# figure the same thing.
NATIVE = [
    ("imd_score",       "IMD score",                             "MHCLG IMD 2025", "2025"),
    ("imd_decile",      "IMD decile (1 = most deprived)",        "MHCLG IMD 2025", "2025"),
    ("imd_rank",        "IMD rank (1 = most deprived in England)", "MHCLG IMD 2025", "2025"),
    ("income_score",       "IMD domain: income deprivation",         "MHCLG IMD 2025", "2025"),
    ("employment_score",   "IMD domain: employment deprivation",     "MHCLG IMD 2025", "2025"),
    ("education_score",    "IMD domain: education and skills", "MHCLG IMD 2025", "2025"),
    ("health_score",       "IMD domain: health and disability",      "MHCLG IMD 2025", "2025"),
    ("crime_score",        "IMD domain: crime",                      "MHCLG IMD 2025", "2025"),
    ("barriers_score",     "IMD domain: barriers to housing and services", "MHCLG IMD 2025", "2025"),
    ("environment_score",  "IMD domain: living environment",         "MHCLG IMD 2025", "2025"),

    ("census_population",      "Population",                    "Census 2021 (TS001)", "2021"),
    ("census_under5_pct",      "Under 5 (%)",                   "Census 2021 (TS007A)", "2021"),
    ("census_under16_pct",     "Under 16 (%)",                  "Census 2021 (TS007A)", "2021"),
    ("census_working_age_pct", "Working age 16-64 (%)",         "Census 2021 (TS007A)", "2021"),
    ("census_over65_pct",      "Aged 65 and over (%)",          "Census 2021 (TS007A)", "2021"),
    ("census_over85_pct",      "Aged 85 and over (%)",          "Census 2021 (TS007A)", "2021"),

    ("census_white_pct",         "White (%)",                   "Census 2021 (TS021)", "2021"),
    ("census_asian_pct",         "Asian or Asian British (%)",  "Census 2021 (TS021)", "2021"),
    ("census_black_pct",         "Black or Black British (%)",  "Census 2021 (TS021)", "2021"),
    ("census_mixed_pct",         "Mixed or multiple (%)",       "Census 2021 (TS021)", "2021"),
    ("census_other_ethnic_pct",  "Other ethnic group (%)",      "Census 2021 (TS021)", "2021"),
    ("census_non_white_pct",     "Not White (%)",               "Census 2021 (TS021)", "2021"),
    ("census_born_outside_uk_pct", "Born outside the UK (%)",   "Census 2021 (TS004)", "2021"),
    ("census_english_hh_all_pct",  "Households where all adults speak English as a first language (%)", "Census 2021 (TS025)", "2021"),
    ("census_english_hh_none_pct", "Households where no adults speak English as a first language (%)",  "Census 2021 (TS025)", "2021"),

    ("census_good_health_pct",  "Good or very good health (%)", "Census 2021 (TS037)", "2021"),
    ("census_bad_health_pct",   "Bad or very bad health (%)",   "Census 2021 (TS037)", "2021"),
    ("census_disability_any_pct", "Disabled - any limitation (%)", "Census 2021 (TS038)", "2021"),
    ("census_disability_lot_pct", "Disabled - limited a lot (%)",  "Census 2021 (TS038)", "2021"),
    ("census_provides_unpaid_care_pct", "Provides unpaid care (%)", "Census 2021 (TS039)", "2021"),

    ("census_owned_pct",           "Owner occupied (%)",        "Census 2021 (TS054)", "2021"),
    ("census_social_rented_pct",   "Social rented (%)",         "Census 2021 (TS054)", "2021"),
    ("census_private_rented_pct",  "Private rented (%)",        "Census 2021 (TS054)", "2021"),
    ("census_housing_deprived_pct", "Deprived in at least one dimension (%)", "Census 2021 (TS044)", "2021"),

    ("census_no_car_pct",          "No car or van (%)",         "Census 2021 (TS045)", "2021"),
    ("census_car_to_work_pct",     "Travel to work by car or van (%)", "Census 2021 (TS061)", "2021"),
    ("census_public_transport_pct", "Travel to work by public transport (%)", "Census 2021 (TS061)", "2021"),
    ("census_active_travel_pct",   "Walk or cycle to work (%)", "Census 2021 (TS061)", "2021"),

    ("census_unemployed_pct",      "Unemployed (%)",            "Census 2021 (TS066)", "2021"),
    ("census_higher_managerial_pct",    "Higher managerial occupations (%)", "Census 2021 (TS062)", "2021"),
    ("census_routine_semi_routine_pct", "Routine or semi-routine occupations (%)", "Census 2021 (TS062)", "2021"),
    ("census_no_qual_pct",         "No qualifications (%)",     "Census 2021 (TS067)", "2021"),
    ("census_level4_qual_pct",     "Level 4 qualifications or above (%)", "Census 2021 (TS067)", "2021"),

    ("claimant_count",   "Claimant count",                      "NOMIS NM_162", "latest month"),
    ("fuel_poverty_pct", "Fuel poverty (%)",                    "DESNZ sub-regional LILEE", "2022"),
    ("ptal_score",       "Transport accessibility (PTAL 0-8)",   "GLA LSOA Atlas (TfL PTAL)", "2014"),
]

# Computed onto the LSOA by this pipeline rather than published at it. Only
# written by --columns all, and suffixed so the heading carries the warning.
MODELLED = [
    ("no2_ugm3",   "Nitrogen dioxide (ug/m3)",  "Defra PCM 1 km model", "2023"),
    ("pm25_ugm3",  "PM2.5 (ug/m3)",             "Defra PCM 1 km model", "2023"),
    ("pm10_ugm3",  "PM10 (ug/m3)",              "Defra PCM 1 km model", "2023"),
    ("green_doorstep_pct",      "Green space - doorstep (%)",      "Defra green and blue, per Output Area", "2023"),
    ("green_local_pct",         "Green space - local (%)",         "Defra green and blue, per Output Area", "2023"),
    ("green_neighbourhood_pct", "Green space - neighbourhood (%)", "Defra green and blue, per Output Area", "2023"),
    ("green_commitment_pct",    "Green space standard met (%)",   "Defra green and blue, per Output Area", "2023"),
    ("blue_commitment_pct",     "Blue space standard met (%)",    "Defra green and blue, per Output Area", "2023"),
    ("gb_commitment_pct",       "Green or blue standard met (%)", "Defra green and blue, per Output Area", "2023"),
    ("gb_total_uprn",           "Addresses assessed",             "Defra green and blue, per Output Area", "2023"),
    ("bus_stop_dist_m",      "Distance to nearest bus stop from LSOA centroid (m)", "TfL Unified API", "live"),
    ("bus_stops_800m",       "Bus stops within 800 m of LSOA centroid",             "TfL Unified API", "live"),
    ("rail_station_dist_m",  "Distance to nearest station from LSOA centroid (m)",  "TfL Unified API", "live"),
    ("rail_stations_1km",    "Stations within 1 km of LSOA centroid",               "TfL Unified API", "live"),
    ("claimant_rate_pct",    "Claimant rate (%)",                                   "NOMIS NM_162 over population", "latest month"),
]

GEOGRAPHY = [
    ("postcode",             "Postcode",                     "ONSPD FEB 2026", "2026"),
    ("lsoa_code",            "LSOA code",                    "ONSPD FEB 2026 (lsoa21cd)", "2021"),
    ("lsoa_name",            "LSOA name",                    "ONS", "2021"),
    ("ward_code",            "Ward code",                    "ONSPD FEB 2026 (wd25cd)", "2025"),
    ("ward_name",            "Ward name",                    "ONS", "2024"),
    ("local_authority",      "Local authority",              "ONS", "2024"),
    ("local_authority_code", "Local authority code",         "ONS", "2024"),
    ("ward_imd_score_mean",  "Ward IMD score (mean of its LSOAs)",  "Computed here from MHCLG IMD 2025", "2025"),
    ("ward_imd_decile_mean", "Ward IMD decile (mean of its LSOAs)", "Computed here from MHCLG IMD 2025", "2025"),
    ("latitude",             "Latitude",                     "ONSPD FEB 2026", "2026"),
    ("longitude",            "Longitude",                    "ONSPD FEB 2026", "2026"),
]

READ_ME = """LONDON POSTCODES WITH AREA DEPRIVATION AND CENSUS FIGURES
=========================================================

{rows:,} live London postcodes. Built {stamp} by
scripts/build_postcode_table.py in the pophealthmap project.

Files
-----
{name}.csv              the table, one row per postcode
{name}_dictionary.csv   every column, what it means, its source and year
{name}_READ_ME.txt      this file


WHAT THESE FIGURES DO NOT SAY
-----------------------------
Every figure describes the LSOA, the ward or the borough the postcode falls in.
None of them describes the postcode, the addresses in it, or anyone living
there.

An LSOA holds about 1,500 people. A postcode holds about 15 addresses. Each
figure is therefore repeated across roughly 35 postcodes, and it was measured
at the larger scale. Reading one back as a fact about a household, a patient or
an applicant reverses what it measures. Deprivation in particular is a property
of an area and is not a property of the people in it.


THE THREE KINDS OF FIGURE HERE
------------------------------
1. LSOA figures. Published at LSOA by the body named in the dictionary, and
   read straight through. These are measurements of that LSOA.

2. Ward IMD, the two columns ending "mean of its LSOAs". Not published. This
   project computes them as a population-weighted mean of the LSOAs in the
   ward. A mean of deciles is not a decile: 3.0 means the typical LSOA there
   sits in the third decile, not that the ward is in the most deprived tenth.

3. Columns ending "_modelled", present only with --columns all. Computed onto
   the LSOA from something that is not LSOA-shaped: a 1 km pollution grid, an
   Output Area rollup, or a distance measured from the LSOA centroid. They are
   estimates for the area, not measurements of it, and the centroid ones
   describe a point rather than the area around it.


A NOTE ON WARDS
---------------
The ward is the one ONSPD places the postcode in, which is right per postcode.
The ward IMD columns were aggregated over the LSOAs best-fitted to each ward.
LSOAs cross ward boundaries, so for some postcodes the ward figure was computed
from a slightly different set of LSOAs than the one the postcode sits in.


LICENCE
-------
Contains OS data (c) Crown copyright and database right.
Contains Royal Mail data (c) Royal Mail copyright and database right.
Source: Office for National Statistics licensed under the Open Government
Licence v3.0.

Census 2021 and IMD 2025 are Crown copyright, Open Government Licence v3.0.
This acknowledgement must travel with the data if it is passed on.
"""


def load_geography():
    src = (REPO / "data" / "map" / "lsoa_imd.js").read_text(encoding="utf-8", errors="replace")
    gj = json.loads(src[src.index("{"): src.rindex("}") + 1])
    return {f["properties"]["code"]: f["properties"]
            for f in gj["features"] if (f.get("properties") or {}).get("code")}


def tidy(v, key):
    """Round so a column reads as a figure rather than as float noise."""
    if v is None or v == "":
        return ""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    if key in ("census_population", "imd_rank", "claimant_count", "gb_total_uprn",
               "imd_decile", "bus_stops_800m", "rail_stations_1km"):
        return str(int(round(f)))
    if f == int(f):
        return str(int(f))
    # MHCLG publishes IMD scores to three places. Rounding them to two here
    # would quietly restate the published figure as something it is not.
    places = 3 if key.endswith("_score") else 2
    return f"{f:.{places}f}".rstrip("0").rstrip(".")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default=str(REPO / "london_postcodes"))
    ap.add_argument("--columns", choices=["native", "all", "none"], default="native")
    ap.add_argument("--terminated", action="store_true")
    args = ap.parse_args()

    if not ONSPD_ZIP.exists():
        sys.exit(f"missing {ONSPD_ZIP}")

    geo = load_geography()
    lsoa = json.loads((REPO / "lsoa_data.json").read_text(encoding="utf-8"))
    wards = json.loads((REPO / "ward_data.json").read_text(encoding="utf-8"))["wards"]

    metrics = [] if args.columns == "none" else list(NATIVE)
    if args.columns == "all":
        metrics += [(k, lab + " [modelled]", s, y) for k, lab, s, y in MODELLED]

    head = ["Postcode", "LSOA code", "LSOA name",
            "IMD score", "IMD decile (1 = most deprived)",
            "IMD rank (1 = most deprived in England)",
            "Ward code", "Ward name",
            "Ward IMD score (mean of its LSOAs)", "Ward IMD decile (mean of its LSOAs)",
            "Local authority", "Local authority code", "Latitude", "Longitude"]
    body_keys = [k for k, _, _, _ in metrics if k not in ("imd_score", "imd_decile", "imd_rank")]
    head += [lab for k, lab, _, _ in metrics if k in body_keys]

    base = pathlib.Path(args.out)
    out_csv = base.with_name(base.name + ".csv")
    kept = skipped_geo = skipped_term = 0

    # utf-8-sig: Excel reads a plain utf-8 CSV as the system codepage and turns
    # every accent into mojibake. The BOM is what tells it otherwise.
    with out_csv.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(head)
        z = zipfile.ZipFile(ONSPD_ZIP)
        with z.open(ONSPD_MEMBER) as raw:
            r = csv.reader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))
            cols = next(r)
            ix = {c: i for i, c in enumerate(cols)}
            for need in ("pcds", "lsoa21cd", "wd25cd", "doterm", "lat", "long"):
                if need not in ix:
                    sys.exit(f"ONSPD is missing the {need} column")
            i_pc, i_ls, i_wd = ix["pcds"], ix["lsoa21cd"], ix["wd25cd"]
            i_dt, i_la, i_lo = ix["doterm"], ix["lat"], ix["long"]

            for row in r:
                code = row[i_ls]
                g = geo.get(code)
                if not g:
                    skipped_geo += 1
                    continue
                if (row[i_dt] or "").strip() and not args.terminated:
                    skipped_term += 1
                    continue

                li = lsoa.get(code) or {}
                li = li.get("indicators", li)
                wc = row[i_wd] if row[i_wd] in wards else (g.get("ward_code") or "")
                wr = wards.get(wc) or {}
                wi = wr.get("indicators") or {}

                rec = [row[i_pc], code, g.get("name", ""),
                       tidy(li.get("imd_score"), "imd_score"),
                       tidy(li.get("imd_decile"), "imd_decile"),
                       tidy(li.get("imd_rank"), "imd_rank"),
                       wc, wr.get("name", g.get("ward", "")),
                       tidy(wi.get("imd_score"), "ward_imd"),
                       tidy(wi.get("imd_decile_mean"), "ward_imd"),
                       wr.get("lad", g.get("borough", "")), wr.get("lad_code", ""),
                       row[i_la], row[i_lo]]
                rec += [tidy(li.get(k), k) for k in body_keys]
                w.writerow(rec)
                kept += 1
                if kept % 50000 == 0:
                    print(f"  {kept:,} postcodes")

    dict_csv = base.with_name(base.name + "_dictionary.csv")
    with dict_csv.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Column", "Describes", "Field name", "Source", "Year", "Kind"])
        for k, lab, s, y in GEOGRAPHY:
            level = ("Ward" if k.startswith("ward_imd") else
                     "Postcode" if k in ("postcode", "latitude", "longitude") else "Geography")
            kind = "Computed here, population-weighted mean of the ward's LSOAs" \
                if k.startswith("ward_imd") else "Geography"
            w.writerow([lab, level, k, s, y, kind])
        for k, lab, s, y in metrics:
            modelled = lab.endswith("[modelled]")
            w.writerow([lab, "LSOA", k, s, y,
                        "Computed onto the LSOA by this pipeline, an estimate for the area"
                        if modelled else "Published at LSOA by the source"])

    readme = base.with_name(base.name + "_READ_ME.txt")
    readme.write_text(READ_ME.format(rows=kept, name=base.name,
                                     stamp="from ONSPD FEB 2026"), encoding="utf-8")

    mb = out_csv.stat().st_size / 1e6
    print()
    print(f"{kept:,} postcodes x {len(head)} columns -> {out_csv.name}  ({mb:,.1f} MB)")
    print(f"  {dict_csv.name}")
    print(f"  {readme.name}")
    print(f"  skipped {skipped_geo:,} outside London, {skipped_term:,} terminated")


if __name__ == "__main__":
    main()
