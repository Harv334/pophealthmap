import sys, time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8902/index.html"
# Any query parameter suppresses the first-visit tour, which would
# otherwise drive the UI out from under every assertion below.
if "?" not in URL:
    URL += "?t=1"
SETTLE = int(sys.argv[2]) if len(sys.argv) > 2 else 26
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

    # ── Query wording ────────────────────────────────────────────────────────
    d.execute_script("""document.querySelector('.mode-btn[data-mode="query"]').click();""")
    time.sleep(1.2)
    q = d.execute_script("""
      return {
        gone: !document.getElementById('tab-query').textContent.includes('Show me wards with'),
        steps: document.querySelectorAll('#tab-query .q-step').length,
        hints: document.querySelectorAll('#tab-query .q-hint').length,
        readback: document.getElementById('q-readback').textContent.trim(),
      };
    """)
    check("the vague 'Show me wards with' label is gone", q["gone"])
    check("three numbered steps", q["steps"] == 3, str(q["steps"]))
    check("each step says what leaving it alone does", q["hints"] == 3, str(q["hints"]))
    check("a plain-English readback of the whole query",
          "at least one" in q["readback"] and "Brent" in q["readback"]
          and "at most" in q["readback"], q["readback"][:220])

    d.execute_script("""
      var s = document.getElementById('q-scope-sel');
      s.value = ''; s.dispatchEvent(new Event('change', {bubbles:true}));
    """)
    time.sleep(0.4)
    check("the readback follows the controls",
          "anywhere in London" in d.execute_script(
              "return document.getElementById('q-readback').textContent"))

    d.execute_script("document.getElementById('q-clear').click();")
    time.sleep(0.5)
    check("cleared, it says so rather than going blank",
          d.execute_script("""
            var t = document.getElementById('q-readback').textContent;
            return t.includes('anywhere in London') && !t.includes('at least one');
          """),
          d.execute_script("return document.getElementById('q-readback').textContent.trim()")[:150])

    # ── Compare colours ──────────────────────────────────────────────────────
    wards = d.execute_script("""
      return Object.values(WARD_DATA)
        .filter(w => w && w.lad === 'Brent' && w.indicators
                     && w.indicators.imd_decile_mean != null
                     && w.indicators.census_bad_health_pct != null
                     && w.indicators.census_social_rented_pct != null)
        .map(w => w.name).slice(0, 3);
    """)
    d.execute_script("""document.querySelector('.mode-btn[data-mode="compare"]').click();""")
    time.sleep(1.0)
    for w in wards:
        d.execute_script("showWard(arguments[0]);", w); time.sleep(0.8)
    time.sleep(1.5)

    col = d.execute_script("""
      function rows(metric) {
        return [...document.querySelectorAll('#cmp-cards .cmp-card:not(.add)')].map(c => {
          var r = [...c.querySelectorAll('.cmp-c-row')].find(x => x.querySelector('.k').textContent.trim() === metric);
          if (!r) return null;
          var v = r.querySelector('.v');
          return { val: parseFloat(v.textContent), cls: v.className,
                   colour: getComputedStyle(v).color };
        }).filter(Boolean);
      }
      return { imd: rows('IMD'), bad: rows('Bad health'), soc: rows('Soc rent'),
               legend: document.getElementById('cmp-legend').textContent.trim() };
    """)
    RED, GREEN = "rgb(212, 53, 28)", "rgb(0, 112, 60)"

    imd = col["imd"]
    hi_imd = max(imd, key=lambda r: r["val"]); lo_imd = min(imd, key=lambda r: r["val"])
    check("IMD decile: the highest is green, because it is the least deprived",
          hi_imd["colour"] == GREEN and "good" in hi_imd["cls"], str(hi_imd))
    check("IMD decile: the lowest is red, because it is the most deprived",
          lo_imd["colour"] == RED and "bad" in lo_imd["cls"], str(lo_imd))

    bad = col["bad"]
    hi_bad = max(bad, key=lambda r: r["val"]); lo_bad = min(bad, key=lambda r: r["val"])
    check("Bad health: the highest is red", hi_bad["colour"] == RED, str(hi_bad))
    check("Bad health: the lowest is green", lo_bad["colour"] == GREEN, str(lo_bad))

    soc = col["soc"]
    check("Social renting has no better end, so it is marked but not coloured",
          all(r["colour"] not in (RED, GREEN) for r in soc)
          and any("hi" in r["cls"] or "lo" in r["cls"] for r in soc), str(soc))

    check("the legend explains the colour is about the indicator, not the size",
          "better" in col["legend"] and "worse" in col["legend"]
          and "not the size of the number" in col["legend"], col["legend"][:180])
    check("and names the rows it did not colour",
          "Soc rent" in col["legend"], col["legend"][-160:])

    check("highest and lowest are still marked, so colour is never the only channel",
          d.execute_script("""
            return document.querySelectorAll('#cmp-cards .v.hi').length >= 3
                && document.querySelectorAll('#cmp-cards .v.lo').length >= 3;
          """))

    sev = [e for e in d.get_log("browser") if e["level"] == "SEVERE" and "favicon" not in e["message"]]
    check("no severe console errors", not sev, str([e["message"][:200] for e in sev[:3]]))
finally:
    d.quit()
print()
print(("FAILED: " + "; ".join(fails)) if fails else "all checks passed")
sys.exit(1 if fails else 0)
