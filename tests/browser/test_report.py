"""Report builder: pick LSOAs, total them up correctly, export."""
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
        "return [...document.querySelectorAll('.mode-btn')].map(b => b.dataset.mode);")
    # The LSOA-area tool is Custom area now; Report builder is the CSV tool.
    check("Custom area is in the rail, straight after Compare",
          rail == ["explore", "query", "compare", "report", "csv", "directory"], str(rail))

    d.execute_script("""document.querySelector('.mode-btn[data-mode="report"]').click();""")
    time.sleep(6)
    st = d.execute_script("""
      return { pane: document.getElementById('tab-report').classList.contains('active'),
               rail: document.querySelector('.mode-btn[data-mode="report"]')
                       .getAttribute('aria-selected'),
               level: typeof currentDataLevel !== 'undefined' ? currentDataLevel : null,
               lsoaOn: document.getElementById('tlsoa').checked,
               strip: getComputedStyle(document.querySelector('.sb-tabs')).display === 'none',
               empty: document.getElementById('rb-summary').textContent.trim().length > 40 };
    """)
    check("the mode opens its own pane", st["pane"] and st["rail"] == "true" and st["strip"], str(st))
    check("and switches the map to neighbourhoods, which is what it builds from",
          st["level"] == "lsoa" and st["lsoaOn"], str(st))
    check("it says what to do before anything is picked", st["empty"])

    # pick five LSOAs from one ward, by firing the layer's own click
    picked = d.execute_script("""
      var codes = [];
      lsoaLyr.eachLayer(function (l) {
        if (codes.length >= 5) return;
        var p = l.feature.properties;
        var r = LSOA_DATA[p.code];
        var i = r && (r.indicators || r);
        if (i && i.census_population != null && i.imd_score != null) {
          codes.push(p.code); l.fire('click');
        }
      });
      return codes;
    """)
    time.sleep(2)
    sel = d.execute_script("return PH_REPORT.selected();")
    check("clicking neighbourhoods adds them to the area",
          len(sel) == 5 and sel == picked, f"{len(sel)} selected")

    # the arithmetic
    agg = d.execute_script("return PH_REPORT.aggregate();")
    expected = d.execute_script("""
      var codes = PH_REPORT.selected();
      var pop = 0, sumClaim = 0, num = 0, den = 0, numImd = 0;
      codes.forEach(function (c) {
        var r = LSOA_DATA[c]; var i = r.indicators || r;
        var p = parseFloat(i.census_population) || 0;
        pop += p;
        if (i.claimant_count != null) sumClaim += parseFloat(i.claimant_count);
        if (i.census_bad_health_pct != null) { num += parseFloat(i.census_bad_health_pct) * p; den += p; }
        if (i.imd_score != null) numImd += parseFloat(i.imd_score) * p;
      });
      return { pop: pop, claim: sumClaim,
               badHealth: den ? num / den : null,
               imd: den ? numImd / den : null };
    """)
    check("population is summed",
          abs(agg["population"] - expected["pop"]) < 0.5,
          f"{agg['population']} vs {expected['pop']}")
    check("counts are summed, not averaged",
          abs(agg["indicators"]["claimant_count"] - expected["claim"]) < 0.5,
          f"{agg['indicators']['claimant_count']} vs {expected['claim']}")
    check("percentages are population-weighted, not a plain mean",
          abs(agg["indicators"]["census_bad_health_pct"] - expected["badHealth"]) < 0.01,
          f"{agg['indicators']['census_bad_health_pct']:.4f} vs {expected['badHealth']:.4f}")
    check("and so are scores",
          abs(agg["indicators"]["imd_score"] - expected["imd"]) < 0.01,
          f"{agg['indicators']['imd_score']:.4f} vs {expected['imd']:.4f}")

    plain = d.execute_script("""
      var codes = PH_REPORT.selected(); var t = 0, n = 0;
      codes.forEach(function (c) {
        var i = LSOA_DATA[c].indicators || LSOA_DATA[c];
        if (i.census_bad_health_pct != null) { t += parseFloat(i.census_bad_health_pct); n++; }
      });
      return n ? t / n : null;
    """)
    check("the weighted figure genuinely differs from a plain average",
          abs(agg["indicators"]["census_bad_health_pct"] - plain) > 1e-9,
          f"weighted {agg['indicators']['census_bad_health_pct']:.4f} vs plain {plain:.4f}")
    check("a national rank is not averaged into a meaningless number",
          "imd_rank" not in agg["indicators"])

    ui = d.execute_script("""
      return { count: document.getElementById('rb-count').textContent.trim(),
               stats: document.querySelectorAll('#rb-summary .rb-stat').length,
               rows: document.querySelectorAll('#rb-list .rb-item').length,
               painted: [...document.querySelectorAll('path')]
                          .filter(p => p.getAttribute('fill') === '#00703C').length };
    """)
    check("the panel totals it up", "5 LSOAs" in ui["count"] and ui["stats"] >= 6,
          ui["count"])
    check("lists what is in it", ui["rows"] == 5, str(ui["rows"]))
    check("and paints the area on the map", ui["painted"] == 5, str(ui["painted"]))

    # clicking again removes
    d.execute_script("""
      var c = PH_REPORT.selected()[0];
      lsoaLyr.eachLayer(function (l) { if (l.feature.properties.code === c) l.fire('click'); });
    """)
    time.sleep(1.2)
    check("clicking a second time takes one out",
          len(d.execute_script("return PH_REPORT.selected();")) == 4)
    d.execute_script("document.querySelector('#rb-list .x').click();")
    time.sleep(1)
    check("and so does the remove button in the list",
          len(d.execute_script("return PH_REPORT.selected();")) == 3)

    # naming and export
    d.execute_script("""
      var n = document.getElementById('rb-name');
      n.value = 'Test neighbourhood'; n.dispatchEvent(new Event('input', {bubbles:true}));
    """)
    handles = len(d.window_handles)
    d.execute_script("document.getElementById('rb-report').click();")
    time.sleep(3)
    check("the report opens", len(d.window_handles) > handles)
    if len(d.window_handles) > handles:
        d.switch_to.window(d.window_handles[-1])
        txt = d.execute_script("return document.body.innerText;")
        check("it is named as the user named it", "Test neighbourhood" in txt)
        check("it says how the figures were combined",
              "population-weighted" in txt and "summed" in txt)
        check("and that it is not an official geography",
              "not an official geography" in txt)
        check("it lists the neighbourhoods it is made of",
              txt.count("E01") >= 3, str(txt.count("E01")))
        d.close()
        d.switch_to.window(d.window_handles[0])

    # leaving takes the paint with it
    d.execute_script("""document.querySelector('.mode-btn[data-mode="explore"]').click();""")
    time.sleep(1.5)
    check("leaving the mode clears the area from the map",
          d.execute_script("""
            return [...document.querySelectorAll('path')]
                     .filter(p => p.getAttribute('fill') === '#00703C').length === 0;
          """))
    d.execute_script("""document.querySelector('.mode-btn[data-mode="report"]').click();""")
    time.sleep(4)
    check("and coming back restores it rather than resetting it",
          len(d.execute_script("return PH_REPORT.selected();")) == 3
          and d.execute_script("""
            return [...document.querySelectorAll('path')]
                     .filter(p => p.getAttribute('fill') === '#00703C').length === 3;
          """))

    sev = [e for e in d.get_log("browser")
           if e["level"] == "SEVERE" and "favicon" not in e["message"]
           and not ("fonts.gstatic.com" in e["message"] and "404" in e["message"])]
    check("no severe console errors", not sev, str([e["message"][:170] for e in sev[:3]]))
finally:
    d.quit()

print()
print(("FAILED: " + "; ".join(fails)) if fails else "all checks passed")
sys.exit(1 if fails else 0)
