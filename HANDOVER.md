# Handover

A map of London population health. A static website, plus one Python script
that rebuilds it from published data. No server, no database, no login.

---

## Do you need to do anything?

**No.** The data refreshes itself and the site publishes itself.

On the 15th of each month GitHub Actions downloads every source, rebuilds the
files the map reads, and commits them. That commit publishes the site. Nobody
has to be there.

Three things are worth doing anyway:

| When | What |
|------|------|
| Monthly, one minute | Open **Actions** and check the last "Refresh data" run is green |
| If a run fails | It opens an issue naming the source that broke. `data/meta/manifest.json` says what happened |
| Once a year | Read "What will go stale" below |
| Once, about ten minutes | Add a Stat-Xplore API key, to switch on the DWP benefit figures (see "DWP benefits" below) |

---

## Refreshing the data

### It happens on its own

`.github/workflows/refresh-data.yml`, 03:00 UTC on the 15th. If nothing
changed upstream, nothing is committed.

### Forcing a refresh

**Actions → Refresh data → Run workflow.** Takes about 20 minutes.

### Doing it on your own machine

```bash
pip install -r requirements.txt
python fetch_all_data.py
```

Then commit and push. Nothing has to be downloaded by hand first: every source
is either an API the script calls or a file already in the repository. Twenty minutes from cold, faster after that: downloads
are kept in `.cache/`, which is not committed.

Two flags worth knowing:

```bash
python fetch_all_data.py --only air_quality   # one source
python fetch_all_data.py --export-only        # rebuild the map's files from
                                              # data already downloaded (~1 min)
```

### When something breaks

Look at `data/meta/manifest.json`. It records, per source, whether the last run
worked, how many rows it wrote, and the error if it failed. The public
methodology page shows the same thing, so a broken source is visible rather
than quietly serving old figures.

One source failing does not stop the others.

### Repair notes, source by source

The methodology page used to carry these as panels that opened under a
failed row. They are for whoever maintains the pipeline, not for readers of
the page, so they live here. Each note says whether a person is needed at
all, because "download this file" and "the query needs fixing" are jobs for
different people. The key in brackets is the source's key in `SOURCES` at the
bottom of `fetch_all_data.py` and in `data/meta/manifest.json`.

**Fingertips, the other profiles** (`fingertips_profiles`). Fetched
automatically, by profile rather than by a list of indicator ids. Both the ids
and the names come from the API, so an indicator cannot end up on the map
under the wrong label, which is what happened when that list was maintained by
hand. To re-run: `python fetch_all_data.py --only fingertips_profiles`. Set
`FINGERTIPS_PER_PROFILE` to change how many indicators are taken from each
profile.

**Fuel poverty** (`fuel_poverty`). Fetched automatically: the pipeline reads
the gov.uk collection, picks the newest sub-regional release by the year in
its name, and takes the spreadsheet from that release's own attachment list,
so a new publication needs no edit. Only if the automatic route fails,
download the newest sub-regional fuel poverty tables (.xlsx) from
https://www.gov.uk/government/collections/fuel-poverty-sub-regional-statistics
into `.cache/fuel_poverty/`, then run
`python fetch_all_data.py --only fuel_poverty`.

**IoD 2025 beyond the IMD** (`iod_extra`, `iod_indicators`). Fetched
automatically until the two parquets in `data/demographics/` hold every
column, then static like the IMD. The pipeline finds File 7 (IDACI, IDAOPI,
sub-domains) and File 8 (underlying indicators) on the release page through
the gov.uk content API and matches every column by the words in its header.
A failure names what was missing: a file it could not download, or a column
it could not find, found twice, or found running the wrong way for its
domain. The run log lists each match as `<key> <- '<header>'`. For a missing
file, download it from
https://www.gov.uk/government/statistics/english-indices-of-deprivation-2025
into `.cache/iod2025/` (or set `IOD_FILE7_URL` or `IOD_FILE8_URL`) and run
`python fetch_all_data.py --only iod_extra iod_indicators`. For a column
whose header changed, edit its keywords in `IOD_FILE7_FIELDS` or
`IOD_FILE8_SPECS`. An indicator the 2025 release does not publish (staying on
after 16 and homes without central heating may be gone) keeps failing until
its spec and its registry entries in `index.html` are removed; until then the
map leaves it out because it has no figures.

