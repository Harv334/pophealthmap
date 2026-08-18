import sys, time, re
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8902/methodology.html"
opts = Options()
opts.add_argument("--headless=new"); opts.add_argument("--window-size=1200,900"); opts.add_argument("--disable-gpu")
opts.set_capability("goog:loggingPrefs", {"browser": "ALL"})
fails = []
def check(n, ok, d=""):
    print(("  PASS  " if ok else "  FAIL  ") + n + (f"   {d}" if d else ""))
    if not ok: fails.append(n)
d = webdriver.Chrome(options=opts)
try:
    d.get(URL); time.sleep(4)
    txt = d.execute_script("return document.body.innerText")
    heads = d.execute_script("return [...document.querySelectorAll('h2')].map(h=>h.textContent.trim())")

    check("page renders", len(txt) > 3000, f"{len(txt)} chars")
    check("MSOA is no longer described as not drawn",
          "not drawn" not in txt, "still says 'not drawn'")
    check("MSOA layer source named", "Middle_layer_Super_Output_Areas" in txt)
    check("a glossary defines the abbreviations",
          d.execute_script("return document.body.innerHTML.includes('Plain word')")
          and "LSOA" in txt)
    check("Query section present", any("Query" in h for h in heads), str(heads))
    for tool in ["Custom area", "Data export", "Directory"]:
        check(f"{tool} is documented", any(tool in h for h in heads), str(heads))
    check("Compare section present", any("Compare" in h for h in heads), str(heads))
    check("service counting method documented", "pharmacy_count" in txt)
    check("missing-is-not-zero documented", "Missing is not zero" in txt)
    check("polarity of the colours explained",
          "colour follows the" in txt.lower() and "least" in txt)
    check("uncoloured rows explained", "no better or worse end" in txt)
    check("the cap is stated as 10 a day", "10 a day" in txt or "10 questions a day" in txt)
    check("and says the other tools are not capped",
          "free to use as much as you like" in txt or "unlimited" in txt)

    # house style
    check("no em dashes", "—" not in txt, txt[max(0,txt.find("—")-60):txt.find("—")+60] if "—" in txt else "")
    check("no US spellings of the words used here",
          not re.search(r"\bcolor\b|\bneighborhood", txt, re.I))

    sev = [e for e in d.get_log("browser") if e["level"] == "SEVERE"
           and "favicon" not in e["message"]
           and not ("fonts.gstatic.com" in e["message"] and "404" in e["message"])]
    check("no severe console errors", not sev, str([e["message"][:180] for e in sev[:3]]))

    # the back link still works
    check("back-to-map link intact",
          d.execute_script("return !!document.querySelector('a[href=\"index.html\"], a[href=\"/\"]')"))
finally:
    d.quit()
print()
print(("FAILED: " + "; ".join(fails)) if fails else "all checks passed")
sys.exit(1 if fails else 0)
