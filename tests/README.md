# Tests

These are the project's regression tests. They lived in Claude Code session
scratchpad directories until August 2026, which meant they were deleted
whenever a session ended and nobody could run them twice. Three separate
sessions' worth are collected here.

There is no runner and no framework. Every file is a script that exits non-zero
if something it checks is wrong, and prints a `PASS` or `FAIL` line per check so
a failure says what broke rather than only that something did.

```
tests/
  check_dashes.py  no em or en dash in user-facing text; runs first in
              GitHub Actions (.github/workflows/tests.yml)
  smoke/      Playwright + axe-core; runs in GitHub Actions on every pull
              request and before every deploy (.github/workflows/tests.yml)
  browser/    Selenium, against a served copy of the site
  pipeline/   Python, against fetch_all_data.py and the files it writes
```

## check_dashes.py

The house style never uses an em dash or an en dash, and writes ranges as
"1 to 2". This reads the map page, the four pages around it, the Ask panel and
the label files in `data/map/`, sets HTML, JavaScript and CSS comments aside,
and prints `file:line` for every dash left, including `&mdash;` and `\u2014`
spellings. Regular expression literals are skipped, because a pattern that
finds dashes to replace them is never shown to anyone; any other line that
matches dashes for that reason carries a `dash-ok` comment saying why.

```bash
python3 tests/check_dashes.py
```

## smoke/

The one suite that runs automatically. It loads London, the North East (when
`data/regions/north-east/` exists), the England warning and the four pages
around the map (methodology, accessibility, privacy and 404), exercises the
indicator picker, cards, two-indicator map, ward sheet, phone drawer and
tapped-area card, census figures by output area (when `data/map/oa/` exists),
and runs axe-core (WCAG 2.2 A/AA and best practice) in each state. Any
accessibility violation fails it. The four pages are opened with the device
set to dark, and checked for the shared stylesheet (`pages.css`), the same
header and footer, and a light page. The 404 page's links are absolute to
pophealth.uk, so the suite answers those requests from the working copy.
It checks that no list (the picker, Query, Compare, Data export or Ask)
offers an indicator the loaded data holds no figures for, that the ones the
page works out from the ward file stay, and that a new one appears once
indicators.js lists it.
It also checks the names: every indicator is named from the one label table
(`IND_NAMES`), Compare offers each measure once, and the old names (Soc rent,
Boards, metric, VCSE, Borough or Local authority as the level) are gone.
For the indicator cards it checks that one control opens a card and a button at its
top closes it, that no card or description carries internal codes, "=",
"i.e.", dashes or title-case categories, that every Census card describes
the method the pipeline uses, and that periods and dates are written out. For the Data
export it checks the column headings (label, unit and period, and "(LA
figure)"), the shared decimal places, the groups, the notes file, the folding
steps, the pinned download bar and the in-panel preview. For charities, the
Directory and the PCN list it checks that the Directory opens on a search
prompt under a heading built from the live count, that "E1 6" finds no SE1
postcodes, that names are in title case and sort past leading punctuation,
that the Charities tab and the Directory agree on one charity count, the key
to the charity dots and the ring style of their groups, that the PCN list has
no placeholder and no "Gp", and that a postcode search lists the nearest GP
practices, pharmacies and charities in the ward sheet. postcodes.io is
answered locally in that block, so it needs no network. For failure and
loading states it blocks files with `page.route`: with `data/ward_core.json`
blocked the whole ward file stands in, with `lsoa_data.json` blocked a plain
message appears and Try again recovers, with both ward files blocked the
outlines show under a banner, with `data/map/wards.js` blocked the loading
screen offers a reload, and an Ask request with no answer is given up after
30 seconds (the clock is fast-forwarded, so the check does not wait).

```bash
npm install --no-save playwright axe-core
python3 -m http.server 8792          # from the repo root
node tests/smoke/smoke.cjs http://localhost:8792
```

`SMOKE_SETTLE_MS` sets how long the map is given to load (default 9000).

## browser/