**The small-area additions** (`sape`, `claimant_youth`, `road_casualties`,
`care_homes`, `service_access`). Each has its own entry in the manifest, so
one that fails holds back only itself. But the region pack check
(`scripts/check_region_pack.py`) refuses any pack with a failed or partial
source, and the monthly region refresh passes no `allow_sources`, so a new
source failing on a region holds that region's whole pack until it is fixed
or the region is rebuilt by hand with `allow_sources` naming it. "Skipped" is
not held: `claimant_youth` is skipped, with the reason in the manifest, when
Nomis does not publish the 18 to 24 band for LSOAs.

- `sape`: the newest LSOA single year of age file on ONS's dataset page. If
  ONS refuses the runner, download it from
  https://www.ons.gov.uk/peoplepopulationandcommunity/populationandmigration/populationestimates/datasets/lowersuperoutputareamidyearpopulationestimates
  into `.cache/sape/` (or set `SAPE_URL`) and run
  `python fetch_all_data.py --only sape`. A sheet whose single years do not
  add up to its total is refused as misread.
- `claimant_youth`: the 18 to 24 code is read from Nomis's age codelist by
  name. Skipped is the expected outcome if Nomis does not publish it for
  LSOAs; the indicator then stays off the map.
- `road_casualties`: DfT's five-year casualty file, found on the data.gov.uk
  road safety page or at its usual address (`STATS19_CASUALTY_URL`
  overrides). A file without `casualty_adjusted_severity_serious` fails
  rather than mixing adjusted and unadjusted figures.
- `care_homes`: the care directory with filters, found on CQC's "Using CQC
  data" page. If CQC refuses the runner, download the file in a browser and
  attach it to a release called `cqc-source`; both refresh workflows copy it
  into `.cache/cqc/`. The column names (`Location ID`, `Care home?`,
  `Care homes beds`, `Dormant (Y/N)`) were not checked against a current
  file, so check the first run's log: a missing column fails and names the
  columns the file has.
- `service_access`: needs no download. It runs after `gp`, `pharmacies` and
  `dentists`, which leave every site in England in `.cache/service_access/`.
  Run on its own without those files it measures only to the scope's own
  sites and is marked partial: run `--only gp pharmacies dentists
  service_access` instead.

Public transport travel times from the Urban Big Data Centre were considered
and not added: they are for 2011 LSOAs from a 2021 timetable, a newer Zenodo
version's contents are unknown, and the licence of the data itself could not
be confirmed.

**Air quality** (`air_quality`). Fetched automatically: the pipeline reads the
UK-AIR index and takes the most recent year listed for each pollutant rather
than a pinned filename, because the filenames carry a version suffix that
changes between releases. Only if the automatic route fails, download
`mapno2<year>.csv`, `mappm25<year>g.csv` and `mappm10<year>g.csv` from
https://uk-air.defra.gov.uk/data/pcm-data into `.cache/air_quality/` (named
`pcm_no2_<year>.csv` and so on), then run
`python fetch_all_data.py --only air_quality`.

**TfL stations and bus stops** (`tfl`). Fetched automatically from the open
Unified API, which needs no key. Unkeyed use is rate limited to roughly 50
requests a minute and this takes 43, so the pipeline paces itself; set
`TFL_APP_KEY` to use a free key and skip the pacing. If the API is
unreachable the previous file is kept. Re-run with
`python fetch_all_data.py --only tfl`.

**PTAL** (`ptal`). Fetched automatically. The download URL carries a resource
id that changes whenever the GLA republish, so the pipeline asks the Datastore
API which file is current rather than remembering one. Only if the automatic
route fails, download the LSOA Atlas CSV from
https://data.london.gov.uk/dataset/lsoa-atlas to
`.cache/ptal/lsoa_atlas.csv`, then run `python fetch_all_data.py --only ptal`.

