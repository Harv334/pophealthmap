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
| LSOA 2021 population-weighted centroids (no download) | ONS Open Geography Portal feature service | Tried as `LSOA_PopCentroids_EW_2021_V4` (then V3, V2), else found in the service list at https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services?f=json | The points the GP, pharmacy and dentist distances are measured from (`service_access`). Cached in `.cache/ons_lookup/`. Without it the distances are measured from the middle of each LSOA's shape, and the ward file's `metadata.small_area.service_access_origin` says `centroid`. |

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
as unused here; the next two rows keep what the map now uses of them.

| File | Source | URL | Used for |
|------|--------|-----|----------|
| `File_7_IoD2025_All_Ranks_Scores_Deciles_Population_Denominators.csv` (again) | MHCLG | same release page | Source `iod_extra`, written to `data/demographics/iod2025_extra.parquet`: IDACI and IDAOPI (income deprivation affecting children aged 0 to 15 and people aged 60 and over) as rate, rank and decile, stored as `idaci_pct` / `idaci_rank` / `idaci_decile` and the `idaopi_` equivalents (the rate times 100); the six sub-domain scores `cyp_education_score`, `adult_skills_score`, `geo_barriers_score`, `wider_barriers_score`, `indoors_score`, `outdoors_score`; and the denominators `iod_pop_children` (dependent children aged 0 to 15) and `iod_pop_older` (aged 60 and over), which weight IDACI and IDAOPI into wards and LAs. **Downloaded at run time** until the committed parquet holds every column: the file is found on the release through the gov.uk content API by its "File 7" title, falling back to `IMD_SOURCE_URL`. A CSV dropped in `.cache/iod2025/` (or `.cache/imd2025/`) is read instead. |
| `File_8_IoD2025_Underlying_Indicators.xlsx` (name as found on the release) | MHCLG | same release page | Source `iod_indicators`, written to `data/demographics/iod2025_indicators.parquet`: the underlying indicators `iod_ypll`, `iod_cidr`, `iod_acute_morbidity`, `iod_mood_anxiety`, `iod_ks2`, `iod_ks4`, `iod_staying_on`, `iod_he_entry`, `iod_adult_quals`, `iod_english`, `iod_housing_poor`, `iod_no_central_heating`, `iod_air_quality`, `iod_road_accidents`. Found on the release by its "File 8" or "underlying indicators" title; every sheet with an LSOA code column is read. Shares are stored as percentages and the two standardised ratios with England at 100. |

