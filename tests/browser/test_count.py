import sys, time, json
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8902/index.html"
if "?" not in URL: URL += "?t=1"
o = Options()
o.add_argument("--headless=new"); o.add_argument("--window-size=1600,1000"); o.add_argument("--disable-gpu")
o.set_capability("goog:loggingPrefs", {"browser": "ALL"})
fails = []
def check(n, ok, dt=""):
    print(("  PASS  " if ok else "  FAIL  ") + n + (f"   {dt}" if dt else ""))
    if not ok: fails.append(n)
d = webdriver.Chrome(options=o)
try:
    d.get(URL); d.execute_script("localStorage.clear();")
    d.get(URL); time.sleep(28)
    d.execute_script("document.getElementById('ai-toggle').click();")
    time.sleep(1.5)
    a = d.execute_script("return document.getElementById('ai-limit').textContent.trim();")
    check("a fresh visitor sees the full allowance as a count", a == "10 of 10 left today", a)

    # Simulate spending three, the way submit() does
    for i in range(3):
        d.execute_script("""
          var raw = localStorage.getItem('phAskUsed');
          var day = new Date().toISOString().slice(0,10);
          var used = raw && JSON.parse(raw).day === day ? JSON.parse(raw).used : 0;
          localStorage.setItem('phAskUsed', JSON.stringify({day: day, used: used + 1}));
        """)
    d.get(URL); time.sleep(28)
    d.execute_script("document.getElementById('ai-toggle').click();")
    time.sleep(1.5)
    b = d.execute_script("return document.getElementById('ai-limit').textContent.trim();")
    check("the count survives a reload and keeps counting down", b == "7 of 10 left today", b)

    # exhausted
    d.execute_script("""
      var day = new Date().toISOString().slice(0,10);
      localStorage.setItem('phAskUsed', JSON.stringify({day: day, used: 10}));
    """)
    d.get(URL); time.sleep(28)
    d.execute_script("document.getElementById('ai-toggle').click();")
    time.sleep(1.5)
    c = d.execute_script("""
      var e = document.getElementById('ai-limit');
      return { txt: e.textContent.trim(), spent: e.classList.contains('spent'),
               colour: getComputedStyle(e).color };
    """)
    check("at zero it says zero and is marked", c["txt"] == "0 of 10 left today" and c["spent"], str(c))

    # yesterday's tally does not carry over
    d.execute_script("""
      localStorage.setItem('phAskUsed', JSON.stringify({day: '2001-01-01', used: 9}));
    """)
    d.get(URL); time.sleep(28)
    d.execute_script("document.getElementById('ai-toggle').click();")
    time.sleep(1.5)
    e = d.execute_script("return document.getElementById('ai-limit').textContent.trim();")
    check("a previous day's count does not carry over", e == "10 of 10 left today", e)

    sev = [x for x in d.get_log("browser") if x["level"] == "SEVERE"
           and "favicon" not in x["message"]
           and not ("fonts.gstatic.com" in x["message"] and "404" in x["message"])]
    check("no severe console errors", not sev, str([x["message"][:160] for x in sev[:3]]))
finally:
    d.quit()
print()
print(("FAILED: " + "; ".join(fails)) if fails else "all checks passed")
sys.exit(1 if fails else 0)
