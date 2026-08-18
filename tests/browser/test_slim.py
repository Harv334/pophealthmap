import sys, time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8902/index.html"
if "?" not in URL: URL += "?t=1"
SETTLE = int(sys.argv[2]) if len(sys.argv) > 2 else 28
o = Options(); o.add_argument("--headless=new"); o.add_argument("--window-size=1680,1000"); o.add_argument("--disable-gpu")
o.set_capability("goog:loggingPrefs", {"browser": "ALL"})
fails = []
def check(n, ok, dt=""):
    print(("  PASS  " if ok else "  FAIL  ") + n + (f"   {dt}" if dt else ""))
    if not ok: fails.append(n)
d = webdriver.Chrome(options=o)
try:
    d.get(URL); time.sleep(SETTLE)
    wd = d.execute_script("return Object.values(WARD_DATA).filter(x=>x&&x.lad==='Brent')[0].name;")
    d.execute_script("showWard(arguments[0]);", wd); time.sleep(2.5)

    m = d.execute_script("""
      var sh = document.getElementById('ward-sheet').getBoundingClientRect();
      var mp = document.getElementById('map').getBoundingClientRect();
      var ins = document.querySelector('#ws-body .ws-insight').getBoundingClientRect();
      var st = document.querySelector('#ws-body .ws-strip').getBoundingClientRect();
      return { sheet: Math.round(sh.height), pct: Math.round(100*sh.height/mp.height),
               sideBySide: Math.abs(ins.top - st.top) < 12 && st.left > ins.right - 4,
               kpis: document.querySelectorAll('#ws-body .ws-kpi').length,
               insightReadable: ins.height > 30 };
    """)
    check("the ward bar is materially smaller", m["sheet"] <= 190, f"{m['sheet']}px, was 250")
    check("and takes a smaller share of the map", m["pct"] <= 27, f"{m['pct']}%, was 35%")
    check("the sentence and the figures sit side by side on a wide screen", m["sideBySide"], str(m))
    check("nothing was dropped to achieve it", m["kpis"] == 6 and m["insightReadable"], str(m))

    # the profile and the bar stop competing
    d.execute_script("""[...document.querySelectorAll('.sb-tab')].find(t=>t.dataset.tab==='ward').click();""")
    time.sleep(1.5)
    check("opening the full profile stands the bar down to peek",
          d.execute_script("return PH_SHEET.state()") == 1
          and d.execute_script("return document.getElementById('tab-ward').classList.contains('active')"),
          f"state {d.execute_script('return PH_SHEET.state()')}")
    check("but the bar still names the ward the profile belongs to",
          d.execute_script("return document.getElementById('ws-name').textContent.trim()") == wd)

    # border slider reaches every layer
    def weights():
        return d.execute_script("""
          function w(sel){var p=document.querySelector(sel); return p? parseFloat(p.getAttribute('stroke-width')):null;}
          return { ward: w('path[stroke="#000"]'), lsoa: w('path[stroke="#1E4B8E"]'),
                   msoa: w('path[stroke="#7A2E8E"]') };
        """)
    d.execute_script("""document.querySelector('#dl-seg button[data-dl="msoa"]').click();""")
    time.sleep(9)
    before = weights()
    d.execute_script("""
      var b = document.getElementById('border-weight');
      b.value = 8; b.dispatchEvent(new Event('input', {bubbles:true}));
    """)
    time.sleep(2)
    after = weights()
    check("the border slider now emboldens MSOA when MSOA is the layer",
          before["msoa"] and after["msoa"] and after["msoa"] > before["msoa"] + 1,
          f"{before['msoa']} -> {after['msoa']}")

    d.execute_script("""
      var b = document.getElementById('border-weight');
      b.value = 1; b.dispatchEvent(new Event('input', {bubbles:true}));
      document.querySelector('#dl-seg button[data-dl="lsoa"]').click();
    """)
    time.sleep(10)
    lb = weights()
    d.execute_script("""
      var b = document.getElementById('border-weight');
      b.value = 8; b.dispatchEvent(new Event('input', {bubbles:true}));
    """)
    time.sleep(2)
    la = weights()
    check("and LSOA too, which it never did either",
          lb["lsoa"] and la["lsoa"] and la["lsoa"] > lb["lsoa"] + 1,
          f"{lb['lsoa']} -> {la['lsoa']}")

    d.execute_script("""
      var b=document.getElementById('border-weight'); b.value=1;
      b.dispatchEvent(new Event('input', {bubbles:true}));
      document.querySelector('#dl-seg button[data-dl="ward"]').click();
    """)
    time.sleep(7)
    wb = weights()
    d.execute_script("""
      var b=document.getElementById('border-weight'); b.value=8;
      b.dispatchEvent(new Event('input', {bubbles:true}));
    """)
    time.sleep(2)
    wa = weights()
    check("wards still respond, as they always did",
          wb["ward"] and wa["ward"] and wa["ward"] > wb["ward"], f"{wb['ward']} -> {wa['ward']}")

    sev = [e for e in d.get_log("browser") if e["level"]=="SEVERE"
           and "favicon" not in e["message"]
           and not ("fonts.gstatic.com" in e["message"] and "404" in e["message"])]
    check("no severe console errors", not sev, str([e["message"][:160] for e in sev[:3]]))
finally:
    d.quit()
print()
print(("FAILED: " + "; ".join(fails)) if fails else "all checks passed")
sys.exit(1 if fails else 0)