**PCN catchments** (`pcn`). NHS Digital's site refuses requests from cloud
hosting, so the *Patients Registered at a GP Practice* file by LSOA has to be
downloaded in a browser and attached to the build. Without it, PCNs show their
practices but no catchment (`has_catchments` is false in
`data/map/pcn.json`).

**DWP benefits** (`dwp_uc_people`, `dwp_uc_households`, `dwp_pip`,
`dwp_dla_children`, `dwp_attendance_allowance`, `dwp_carers_allowance`,
`dwp_pension_credit`, `dwp_cilif`). See "DWP benefits" below. Until a key is
added all eight are `skipped`, which is expected and opens no issue. With a
key, a failure names what was wrong: a refused key, an error from
Stat-Xplore, or a database or field whose label no longer matches (the
message lists the nearest labels it found). Re-run with
`python fetch_all_data.py --only dwp_uc_people` (and so on), with
`STAT_XPLORE_API_KEY` set in your shell.

### DWP benefits

Universal Credit, Personal Independence Payment (PIP), Disability Living
Allowance for children, Attendance Allowance, Carer's Allowance, Pension Credit
and children in relative low income families come from DWP's Stat-Xplore. Its
API needs a free key that belongs to a person, so the pipeline cannot get one
by itself. Until you add one, these figures are not fetched and the map does
not offer them.

**To switch them on:**

1. Register for a free account at https://stat-xplore.dwp.gov.uk (the
   registration link is on the login page), and confirm it from the email
   Stat-Xplore sends.
2. Sign in and copy your API key. It is shown on your account page once you
   are signed in; Stat-Xplore's help pages, under "Open Data API", say where.
3. In this repository on GitHub, open **Settings**, then **Secrets and
   variables**, then **Actions**, and choose **New repository secret**. Name
   it `STAT_XPLORE_API_KEY` exactly, paste the key as the value, and save.
4. Open **Actions**, choose **Refresh data** and **Run workflow** (or wait for
   the 15th). **Refresh regions** passes the same secret to each region's
   build, so the regions pick it up too.

The key goes only to the pipeline step of `refresh-data.yml` and
`build-region.yml`; nothing else in the workflows sees it. If you replace the
key, update the secret; nothing else changes.

**What the run log says.** Without a key, each of the eight sources prints
its heading ("DWP Stat-Xplore: people on Universal Credit (LSOA)" and so on)
and one line, `dwp_uc_people: skipped, no Stat-Xplore API key, ...`, and the
manifest records `"status": "skipped"` with a note saying the same. With a
key, the first source prints how many databases Stat-Xplore lists, and each
source then prints the ids it chose, for example:

```
[..] dwp_uc_people: database str:database:... ('People on Universal Credit', in Universal Credit)
[..] dwp_uc_people: measure ...; date field ... ('Month'), latest ... (May 2026)
[..] dwp_uc_people: LSOA level ... ('National - Regional - LA - OAs, Lower Layer Super Output Areas') in field ...
[..] dwp_uc_people: the LSOA values are 2021 LSOAs (...); 4,994 of the 4,994 this scope needs are there
[..] dwp_uc_people: 4,994 LSOA values in 5 table request(s) of at most 1,000 (1 cell(s) each)
[OK] dwp_uc_people: 4,994 LSOAs, May 2026, ... people on Universal Credit (... requests) -> data/economy/dwp_uc_people_lsoa.parquet
```

On that first run, check four things: that each database named is the one
you would expect (the labels are matched by pattern, in `SX_DATABASES`);
that the date is the newest release; whether each benefit's LSOAs are "2021"
or "2011" (2011 ones are moved onto 2021 LSOAs through the ONS lookup, which
is expected for some of the older benefits); and how many cells are
"withheld or blank". In London each LSOA source should write 4,994 rows, one
per LSOA, and `dwp_cilif` one row per ward of each LA it could match. The
export then logs `small-area additions -> wards: dwp_uc_people_pct ...` with a
count for each new key, and the map offers them from that build on.

