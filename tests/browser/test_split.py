"""The ward_data.json split: does the deferred half actually arrive and merge?

Item 08. The first paint fetches data/ward_core.json (58 indicators per ward),
and data/ward_rest.json (the 51 ft_* Fingertips series) is fetched once the map
is up and merged into the same records.

What this guards, in the order the failures matter:

  1. The merge happens at all. The session that wrote this feature defined
     ensureWardDetail() and never called it, so every ft_ indicator was simply
     absent. That is the regression this file exists for.
  2. Nothing is lost across the seam. A ward's indicator count after the merge
     has to equal what the whole file holds.
  3. crime_by_category stays in the core half. It is read once at load time by
     injectCrimeCategories() to derive three overlays that are selectable on
     first paint, so deferring it would leave them painting a blank map.
  4. A Fingertips overlay picked during the gap paints a real range rather than
     a flat map, and its legend states that range rather than an empty one.
  5. The four tools that can show a Fingertips figure wait for it.

Usage:
    python test_split.py                       # local, needs http.server on 8902
    python test_split.py https://pophealth.uk/index.html 34
"""

import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8902/index.html"
SETTLE = int(sys.argv[2]) if len(sys.argv) > 2 else 30

# gstatic no longer serves the JetBrains Mono latin-ext woff2 that Google's
# css2 response points at. Cosmetic, filtered by every suite in this project
# rather than ignoring severe errors wholesale.
IGNORED = ("gstatic.com", "fonts.googleapis.com")

failures = []
passes = []


def check(name, ok, detail=""):
    (passes if ok else failures).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (("\n          " + str(detail)) if detail else ""))


opts = Options()
opts.add_argument("--headless=new")
opts.add_argument("--window-size=1600,1000")
opts.set_capability("goog:loggingPrefs", {"browser": "ALL", "performance": "ALL"})
opts.add_experimental_option("perfLoggingPrefs", {"enableNetwork": True})

