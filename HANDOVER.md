# Handover

You have inherited a map of London population health. It is a static website
and one Python script that rebuilds it. There is no server, no database and no
login. GitHub hosts the site, GitHub Actions refreshes the data, and everything
a visitor sees is a file in this repository.

This page is the one to read first. It covers the three things you need: how to
run it, how the data refreshes and what will go stale, and how to add data of
your own.

---

## 1. Running it

### Look at it locally

The site is static, but it fetches JSON, so opening `index.html` from the file
system will not work. Serve it:

```bash
python -m http.server 8000
```

Then open `http://127.0.0.1:8000/index.html`.

### Publish it

Every push to `main` publishes. `.github/workflows/jekyll-gh-pages.yml` builds
the repository and deploys it to GitHub Pages, and the live site is whatever is
on `main`. There is no build step of your own to run and no artefact to upload.

This copy carries no `CNAME`, so it serves from
`https://<your-account>.github.io/<repo>/`. Add a `CNAME` file holding one
domain name to serve it from your own address. It was left out rather than
copied because the original site uses `pophealth.uk`, and two repositories
claiming one domain is a conflict that GitHub resolves in favour of whichever
published most recently.

**Three things still name the original site**, and they are yours to decide on
rather than something to change on your behalf:

- a `<link rel="canonical">` in `index.html`, which tells search engines this
  page is a copy of `pophealth.uk` and that the original should rank instead
- an `og:url`, which sets what a link preview shows when the page is shared
- a JSON-LD block naming the dataset, its licence and its download URLs

While this is a copy of that site they are accurate. Once it becomes its own
thing they are wrong, and the canonical tag in particular will keep this
version out of search results. `sitemap.xml` and `robots.txt` were left out
for the same reason and would need writing fresh.

### Rebuild the data

```bash
pip install -r requirements.txt
python fetch_all_data.py
```

That takes about twenty minutes from cold and downloads roughly 400 MB. It
writes into `.cache/`, which is gitignored, so a second run is much faster.

Useful flags:

```bash
python fetch_all_data.py --only air_quality      # one source
python fetch_all_data.py --skip crime            # everything but one
python fetch_all_data.py --export-only           # rebuild the JSON the site
                                                 # reads, fetch nothing
```

`--export-only` is the one you will use most. It rebuilds `ward_data.json`,
`lsoa_data.json` and the rest from data already downloaded, in about a minute.

### Run the tests

```bash
pip install selenium
python -m http.server 8902            # leave this running
python tests/browser/test_landing.py
python tests/pipeline/test_split.py
```

There is no test runner. Each file is a script that prints `PASS` or `FAIL` per
check and exits non-zero if anything failed. **The browser tests do not all
pass today** — see "What is already broken" at the end.

---

## 2. How the data refreshes, and what will go stale

### The monthly run

`.github/workflows/refresh-data.yml` runs at 03:00 UTC on the 15th of each
month. It fetches every source, rebuilds four derived files, and commits the
result only if something actually changed. Pushing that commit publishes the
site, so a successful refresh updates the live map with no further action.

To run it yourself: **Actions → Refresh data → Run workflow**.

If a source fails, the run still finishes and opens an issue naming it. The
pipeline isolates each source, so one broken download cannot stop the other
twenty.

### Where to look when something breaks

`data/meta/manifest.json` records what every source did on its last run: status,
row count, output path and error. The methodology page reads it, so the public
site shows a source that has started failing rather than quietly serving older
figures. Check it first.

### What will need updating, and roughly when

These are not faults. They are dates and identifiers that were true when they
were written and will stop being true.