**Things that are expected to need a look.** The fact checks behind these
sources could not reach Stat-Xplore, so a few points are unconfirmed and the
pipeline handles them either way:

- PIP by main disabling condition may not be published for LSOAs. If not,
  `dwp_pip` is ok with a note, and only the three condition figures are left
  out.
- Universal Credit households: the payment filter is used only if a field
  offers an "In payment" value. The log says which.
- Limited capability for work is taken twelve months before the newest
  month, because assessments are backdated (`SX_LCW_LAG_MONTHS`).
- PIP, DLA, Attendance Allowance, Carer's Allowance and Pension Credit are
  shown for wards and LAs only. If the log shows they are on 2021 LSOAs with
  few withheld cells, their LSOA figures can be switched on by setting the
  fourth value of their row in `DWP_SHARES` to `True` and giving their
  `CATS` rows in index.html `g: "lsoa", lv: ["ward","lsoa"]`.
- Stat-Xplore caps the cells in one table. `SX_MAX_CELLS` and `SX_GEO_BATCH`
  keep each request small; lower them if Stat-Xplore refuses a table as too
  big.

**Children in low income families and ward boundaries.** DWP publishes these
for wards, not LSOAs, and its ward boundaries can lag the 2025 wards the map
uses. The pipeline matches DWP's wards to the map's by code and uses an LA
only when every one of its 2025 wards is there; the manifest note names any
LA left out, and if none matches the source is skipped. It does not force an
older ward onto a newer one, because a redrawn ward is a different place. To
use older wards properly you would need to share each old ward's count among
the new wards that overlap it, weighted by where its children live: the ONS
output area to ward best-fit lookups for both ward years, and the Census 2021
children aged 0 to 15 for each output area, give the weights. Until then the
simplest course is to wait for DWP's next release (each spring), which
usually moves to the latest wards. OHID's LA figure for the same measure stays
on the map either way.

### Rebuilding every region and the England view

`fetch_all_data.py` rebuilds the whole map with no manual downloads (apart
from the PCN file above): once for London and once per region
(`--scope north-east` and so on), with the England view merged from the
results by `scripts/build_england.py`. Sources, licences and per-source notes
are in `RAW_DATA_SOURCES.md` and `DATA_LICENCES.md`.

---

## What will go stale

Not faults. Dates and identifiers that were true when written.

| What | When | What to do |
|------|------|------------|
| **Ward boundaries** | Next boundary review | `ONS_LOOKUP_LAYER` in `fetch_all_data.py` names the 2025 lookup. Point it at the new one. Everything ward-level flows through it |
| **Air quality year** | Each autumn | The run prints a warning telling you which labels to update |
| **IMD** | About every 6 years | New release changes the file name and columns |
| **Census** | 2031 | Fixed until then |

---

## Adding new data

There are two cases, and they are very different amounts of work. Start by
working out which one you have.

### You have a spreadsheet of your own figures

Drop two files in `data/custom/` and run the pipeline. No Python to edit.

`my_figures.csv` — first column is the area code, `LSOA21CD` or `WD25CD`:

    LSOA21CD,wellbeing_score
    E01000001,7.2
    E01000002,6.8

`my_figures.json` beside it:

    {
      "source": "My team's survey, 2026",
      "indicators": [{
        "column": "wellbeing_score",
        "label": "Wellbeing score (1-10)",
        "higher_is_worse": false
      }]
    }

Then:

```bash
python fetch_all_data.py --only custom
python fetch_all_data.py --export-only
```

Commit and push. It appears in the indicator list under "Your data", colours
the map, and rolls up from neighbourhoods to wards on the same population
weighting every other source uses.

`data/custom/README.md` has the full field list, and there is a worked example
in that folder you can copy or delete.

`higher_is_worse` is the one field with no default. It decides which end of the
scale is red, and a wrong guess does not look like an error, it looks like a
finding.

### You are adding a national source that refreshes itself

