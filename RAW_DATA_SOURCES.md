# PopHealth Map — Raw Data Sources

One-page inventory of every raw file needed to reproduce the ward + LSOA
health map. If you hand this document to a colleague, they can obtain each
source themselves and re-run the pipeline, with no script access needed.

**Geography scope:** all 33 London boroughs plus the City of London. The
authoritative list is the `BOROUGHS` constant at the top of
`fetch_all_data.py`; this document does not repeat it, so that the two
cannot drift apart.

**Levels used:** LSOA 2021 (≈33,755 nationally, 4,994 in London),
Ward 2025, GP practice, postcode.

---

## 1. Geography & boundaries

| File | Source | URL | Used for |
|------|--------|-----|----------|
| `Wards_May_2024_Boundaries_UK_BFC_V2` (GeoJSON) | ONS Open Geography Portal | https://geoportal.statistics.gov.uk/datasets/ons::wards-may-2024-boundaries-uk-bfc-v2 | Ward polygons (`GJ` in `index.html`) |
| `LSOA_2021_EW_BFC_V3` (GeoJSON) | ONS Open Geography Portal | https://geoportal.statistics.gov.uk/datasets/ons::lsoa-december-2021-ew-bfc-v3 | LSOA polygons (`LSOA_IMD` in `index.html`) |
| `Local_Authority_Districts_Boundaries_UK_BFC` (GeoJSON) | ONS Open Geography Portal | https://geoportal.statistics.gov.uk/datasets/ons::local-authority-districts-may-2024-boundaries-uk-bfc | Borough outlines (`BOROUGH_GJ`) |
| postcodes.io bulk API (no download) | postcodes.io, over the ONS Postcode Directory | https://api.postcodes.io/postcodes (POST, up to 100 postcodes per request) | Postcode → LSOA 2021 / ward / borough + coordinates, for GP practices, pharmacies, hospitals and charity HQs. Replaces the ONSPD zip. |
| `LSOA21_WD25_LAD25_EW_LU_v2` (no download) | ONS Open Geography Portal feature service | https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/LSOA21_WD25_LAD25_EW_LU_v2/FeatureServer/0/query | Official LSOA 2021 → ward 2025 → LAD 2025 best-fit lookup. Every LSOA-derived ward indicator is aggregated through it. |

> **On the retired ONSPD download.** The pipeline used to need the ~250 MB
> quarterly ONS Postcode Directory zip for two jobs: geocoding postcodes,
> and inferring each LSOA's ward from the modal ward of its postcodes. The
> first is now the postcodes.io API, which serves the same ONSPD fields;
> the second is now the ONS best-fit lookup, which is the authoritative
> answer rather than an inference. Neither needs a manual download.

> **Trim to scope**: filter each GeoJSON by LAD25CD / LAD24CD against
> `SCOPE_LADS` before writing it into `data/map/`, to keep file size down.

---

## 2. Deprivation — English Indices of Deprivation 2025

| File | Source | URL | Used for |
|------|--------|-----|----------|
| `File_7_IoD2025_All_Ranks_Scores_Deciles_Population_Denominators.csv` | MHCLG | https://www.gov.uk/government/statistics/english-indices-of-deprivation-2025 | IMD score + decile + all 7 domain scores at LSOA 2021 level. This is the single richest source: File 7 gives every domain in one CSV. **Not downloaded at run time.** IMD is static between releases, so the filtered output is committed as `data/demographics/imd2025.parquet` and used as the source of truth. Drop the raw CSV in `.cache/imd2025/` only to regenerate it after a new IoD release. |

Domains pulled from this file: Income, Employment, Education &
Skills, Health Deprivation & Disability, Crime, Barriers to Housing
& Services, Living Environment. Plus the overall IMD score / decile /
rank.

**Filtering applied to the committed parquet.** Rows: every English LSOA
2021 in File 7 carrying an LSOA code (33,755). There is no geographic
subsetting at this stage: the parquet holds all of England, and the London
scoping happens when `lsoa_data.json` is built. Columns: 11 of File 7's ~60, being `LSOA21CD`, the
overall IMD score / decile / rank, and the seven domain scores. The
population denominators and the per-domain ranks and deciles are dropped
as unused.