| What | When it bites | What to do |
|------|---------------|------------|
| **Ward boundaries** | Next boundary review | `ONS_LOOKUP_LAYER` in `fetch_all_data.py` names `LSOA21_WD25_LAD25_EW_LU_v2`. When wards are redrawn, ONS publish a new lookup and this must point at it. Everything ward-level flows through it. |
| **Census 2021** | 2031 | Fixed until the next census. The table IDs in `RAW_DATA_SOURCES.md` §3 will all change. |
| **IMD 2025** | ~2031 | Released about every six years. A new release changes the file name and the column set. |
| **Air quality year** | Each autumn | The pipeline takes the newest year Defra publish, but the map labels it with `PCM_LABELLED_YEAR`. When Defra publish a new year the run prints a warning telling you which labels to update. |
| **PTAL** | Whenever the GLA republish | The Atlas download URL carries a resource id that changes on republication, so the pipeline asks the Datastore API which file is current rather than remembering one. |
| **Fingertips MSOA codes** | Ongoing | Fingertips still publish against 2011 MSOA codes. 39 London MSOAs were renumbered in 2021 and are left missing rather than guessed. |
| **Hospitals** | Any time | Needs a manual `Hospital.csv`; no machine-readable link exists. The methodology page says so. |
| **DWP benefits** | Already stale | Not included. NOMIS has nothing newer than November 2018. The live figures are on DWP Stat-Xplore, which needs a registered API key and its own client. |

---

## 3. Adding data of your own

A source touches five places. The comments in `fetch_all_data.py` are written
to be read, and the two most recent sources, `run_air_quality` and
`run_tfl_transport`, are the ones to copy: both were added the way this
describes.

1. **Write a fetcher** in `fetch_all_data.py`. It returns a DataFrame keyed by
   `LSOA21CD` and writes a parquet under `data/`. Discover the download URL
   from the publisher's own index rather than pinning one; pinned URLs rot, and
   two sources here have already been silently broken by that.

2. **Register it** in the `SOURCES` dict near the bottom of the same file. The
   key is what `--only` takes.

3. **Aggregate it to wards** in `build_ward_data`, next to the other
   LSOA-level sources. `_agg_to_wards` does population weighting. Counts are
   summed; everything else is averaged.

4. **Carry it into the LSOA payload** in `build_lsoa_data`.

5. **Show it** in `index.html`: an `<option>` in the indicator `<select>`, an
   `OV_CFG` entry for the colour range and direction, an `OV_META` entry for
   the description, and a `CATS` entry for which group it appears in.

Then run `python fetch_all_data.py --only your_source` and
`python fetch_all_data.py --export-only`.

### What you do not have to write

`data/map/indicators.js` is generated by the pipeline from the payloads it has
just written. It carries, per indicator, which levels hold figures, a 5th-95th
percentile colour range, and how many distinct values exist at each level. So
step 5 needs a label, a group and a direction, and the numbers look after
themselves.

**Direction cannot be guessed.** An indicator with no stated polarity is left
out of the registry rather than given a default, because guessing paints the
best areas red. If your indicator does not appear on the map, that is the first
thing to check; the browser console logs how many indicators were configured
from measured data.

### Asking Claude to do it

This repository is written to be worked on with an AI assistant. The comments
explain why decisions were made, not just what the code does, which is what
makes that work. "Add a source for X, following how `run_air_quality` does it"
is a reasonable instruction, and the five steps above are the checklist to hold
the result against.

Whatever writes it, check the same things: does the map draw, does the number
of areas match, does the colour run the right way, and does the manifest say
the source succeeded.

---

## What is already broken

Told plainly, because a handover that hides this is worse than none.

- **The browser tests do not all pass.** Seven of the eleven have at least one
  failing check, mostly selectors that rotted as the interface changed. They
  are worth fixing before you trust them, and worth fixing *first*, because a
  suite that always fails teaches you to ignore it.
- **Hospitals** needs a manual file, as above.
- **DWP benefits** are absent, as above.
- **The City of London's 20 wards** have no deprivation score and no Local
  Health indicators. That is the geography, not a fault: the wards are tiny and
  share very few LSOAs, so there is nothing to aggregate.
- **Five layers cover North West London only** — schools, community centres,
  libraries, ESOL providers and CICs. They are badged NWL on the map. Blank
  elsewhere means not yet collected.

## Where everything else is written down

| File | What it holds |
|------|---------------|
| `README.md` | What the project is |
| `RAW_DATA_SOURCES.md` | Every raw file, its URL, and how to fetch it by hand |
| `DATA_LICENCES.md` | Licence and required attribution per source. **Read this before republishing anything** |
| `methodology.html` | The public page: every source, its geography and its year |
| `tests/README.md` | How to run the tests |
| `fetch_all_data.py` | The pipeline. Long, and commented to be read |
