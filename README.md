# NW London Population Health Mapping

Interactive Leaflet map + a single-file Python script that refreshes it.
Covers the 8 NW London ICS local authorities: Brent, Ealing,
Hammersmith & Fulham, Harrow, Hillingdon, Hounslow, Kensington & Chelsea,
City of Westminster.

Live map: https://harv334.github.io/NW-PH-Mapping/

## What's in this repo

| Path                | What it is |
|---------------------|----------------------------------------------------|
| `index.html`        | The map itself - Leaflet, deployed to GitHub Pages |
| `data/map/`         | The map's data, as classic scripts loaded before the app: ward and borough boundaries, LSOA IMD, GP practices, hospitals. Moved out of `index.html` in Phase 3.1, which took it from 1.77 MB to 484 KB. |
| `map_data.py`       | Reads `data/map/*.js` from Python. Use this rather than parsing `index.html`. |
| `fetch_all_data.py` | One script. Downloads everything, builds the JSON the map reads, writes `data/map/`. |
| `ward_data.json`    | Ward-level indicators (188 wards) - consumed by the map at load |
| `lsoa_data.json`    | LSOA-level IMD scores + census columns (33,755 LSOAs) |
| `pharmacies.json`   | Pharmacy point data (~540 rows) |
| `data/`             | Intermediate Parquet files - one per source. Committed so you can open them in Power BI or pandas without rerunning fetches. |
| `data/boundaries/`  | LSOA + ward + LAD GeoJSONs |
| `.cache/`           | Raw downloads (gitignored). Drop manual files here - see below. |

## Quick start (one-time setup)

```bash
pip install -r requirements.txt
```

That is the whole setup. **No manual downloads are required** - every source
is either fetched from an open API or committed to the repo.

Two files are optional, and only if you want them:

| File | Where to drop it | Why you might |
|------|------------------|---------------|
| IMD File 7 CSV | `.cache/imd2025/*.csv` | Only to regenerate the committed IMD parquet after a new IoD release. See below. |
| Hospital CSV | `.cache/hospitals/Hospital.csv` | Adds hospital markers to the map. https://www.nhs.uk/about-us/nhs-website-datasets/ |

OHID Fingertips (health outcomes) and police.uk (crime) are API-backed -
the script hits them directly the first time and caches the responses.

Deprivation is committed, not downloaded. IMD 2025 is static between releases
(the previous one was 2019), and the gov.uk asset URL carries a media hash that
changes every release, so `data/demographics/imd2025.parquet` ships with the
repo and is used as-is. It holds 33,755 English LSOAs and 11 columns: the LSOA
code, the overall IMD score, decile and rank, and the seven domain scores.
To rebuild it after a new IoD is published, download File 7 (All Ranks, Scores,
Deciles and Population Denominators) from
https://www.gov.uk/government/statistics/english-indices-of-deprivation-2025
into `.cache/imd2025/` and rerun `--only imd`; the pipeline prefers a raw file
over the parquet whenever one is present.

The GP practice and pharmacy registers are API-backed. `fetch_all_data.py`
pulls the NHS ODS `epraccur` and `edispensary` extracts from the ODS Data
Search and Export API and caches each one alongside an `.etag` sidecar. Reruns
send that ETag as `If-None-Match`, so an unchanged file costs a single `304`
and no download. Each download is validated before it replaces the cache; if
it fails, the cached copy is kept and the run continues.

If you have an `.cache/gp_practices/epraccur.zip` from before this change, it
is no longer needed - it is only used as a last-resort fallback when the API
is unreachable, and can be deleted.

Postcode geography is API-backed too. Postcodes are resolved to LSOA,
ward and borough through the [postcodes.io](https://postcodes.io) bulk
API, cached per postcode in `.cache/postcodes/postcodes_io.json`, and
LSOAs are attributed to wards using the ONS
`LSOA21_WD25_LAD25_EW_LU_v2` best-fit lookup from the Open Geography
Portal, cached in `.cache/ons_lookup/`. Neither needs a manual download,
so the old 250 MB ONSPD zip is no longer required.

## Running

```bash
# Run everything: fetches, transforms, writes ward/lsoa/pharmacy JSON.
python fetch_all_data.py

# Run a single source, then re-export:
python fetch_all_data.py --only imd

# Skip a slow source:
python fetch_all_data.py --skip crime

# Skip all fetching; just rebuild the JSON from cached Parquets:
python fetch_all_data.py --export-only
```

After running, refresh `index.html` in your browser (or push and let
GitHub Pages redeploy).

## When a source breaks

The script keeps going on per-source failures - a broken URL won't wipe
out your other outputs. The failing source's Parquet is left alone, so
the previous run's data survives.

To update one source:
1. Re-download the file from the URL in the table above
2. Overwrite it in `.cache/<source>/`
3. Run `python fetch_all_data.py --only <source>`

## Handing this over

For someone who just wants to refresh the map:

1. Clone the repo.
2. `pip install -r requirements.txt`
3. `python fetch_all_data.py`
4. `git commit -am "data refresh YYYY-MM" && git push`

No downloads, no accounts, no API keys.

That's it. The script has a long docstring at the top restating every
download URL in case this README ever goes stale.
