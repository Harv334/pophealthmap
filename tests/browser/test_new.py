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

    # ── Compare marks a rank, and does not call it a verdict ──────────
    #
    # These rows ran green at the indicator's good end and red at its bad one.
    # Across four pinned areas that is a claim the numbers cannot carry: all
    # four can be poor, or all four fine, and the arithmetic still paints one
    # green. So the colour is gone and the arrows stay, and what is asserted
    # here is that the marking survived without the verdict.
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

    every = col["imd"] + col["bad"] + col["soc"]
    check("no compared value is painted better or worse",
          all(r["colour"] not in (RED, GREEN) for r in every),
          str([r for r in every if r["colour"] in (RED, GREEN)][:3]))
    check("and none carries a good/bad class",
          all("good" not in r["cls"] and "bad" not in r["cls"] for r in every),
          str([r["cls"] for r in every][:4]))

    for name, rowset in (("IMD decile", col["imd"]), ("Bad health", col["bad"])):
        hi = max(rowset, key=lambda r: r["val"]); lo = min(rowset, key=lambda r: r["val"])
        check(f"{name}: the highest is still marked highest",
              "hi" in hi["cls"].split(), str(hi))
        check(f"{name}: the lowest is still marked lowest",
              "lo" in lo["cls"].split(), str(lo))

    check("the legend says the arrows rank rather than judge",
          "not the best and worst" in col["legend"], col["legend"][:200])
    check("and no longer offers a better/worse key",
          "better" not in col["legend"] and "worse" not in col["legend"],
          col["legend"][:200])

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