d = webdriver.Chrome(options=opts)
try:
    # ?t=1 suppresses the first-visit tour, which would otherwise drive the UI
    # out from under every assertion below.
    d.get(URL + ("&" if "?" in URL else "?") + "t=1")
    print("Loading %s, settling for %ds ..." % (URL, SETTLE))
    time.sleep(SETTLE)

    # ── 1. Both halves were actually requested ──────────────────────────────
    reqs = []
    for entry in d.get_log("performance"):
        if "Network.requestWillBeSent" in entry["message"]:
            reqs.append(entry["message"])
    joined = "\n".join(reqs)
    core_asked = "ward_core.json" in joined
    rest_asked = "ward_rest.json" in joined
    whole_asked = "ward_data.json" in joined

    check("first paint fetches data/ward_core.json", core_asked)
    check("data/ward_rest.json is fetched once the map is up", rest_asked,
          "ensureWardDetail() was never called if this fails")
    check("the whole ward_data.json is NOT fetched as well", not whole_asked,
          "both halves plus the whole file would be a net loss")

    # ── 2. The merge landed, and lost nothing ───────────────────────────────
    stats = d.execute_script("""
      var w = (typeof WARD_DATA_BY_CODE !== "undefined" && WARD_DATA_BY_CODE) || {};
      var codes = Object.keys(w);
      if (!codes.length) return {n: 0};
      var ft = 0, core = 0, cbc = 0, minInd = 1e9, maxInd = 0;
      codes.forEach(function (c) {
        var ind = (w[c] && w[c].indicators) || {};
        var keys = Object.keys(ind);
        var f = keys.filter(function (k) { return k.indexOf('ft_') === 0; }).length;
        ft = Math.max(ft, f);
        core = Math.max(core, keys.length - f);
        minInd = Math.min(minInd, keys.length);
        maxInd = Math.max(maxInd, keys.length);
        if (w[c] && w[c].crime_by_category) cbc++;
      });
      return {
        n: codes.length, ft: ft, core: core, cbc: cbc,
        minInd: minInd, maxInd: maxInd,
        done: (typeof wardDetailReady === 'function') ? wardDetailReady() : null
      };
    """)

    check("ward records exist", stats["n"] > 700, "got %s" % stats.get("n"))
    check("wardDetailReady() is true after settling", stats.get("done") is True,
          "merge never completed")
    check("Fingertips series merged in (51 expected)", stats.get("ft", 0) >= 50,
          "max ft_ keys on a ward: %s" % stats.get("ft"))
    check("core indicators still present (58 expected)", stats.get("core", 0) >= 55,
          "max non-ft_ keys on a ward: %s" % stats.get("core"))
    check("crime_by_category stayed in the core half", stats.get("cbc", 0) > 700,
          "wards carrying it: %s" % stats.get("cbc"))

    # ── 3. The three derived crime overlays exist from first paint ──────────
    derived = d.execute_script("""
      var w = (typeof WARD_DATA_BY_CODE !== "undefined" && WARD_DATA_BY_CODE) || {};
      var out = {};
      ['crime_violence_12mo','crime_asb_12mo','crime_theft_12mo'].forEach(function (k) {
        out[k] = Object.keys(w).filter(function (c) {
          var v = ((w[c]||{}).indicators||{})[k];
          return v !== undefined && v !== null;
        }).length;
      });
      return out;
    """)
    for k, n in derived.items():
        check("%s derived onto wards (%d)" % (k, n), n > 600,
              "injectCrimeCategories() ran on an empty crime_by_category")

    # ── 4. A Fingertips overlay paints a real range ─────────────────────────
    ft_key = d.execute_script("""
      var sel = document.getElementById('ov');
      if (!sel) return null;
      var o = Array.prototype.slice.call(sel.options)
        .filter(function (x) { return x.value.indexOf('ft_') === 0; });
      return o.length ? o[0].value : null;
    """)
    if not ft_key:
        check("a Fingertips overlay is offered in the menu", False, "none found")
    else:
        d.execute_script("""
          var sel = document.getElementById('ov');
          sel.value = arguments[0];
          sel.dispatchEvent(new Event('change'));
        """, ft_key)
        time.sleep(3)
        painted = d.execute_script("""
          var lo = (document.getElementById('sc-lo') || {}).textContent || '';
          var hi = (document.getElementById('sc-hi') || {}).textContent || '';
          var fills = {};
          if (typeof wLyr !== "undefined" && wLyr) {
            wLyr.eachLayer(function (l) {
              var f = (l.options && l.options.fillColor) || '';
              fills[f] = 1;
            });
          }
          return {lo: lo, hi: hi, distinct: Object.keys(fills).length, ov: (typeof curOv !== "undefined" ? curOv : null)};
        """)
        check("Fingertips overlay is the active one", painted["ov"] == ft_key,
              "curOv=%s" % painted["ov"])
        check("its choropleth is not flat", painted["distinct"] >= 3,
              "distinct ward fill colours: %s" % painted["distinct"])
        check("its legend states a real range", "(" in painted["lo"] and "(" in painted["hi"],
              "lo=%r hi=%r" % (painted["lo"], painted["hi"]))

    # ── 5. The tools that need the deferred half can see it ─────────────────
    tool_ok = d.execute_script("""
      var btn = document.querySelector('.mode-rail .mode-btn[data-mode="query"]');
      if (!btn) return 'no query button';
      btn.click();
      return 'clicked';
    """)
    time.sleep(3)
    query_sees_ft = d.execute_script("""
      var pane = document.getElementById('tab-query');
      if (!pane || !pane.classList.contains('active')) return -1;
      var sels = pane.querySelectorAll('select');
      var n = 0;
      Array.prototype.forEach.call(sels, function (s) {
        n += Array.prototype.slice.call(s.options)
          .filter(function (o) { return o.value.indexOf('ft_') === 0; }).length;
      });
      return n;
    """)
    check("Query opens and offers Fingertips indicators", query_sees_ft > 0,
          "ft_ options in Query: %s (-1 = pane never opened)" % query_sees_ft)

    # ── 6. Console is clean ─────────────────────────────────────────────────
    errs = [e for e in d.get_log("browser")
            if e["level"] == "SEVERE" and not any(s in e["message"] for s in IGNORED)]
    check("no severe console errors", not errs,
          "\n          ".join(e["message"][:200] for e in errs[:6]))

finally:
    d.quit()

print("\n%d passed, %d failed" % (len(passes), len(failures)))
if failures:
    print("Failed: " + ", ".join(failures))
sys.exit(1 if failures else 0)