**How the columns are found.** Every header is matched by keywords after
normalising punctuation (`IOD_FILE7_FIELDS`, `IOD_FILE8_SPECS` in
`fetch_all_data.py`), and the domain lookups rule out the narrower measures
that share their words, so `income` and `score` can no longer pick up the
IDACI column. Each File 7 measure must rise with its own domain, each rank and
decile must fall as its rate rises, and each File 8 indicator must move with
its domain score in the direction the map colours it (attainment falls as
education deprivation rises). A column that is missing, matches twice, sits on
the wrong scale or runs the wrong way is not used, and the source is marked
failed in the manifest saying which. The 2025 File 8 headers were not seen
when this was written, so the first refresh's log (`iod_indicators: <key> <-
'<header>'`) is the place to confirm what was matched; the 2019 indicator
set may have changed (staying on after 16 and homes without central heating
in particular), and an indicator the release does not carry stays off the map.

**Ward and LA figures.** IDACI is weighted by the File 7 children aged 0 to
15 and IDAOPI by its people aged 60 and over, so a ward's figure is its
income-deprived children over its children. Everything else is weighted by
Census 2021 residents. LA figures are built from all of the LA's LSOAs and
written to `borough_data.json`. Ranks and deciles stay LSOA-only. If File 7
gives no denominators, IDACI and IDAOPI fall back to residents with a
warning, and `ward_data.json` metadata `iod_weights` says so.

---

## 3. Census 2021 (ONS, via NOMIS bulk)

Each table is published as `census2021-TSxxx.zip` containing CSVs at
every geography level. LSOA-level CSVs inside are named
`census2021-TSxxx-lsoa.csv`.

| Table | URL pattern | What it gives us |
|-------|-------------|------------------|
| **TS001** — Usual resident population | https://www.nomisweb.co.uk/output/census/2021/census2021-ts001.zip | `census_population` |
| **TS003** — Household composition | https://www.nomisweb.co.uk/output/census/2021/census2021-ts003.zip | `census_one_person_hh_pct`, `census_pensioner_alone_pct` (one-person households aged 66 and over), `census_lone_parent_dep_pct` (lone parents with dependent children), all as shares of households |
| **TS004** — Country of birth | https://www.nomisweb.co.uk/output/census/2021/census2021-ts004.zip | `census_born_outside_uk_pct` |
| **TS007A** — Age by 5-year bands | https://www.nomisweb.co.uk/output/census/2021/census2021-ts007a.zip | Under 16 / 18-64 / 65+ / 85+ % |
| **TS011** — Households by deprivation dimensions | https://www.nomisweb.co.uk/output/census/2021/census2021-ts011.zip | `census_hh_deprived_2plus_pct` (two, three or four dimensions), `census_hh_deprived_any_pct` (total less "not deprived in any dimension") |
| **TS021** — Ethnic group | https://www.nomisweb.co.uk/output/census/2021/census2021-ts021.zip | White / Asian / Black / Mixed / Other / non-white %, and six detailed groups from the leaf columns: `census_eth_bangladeshi_pct`, `_pakistani_`, `_black_african_`, `_black_caribbean_`, `_gypsy_traveller_`, `_roma_` |
| **TS025** — Household language | https://www.nomisweb.co.uk/output/census/2021/census2021-ts025.zip | `census_english_hh_all_pct`, `census_english_hh_none_pct` |
| **TS029** — Proficiency in English language | https://www.nomisweb.co.uk/output/census/2021/census2021-ts029.zip | `census_poor_english_pct` (cannot speak English well, or at all, over residents aged 3 and over). The output area file may carry only the main language split; then wards and LAs are built from the LSOA counts |
| **TS037** — General health | https://www.nomisweb.co.uk/output/census/2021/census2021-ts037.zip | Good/bad health % |
| **TS038** — Disability | https://www.nomisweb.co.uk/output/census/2021/census2021-ts038.zip | `census_disability_any_pct`, `census_disability_lot_pct` |
| **TS039** — Provision of unpaid care | https://www.nomisweb.co.uk/output/census/2021/census2021-ts039.zip | `census_provides_unpaid_care_pct`, `census_unpaid_care_50h_pct` (50 or more hours a week) |
| **TS045** — Car or van availability | https://www.nomisweb.co.uk/output/census/2021/census2021-ts045.zip | `census_no_car_pct` |
| **TS052** — Occupancy rating for bedrooms | https://www.nomisweb.co.uk/output/census/2021/census2021-ts052.zip | `census_overcrowded_pct` (-1 or less), `census_underoccupied_pct` (+2 or more), as shares of households |
| **TS054** — Tenure | https://www.nomisweb.co.uk/output/census/2021/census2021-ts054.zip | Owner / social / private rented % |
| **TS061** — Method of travel to work | https://www.nomisweb.co.uk/output/census/2021/census2021-ts061.zip | Active / public transport / car to work % |
| **TS062** — NS-SEC | https://www.nomisweb.co.uk/output/census/2021/census2021-ts062.zip | Higher managerial (L1 to L3), routine and semi-routine (L12 and L13 only), `census_never_worked_ltu_pct` (L14.1 and L14.2) % |
| **TS066** — Economic activity status | https://www.nomisweb.co.uk/output/census/2021/census2021-ts066.zip | `census_unemployed_pct`, `census_inactive_ltsick_pct` (economically inactive: long-term sick or disabled, over residents aged 16 and over) |
| **TS067** — Highest qualifications | https://www.nomisweb.co.uk/output/census/2021/census2021-ts067.zip | `census_level4_qual_pct`, `census_no_qual_pct` |

> TS009 was considered but its LSOA CSV doesn't ship in the bulk zip — use TS007A for age instead.
>
> TS044 is accommodation type, not household deprivation. The pipeline read it for household deprivation until October 2026, found none of the columns it looked for and wrote nothing; household deprivation is TS011. A column or table the pipeline cannot find is now reported, and the census source is marked partial in the manifest.

---

## 4. Economy & benefits (NOMIS / DWP)

Downloadable as CSV via the NOMIS API. Replace `{geocodes}` with
comma-separated LSOA 2021 codes (London: 4,994).

| Dataset | NOMIS ID | What we pull |
|---------|----------|--------------|
| **Claimant count (CLA01)** | `NM_162_1` | Monthly claimant count at LSOA; we compute rate ÷ working-age population |
| **Claimant count aged 18 to 24** (source `claimant_youth`) | `NM_162_1` | The same request for one age band. The band's code is read from `https://www.nomisweb.co.uk/api/v01/dataset/NM_162_1/age.def.sdmx.json` by its name ("18" to "24"), not typed in, and the response is checked by `AGE_NAME`. Whether Nomis publishes the band for LSOAs was not confirmed: if the codelist has no such band, or Nomis refuses or returns nothing for LSOAs, the source is "skipped" with the reason in the manifest. Written to `data/economy/claimant_18_24.parquet`. Counts are rounded to 5, so the rate (`claimant_18_24_pct`) is shown for wards and LAs only: claimants and people aged 18 to 24 (from the mid-year estimates below) are added up over the LSOAs before dividing. |

**Download template:**
```
https://www.nomisweb.co.uk/api/v01/dataset/{ID}.data.csv?date=latest&geography={geocodes}&measures=20100
```

Limit `geography` lists to ~500 LSOA codes per request to keep the URL
under 8 kB. Concatenate the chunks. NOMIS silently caps unregistered
responses at 25,000 rows, which is why we chunk by LSOA.

### Mid-year population estimates (ONS)

| File | Source | URL | Used for |
|------|--------|-----|----------|
| LSOA population estimates by single year of age (`sapelsoasyoa20222024.xlsx` in the November 2025 edition) | ONS | https://www.ons.gov.uk/peoplepopulationandcommunity/populationandmigration/populationestimates/datasets/lowersuperoutputareamidyearpopulationestimates | Source `sape`. Found on that page (and its `/data` JSON) as the newest file whose name has `lsoa` and `syoa`; `SAPE_URL` overrides, and a file put in `.cache/sape/` by hand is used when the page lists none. The newest `Mid-YYYY LSOA 2021` sheet is read: `LSOA 2021 Code`, `Total`, then `F0` to `F90` and `M0` to `M90` (90 meaning 90 and over). The single years must add up to `Total`, or nothing is kept. Written to `data/demographics/sape_lsoa.parquet` as counts (population, 0 to 15, 16 to 64, 65+, 75+, 18 to 24) and shown as `sape_population` and four age shares for LSOAs, wards and LAs (counts added up). The Census figures stay the base of the existing rates: moving them would change almost every rate on the map at once. |

