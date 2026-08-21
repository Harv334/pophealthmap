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
    # This page documents the DATA, not the tools. The walkthroughs of Query,
    # Compare, Custom area, Data export, Directory and the question panel were
    # removed deliberately: they described what the buttons do, which the
    # buttons already do, and they buried the two things a reader comes here
    # for. What survived from them is the part that changes how a number should
    # be read, and that is what is checked below.
    check("the sources table is the centre of the page",
          any("Every data source" in h for h in heads), str(heads))
    check("and every geography has a stated year",
          any("which year" in h for h in heads), str(heads))
    check("service counting method documented",
          "ward polygon" in txt and "stands in" in txt)
    check("missing-is-not-zero documented", "Missing is not zero" in txt)
    check("polarity of the colours explained",
          "colour follows the" in txt.lower() and "least" in txt)
    check("uncoloured rows explained", "no better or worse end" in txt)
    check("borough-level repetition is flagged",
          "repeated" in txt and "every ward in that borough" in txt)
    check("known gaps still listed",
          any("Known gaps" in h for h in heads)
          and "City of London" in txt and "North West London" in txt)

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
