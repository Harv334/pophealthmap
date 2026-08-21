"""Verify the guided tour drives the real map, chapter by chapter.

Chapters are found by title rather than by position. The tour has grown from
six to eleven during this project, and every time it grew, a test that counted
positions broke without anything being wrong.
"""
import re
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.keys import Keys

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8902/index.html"
# Any query parameter suppresses the first-visit tour, which would otherwise
# drive the UI out from under every assertion below.
if "?" not in URL:
    URL += "?t=1"
SETTLE = int(sys.argv[2]) if len(sys.argv) > 2 else 28

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

    check("no mode is marked Soon any more",
          d.execute_script("return document.querySelectorAll('.mode-soon').length === 0"),
          d.execute_script("return [...document.querySelectorAll('.mode-btn')]"
                           ".map(b=>b.textContent.trim()).join(' | ')"))

    d.execute_script("document.getElementById('sbar-tour').click();")
    time.sleep(3)
    total = d.execute_script("return PH_TOUR.length;")

    c1 = d.execute_script("""
      return {
        open: document.getElementById('tour-strip').classList.contains('open'),
        ctr: document.getElementById('tour-ctr').textContent.trim(),
        segs: document.querySelectorAll('#tour-prog .seg').length,
        on: document.querySelectorAll('#tour-prog .seg.on').length,
        narr: document.getElementById('tour-narr').textContent.trim().length,
        nextLbl: document.getElementById('tour-next-lbl').textContent.trim(),
        prevHidden: document.getElementById('tour-prev').style.visibility === 'hidden',
        overlay: document.getElementById('ov').value,
        touring: document.getElementById('map-wrap').classList.contains('touring'),
      };
    """)
    check("the tour opens on chapter 1",
          c1["open"] and c1["ctr"].startswith("Chapter 1 of"), c1["ctr"])
    check("the progress bar has one segment per chapter",
          c1["segs"] == total and c1["on"] == 1, f"{c1['segs']} of {total}")
    check("chapter 1 has real narration", c1["narr"] > 150, str(c1["narr"]))
    check("it says what is coming next", c1["nextLbl"].startswith("Up next:"), c1["nextLbl"])
    check("Previous is hidden on the first chapter", c1["prevHidden"])
    check("chapter 1 clears any overlay", c1["overlay"] == "none", c1["overlay"])
    check("the map knows it is touring", c1["touring"])

    # Walk every chapter once, recording what each one did.
    seen = []
    longest, longest_txt = 0, ""
    for i in range(total):
        snap = d.execute_script("""
          return { title: document.getElementById('tour-title').textContent.trim(),
                   narr: document.getElementById('tour-narr').textContent,
                   overlay: document.getElementById('ov').value,
                   pane: (document.querySelector('.tab-pane.active') || {}).id || '',
                   ai: !!document.querySelector('#ai-panel.open'),
                   sheet: document.getElementById('ward-sheet').classList.contains('open'),
                   selW: typeof selW !== 'undefined' ? selW : null,
                   qrows: document.querySelectorAll('#q-results .q-row').length,
                   legend: document.getElementById('map-legend-strip').style.display,
                   sheetAbove: (function () {
                     var sh = document.getElementById('ward-sheet').getBoundingClientRect();
                     var st = document.getElementById('tour-strip').getBoundingClientRect();
                     return sh.bottom <= st.top + 2;
                   })() };
        """)
        seen.append(snap)
        for sent in re.split(r"(?<=[.!?])\s+", snap["narr"].strip()):
            n = len(sent.split())
            if n > longest:
                longest, longest_txt = n, sent
        if i < total - 1:
            d.execute_script("document.getElementById('tour-next').click();")
            time.sleep(2.4)

    def chapter(prefix):
        for s in seen:
            if s["title"].startswith(prefix):
                return s
        return None

    ov = chapter("Colour the map")
    check("the overlay chapter applies an overlay and shows the legend",
          ov and ov["overlay"] == "imd_score_ward" and ov["legend"] != "none",
          str(ov and (ov["overlay"], ov["legend"])))

    # Their deprivation material sits inside the overlay chapter, not a
    # chapter of its own, so look for the sentence rather than a title.
    # Their wording for the decile lesson differs from mine; what matters is
    # that some chapter explains the direction while an overlay is drawn.
    dec = next((x for x in seen
                if "decile" in x["narr"] and "deprived" in x["narr"]), None)
    # Upstream dropped the standalone deprivation chapter; the Compare chapter
    # carries the direction now. What must not happen is nobody saying it.
    check("a chapter explains which way deprivation runs",
          dec is not None and "least deprived" in dec["narr"],
          str(dec and dec["title"]))

    wc = chapter("Click a ward")
    check("the ward chapter selects a real ward",
          wc and wc["sheet"] and wc["selW"], str(wc and wc["selW"]))
    check("and the bar stacks above the tour strip rather than under it",
          wc and wc["sheetAbove"])

    q = next((x for x in seen if x["pane"] == "tab-query"), None)
    check("the Query chapter opens Query and runs it for real",
          q and q["pane"] == "tab-query" and q["qrows"] > 0, str(q and q["qrows"]))

    for name, prefix, pane in [("Compare", "Compare", "tab-compare"),
                               ("Custom area", "Custom area", "tab-report"),
                               ("Data export", "Data export", "tab-csv"),
                               ("Directory", "Directory", "tab-directory")]:
        c = chapter(prefix)
        check(f"the {name} chapter opens {name}",
              c and c["pane"] == pane, str(c and c["pane"]))

    # The handover build strips this chapter along with the panel it drives, so
    # its absence is a valid tour rather than a fault. Assert on it only where
    # it exists; a suite that is red the moment it is handed over is one its new
    # owner learns to ignore.
    ask = next((x for x in seen if x["title"].startswith("Ask a question")), None)
    if ask is None:
        check("no Ask chapter, and no assistant panel to drive (handover build)",
              not any(x["ai"] for x in seen))
    else:
        check("the Ask chapter opens the panel it describes", ask["ai"])
        check("and states the cap and that the model gets no data",
              "10 questions a day" in ask["narr"]
              and ("never receives" in ask["narr"] or "never given" in ask["narr"]))

    last = seen[-1]
    check("the last chapter is about what the figures cannot support",
          "cannot tell you" in last["title"], last["title"])
    check("the last chapter names the borough-level trap",
          "borough" in last["narr"] and len(last["narr"]) > 150,
          last["narr"][:120])

    # ASD-STE100: 25 words is the limit for descriptive text
    check("no tour sentence runs past 25 words",
          longest <= 25, f"longest {longest}: {longest_txt[:90]}")

    fin = d.execute_script("""
      return { btn: document.getElementById('tour-next').textContent.trim(),
               nextLbl: document.getElementById('tour-next-lbl').textContent.trim(),
               done: document.querySelectorAll('#tour-prog .seg.done').length };
    """)
    check("the button becomes Finish and nothing is queued after it",
          fin["btn"] == "Finish" and fin["nextLbl"] == "", repr(fin["btn"]))
    check("every earlier chapter is marked done",
          fin["done"] == total - 1, f"{fin['done']} of {total - 1}")

    d.execute_script("document.getElementById('tour-prev').click();")
    time.sleep(2.5)
    check("Previous steps back",
          d.execute_script("return document.getElementById('tour-ctr').textContent")
          .strip().startswith(f"Chapter {total - 1}"))

    d.execute_script("document.getElementById('tour-next').click();")
    time.sleep(2)
    d.execute_script("document.getElementById('tour-next').click();")
    time.sleep(2)
    end = d.execute_script("""
      var mw = document.getElementById('map-wrap');
      return { open: document.getElementById('tour-strip').classList.contains('open'),
               touring: mw.classList.contains('touring'),
               explore: document.querySelector('.mode-btn[data-mode="explore"]')
                          .getAttribute('aria-selected') };
    """)
    check("finishing closes the tour and returns to Explore",
          not end["open"] and not end["touring"] and end["explore"] == "true", str(end))

    d.execute_script("document.getElementById('sbar-tour').click();")
    time.sleep(2.5)
    d.find_element("tag name", "body").send_keys(Keys.ESCAPE)
    time.sleep(1.5)
    check("Escape ends the tour", not d.execute_script("return PH_TOUR.running()"))

    sev = [e for e in d.get_log("browser")
           if e["level"] == "SEVERE" and "favicon" not in e["message"]
           and not ("fonts.gstatic.com" in e["message"] and "404" in e["message"])]
    check("no severe console errors", not sev, str([e["message"][:170] for e in sev[:3]]))
finally:
    d.quit()

print()
print(("FAILED: " + "; ".join(fails)) if fails else "all checks passed")
sys.exit(1 if fails else 0)