The Nomis dataset id `NM_2014_1` for the same estimates was not confirmed, so
the ONS file is used instead.
### ONS small area income estimates (`saie`)

ONS, *Income estimates for small areas, England and Wales: financial year
ending 2023* (published 10 December 2025, accredited official statistics),
the first edition on the 2021 MSOAs. Modelled from the Family Resources
Survey with Census and administrative data. Three measures, each a mean in
pounds a year with 95% confidence limits: total annual household income
(`saie_total_income`), and net household income before (`saie_net_bhc`) and
after (`saie_net_ahc`) housing costs, equivalised. Found by discovery from the
dataset landing page
(`/employmentandlabourmarket/peopleinwork/earningsandworkinghours/datasets/smallareaincomeestimatesformiddlelayersuperoutputareasenglandandwales`),
read as JSON for its editions and files, then the bulletin
(`.../bulletins/smallareamodelbasedincomeestimates/latest`) for its related
datasets, then ONS's search; the newest edition's spreadsheet is taken.
`SAIE_URL` overrides with a direct link, and a file dropped in `.cache/saie/`
is read instead. Columns are matched by the measure's words in the header or
the sheet's title; a measure matched twice, on the wrong scale (not pounds a
year), or with after housing costs above before housing costs is reported and
not used, and a file on the 2011 MSOAs is refused. Output:
`data/demographics/saie_msoa.parquet` (English MSOAs, national), static
between releases. Wards: household-weighted mean of their LSOAs, each taking
its MSOA's figure (Census 2021 households, residents if those are missing).
LAs: household-weighted mean of all their MSOAs. The limits stay at MSOA.
Not comparable with earlier editions (2011 MSOAs, a different model).

### The DWP benefit series are not on NOMIS

This table used to list five more: PIP `NM_208_1`, Universal Credit
`NM_210_1`, ESA `NM_209_1`, Carer's Allowance `NM_189_1` and Pension
Credit `NM_193_1`. **Every one of those ids is the wrong dataset.**
They return HTTP 200 with an empty body, which is why this read as an
upstream outage for months rather than as a mistake here:

| id used | what it actually is |
|---------|---------------------|
| `NM_210_1` | Annual population survey — regional — occupation (SOC) |
| `NM_189_1` | Business Register and Employment Survey |
| `NM_193_1` | Annual Employment Survey employee analysis |
| `NM_208_1`, `NM_209_1` | do not return a parseable definition |

Asking NOMIS for the real ones by name settles it, and not in favour of
a fix:

| Benefit | Real NOMIS id | Last period published |
|---------|---------------|-----------------------|
| PIP | **not on NOMIS at all** | — |
| Universal Credit | **not on NOMIS at all** | — |
| ESA | `NM_12_1` (small areas), `NM_134_1` | 2018-11 |
| Pension Credit | `NM_109_1` (small areas), `NM_114_1` | 2018-11 |
| Carer's Allowance | `NM_116_1` | 2018-11 |
| DLA | `NM_110_1` (small areas), `NM_115_1` | 2018-11 |
| Attendance Allowance | `NM_78_1` | 2019-02 |

So the two benefits that matter most for a current picture were never
on NOMIS, and the rest stopped eight years ago. The "small areas"
variants publish at `TYPE304` / `TYPE307` / `TYPE312`, which predate
LSOA 2021 and would not join to anything else in this repo.

**The live route is DWP Stat-Xplore**, `https://stat-xplore.dwp.gov.uk/webapi/rest/v1/`.
It needs a free account and an API key sent as an `APIKey` header (the
endpoint answers `401` without one), and it speaks its own query
language rather than NOMIS's. The broken NOMIS source was removed rather
than left failing, because a source that always fails teaches everyone to
ignore the failure list. The Stat-Xplore client below replaces it.

### DWP benefits from Stat-Xplore

Eight sources in `fetch_all_data.py` ("DWP BENEFITS, FROM STAT-XPLORE"),
each with its own manifest entry. They read the key from the environment
variable `STAT_XPLORE_API_KEY`, which the refresh and region workflows set
from the repository secret of the same name (HANDOVER.md, "DWP benefits").
**Without the key each is `skipped`, with a note naming the secret, and
nothing is fetched or changed.** Stat-Xplore is published under the Open
Government Licence v3.0.

The API: `GET schema` (the top folder), `GET schema/{id}` (a folder,
database, field or value set, with its children) and `POST table` (a
database, a measure, and for each field a recode of the values wanted).
Nothing is typed in by id. The client walks the catalogue's folders, picks
each database by its label (the newest of several that fit), takes its
count measure, its date field and newest value, and the LSOA (or ward) level
of the geography of where people live, and logs every id it chose. The
geography values go in batches of at most 1,000 and 5,000 cells per table
request; 429 and server errors are retried with a pause (`Retry-After` is
honoured), and an error body, a refused key or an answer of the wrong shape
fails that source with the message. The JSON layout read is the API's
published documentation as remembered when this was written; the first run
with a key is the check (see HANDOVER.md for what its log says).