These drive real Chrome. They need the site served over HTTP, because several
of them read `fetch()` responses that a `file://` page cannot make.

```bash
python -m http.server 8902          # from the repo root, in its own terminal
.venv/Scripts/python.exe tests/browser/test_split.py
```

Each takes an optional URL and settle time:

```bash
# against production, which needs the longer settle
.venv/Scripts/python.exe tests/browser/test_split.py https://pophealth.uk/index.html 34
```

`test_meth.py` is the exception: it tests `methodology.html`, so give it that
page and not `index.html`. Pointing it at `index.html` produces three confident
failures about content that was never supposed to be there.

Notes that save time:

- **The map needs 28 to 34 seconds to settle.** Production needs the longer
  figure. The default in each file is tuned for local.
- **`?t=1` is appended by most of these** and suppresses the first-visit tour,
  which would otherwise drive the UI out from under every assertion.
- **Find tour chapters by title or by the pane they open, never by position.**
  The tour has gone from 6 chapters to 11 and back to 10, and position-based
  assertions broke on every one of those changes without anything being wrong.
- **Mobile checks use Chrome `mobileEmulation` at 390x844 with touch.** A narrow
  desktop window reports 500px for a 390px device and is not a substitute.
- **A `gstatic.com` 404 is filtered explicitly**, not ignored wholesale. Google's
  `css2` response points at a JetBrains Mono `latin-ext` woff2 that gstatic no
  longer serves. It is cosmetic, and it is a real console 404.
- **Read a failure before believing it.** Roughly a third of the failures seen
  in this project were stale assertions describing behaviour that had been
  deliberately changed. The other two thirds were real. `test_tools` spent a
  week looking stale while correctly reporting that the LSOA export produced an
  empty file.

## pipeline/

These import `fetch_all_data.py` or read what it writes. They work out the repo
root from their own location, so they run from anywhere:

```bash
.venv/Scripts/python.exe tests/pipeline/test_manifest.py
```

`test_names_and_ci.py` checks the display-name and phone helpers, the rule that
stores a Fingertips placeholder row as missing, and the DESNZ fuel poverty year,
all offline, so they are tested before the next refresh runs them.

`test_census_counts.py` builds Nomis-shaped Census tables for a dozen output
areas, runs them through the pipeline's own Census rules, and checks that ward
and LA percentages are the summed numerator counts over the summed
denominator counts, that output area counts added up to LSOAs give the LSOA
percentages, and that the files the map reads keep their shape. It needs
pandas and numpy but no network.

`test_census_additions.py` does the same for the Census tables added or fixed
in October 2026: routine and semi-routine is NS-SEC L12 and L13 only (not L7
to L9 as well), household deprivation comes from TS011, and household
composition, spoken English, overcrowding, 50 hours of care, long-term
sickness and six detailed ethnic groups are counted from the columns the
publisher prints. It checks the ward and LA figures from the counts, wards
built from LSOAs when the output area file lacks a figure, and that a label
that does not match or a table that did not download writes nothing and marks
the census source partial.

`test_saie.py` and `test_hle_msoa.py` cover the two ONS MSOA releases, ONS's
income estimates for small areas and its healthy and disability-free life
expectancy, from synthetic workbooks laid out as ONS lays them out: finding
the newest edition from the dataset landing page (its JSON, its links, the
bulletin's related datasets, ONS's search), matching the columns and their
95% limits, refusing a file on the 2011 MSOAs, wards and LAs weighted by
households for income and by residents for life expectancy, and the failure
paths. `test_local_health_extra.py` covers low birth weight and cancer
incidence from Local Health and the per-series checks that decide whether
each ships (coverage, withheld values, interval width), with the skipped,
partial and failed outcomes in the manifest. `test_fingertips_profiles.py`
checks that each Fingertips profile's indicators are chosen by name from
`data/curated/fingertips_profile_indicators.csv`, not as the first ids in
number order. All four are offline.

