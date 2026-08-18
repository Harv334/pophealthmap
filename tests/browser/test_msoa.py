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
opts.add_argument("--headless=new"); opts.add_argument("--window-size=1600,1000")
opts.add_argument("--disable-gpu")
opts.set_capability("goog:loggingPrefs", {"browser": "ALL"})
fails = []
def check(n, ok, d=""):
    print(("  PASS  " if ok else "  FAIL  ") + n + (f"   {d}" if d else ""))
    if not ok: fails.append(n)

d = webdriver.Chrome(options=opts)
try:
    d.get(URL); time.sleep(SETTLE)
    base = d.execute_script("return document.querySelectorAll('path').length")

    check("MSOA toggle exists and starts off",
          d.execute_script("""
            var t = document.getElementById('tmsoa');
            return !!t && !t.checked;
          """))
    check("nothing fetched before it is ticked",
          d.execute_script("""
            return performance.getEntriesByType('resource')
              .filter(r => r.name.includes('msoa_boundaries')).length === 0;
          """))

    d.execute_script("document.getElementById('tmsoa').click();")
    time.sleep(6)
    on = d.execute_script("""
      var s = [...document.querySelectorAll('path')].map(p => p.getAttribute('stroke'));
      return {
        paths: document.querySelectorAll('path').length,
        msoaStrokes: s.filter(x => x === '#7A2E8E').length,
        count: document.getElementById('msoa-count').textContent.trim(),
        fetched: performance.getEntriesByType('resource')
                   .filter(r => r.name.includes('msoa_boundaries')).length,
      };
    """)
    check("ticking it fetches the layer once", on["fetched"] == 1, str(on["fetched"]))
    check("1,002 MSOAs drawn", on["msoaStrokes"] == 1002, str(on["msoaStrokes"]))
    check("the count in the sidebar matches", on["count"] == "1,002", on["count"])
    check("paths grew by the MSOA count", on["paths"] == base + 1002,
          f"{base} -> {on['paths']}")

    name = d.execute_script("""
      var f = msoaLyr.getLayers()[0].feature.properties;
      return { name: f.name, code: f.code };
    """)
    check("features carry the ONS name and code",
          name["name"] and name["code"].startswith("E02"), str(name))

    d.execute_script("document.getElementById('tmsoa').click();")
    time.sleep(1.5)
    off = d.execute_script("""
      return {
        paths: document.querySelectorAll('path').length,
        msoaStrokes: [...document.querySelectorAll('path')]
                       .filter(p => p.getAttribute('stroke') === '#7A2E8E').length,
      };
    """)
    check("unticking removes it", off["msoaStrokes"] == 0 and off["paths"] == base, str(off))

    d.execute_script("document.getElementById('tmsoa').click();")
    time.sleep(2.5)
    again = d.execute_script("""
      return {
        fetched: performance.getEntriesByType('resource')
                   .filter(r => r.name.includes('msoa_boundaries')).length,
        msoaStrokes: [...document.querySelectorAll('path')]
                       .filter(p => p.getAttribute('stroke') === '#7A2E8E').length,
      };
    """)
    check("re-ticking reuses the cached layer, no second fetch",
          again["fetched"] == 1 and again["msoaStrokes"] == 1002, str(again))

    # coexists with the ward layer rather than replacing it
    both = d.execute_script("""
      return { wards: document.getElementById('tnw').checked,
               wardPaths: document.querySelectorAll('path').length };
    """)
    check("wards stay on alongside it", both["wards"] and both["wardPaths"] > 1002, str(both))

    sev = [e for e in d.get_log("browser") if e["level"] == "SEVERE" and "favicon" not in e["message"]]
    check("no severe console errors", not sev, str([e["message"][:200] for e in sev[:3]]))
finally:
    d.quit()
print()
print(("FAILED: " + "; ".join(fails)) if fails else "all checks passed")
sys.exit(1 if fails else 0)