| Source | Database, matched by label | Count | Written to | Map keys (levels) | Base |
|--------|----------------------------|-------|------------|-------------------|------|
| `dwp_uc_people` | People on Universal Credit | all people, newest month | `data/economy/dwp_uc_people_lsoa.parquet` | `dwp_uc_people_pct` (LSOA, ward, LA) | residents aged 16 to 64, mid-year estimates |
| `dwp_uc_households` | Households on Universal Credit | households in payment where that value exists; with Child Entitlement; with Limited Capability for Work Entitlement (LCW or LCWRA) 12 months before the newest month, because assessments are backdated | `data/economy/dwp_uc_households_lsoa.parquet` | `dwp_uc_hh_pct`, `dwp_uc_hh_lcw_pct` (Census 2021 households), `dwp_uc_hh_children_pct` (UC households) (LSOA, ward, LA) | households at the 2021 Census; UC households |
| `dwp_pip` | PIP Cases with Entitlement | all, and by main disabling condition (psychiatric, musculoskeletal general and regional, neurological) where offered | `data/economy/dwp_pip_lsoa.parquet` | `dwp_pip_pct`, `dwp_pip_psych_pct`, `dwp_pip_msk_pct`, `dwp_pip_neuro_pct` (ward, LA) | residents aged 16 and over |
| `dwp_dla_children` | DLA Cases with Entitlement (in payment if not found) | ages 0 to 15, from the age bands | `data/economy/dwp_dla_children_lsoa.parquet` | `dwp_dla_child_pct` (ward, LA) | children aged 0 to 15 |
| `dwp_attendance_allowance` | Attendance Allowance cases with entitlement | all | `data/economy/dwp_attendance_allowance_lsoa.parquet` | `dwp_aa_pct` (ward, LA) | residents aged 66 and over (`sape_age66plus`) |
| `dwp_carers_allowance` | Carer's Allowance Cases in Payment (never the entitled count, which includes pensioners paid their State Pension instead) | all | `data/economy/dwp_carers_allowance_lsoa.parquet` | `dwp_carers_pct` (ward, LA) | residents aged 16 and over |
| `dwp_pension_credit` | Pension Credit | claims (a couple makes one) | `data/economy/dwp_pension_credit_lsoa.parquet` | `dwp_pension_credit_per_100` (ward, LA) | residents aged 66 and over |
| `dwp_cilif` | Children in Low Income Families: Relative Low Income | children aged 0 to 15, by ward, before housing costs | `data/economy/dwp_cilif_ward.parquet` | `dwp_cilif_pct` (ward, LA) | children aged 0 to 15 in the LSOAs that best fit each ward |

- **Vintage.** Universal Credit uses the output areas of 2021 since its
  October 2025 release. For the rest it was not confirmed, so it is read
  from the codes: a value set holding any English LSOA code above
  `E01033768` (the last of 2011) is on 2021 boundaries; one without is a
  2011 set, asked for the 2011 parents of the scope's LSOAs and moved onto
  2021 LSOAs through `get_lsoa11_lsoa21_rows`, a split LSOA's count shared
  among its parts by Census 2021 residents and a merged one added up.
- **Rounded and withheld cells.** DWP adjusts or rounds small counts. A cell
  that is empty, not a number or marked (`..`, `*`) is no figure, never 0:
  that LSOA is left out of its ward and LA, count and base alike, and an
  area whose LSOAs with a count hold under four fifths of its base has no
  figure. The counts are used as published, so sums do not match DWP's own
  ward or LA totals exactly.
- **Levels.** LSOA, ward and LA for Universal Credit; wards and LAs only for
  the rest, because their small-area geography and vintage were not
  confirmed and their LSOA counts are small. Ward figures add up the LSOAs
  that best fit each ward, LA figures all of the LA's LSOAs, counts before
  dividing.
- **CiLIF wards.** DWP's ward list is matched to the map's 2025 wards by
  code. An LA is used only when every one of its wards is there; the others
  are named in the manifest note, and if no LA matches the source is
  skipped. Wards of an older vintage are not forced onto the 2025 wards
  (HANDOVER.md says what would be needed).
- **Not added:** Employment and Support Allowance, the absolute low income
  measure, and the after housing costs estimates (published by DWP as ODS
  tables, not on Stat-Xplore).

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

GP practice QOF prevalence (`QOF_INDICATORS` in `fetch_all_data.py`). The
name in the middle column is Fingertips' own, as recorded in
`data/outcomes/fingertips_profiles.parquet`. `run_qof` compares it with the
`Indicator Name` in each download and skips any id whose figures are
something else, because a wrong id still returns plausible percentages.

| Fingertips ID | Fingertips name | Short name |
|--------------|-----------------|-----------|
| 219 | Hypertension: QOF prevalence | `qof_hypertension_pct` |
| 241 | Diabetes: QOF prevalence | `qof_diabetes_pct` |
| 253 | COPD: QOF prevalence | `qof_copd_pct` |
| 273 | CHD: QOF prevalence | `qof_chd_pct` |
| 258 | CKD: QOF prevalence | `qof_ckd_pct` |
| 262 | Heart Failure: QOF prevalence | `qof_heart_failure_pct` |
| 848 | Depression: QOF prevalence | `qof_depression_pct` |
| 90581 | Mental Health: QOF prevalence | `qof_mental_health_pct` |
| 90933 | Asthma: QOF prevalence | `qof_asthma_pct` |
| 247 | Dementia: QOF prevalence | `qof_dementia_pct` |
| 280 | Atrial fibrillation: QOF prevalence | `qof_af_pct` |
| 212 | Stroke: QOF prevalence | `qof_stroke_pct` |
| 200 | Learning disability: QOF prevalence | `qof_ld_pct` |
| 94136 | Obesity: QOF prevalence (new definition) | `qof_obesity_pct` |
| 91280 | Smoking: QOF prevalence | `qof_smoking_pct` |

There is no cancer prevalence column: no QOF cancer prevalence id could be
confirmed by name. 90813 is self-harm admissions for ages 10 to 24, a borough
indicator, and 295 is not a prevalence (its practice values run to 100), so
neither is used here.