`test_ahah.py`, `test_samhi.py`, `test_qof_msoa.py`, `test_ppfi.py` and
`test_fhrs.py` cover the last small-area sources, one per publisher: GeoDS's
Access to Healthy Assets and Hazards, PLDR's Small Area Mental Health Index,
the House of Commons Library's QOF estimates for MSOAs, CDRC's Priority
Places for Food Index and the FSA food hygiene register. Each uses synthetic
files laid out as the publisher's are believed to be (the docstring says
where that comes from and that it is unverified), and checks the column
matching and its refusals, the maths, ward and LA figures, a file attached by
hand in `.cache/<source>/`, the skip (never a failure) when there is no file,
and kept figures. All five are offline.

`test_guards.py` and `test_guards_gp.py` copy real downloads out of `.cache/`
to build their fixtures, so they need a populated cache. `.cache/` is not in the
repo: run the relevant pipeline source once first, or skip them.

`test_worker_cap.py`, `test_worker_followup.py` and `test_worker_guard.py` run
the Cloudflare Worker (`worker/src/index.js`) under Node through
`worker_harness.py`, with its real Durable Object counter over in-memory
storage, a fake burst limiter and a stubbed Anthropic API. They check the caps,
which messages count as questions, and what the Worker refuses and sends
upstream. They need Node 18 or later, cost nothing and call nothing real.

`test_assistant_tools.py` is the assistant's tool functions, transliterated into
Python and checked against `ward_data.json` directly. It was called
`test_tools.py` and was renamed here: the browser directory has a different
`test_tools.py`, and two files with one name in one project is a trap.

## What passes

Recorded so a failure can be told from a known gap. Against production at
`9a7b73d` plus the Data export fix.

| suite | state |
|---|---|
| `test_count` | passes |
| `test_dir` | passes |
| `test_fixes` | passes |
| `test_landing` | passes |
| `test_meth` | passes |
| `test_msoa` | passes |
| `test_new` | passes |
| `test_report` | passes |
| `test_sheet_query` | passes |
| `test_split` | passes |
| `test_tools` | passes |
| `test_tour` | rewritten for the six-chapter tour, see below |
| `test_mobile` | **fails, deliberately left that way** |
| `test_ovpicker` | fails, one stale assertion |
| `test_slim` | fails, one stale assertion |
| `test_tidy` | fails, one stale assertion |

`test_tour` was written for the eleven-chapter tour and looked for chapters
called "Colour the map" and "Click a ward" that no longer exist. It now walks
the six chapters (What PopHealth Map is, Choose an indicator, Select a place,
The tools, What the figures cannot tell you, Your turn), completes the last
one by choosing an indicator, and checks Previous, Finish and Escape. Its
page scripts were checked against the site in Playwright; it has not yet been
run under Selenium, because the machine it was written on had no ChromeDriver
matching its Chrome. Set `CHROME_BIN` to choose the browser.

`test_mobile` asserts a sidebar structure that changed upstream: commit
`b9dd9aa` made Indicators the default tab and `cfa5c69` folded the Map controls
sliders away. It looks for `.lrow` rows and `#zoom-row` in a pane that no longer
holds them, and dies on `getComputedStyle(null)`.

It needs rewriting against the new sidebar, not patching. Do not relax it into
something that passes without checking anything: the touch-target sizes it
guards are real, and they were a real fix.

`test_ovpicker`, `test_slim` and `test_tidy` were each written to verify one
change and have not been kept current. Their surviving failures are stale, and
each was checked rather than assumed:

- `test_slim` looks for LSOA paths by a hardcoded stroke colour,
  `path[stroke="#1E4B8E"]`, which the styling no longer uses, so it measures
  nothing and compares `None` with `None`. The layer itself is fine: it draws
  all 4,994 within five seconds of picking the LSOA level.
- `test_ovpicker` expects a category count the indicator menu has moved past.
- `test_tidy` asserts wording that has since been rewritten.

Fix them by asserting what the thing does rather than the colour or the count
it happened to have on the day.

`shot.py` is not a test. It takes screenshots into `browser/_shots/`.
