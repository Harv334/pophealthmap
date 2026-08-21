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

---

## What will go stale

Not faults. Dates and identifiers that were true when written.

| What | When | What to do |
|------|------|------------|
| **Ward boundaries** | Next boundary review | `ONS_LOOKUP_LAYER` in `fetch_all_data.py` names the 2025 lookup. Point it at the new one. Everything ward-level flows through it |
| **Air quality year** | Each autumn | The run prints a warning telling you which labels to update |
| **IMD** | About every 6 years | New release changes the file name and columns |
| **Census** | 2031 | Fixed until then |
| **Culture** | Yearly | The GLA republishes the Cultural Infrastructure Map. The pipeline finds the newest edition itself |

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

**Three things still name the original site** and are yours to change: a
`canonical` link, an `og:url`, and a JSON-LD block in `index.html`. The
canonical tag in particular tells search engines this page is a copy and should
not rank. `sitemap.xml` and `robots.txt` were left out and would need writing.

---

## Known limitations

- **The browser tests do not all pass.** Seven have a failing check, mostly
  selectors that rotted as the interface changed. Worth fixing first: a suite
  that always fails is one you learn to ignore.
- **The City of London's 20 wards** have no deprivation or health figures. The
  wards are tiny and share almost no LSOAs, so there is nothing to average.
- **Culture means where culture is made.** The GLA set holds rehearsal rooms,
  workshops and studios, not cinemas or libraries. It replaced five
  hand-compiled layers that covered North West London only.
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