`data/healthcare/qof_prevalence.parquet` and the `qof` object on each practice
in `data/map/gp_practices.js` carry the seven of these that the last refresh
returned: hypertension, diabetes, COPD, CHD, CKD, heart failure and depression.

> Swap area_type_id for different geographies: `3` MSOA, `6` LA,
> `7` GP practice, `15` STP/ICB.

### 5a. Local Health: low birth weight and cancer incidence (`fingertips_msoa_extra`)

Six Local Health series, fetched at MSOA (area type 3, England as parent) and
for LAs (area type 501), in a source of their own so that one that does not
come back cannot mark the established Local Health sources partial or failed.

| Fingertips ID | Fingertips name | Map key |
|--------------|-----------------|---------|
| 93092 | Low birth weight of live babies (%, five years pooled; 2018 - 22 in the September 2026 snapshot) | `ft_93092` |
| 93234 | Cancer incidence: all cancers (ratio, England = 100) | `ft_93234` |
| 93235 | Cancer incidence: breast cancer | `ft_93235` |
| 93236 | Cancer incidence: colorectal cancer | `ft_93236` |
| 93237 | Cancer incidence: lung cancer | `ft_93237` |
| 93238 | Cancer incidence: prostate cancer | `ft_93238` |

Each series ships only if `ft_msoa_series_check` passes it for the scope:
figures for at least half of the scope's MSOAs once OHID's withheld values,
placeholders and bare zeros are taken out, and a median 95% interval no wider
either side than the figure. A dropped series writes no rows, is logged with
its reason, and is listed under `known_gaps` in `data/map/indicators.js`, so
the region pack check does not hold back a region where OHID publishes too
little of it. Every verdict is written to
`data/outcomes/fingertips_msoa_extra_checks.json`. Outputs:
`data/outcomes/fingertips_msoa_extra.parquet` (MSOA, with limits and OHID's
note) and `fingertips_msoa_extra_la.parquet` (OHID's own LA figures, for the
series that ship). Wards come from MSOAs through their LSOAs, weighted by
residents, as for every Local Health series. Whether Fingertips still returns
MSOA rows for the cancer series could not be checked offline; the first
GitHub Actions run settles it.

### 5b. ONS healthy and disability-free life expectancy for MSOAs (`hle_msoa`)

ONS, *Health state life expectancy estimates for Middle layer Super Output
Areas, England and Wales: 2019 to 2023*. Healthy life expectancy (HLE) and
disability-free life expectancy (DFLE) at birth, for females and males, with
95% confidence intervals, from deaths in 2019 to 2023 and the Census 2021
health questions. Found by discovery: the dataset landing page
(`/peoplepopulationandcommunity/healthandsocialcare/healthandlifeexpectancies/datasets/healthstatelifeexpectancyestimatesformiddlelayersuperoutputareasenglandandwales`)
is read as JSON (`<page>/data`) for its editions and their files, then the
bulletin's related datasets, then ONS's search; the newest edition's
spreadsheet is taken. `HLE_MSOA_URL` overrides with a direct link, and a file
dropped in `.cache/hle_msoa/` is read instead. A file whose MSOA codes are
all 2011 ones is refused. Output: `data/outcomes/hle_msoa.parquet` (MSOA,
national, with limits, period and edition) and, where the file has LA rows,
`hle_msoa_la.parquet`. Static between releases: once the parquet is complete
it is used with no download. Wards: residents-weighted mean of their LSOAs,
each taking its MSOA's figure. LAs: ONS's own rows where present, otherwise
the map's estimate from wards, marked as such. ONS says these are not
comparable with its survey-based LA figures or the 2009 to 2013 MSOA
estimates.

### 5c. Fingertips profiles: which indicators are taken (`fingertips_profiles`)

