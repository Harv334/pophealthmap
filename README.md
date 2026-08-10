# PopHealth Map

An interactive map of population health across all 33 London boroughs, at ward
and LSOA level, and a single Python script that rebuilds it from public data.

Live map: https://harv334.github.io/pophealthmap/

The pophealthmap.uk domain is not registered yet. See `DOMAIN.md` for how to
move the site onto it, and why the `CNAME` file must not be added first.

Every figure comes from an open government or NHS source. Nothing is modelled,
estimated or smoothed: if a number is on the map, it is in a published dataset,
and `RAW_DATA_SOURCES.md` says which one.

## What's in this repo

| Path                | What it is |
|---------------------|----------------------------------------------------|
| `index.html`        | The map itself - Leaflet, deployed to GitHub Pages |
| `data/map/`         | The map's data as classic scripts loaded before the app: ward and borough boundaries, LSOA IMD, GP practices, hospitals. Moved out of `index.html` in Phase 3.1, which took it from 1.77 MB to 486 KB. |
| `data/map/assistant.js` | The AI question panel. Inert until `ASSISTANT_ENDPOINT` points at a deployed Worker. |
| `worker/`           | Cloudflare Worker that proxies the Anthropic API. Holds the only secret in the project. See `worker/README.md`. |
| `map_data.py`       | Reads `data/map/*.js` from Python. Use this rather than parsing `index.html`. |
| `fetch_all_data.py` | One script. Downloads everything, builds the JSON the map reads, writes `data/map/`. |
| `ward_data.json`    | Ward-level indicators (704 wards) - consumed by the map at load |
| `lsoa_data.json`    | LSOA-level IMD and census columns (4,994 London LSOAs) |
| `pharmacies.json`   | Pharmacy point data (1,737 rows) |
| `data/`             | Intermediate Parquet files, one per source. Committed so you can open them in Power BI or pandas without rerunning fetches. |
| `data/boundaries/`  | LSOA, ward and borough GeoJSONs |
| `data/meta/manifest.json` | Per-source status and timestamps from the last run. The map's freshness label reads this. |
| `.cache/`           | Raw downloads (gitignored) |

## Coverage

All 33 London boroughs plus the City of London: 704 wards, 4,994 LSOAs, 1,146
GP practices, 1,737 pharmacies and 1,877 dental practices.

Deprivation covers 684 of the 704 wards. The 20 without a score are all City of
London wards, which are tiny and share very few LSOAs between them, so there is
nothing to aggregate. That is a property of the geography, not a gap in the
fetch.

## Quick start

```bash
pip install -r requirements.txt
python fetch_all_data.py
```

That is the whole setup. **No manual downloads are required** - every source is
either fetched from an open API or committed to the repo. No accounts, no API
keys.

One file is optional: drop an NHS hospital CSV at `.cache/hospitals/Hospital.csv`
to add hospital markers ([source](https://www.nhs.uk/about-us/nhs-website-datasets/)).

### Where the data comes from

The NHS registers are API-backed. `fetch_all_data.py` pulls the ODS `epraccur`
(GP), `edispensary` (pharmacy) and `egdpprac` (dental) extracts from the ODS
Data Search and Export API, caching each with an `.etag` sidecar. Reruns send
that ETag as `If-None-Match`, so an unchanged file costs a single `304` and no
download. Each download is validated before it replaces the cache; if validation
fails the cached copy is kept and the run continues.

Dental practices are ODS plus a curated overlay
(`data/curated/dental_practices_curated.json`) that adds NHS availability, which
ODS does not record. The two are joined on postcode rather than name, because
ODS names practices generically ("Dental Surgery") often enough that name
matching double-plots them.

Postcode geography is API-backed. Postcodes resolve to LSOA, ward and borough
through the [postcodes.io](https://postcodes.io) bulk API, cached per postcode,
and LSOAs are attributed to wards using the ONS `LSOA21_WD25_LAD25_EW_LU_v2`
best-fit lookup. Boundaries come from the ONS Open Geography Portal. None of
this needs a manual download, so the old 250 MB ONSPD zip is gone.

Deprivation is committed, not downloaded. IMD 2025 is static between releases
(the previous was 2019) and the gov.uk asset URL carries a media hash that
changes each time, so `data/demographics/imd2025.parquet` ships with the repo:
33,755 English LSOAs, the overall score, decile and rank, and the seven domain
scores. To rebuild after a new IoD, download File 7 (All Ranks, Scores, Deciles
and Population Denominators) from
https://www.gov.uk/government/statistics/english-indices-of-deprivation-2025
into `.cache/imd2025/` and rerun `--only imd`; a raw file present is always
preferred over the parquet.

Ward deprivation is population-weighted from LSOAs, which is how MHCLG
aggregates IMD. An average of deciles is not a decile, so that field is
published as `imd_decile_mean` rather than pretending to be one.

## Running

```bash
python fetch_all_data.py                 # everything
python fetch_all_data.py --only imd      # one source, then re-export
python fetch_all_data.py --skip crime    # skip a slow source
python fetch_all_data.py --export-only   # rebuild JSON from cached Parquets
```

Refresh `index.html` in your browser afterwards, or push and let Pages redeploy.

The script keeps going on per-source failures: a broken URL will not wipe your
other outputs, and the failing source's Parquet is left alone so the previous
run's data survives. It refuses to replace a non-empty output with an empty one,
which is what a wholesale fetch failure looks like.

## Automatic refresh

`.github/workflows/refresh-data.yml` runs the pipeline on a schedule and commits
whatever changed. It rebases before pushing, so a refresh landing at the same
time as a manual push retries instead of failing.

To trigger one by hand, use **Run workflow** in the Actions tab, not
**Re-run jobs** - a re-run replays the original commit rather than current
`main`.

## The AI panel

The map has an optional question panel: ask for a comparison, a ranking or a
summary of any ward or borough in plain English.

The model never receives the dataset. It is given four tools, and the browser
runs them against the JSON already loaded on the page, so every figure in an
answer is the same number the map is drawing, nothing is uploaded, and the model
cannot invent a statistic because it has none in its context.

It is off by default. `data/map/assistant.js` has an empty `ASSISTANT_ENDPOINT`
and the panel does not render until that points at a deployed Worker, so the map
works normally without it. See `worker/README.md` to deploy one.

## Hosting

GitHub Pages serves the repo root, so the site is the repo. Two constraints
worth knowing before changing paths:

- Jekyll drops any path beginning with an underscore. That is why the manifest
  lives at `data/meta/`, not `data/_meta/`. Renaming it back will 404 in
  production while working perfectly locally.
- The custom domain is set by the `CNAME` file, and adding one before the domain
  resolves takes the site offline rather than moving it. See `DOMAIN.md`.

Point the domain at Pages with four `A` records for the apex
(`185.199.108.153`, `.109.153`, `.110.153`, `.111.153`) and a `CNAME` on `www`
to `<user>.github.io`. If the DNS is on Cloudflare, set those records to
**DNS only** rather than proxied, or Pages cannot issue its certificate.

## Handing this over

For someone who just wants to refresh the map:

1. Clone the repo
2. `pip install -r requirements.txt`
3. `python fetch_all_data.py`
4. `git commit -am "data refresh YYYY-MM" && git push`

`fetch_all_data.py` has a long docstring at the top restating every download URL
in case this README goes stale. `RAW_DATA_SOURCES.md` documents each source and
`DATA_LICENCES.md` the attribution each one requires.

Licensed MIT (see `LICENSE`). The data is not: it is mostly Open Government
Licence v3 and carries its own attribution requirements.
