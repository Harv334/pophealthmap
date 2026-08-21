import sys, time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8902/index.html"
# Any query parameter suppresses the first-visit tour, which would
# otherwise drive the UI out from under every assertion below.
if "?" not in URL:
    URL += "?t=1"
SETTLE = int(sys.argv[2]) if len(sys.argv) > 2 else 28
opts = Options()
opts.add_argument("--headless=new"); opts.add_argument("--window-size=1680,1000"); opts.add_argument("--disable-gpu")
opts.set_capability("goog:loggingPrefs", {"browser": "ALL"})
fails = []
def check(n, ok, d=""):
    print(("  PASS  " if ok else "  FAIL  ") + n + (f"   {d}" if d else ""))
    if not ok: fails.append(n)
d = webdriver.Chrome(options=opts)
try:
    d.get(URL); time.sleep(SETTLE)
    check("Directory is no longer marked Soon",
          d.execute_script("""return !document.querySelector('.mode-btn[data-mode="directory"] .mode-soon')"""))

    d.execute_script("""document.querySelector('.mode-btn[data-mode="directory"]').click();""")
    time.sleep(12)
    st = d.execute_script("""
      return {
        pane: document.getElementById('tab-directory').classList.contains('active'),
        rail: document.querySelector('.mode-btn[data-mode="directory"]').getAttribute('aria-selected'),
        strip: getComputedStyle(document.querySelector('.sb-tabs')).display === 'none',
        count: document.getElementById('dir-count').textContent.trim(),
        rows: document.querySelectorAll('#dir-list .dir-item').length,
        types: document.querySelectorAll('#dir-types .q-chip').length,
        boroughs: document.querySelectorAll('#dir-borough option').length,
        sub: document.getElementById('dir-sub').textContent.trim(),
        total: PH_DIR.records() ? PH_DIR.records().length : 0,
      };
    """)
    check("Directory mode opens its own pane",
          st["pane"] and st["rail"] == "true" and st["strip"], str(st))
    # Six, not ten: schools, community centres, libraries, ESOL providers
    # and CICs were replaced by one London-wide cultural infrastructure set.
    check("all six datasets are listed", st["types"] == 6, str(st["types"]))
    check("a substantial number of places loaded", st["total"] > 20000, str(st["total"]))
    check("the count states matches out of the total",
          "of" in st["count"] and "places" in st["count"], st["count"])
    check("the list is capped rather than rendering 26,000 rows",
          st["rows"] == 250, str(st["rows"]))
    # This asserted a flat 34, being the 33 boroughs plus All of London. The
    # filter gained the four ICB boards when ICB scope was added everywhere
    # borough scope was offered, so 34 stopped describing it. Counting the
    # parts rather than the total: a bumped number would pass just as happily
    # on 33 boroughs and five boards, or on 32 and one stray.
    parts = d.execute_script("""
      var opts = [...document.querySelectorAll('#dir-borough option')].map(o => o.text.trim());
      return { total: opts.length,
               all: opts.filter(t => /^All of London$/i.test(t)).length,
               icbs: opts.filter(t => /ICB$/.test(t)).length,
               boroughs: opts.filter(t => !/^All of London$/i.test(t) && !/ICB$/.test(t)).length };
    """)
    check("the filter lists the 33 boroughs, All of London and the four boards",
          parts["boroughs"] == 33 and parts["all"] == 1 and parts["icbs"] == 4,
          str(parts))
    check("no ONS codes leaked into the borough filter",
          d.execute_script(r"""
            return [...document.querySelectorAll('#dir-borough option')]
              .every(o => !/^E\d{8}$/.test(o.value));
          """))
    check("every record resolved to a London borough",
          d.execute_script("return PH_DIR.records().every(r => r.lad)"))
    check("the header says how much data is behind it",
          "datasets" in st["sub"], st["sub"])

    # search
    d.execute_script("""
      var q = document.getElementById('dir-q');
      q.value = 'wembley'; q.dispatchEvent(new Event('input', {bubbles:true}));
    """)
    time.sleep(1.2)
    s = d.execute_script("""
      return { count: document.getElementById('dir-count').textContent.trim(),
               matched: PH_DIR.filtered().length,
               total: PH_DIR.records().length,
               n: document.querySelectorAll('#dir-list .dir-item').length,
               allMatch: [...document.querySelectorAll('#dir-list .dir-item')]
                 .every(r => r.textContent.toLowerCase().includes('wembley')) };
    """)
    check("search narrows the list", 0 < s["matched"] < s["total"], f"{s['matched']} of {s['total']}")
    check("and every row shown actually matches", s["allMatch"])

    # type filter
    d.execute_script("""
      var q = document.getElementById('dir-q'); q.value = '';
      q.dispatchEvent(new Event('input', {bubbles:true}));
      document.querySelector('#dir-types [data-dtype="culture"]').click();
    """)
    time.sleep(1.2)
    t = d.execute_script("""
      return { rows: [...document.querySelectorAll('#dir-list .dir-item')]
                 .map(r => r.textContent),
               count: document.getElementById('dir-count').textContent.trim() };
    """)
    check("a type filter restricts to that type",
          t["rows"] and all("Culture" in r for r in t["rows"]),
          str(t["count"]) + " / " + (t["rows"][0][:60] if t["rows"] else "none"))

    # clicking a row puts it on the map
    d.execute_script("document.querySelector('#dir-list .dir-item').click();")
    time.sleep(2)
    m = d.execute_script("""
      return { marker: PH_DIR.marked(),
               popup: !!document.querySelector('.leaflet-popup'),
               zoom: map.getZoom() };
    """)
    check("clicking a row pins it on the map and opens its details",
          m["marker"] and m["popup"] and m["zoom"] >= 15, str(m))

    # leaving takes the pin with it
    d.execute_script("""document.querySelector('.mode-btn[data-mode="explore"]').click();""")
    time.sleep(1.2)
    left = d.execute_script("""
      return { marker: PH_DIR.marked(),
               pane: document.getElementById('tab-directory').classList.contains('active'),
               strip: getComputedStyle(document.querySelector('.sb-tabs')).display !== 'none' };
    """)
    check("leaving Directory removes its pin and restores the tabs",
          not left["marker"] and not left["pane"] and left["strip"], str(left))

    # re-entering does not refetch
    before = d.execute_script("""
      return performance.getEntriesByType('resource').filter(r => r.name.includes('culture.json')).length;
    """)
    d.execute_script("""document.querySelector('.mode-btn[data-mode="directory"]').click();""")
    time.sleep(3)
    after = d.execute_script("""
      return { fetches: performance.getEntriesByType('resource').filter(r => r.name.includes('culture.json')).length,
               rows: document.querySelectorAll('#dir-list .dir-item').length };
    """)
    check("re-entering reuses what it loaded", after["fetches"] == before and after["rows"] > 0,
          f"{before} -> {after}")

    sev = [e for e in d.get_log("browser") if e["level"] == "SEVERE"
           and "favicon" not in e["message"]
           and not ("fonts.gstatic.com" in e["message"] and "404" in e["message"])]
    check("no severe console errors", not sev, str([e["message"][:180] for e in sev[:3]]))
finally:
    d.quit()
print()
print(("FAILED: " + "; ".join(fails)) if fails else "all checks passed")
sys.exit(1 if fails else 0)
