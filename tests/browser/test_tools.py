"""Custom area rename, the CSV Report builder, a tour chapter per tool, and STE."""
import re
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8902/index.html"
if "?" not in URL:
    URL += "?t=1"
SETTLE = int(sys.argv[2]) if len(sys.argv) > 2 else 30

opts = Options()
opts.add_argument("--headless=new")
opts.add_argument("--window-size=1680,1000")
opts.add_argument("--disable-gpu")
opts.set_capability("goog:loggingPrefs", {"browser": "ALL"})

fails = []


def check(name, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   {detail}" if detail else ""))
    if not ok:
        fails.append(name)


d = webdriver.Chrome(options=opts)
try:
    d.get(URL)
    time.sleep(SETTLE)

    rail = d.execute_script(
        "return [...document.querySelectorAll('.mode-btn')]"
        ".map(b => [b.dataset.mode, b.textContent.trim()]);")
    modes = [m for m, _ in rail]
    check("Custom area replaced the old Report builder name",
          ["report", "Custom area"] in rail, str(rail))
    check("and Report builder is now the CSV tool, after it",
          modes == ["explore", "query", "compare", "report", "csv", "directory"], str(modes))

    # ── the CSV builder ──────────────────────────────────────────────────────
    d.execute_script("""document.querySelector('.mode-btn[data-mode="csv"]').click();""")
    time.sleep(3)
    st = d.execute_script("""
      return { pane: document.getElementById('tab-csv').classList.contains('active'),
               levels: [...document.querySelectorAll('#cb-level button')].map(b => b.dataset.lvl),
               groups: document.querySelectorAll('#cb-cols .cb-grp-hd').length,
               scopes: document.querySelectorAll('#cb-scope option').length,
               note: document.getElementById('cb-note').textContent.trim() };
    """)
    check("the CSV builder opens", st["pane"])
    check("all four levels are offered",
          st["levels"] == ["ward", "lsoa", "msoa", "borough"], str(st["levels"]))
    check("figures are grouped", st["groups"] >= 4, str(st["groups"]))
    check("borough scope is populated", st["scopes"] > 30, str(st["scopes"]))
    check("it says how many rows and figures the file will have",
          "704 wards" in st["note"], st["note"][:80])

    got = d.execute_script("""
      PH_CSV.pick('census_population'); PH_CSV.pick('imd_decile_mean');
      var out = PH_CSV.build();
      return { rows: out.rows, cols: out.cols, header: out.header };
    """)
    check("picking figures builds a file with those columns",
          got["cols"] == 2 and got["rows"] > 600,
          f"{got['rows']} rows x {got['cols']} figures")
    check("the header names the area and then the figures",
          got["header"][:2] == ["code", "name"] and len(got["header"]) == 5,
          str(got["header"]))

    lv = d.execute_script("""
      PH_CSV.setLevel('borough');
      var a = PH_CSV.build();
      return { boroughs: a.rows,
               warned: document.getElementById('cb-note').textContent,
               colsAfterBorough: PH_CSV.columns().length };
    """)

    # LSOA is the one level whose figures are not in memory already: ward,
    # borough and MSOA all arrive during loadData, and lsoa_data.json is 7.8 MB
    # fetched on demand. Choosing it starts that fetch, so the count is not
    # there on the next line and this has to wait for it.
    #
    # It used to read the count immediately and got nought, which was taken for
    # a stale assertion. It was not: nothing in the export pane asked for the
    # file, so the count stayed at nought for the rest of the session and the
    # panel offered a file with no rows in it.
    d.execute_script("PH_CSV.setLevel('lsoa');")
    pending_note = d.execute_script(
        "return document.getElementById('cb-note').textContent")
    for _ in range(60):
        if d.execute_script("return PH_CSV.build().rows > 0"):
            break
        time.sleep(1)
    lv["lsoas"] = d.execute_script("return PH_CSV.build().rows")

    check("switching level changes what a row is",
          lv["boroughs"] == 33 and lv["lsoas"] == 4994,
          f"{lv['boroughs']} boroughs / {lv['lsoas']} LSOAs")
    check("and the wait says so rather than offering a file of nought rows",
          "0 LSOAs" not in pending_note, pending_note[:120])
    # Boroughs hold only the 18 borough-published health figures here, so
    # population and IMD genuinely cannot come along. What matters is that the
    # tool says so instead of quietly writing a narrower file.
    check("a figure the new level does not hold is dropped, and the drop is reported",
          lv["colsAfterBorough"] == 0 and "been removed" in lv["warned"],
          f"{lv['colsAfterBorough']} kept; note: {lv['warned'][:120]}")
    check("and it says what boroughs do hold",
          "published at borough level only" in lv["warned"], lv["warned"][-80:])

    # scoping to one borough
    sc = d.execute_script("""
      PH_CSV.setLevel('ward');
      var all = PH_CSV.build().rows;
      var s = document.getElementById('cb-scope');
      s.value = 'Brent'; s.dispatchEvent(new Event('change', {bubbles:true}));
      return { all: all, brent: PH_CSV.build().rows };
    """)
    check("scoping to a borough narrows the file",
          0 < sc["brent"] < sc["all"], f"{sc['brent']} of {sc['all']}")
    d.execute_script("""
      var s = document.getElementById('cb-scope');
      s.value = ''; s.dispatchEvent(new Event('change', {bubbles:true}));
    """)

    # ── a tour chapter per tool ──────────────────────────────────────────────
    d.execute_script("""document.querySelector('.mode-btn[data-mode="explore"]').click();""")
    time.sleep(1)
    d.execute_script("document.getElementById('sbar-tour').click();")
    time.sleep(2.5)
    total = d.execute_script("return PH_TOUR.length;")
    check("the tour has a chapter for each tool", total == 10, str(total))

    # Recorded by which pane each chapter opens, not by its title. The titles
    # are prose and get rewritten; the pane is what the chapter is about.
    seen, longest, longest_txt = [], 0, ""
    for i in range(total):
        seen.append(d.execute_script("""
          return { title: document.getElementById('tour-title').textContent.trim(),
                   narr: document.getElementById('tour-narr').textContent,
                   pane: (document.querySelector('.tab-pane.active') || {}).id || '' };
        """))
        for sent in re.split(r"(?<=[.!?])\s+", seen[-1]["narr"].strip()):
            n = len(sent.split())
            if n > longest:
                longest, longest_txt = n, sent
        if i < total - 1:
            d.execute_script("document.getElementById('tour-next').click();")
            time.sleep(2.3)

    for name, pane in [("Query", "tab-query"), ("Compare", "tab-compare"),
                       ("Custom area", "tab-report"), ("Report builder", "tab-csv"),
                       ("Directory", "tab-directory")]:
        check(f"a chapter of its own for {name}",
              any(x["pane"] == pane for x in seen),
              str([x["title"] for x in seen]))

    # ASD-STE100: 25 words is the descriptive limit
    check("no tour sentence runs past the 25-word limit",
          longest <= 25, f"longest {longest} words: {longest_txt[:90]}")

    d.execute_script("document.getElementById('tour-end').click();")
    time.sleep(1.5)

    sev = [e for e in d.get_log("browser")
           if e["level"] == "SEVERE" and "favicon" not in e["message"]
           and not ("fonts.gstatic.com" in e["message"] and "404" in e["message"])]
    check("no severe console errors", not sev, str([e["message"][:150] for e in sev[:3]]))
finally:
    d.quit()

print()
print(("FAILED: " + "; ".join(fails)) if fails else "all checks passed")
sys.exit(1 if fails else 0)