---

## 3. Census 2021 (ONS, via NOMIS bulk)

Each table is published as `census2021-TSxxx.zip` containing CSVs at
every geography level. LSOA-level CSVs inside are named
`census2021-TSxxx-lsoa.csv`.

| Table | URL pattern | What it gives us |
|-------|-------------|------------------|
| **TS001** — Usual resident population | https://www.nomisweb.co.uk/output/census/2021/census2021-ts001.zip | `census_population` |
| **TS004** — Country of birth | https://www.nomisweb.co.uk/output/census/2021/census2021-ts004.zip | `census_born_outside_uk_pct` |
| **TS007A** — Age by 5-year bands | https://www.nomisweb.co.uk/output/census/2021/census2021-ts007a.zip | Under 16 / 18-64 / 65+ / 85+ % |
| **TS021** — Ethnic group | https://www.nomisweb.co.uk/output/census/2021/census2021-ts021.zip | White / Asian / Black / Mixed / Other / non-white % |
| **TS025** — Household language | https://www.nomisweb.co.uk/output/census/2021/census2021-ts025.zip | `census_english_hh_all_pct`, `census_english_hh_none_pct` |
| **TS037** — General health | https://www.nomisweb.co.uk/output/census/2021/census2021-ts037.zip | Good/bad health % |
| **TS038** — Disability | https://www.nomisweb.co.uk/output/census/2021/census2021-ts038.zip | `census_disability_any_pct`, `census_disability_lot_pct` |
| **TS039** — Provision of unpaid care | https://www.nomisweb.co.uk/output/census/2021/census2021-ts039.zip | `census_provides_unpaid_care_pct` |
| **TS044** — Household deprivation | https://www.nomisweb.co.uk/output/census/2021/census2021-ts044.zip | `census_housing_deprived_pct` |
| **TS045** — Car or van availability | https://www.nomisweb.co.uk/output/census/2021/census2021-ts045.zip | `census_no_car_pct` |
| **TS054** — Tenure | https://www.nomisweb.co.uk/output/census/2021/census2021-ts054.zip | Owner / social / private rented % |
| **TS061** — Method of travel to work | https://www.nomisweb.co.uk/output/census/2021/census2021-ts061.zip | Active / public transport / car to work % |
| **TS062** — NS-SEC | https://www.nomisweb.co.uk/output/census/2021/census2021-ts062.zip | Higher managerial / routine-semi-routine % |
| **TS066** — Economic activity status | https://www.nomisweb.co.uk/output/census/2021/census2021-ts066.zip | `census_unemployed_pct` |
| **TS067** — Highest qualifications | https://www.nomisweb.co.uk/output/census/2021/census2021-ts067.zip | `census_level4_qual_pct`, `census_no_qual_pct` |

> TS009 was considered but its LSOA CSV doesn't ship in the bulk zip — use TS007A for age instead.

---

## 4. Economy & benefits (NOMIS / DWP)

All downloadable as CSV via NOMIS "bulk query" URL. Replace
`{geocodes}` with comma-separated LSOA 2021 codes (London: 4,994).

| Dataset | NOMIS ID | What we pull |
|---------|----------|--------------|
| **Claimant count (CLA01)** | `NM_162_1` | Monthly claimant count at LSOA; we compute rate ÷ working-age population |
| **PIP: cases in payment** | `NM_208_1` | PIP claimants at LSOA |
| **Universal Credit: households on UC** | `NM_210_1` | UC households at LSOA |
| **ESA claimants** | `NM_209_1` | ESA at LSOA |
| **Carer's Allowance** | `NM_189_1` | CA at LSOA |
| **Pension Credit** | `NM_193_1` | PC at LSOA (aged 65+) |

**Download template** (same for all DWP datasets):
```
https://www.nomisweb.co.uk/api/v01/dataset/{ID}.data.csv?date=latest&geography={geocodes}&measures=20100
```

Limit `geography` lists to ~500 LSOA codes per request to keep URL
under 8 kB. Concatenate the chunks. NOMIS silently caps unregistered
responses at 25,000 rows, which is why we chunk by LSOA.