`run_fingertips_profiles` takes the indicators named in
`data/curated/fingertips_profile_indicators.csv` (profile id, the id when it
was checked, and Fingertips' own name), matched against each profile's
indicator metadata on every run, at most `FINGERTIPS_PER_PROFILE` (40) per
profile. An id whose name still matches is taken; otherwise the id now under
that name; a name found nowhere is logged ("wanted indicator not found in
profile N's metadata") and not guessed at. It used to take the first 40 ids in
numerical order, which left out the Public Health Outcomes Framework's life
expectancy. The list keeps every QOF prevalence that `QOF_INDICATORS` is
checked against.

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

### 6e. Road casualties (DfT STATS19)

| File | Source | URL | Used for |
|------|--------|-----|----------|
| `dft-road-casualty-statistics-casualty-last-5-years.csv` | DfT, road safety data | Found on https://www.data.gov.uk/dataset/cb7ae6f0-4be6-4935-9277-47e5ce24a11f/road-safety-data, else https://data.dft.gov.uk/road-accident-safety-data/dft-road-casualty-statistics-casualty-last-5-years.csv; `STATS19_CASUALTY_URL` overrides | Source `road_casualties`. Columns found by name: `collision_year` (or `accident_year`), `casualty_severity` (1 fatal), `casualty_adjusted_severity_serious` and `lsoa_of_casualty`. A fatal casualty counts 1, any other its adjusted chance of a serious injury, over the latest five years in the file. Counted by the casualty's home LSOA, not where the collision happened. A home code in the 2021 set is used as it is; a retired 2011 code is shared among its 2021 LSOAs by Census 2021 residents (ONS LSOA 2011 to 2021 lookup). A file without the adjusted column is refused. Written to `data/environment/road_casualties_lsoa.parquet`; `road_ksi_per_1000` is shown for wards and LAs from summed counts over Census 2021 residents. |

## 7. Services — GP, pharmacy, dentist

| File | Source | URL | Used for |
|------|--------|-----|----------|
| `epraccur` extract (GP practices, no download) | NHS ODS Data Search and Export | https://www.odsdatasearchandexport.nhs.uk/api/getReport?report=epraccur | GP practice names, postcodes, status. Join to postcode → ward lookup. Fetched by the pipeline and revalidated by ETag. The former bulk-zip URL `https://files.digital.nhs.uk/assets/ods/current/epraccur.zip` now returns 403; that whole route has been retired. |
| GP list-size / registered patients CSV | NHS Digital — "Patients Registered at a GP Practice" | https://digital.nhs.uk/data-and-information/publications/statistical/patients-registered-at-a-gp-practice | Sizes the GP marker on the map. Download the monthly "gp-reg-pat-prac-all.csv". |
| `edispensary` extract (pharmacies, no download) | NHS ODS Data Search and Export | https://www.odsdatasearchandexport.nhs.uk/api/getReport?report=edispensary | Pharmacy name + postcode. Headerless 27-column ODS CSV, same layout as epraccur. Fetched by the pipeline and revalidated by ETag. |
| `ets` extract (NHS trust sites, no download) | NHS ODS Data Search and Export | https://www.odsdatasearchandexport.nhs.uk/api/getReport?report=ets | Hospital sites. ODS has no hospital category, so a site is recovered from the records filed at its postcode. Headerless, and the status column is blank: a site is open until it has a close date. |
| Distance to the nearest GP practice, pharmacy and NHS dentist (nothing to download) | Measured here from the three registers above | | Source `service_access`, after `gp`, `pharmacies` and `dentists`. Each register fetch also writes every site it keeps in England to `.cache/service_access/<kind>_sites.csv` (dentists: high street practices only), so an LSOA near the scope's edge is measured to the nearest site even when it is in the next LA. Straight-line km from the LSOA's population-weighted centroid (§1), in British National Grid. Written to `data/environment/service_access_lsoa.parquet` as `gp_dist_km`, `pharmacy_dist_km` and `dentist_dist_km`; ward and LA figures are averages weighted by Census 2021 residents. Without the national files (a `--only service_access` run on a fresh checkout) it measures to the scope's own sites and marks the source partial. |
| Care directory with filters | Care Quality Commission | Found on https://www.cqc.org.uk/about-us/transparency/using-cqc-data (`CQC_CARE_DIRECTORY_URL` overrides; a file in `.cache/cqc/`, or attached to a `cqc-source` release, is used when the page cannot be read) | Source `care_homes`. The table is read from its `Location ID` header row (.ods, .xlsx or .csv). Kept: `Care home?` = Y, not `Dormant`, with `Care homes beds` above 0, one row per location. The column names were not checked against a current file, so a file without a location, postcode or beds column fails the source rather than reporting no beds. Homes are placed by postcode (postcodes.io) in their LSOA. Written to `data/healthcare/care_homes.parquet`; `care_home_beds_per_100_75plus` is shown for wards and LAs from summed beds over people aged 75 and over in the mid-year estimates. Licence OGL v3, with CQC named as the source. |

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

Each crime is placed in the ward and the LSOA whose boundary holds its point.
Ward figures count the crimes inside each ward. `lsoa_data.json` carries the
same keys for each LSOA (`crime_total`, `crime_rate_per_1000` per 1,000
Census 2021 residents, and violence, burglary, robbery, theft and anti-social
behaviour), counted from the same points (`crime_by_lsoa`), so the map shows one
indicator at two levels. Ward figures are not rebuilt from the LSOAs: in London
the LSOAs that best fit a ward hold a tenth more or fewer of its crimes in 318
of 704 wards.

Select the Metropolitan Police and City of London forces for full coverage. Each month is one ZIP; inside,
one CSV per force per month.

---

## 10. London-specific atlases (optional enrichment)

| File | Source | URL | Used for |
|------|--------|-----|----------|
| LSOA Atlas (GLA) | London Datastore | https://data.london.gov.uk/dataset/lsoa-atlas | Supplementary LSOA stats compiled by GLA, including PTAL. Handy if you want transport accessibility. |

---

## 11. Healthy places, mental health, recorded illness and food

Five sources, one per publisher, each with its own entry in the manifest
(fetch_all_data.py, "HEALTHY PLACES, MENTAL HEALTH, RECORDED ILLNESS, FOOD
ACCESS AND FOOD OUTLETS"). Any of them may sit behind a login or on a host
that refuses GitHub's servers, so each reads a file from `.cache/<source>/`
first, and both refresh workflows copy into that folder whatever is attached
to a GitHub release named `<source>-source` (`ahah-source`, `samhi-source`,
`qof-msoa-source`, `ppfi-source`, `fhrs-source`). With no file and no way to
fetch one, the source is **skipped** with a note in the manifest saying what
to download from where and which release to attach it to. It never fails for
lack of a file, so it never holds back a region pack. A file that is there but
reads wrong does fail. With no new file, the figures an earlier run wrote are
kept.

| Source | Publisher and file | Where it is found | Used for |
|--------|--------------------|-------------------|----------|
| `ahah` | GeoDS (University of Liverpool), Access to Healthy Assets and Hazards (AHAH) version 5.1 (20 May 2026), LSOA 2021 | GeoDS's own record of the dataset (`https://data.geods.ac.uk/api/3/action/package_show?id=access-to-healthy-assets-hazards-ahah`, then the dataset id `8fa47a8b-345c-438d-a8c3-0ac758ee12be`), else the links on https://data.geods.ac.uk/dataset/access-to-healthy-assets-hazards-ahah. The LSOA data file, CSV before Parquet; never the variable dictionary, the methodology or Scotland's data zones. Whether a free GeoDS login is needed was not confirmed: a login page in place of the file counts as no file. Used only when the record names an open licence (it is listed under the Open Government Licence) | The overall index and its four domains (health services, retail hazards, air quality, green and blue space) as `ahah_index`, `ahah_health_services`, `ahah_retail`, `ahah_air_quality`, `ahah_green_blue` (higher is a less healthy place), and the drive time in minutes by car to the nearest fast food outlet, pub, off-licence, gambling outlet and tobacconist (`ahah_fast_food_min`, `ahah_pubs_min`, `ahah_off_licence_min`, `ahah_gambling_min`, `ahah_tobacco_min`). The column names of 5.1 were not seen, so columns are matched by their words: a release prefix such as `ah5` is set aside, then the base names of earlier releases (`ffood`, `pubs`, `off`, `gamb`, `tob`, `h`, `r`, `e`, `g`, `ahah`) or spelled-out headers; ranks and percentiles are not taken. A measure matched twice or not at all is reported and left out. Refused: a file on the 2011 LSOAs, a release before 5 (which measured distance, not minutes), drive times whose median is not minutes, and scores that rise with the drive times to outlets (the documented direction is the other way). `ahah_decile` is worked out here from the index across England's LSOAs, 1 the least healthy tenth. Written to `data/environment/ahah_lsoa.parquet` (this scope's LSOAs). Wards and LAs: averages of the LSOAs weighted by Census 2021 residents; the decile stays LSOA only |
| `samhi` | PLDR (University of Liverpool), Small Area Mental Health Index (SAMHI) version 4.00 | The CSV links on https://pldr.org/dataset/2noyv/small-area-mental-health-index-samhi, the wide file before the long one. Used only when that page names an open licence (the licence was not confirmed by the fact-check), or when the file was attached by hand to `samhi-source` | `samhi_index` (higher is worse mental health) for the latest year in the file, long (a year column) or wide (the year in the column name), and `samhi_decile`, worked out here across England, 1 the worst tenth. Its LSOA vintage was not confirmed: a file with no code above E01033768 (the last 2011 code) is bridged to 2021 with the ONS lookup (an unchanged or split LSOA takes its 2011 figure, a merged or redrawn one the average of its parents). Written to `data/outcomes/samhi_lsoa.parquet` with the year, the vintage and the licence it was used under. Wards and LAs: averages weighted by residents |
| `qof_msoa` | House of Commons Library, "Constituency data: health conditions", modelled estimates of GP-recorded prevalence from QOF 2024/25, MSOA | A spreadsheet linked from https://commonslibrary.parliament.uk/constituency-data-how-healthy-is-your-area/ whose name or link text says MSOA, health, condition, prevalence or QOF; failing that, MSOA files in the GitHub project `local-health-data-from-QOF` when the page links to it. The layout was not seen | `qof_est_<condition>_pct` for up to 20 conditions (AF, asthma, cancer, CHD, CKD, COPD, dementia, depression, diabetes, epilepsy, heart failure, hypertension, learning disability, serious mental illness, non-diabetic hyperglycaemia, obesity, osteoporosis, PAD, rheumatoid arthritis, stroke and TIA): long tables (a condition column beside a prevalence column) and wide ones (a column per condition) are both read, counts and age-standardised columns left out, proportions turned into percentages. A file on the 2011 MSOAs is refused. Written to `data/healthcare/qof_msoa.parquet` (every English MSOA) with the QOF year. Wards: each LSOA takes its MSOA's figure and a ward is the average of its LSOAs weighted by residents, with no figure under 80% cover (`msoa_figures_to_areas`); LAs from their MSOAs the same way |
| `ppfi` | CDRC, Priority Places for Food Index (PPFI) version 2.1 (July 2024), LSOA 2021 | Never fetched: the download needs a free CDRC login (https://data.cdrc.ac.uk/node/2515). Read from `.cache/ppfi/`, filled from the `ppfi-source` release | The combined index and five domains as England deciles, 1 the highest priority: `ppfi_decile`, `ppfi_supermarket_proximity_decile`, `ppfi_supermarket_access_decile`, `ppfi_online_decile`, `ppfi_nonsupermarket_decile`, `ppfi_family_decile`. The fuel poverty domain (the map has DESNZ's figure) and socio-demographic barriers (which repeats the IMD) are left out. The ranks may be for the whole UK, so deciles are worked out again within England from the ranks, their direction taken from the published deciles; deciles with no ranks are used only if they split England's LSOAs into tenths. Written to `data/environment/ppfi_lsoa.parquet`. Wards and LAs: `ppfi_priority_pct`, the share of residents (Census 2021) living in LSOAs in deciles 1 and 2 |
| `fhrs` | Food Standards Agency (FSA), food hygiene rating scheme | FSA's API, version 2 (`https://api.ratings.food.gov.uk/Authorities`, with the header `x-api-version: 2`, no key), for each council's open data XML file (its `FileName`), or the API's pages of establishments when there is no file. The scope's councils are found by name, with every council in the scope's FSA region. Files are kept in `.cache/fhrs/` for 20 days | Each business is placed in its LSOA by its coordinates (point in polygon on the LSOA boundaries), else its postcode (postcodes.io), and counted: every business, takeaways and sandwich shops (business type "Takeaway/sandwich shop"), pubs, bars and nightclubs ("Pub/bar/nightclub"), businesses with a rating of 0 to 5, and those rated 0, 1 or 2 ("Awaiting inspection", "Exempt" and the like are not ratings). Written to `data/environment/fhrs_lsoa.parquet`. Wards and LAs only, from summed counts: `fhrs_takeaways_per_1000` and `fhrs_pubs_per_1000` over Census 2021 residents, and `fhrs_low_hygiene_pct` over the rated businesses (none under 20 rated). An LA whose file could not be had leaves its LSOAs out and fails the source, rather than counting them as having no businesses |

---

## Minimum download checklist to reproduce the map

To rebuild from scratch, a colleague needs:

1. **Boundaries**: 3 GeoJSONs from ONS (wards, LSOAs, LADs). Postcode and
   LSOA-to-ward geography come from APIs, so there is nothing to download.
2. **IMD 2025**: nothing. The filtered parquet is committed; the raw File 7
   CSV is only needed to regenerate it after a new IoD release. The IDACI,
   IDAOPI, sub-domain and underlying indicator parquets download Files 7 and
   8 by themselves until they are committed complete.
3. **Census** — 15 ZIPs from NOMIS (one per TS table listed in §3).
4. **Claimant count**: 1 CSV via the NOMIS API. The DWP benefits come from
   Stat-Xplore instead (§4), and only with a free API key.
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
13. **Population estimates**: 1 ONS XLSX (LSOA single year of age).
14. **Road casualties**: 1 DfT CSV (the casualty file for the last five years).
15. **Care homes**: 1 CQC care directory file (ODS, XLSX or CSV).
16. **Household income (SAIE)** and **healthy and disability-free life
    expectancy for MSOAs**: nothing, once their parquets are committed. Each is
    found on its ONS dataset page by the first refresh.
17. **AHAH**: 1 GeoDS file (the LSOA CSV), found by the pipeline unless a
    login is needed; then attach it to the `ahah-source` release.
18. **SAMHI**: 1 PLDR CSV, found by the pipeline when PLDR's page names an open
    licence; otherwise attach it to `samhi-source`.
19. **QOF estimates for MSOAs**: 1 House of Commons Library spreadsheet, found
    on its page if it links one; otherwise attach it to `qof-msoa-source`.
20. **PPFI**: 1 CDRC CSV, always by hand (free CDRC login), attached to
    `ppfi-source`.
21. **Food hygiene ratings**: nothing; one FSA XML file per council, fetched by
    the pipeline (or attached to `fhrs-source` if FSA refuses GitHub).

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
| NOMIS claimant count | Monthly | Monthly |
| OHID Fingertips / QOF | Annually | Annually (usually Oct) |
| Fuel poverty | Annually (Feb–Mar) | Annually |
| Defra green/blue space access | Not yet set (first release Mar 2026) | Watch landing page — status is "in development" |
| Defra UK-AIR PCM (air quality) | Annually (usually autumn) | Annually. Automatic — the newest year on the index is taken per pollutant, so a new release needs no edit |
| TfL Unified API (stops and stations) | Continuously | Any run picks up the current register. Clear `.cache/tfl/` to force a refresh |
| NHS ODS (GP practices + pharmacies) | Monthly | Automatic. The pipeline revalidates by ETag on every run and re-downloads only when the extract actually changes |
| Charity Commission | Monthly | Monthly |
| Crime (police.uk) | Monthly (3-month lag) | Quarterly is enough |
| ONS mid-year population estimates (LSOA) | Yearly (last: November 2025, mid-2024) | Automatic; the newest file on the dataset page is taken |
| DfT STATS19 casualties | Yearly (final figures each September) | Automatic; re-downloaded after 30 days |
| CQC care directory | Monthly | Automatic; the newest file on the page is taken |
| DWP Stat-Xplore | Universal Credit and PIP every three months; DLA, Attendance Allowance, Carer's Allowance and Pension Credit once a year (March) since 2026; low income families each spring | Automatic once `STAT_XPLORE_API_KEY` is set; the newest period is taken each run |
| Distances to services | With the NHS registers | Every run that fetches the GP, pharmacy and dental registers |
| ONS small area income estimates | Every few years (FYE 2023 in Dec 2025, next not announced) | Static. Delete `data/demographics/saie_msoa.parquet` to take a new edition |
| ONS health state life expectancy for MSOAs | After each census | Static. Delete `data/outcomes/hle_msoa.parquet` to take a revised edition |
| AHAH (GeoDS) | Irregular (version 4 in 2024, 5.1 in May 2026) | Static: the file stays in `.cache/ahah/`. Attach a new version to `ahah-source` to take it |
| SAMHI (PLDR) | Irregular (v4.00 latest) | Static: the file stays in `.cache/samhi/`. Attach a new version to `samhi-source` |
| QOF estimates for MSOAs (House of Commons Library) | Yearly, after each QOF year (usually autumn) | Attach the new file to `qof-msoa-source`, or clear `.cache/qof_msoa/` so the page is read again |
| PPFI (CDRC) | Irregular (v2.1 in July 2024) | By hand: attach a new version to `ppfi-source` |
| FSA food hygiene ratings | Daily | Automatic; each council's file is fetched again after 20 days |

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
- LSOA 2021 is the authoritative small-area unit. The claimant requests
  send explicit LSOA 2021 codes rather than a Nomis geography type (`TYPE298`
  looks like the 2011 LSOA type, beside `TYPE297` for 2011 MSOAs, so it is not
  used). Any data that still comes
  in LSOA 2011 codes (DESNZ is the main one) aligns 1:1 with LSOA 2021
  for the London boroughs, so no re-mapping is needed.
- All authoritative URLs are stable landing pages — deep links to
  specific file versions break each release cycle. If a link 404s,
  navigate to the landing page and pick the latest release there.
