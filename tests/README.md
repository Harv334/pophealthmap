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
  browser/    Selenium, against a served copy of the site
  pipeline/   Python, against fetch_all_data.py and the files it writes
```

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

`test_guards.py` and `test_guards_gp.py` copy real downloads out of `.cache/`
to build their fixtures, so they need a populated cache. `.cache/` is not in the
repo: run the relevant pipeline source once first, or skip them.

`test_worker_cap.py` runs the Cloudflare Worker's rate limiting in Chrome
against a fake KV and a stubbed Anthropic API. There is no Node on the machine
this was written on, but the Worker is plain JavaScript and Chrome can execute
it. It costs nothing and calls nothing real.

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
| `test_tour` | passes |
| `test_mobile` | **fails, deliberately left that way** |
| `test_ovpicker` | fails, one stale assertion |
| `test_slim` | fails, one stale assertion |
| `test_tidy` | fails, one stale assertion |

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