This is the harder case: a feed somebody else publishes, that you want pulled
fresh every month. It touches five places. Copy `run_air_quality` in
`fetch_all_data.py`; it was added exactly this way and is commented
throughout.

1. **Fetch it** — write a `run_yoursource()` that returns a table keyed by
   `LSOA21CD` and writes a parquet under `data/`. Find the download URL from
   the publisher's index rather than hardcoding one. Hardcoded URLs rot, and
   two sources here were silently broken by that for months.
2. **Register it** — add it to the `SOURCES` dict at the bottom of the file.
3. **Roll it up to wards** — in `build_ward_data`, beside the other LSOA
   sources. Counts are summed, everything else is population-weighted.
4. **Add it to the LSOA file** — in `build_lsoa_data`.
5. **Show it** — in `index.html`: an `<option>` in the indicator list, plus an
   `OV_CFG` entry (colour range and direction), an `OV_META` entry
   (description) and a `CATS` entry (which group it sits in).

Then:

```bash
python fetch_all_data.py --only yoursource
python fetch_all_data.py --export-only
```

### What you do not have to work out

`data/map/indicators.js` is generated by the pipeline. It works out, for every
indicator, which area sizes have figures, a sensible colour range, and how many
distinct values exist. So step 5 needs a name, a group and a direction.

**The direction is the one thing nothing can guess.** An indicator with no
stated direction is left off the map rather than given one, because guessing
paints the best areas red. If your new indicator does not appear, that is the
first thing to check.

### Using an AI assistant

This repository is written to be worked on with one. The comments explain *why*
decisions were made, which is what makes that work. "Add a source for X,
following how `run_air_quality` does it" is a reasonable instruction.

Check the same four things however it was written: does the map draw, does the
number of areas look right, does the colour run the right way, and does
`manifest.json` say the source succeeded.

---

## Two switches you may want

Near the bottom of `index.html`:

```js
var PH_SHOW = {
  devBanner:   false,   // the "this tool is in development" strip
  methodology: false,   // the Methodology link in the footer
};
```

The development banner is off in this copy: it was a caveat about the original
site's release schedule. The methodology link is off too, though
`methodology.html` is still published and still reachable at its own URL, so
turning it back on is a one-word change.

---

## Publishing

Every push to `main` publishes the site. There is nothing to build or upload.

This copy has no `CNAME`, so it serves from
`https://<account>.github.io/<repo>/`. Add a `CNAME` file containing one domain
name to use your own address.

**Do not put `pophealth.uk` in it.** That domain belongs to the original site,
which is still maintained and still updated. A domain can be claimed by only
one repository at a time and GitHub gives it to whichever published last, so a
second `CNAME` naming it does not fail: it takes the domain, and pophealth.uk
starts serving this copy instead.

**Two things still name the original site** and are yours to change: a
`canonical` link and a JSON-LD block in `index.html`. The
canonical tag in particular tells search engines this page is a copy and should
not rank. `sitemap.xml` and `robots.txt` were left out and would need writing.

---

## Known limitations

- **The browser tests do not all pass.** Seven have a failing check, mostly
  selectors that rotted as the interface changed. Worth fixing first: a suite
  that always fails is one you learn to ignore.
- **The City of London's 20 wards** have no deprivation or health figures. The
  wards are tiny and share almost no LSOAs, so there is nothing to average.
- **DWP benefits are absent.** The public figures stop in 2018. Current ones
  need a DWP Stat-Xplore API key.
- **The question panel** from the original site is not here. It needed a paid
  API key. Four tests that exercised it were removed with it.

---

## Where things are

| File | What it is |
|------|-----------|
| `fetch_all_data.py` | The pipeline. Long, and written to be read |
| `index.html` | The whole site |
| `methodology.html` | Public page: every source, its geography, its year |
| `RAW_DATA_SOURCES.md` | Every source, with the exact URL the pipeline uses |
| `DATA_LICENCES.md` | **Read before republishing.** Licence and required wording per source |
| `tests/README.md` | How to run the tests |