> **Alternative**: DWP Stat-Xplore (https://stat-xplore.dwp.gov.uk/)
> is the authoritative source and goes back further. Requires a free
> login. NOMIS mirrors the headline tables with one month's lag.

---

## 5. Health (OHID Fingertips)

Fingertips publishes one CSV per indicator, queryable by area type.
The pipeline downloads one CSV per indicator into
`.cache/qof_fingertips/`.

**URL template:**
```
https://fingertips.phe.org.uk/api/all_data/csv/by_indicator_id?indicator_ids={ID}&child_area_type_id={AREA_TYPE}
```

Area type 7 = GP practice. Area type 3 = MSOA.

| Indicator | Fingertips ID | Short name |
|-----------|--------------|-----------|
| Hypertension (QOF) | 241 | `qof_hypertension_pct` |
| Depression (18+, QOF) | 848 | `qof_depression_pct` |
| Severe mental illness (QOF) | 90813 | `qof_smi_pct` |
| Diabetes (17+, QOF) | 253 | `qof_diabetes_pct` |
| COPD (QOF) | 273 | `qof_copd_pct` |
| Asthma (QOF) | 258 | `qof_asthma_pct` |
| CHD (QOF) | 263 | `qof_chd_pct` |
| CKD (18+, QOF) | 268 | `qof_ckd_pct` |
| Dementia (65+, QOF) | 282 | `qof_dementia_pct` |
| Atrial fibrillation (QOF) | 349 | `qof_af_pct` |
| Smoking (15+, QOF) | 219 | `qof_smoking_pct` |
| Obesity (18+, QOF) | 324 | `qof_obesity_pct` |
| Stroke/TIA (QOF) | 265 | `qof_stroke_tia_pct` |
| Heart failure (QOF) | 295 | `qof_heart_failure_pct` |
| Cancer (QOF) | 262 | `qof_cancer_pct` |
| Learning disability (QOF) | 266 | `qof_ld_pct` |

> Swap area_type_id for different geographies: `3` MSOA, `6` LA,
> `7` GP practice, `15` STP/ICB.

---

## 6. Environment

### 6a. Fuel poverty

| File | Source | URL | Used for |
|------|--------|-----|----------|
| **Sub-regional fuel poverty 2023, LSOA table** (XLSX) | DESNZ | https://www.gov.uk/government/collections/fuel-poverty-sub-regional-statistics | `fuel_poverty_pct` (Low Income Low Energy Efficiency metric). Open the "LSOA" sheet. |

Codes in DESNZ are usually LSOA 2011 — they align with LSOA 2021 for
London so no re-mapping is needed for our boroughs.

### 6b. Access to green and blue space

| File | Source | URL | Used for |
|------|--------|-----|----------|
| `Access_to_green_and_blue_space_England_data_table.ods` | Defra (with ONS, OS, Natural England) | https://www.gov.uk/government/statistics/access-to-green-and-blue-space-in-england-2025 | `gb_commitment_pct`, `green_commitment_pct`, `green_doorstep_pct`, `green_local_pct`, `green_neighbourhood_pct`, `blue_commitment_pct`, `gb_total_uprn` |
| `uprn_logic_access_publication.ods` | Defra (methodology supplement) | same landing page | UPRN classification rules used upstream of the headline table (reference only; not loaded by the pipeline). |

Published 04/03/2026 as an **"official statistic in development"** — the
methodology and layer set may change before it receives full "accredited
official statistic" status.

**What the fields mean.** The dataset scores every Ordnance Survey
UPRN (Unique Property Reference Number — essentially, a dwelling /
addressable unit) against the [Defra 15-minute commitment standards](https://www.gov.uk/government/news/defra-unveils-plans-to-expand-access-to-nature)
for green space — doorstep (≤200m), local (≤1km), neighbourhood (≤2km) —
plus a separate test for access to blue space (rivers, lakes, coast).
`commitment` means all three green distances *and* blue access are
satisfied.

**Geography — OA21, not LSOA.** The source table publishes one row per
2021 Output Area (≈535k rows nationwide). OA is one level below LSOA
(≈4–6 OAs per LSOA). We aggregate to LSOA21 by **summing the UPRN
numerator and denominator across the OAs in each LSOA, then dividing**
— this preserves the UPRN-weighted interpretation. A simple mean of the
OA percentages would over-weight small OAs. Example: if an LSOA has two
OAs — one with 200 UPRNs and 50% commitment, the other with 800 UPRNs
and 10% commitment — the correct LSOA figure is `(100+80) / (200+800)
= 18%`, not `(50+10) / 2 = 30%`.

Aggregator: `aggregate_greenblue.py` (streaming ODS parser — the full
file is a 1.37 GB XML once unzipped and doesn't fit in odfpy's DOM).
Output: `data/environment/greenblue_lsoa.parquet` (4,994 London LSOA
rows, 38 columns — the `commitment` / `doorstep` / `local` /
`neighbourhood` suffixes plus each pairwise + triple combination, as
counts and as percentages).

### 6c. Background air quality

| File | Source | URL | Used for |
|------|--------|-----|----------|
| `mapno2<year>.csv`, `mappm25<year>g.csv`, `mappm10<year>g.csv` | Defra UK-AIR, Pollution Climate Mapping (PCM) | https://uk-air.defra.gov.uk/data/pcm-data | `no2_ugm3`, `pm25_ugm3`, `pm10_ugm3` |

Fetched automatically; nothing needs downloading by hand. The pipeline
reads the index and takes the newest year listed **per pollutant**,
because a pinned filename rots at every release.

**Three things about these files that are easy to get wrong.**

1. The pollutant token runs straight into the year with no separator,
   and some files carry a version suffix after it (`mappm252024g.csv`
   but `mapno22024.csv`). A greedy regex reads `mapno22024` as
   pollutant `no`, year `2200`. Match the token against a known
   vocabulary, longest first.
2. The index's links are relative and written `../datastore/pcm/…`,
   relative to `/data/pcm-data` **as a page, not a directory**. Joining
   them onto a trailing slash puts them under `/data/datastore/`, and
   every one 404s.
3. Each CSV has five metadata lines (pollutant, year, statistic, unit,
   blank) before the header row — `skiprows=5`.

**Geography — a 1 km grid, not LSOA.** `x` and `y` are the **centre**
of each square, not a corner: every `x` satisfies `x mod 1000 == 500`.
Cells are rebuilt as squares spanning ±500 m and each LSOA takes the
**area-weighted mean** of the cells it overlaps. Reading one cell at the
LSOA centroid is cheaper and fine for dense inner-London LSOAs, but
outer-borough ones run to several km² and would take a single cell's
value for the whole area.

These are **modelled**, not measured. The model is calibrated against
the national monitoring network, but there is no monitor in most LSOAs.
They are *background* concentrations over an area and do not capture the
roadside peak on a particular street.

Output: `data/environment/air_quality_lsoa.parquet` (4,994 London LSOA
rows). Cells with the literal value `MISSING` (open sea, some Scottish
islands) are dropped before weighting.

### 6d. Public transport stops and stations

| File | Source | URL | Used for |
|------|--------|-----|----------|
| `StopPoint/Mode/{modes}?page=N` (JSON) | Transport for London, Unified API | https://api.tfl.gov.uk | `rail_station_dist_m`, `rail_stations_1km`, `bus_stop_dist_m`, `bus_stops_800m` |

Open API, no key needed. Unkeyed use is rate limited to roughly 50
requests a minute and this takes 43 (10 pages of rail modes, 33 of bus),
so the pipeline paces itself. Set `TFL_APP_KEY` to use a free key and
skip the pacing. Pages are cached individually under `.cache/tfl/`.

**What comes back is not a list of stations.** It is a list of stop
*points*, which includes platforms, entrances and access areas as
separate records sharing a station's name and sitting metres apart —
counting those as stations reports eleven "stations" at King's Cross.
Only station-level `stopType`s are kept
(`NaptanMetroStation`, `NaptanRailStation`, `TransportInterchange`),
then deduplicated by `stationNaptan` so a combined Tube-and-National-Rail
interchange counts once.

Bus stops are deduplicated by **`naptanId`, not `stationNaptan`** — the
two stops facing each other across a road share a `stationNaptan`, and
keying on it halves every stop count (21,043 stops becomes 13,160).

**Stations outside London are deliberately kept.** For an LSOA in
Havering the nearest station is often over the Essex border, and
clipping to the boundary would push its distance to the next London
station and overstate it. The register is trimmed to a London bounding
box with roughly 8 km of margin instead.

Distances are computed in **British National Grid**, in metres, from
each LSOA's centroid (or `representative_point()` where the centroid
falls outside a crescent-shaped or river-split polygon). Working in
degrees would count a degree of longitude the same as a degree of
latitude — at London's latitude, a 38% error. They are straight lines,
not walking routes.

Radius counts use shapely's `dwithin` predicate. A plain
`STRtree.query(point.buffer(r))` filters on **bounding boxes**, so it
counts everything in the buffer's square envelope — about 27% more area
than the circle, overstating most in the dense centre where it matters
most.

Output: `data/environment/tfl_transport_lsoa.parquet` (4,994 rows).

**Not built: step-free access.** `AccessViaLift` is present on only 156
of 2,996 station records, and lift access is not the same as step-free
access anyway (much of the Overground and Elizabeth line is step-free by
level boarding or ramp). There is no complete step-free flag on this
endpoint, so no step-free indicator is published rather than one that
would silently be mostly guesswork.

---

## 7. Services — GP, pharmacy, dentist

| File | Source | URL | Used for |
|------|--------|-----|----------|
| `epraccur` extract (GP practices, no download) | NHS ODS Data Search and Export | https://www.odsdatasearchandexport.nhs.uk/api/getReport?report=epraccur | GP practice names, postcodes, status. Join to postcode → ward lookup. Fetched by the pipeline and revalidated by ETag. The former bulk-zip URL `https://files.digital.nhs.uk/assets/ods/current/epraccur.zip` now returns 403; that whole route has been retired. |
| GP list-size / registered patients CSV | NHS Digital — "Patients Registered at a GP Practice" | https://digital.nhs.uk/data-and-information/publications/statistical/patients-registered-at-a-gp-practice | Sizes the GP marker on the map. Download the monthly "gp-reg-pat-prac-all.csv". |
| `edispensary` extract (pharmacies, no download) | NHS ODS Data Search and Export | https://www.odsdatasearchandexport.nhs.uk/api/getReport?report=edispensary | Pharmacy name + postcode. Headerless 27-column ODS CSV, same layout as epraccur. Fetched by the pipeline and revalidated by ETag. |
| NHS.uk GP / Dentist JSON datasets | NHS.uk | https://www.nhs.uk/about-us/nhs-website-datasets/ | Dentist and pharmacy pins; also has opening hours + services. Requires free sign-up. |

---

## 8. Community — VCSE (Charity Commission)

| File | Source | URL | Used for |
|------|--------|-----|----------|
| `publicextract.charity.json.zip` | Charity Commission public register | https://ccewuksprdoneregsadata1.blob.core.windows.net/data/json/publicextract.charity.zip | Core charity record — name, income, HQ postcode. |
| `publicextract.charity_classification.json.zip` | Charity Commission | https://ccewuksprdoneregsadata1.blob.core.windows.net/data/json/publicextract.charity_classification.zip | What causes each charity supports (mental health, older people etc.). |
| `publicextract.charity_area_of_operation.json.zip` | Charity Commission | https://ccewuksprdoneregsadata1.blob.core.windows.net/data/json/publicextract.charity_area_of_operation.zip | Which London boroughs / LAs each charity operates in. Used to filter to "operates in London" rather than "HQ in London". |

All three extracts refresh monthly at the same folder:
https://register-of-charities.charitycommission.gov.uk/register/full-register-download

---

## 9. Crime — data.police.uk

| File | Source | URL | Used for |
|------|--------|-----|----------|
| `<YYYY-MM>-{force}-street.csv` — monthly street-crime archives for Metropolitan, City of London | data.police.uk | https://data.police.uk/data/ (under "Custom download") | Crime points, aggregated to ward. We pull the last 12 months. |

Select the Metropolitan Police and City of London forces for full coverage. Each month is one ZIP; inside,
one CSV per force per month.

---

## 10. London-specific atlases (optional enrichment)

| File | Source | URL | Used for |
|------|--------|-----|----------|
| LSOA Atlas (GLA) | London Datastore | https://data.london.gov.uk/dataset/lsoa-atlas | Supplementary LSOA stats compiled by GLA, including PTAL. Handy if you want transport accessibility. |

---

## Minimum download checklist to reproduce the map

To rebuild from scratch, a colleague needs:

1. **Boundaries**: 3 GeoJSONs from ONS (wards, LSOAs, LADs). Postcode and
   LSOA-to-ward geography come from APIs, so there is nothing to download.
2. **IMD 2025**: nothing. The filtered parquet is committed; the raw File 7
   CSV is only needed to regenerate it after a new IoD release.
3. **Census** — 15 ZIPs from NOMIS (one per TS table listed in §3).
4. **Claimant + DWP** — 6 CSVs via NOMIS bulk (claimant + 5 DWP).
5. **Fuel poverty** — 1 DESNZ XLSX.
6. **Green/blue space access** — 1 Defra ODS (≈46 MB zipped, 1.37 GB uncompressed).
7. **QOF** — 16 CSVs from Fingertips (one per indicator).
8. **GP / pharmacy**: 1 file, the GP registered-patients CSV. Both the GP
   practice and pharmacy registers are fetched from the ODS API.
9. **VCSE** — 3 Charity Commission JSON ZIPs.
10. **Crime** — Last 12 monthly ZIPs from data.police.uk for Met + City forces.
11. **Air quality** — 3 Defra PCM CSVs (≈10 MB each, UK-wide 1 km grid).
12. **Transport** — nothing to download. 43 paged JSON calls to the TfL
    Unified API, cached under `.cache/tfl/`.

**Total raw data:** ≈ 350–450 MB uncompressed, from 9 distinct source
organisations. All files are free and published under OGL or
equivalent open licence.

## Refresh cadence

| Source | Updated | How often to refresh |
|--------|---------|----------------------|
| ONS boundaries | Annually (May) | Once a year |
| postcodes.io / ONS best-fit lookup | Quarterly / annually | Automatic. Cached misses are re-checked every 90 days; bump `ONS_LOOKUP_LAYER` when the ward vintage moves past 2025 |
| IMD | ~Every 6 years (last 2019 → 2025) | As and when released |
| Census 2021 | One-off decennial | Fixed — refreshes in ~2031 |
| NOMIS claimant / DWP | Monthly | Monthly |
| OHID Fingertips / QOF | Annually | Annually (usually Oct) |
| Fuel poverty | Annually (Feb–Mar) | Annually |
| Defra green/blue space access | Not yet set (first release Mar 2026) | Watch landing page — status is "in development" |
| Defra UK-AIR PCM (air quality) | Annually (usually autumn) | Annually. Automatic — the newest year on the index is taken per pollutant, so a new release needs no edit |
| TfL Unified API (stops and stations) | Continuously | Any run picks up the current register. Clear `.cache/tfl/` to force a refresh |
| NHS ODS (GP practices + pharmacies) | Monthly | Automatic. The pipeline revalidates by ETag on every run and re-downloads only when the extract actually changes |
| Charity Commission | Monthly | Monthly |
| Crime (police.uk) | Monthly (3-month lag) | Quarterly is enough |

---

## Notes for handover

- Every indicator in the current map traces back to exactly one of
  the files above. If a field in `lsoa_data.json` or `ward_data.json`
  isn't populated, the issue is one of these downloads — not the
  pipeline logic.
- The map UI (`index.html`) only reads two JSON files at runtime —
  `lsoa_data.json` (LSOA-level indicators) and `ward_data.json`
  (ward-level indicators + geometry metadata). Everything above feeds
  into those two JSONs.
- LSOA 2021 is the authoritative small-area unit; NOMIS calls it
  `TYPE298` on the claimant/DWP endpoints. Any data that still comes
  in LSOA 2011 codes (DESNZ is the main one) aligns 1:1 with LSOA 2021
  for the London boroughs, so no re-mapping is needed.
- All authoritative URLs are stable landing pages — deep links to
  specific file versions break each release cycle. If a link 404s,
  navigate to the landing page and pick the latest release there.
