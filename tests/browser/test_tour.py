"""Verify the guided tour drives the real map, chapter by chapter.

The tour is six chapters: What PopHealth Map is, Choose an indicator, Select a
place, The tools, What the figures cannot tell you, and Your turn, where the
reader chooses an indicator themselves. Chapters are found by title rather than
by position, so a chapter added or moved fails only the checks about it.

    python3 tests/browser/test_tour.py http://127.0.0.1:8902/index.html [settle]

CHROME_BIN, when set, names the Chrome or Chromium to drive.
"""
import os
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

CHAPTERS = ["What PopHealth Map is", "Choose an indicator", "Select a place",
            "The tools", "What the figures cannot tell you", "Your turn"]

opts = Options()
opts.add_argument("--headless=new")
opts.add_argument("--window-size=1680,1000")
opts.add_argument("--disable-gpu")
opts.set_capability("goog:loggingPrefs", {"browser": "ALL"})
if os.environ.get("CHROME_BIN"):
    opts.binary_location = os.environ["CHROME_BIN"]
# Chrome will not start its sandbox as root, as in a container.
if hasattr(os, "geteuid") and os.geteuid() == 0:
    opts.add_argument("--no-sandbox")

fails = []


def check(name, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   {detail}" if detail else ""))
    if not ok:
        fails.append(name)


d = webdriver.Chrome(options=opts)
try:
    d.get(URL)
    time.sleep(SETTLE)

    d.execute_script("document.getElementById('sbar-tour').click();")
    time.sleep(3)
    total = d.execute_script("return PH_TOUR.length;")
    check("the tour has six chapters", total == 6, str(total))

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
    check("the tour opens on chapter 1", c1["open"] and c1["ctr"] == f"Chapter 1 of {total}", c1["ctr"])
    check("the progress bar has one segment per chapter",
          c1["segs"] == total and c1["on"] == 1, f"{c1['segs']} of {total}")
    check("chapter 1 has real narration", c1["narr"] > 150, str(c1["narr"]))
    check("it says what is coming next", c1["nextLbl"].startswith("Up next:"), c1["nextLbl"])
    check("Previous is hidden on the first chapter", c1["prevHidden"])
    check("chapter 1 shows the map with no indicator", c1["overlay"] == "none", c1["overlay"])
    check("the map knows it is touring", c1["touring"])

    # Walk every chapter once, recording what each one did.
    seen = []
    for i in range(total):
        snap = d.execute_script("""
          return { title: document.getElementById('tour-title').textContent.trim(),
                   narr: document.getElementById('tour-narr').innerText,
                   overlay: document.getElementById('ov').value,
                   pane: (document.querySelector('.tab-pane.active') || {}).id || '',
                   sheet: document.getElementById('ward-sheet').classList.contains('open'),
                   selW: typeof selW !== 'undefined' ? selW : null,
                   legend: document.getElementById('map-legend-strip').style.display,
                   spots: [...document.querySelectorAll('.tour-spot')].map(e => e.id || e.className),
                   next: document.getElementById('tour-next').textContent.trim(),
                   sheetAbove: (function () {
                     var sh = document.getElementById('ward-sheet').getBoundingClientRect();
                     var st = document.getElementById('tour-strip').getBoundingClientRect();
                     return sh.bottom <= st.top + 2;
                   })() };
        """)
        seen.append(snap)
        if i < total - 1:
            d.execute_script("document.getElementById('tour-next').click();")
            time.sleep(2.4)

    check("the chapters are the six, in order",
          [s["title"] for s in seen] == CHAPTERS, " | ".join(s["title"] for s in seen))

    def chapter(title):
        return next((s for s in seen if s["title"] == title), None)

    allText = " ".join(s["title"] + " " + s["narr"] for s in seen)
    check("the tour has no dashes and says select rather than click",
          not re.search("[–—]", allText) and not re.search(r"\bclick", allText, re.I))

    first = chapter("What PopHealth Map is")
    check("chapter 1 counts indicators, LAs and wards from the data and names the level LA",
          first and re.search(r"\b\d{2,3} indicators\b", first["narr"])
          and re.search(r"\b\d{2} local authorities \(LAs\)", first["narr"])
          and re.search(r"\b\d{3} wards\b", first["narr"]),
          first and first["narr"][:160])

    ind = chapter("Choose an indicator")
    check("the indicator chapter colours the map, shows the key and opens the Indicators tab",
          ind and ind["overlay"] == "imd_score_ward" and ind["legend"] != "none" and ind["pane"] == "tab-overlay",
          str(ind and (ind["overlay"], ind["legend"], ind["pane"])))
    check("and says which way deprivation deciles run",
          ind and "deciles" in ind["narr"] and "most deprived" in ind["narr"])

    place = chapter("Select a place")
    check("the place chapter selects a real ward",
          place and place["sheet"] and place["selW"], str(place and place["selW"]))
    check("and the panel stacks above the tour strip rather than under it",
          place and place["sheetAbove"])
    check("and it mentions postcode search", place and "postcode" in place["narr"])

    tools = chapter("The tools")
    check("the tools chapter names every tool",
          tools and all(w in tools["narr"] for w in
                        ["Query", "Compare", "Custom area", "Data export", "Directory", "Ask about this data"]),
          tools and tools["narr"][:120])
    check("and describes Ask as the panel does",
          tools and "this site's server" in tools["narr"] and "Anthropic's Claude model" in tools["narr"]
          and "10 questions a day" in tools["narr"] and "never receives" not in tools["narr"])
    check("and points at the tabs", tools and any("mode-tabs" in s for s in tools["spots"]), str(tools and tools["spots"]))

    limits = chapter("What the figures cannot tell you")
    check("the limits chapter names the whole-LA figures",
          limits and "whole LA" in limits["narr"] and "borough" not in limits["narr"].lower(),
          limits and limits["narr"][:120])

    # House style: connected sentences, so never three short ones in a row.
    def short_run(text):
        n = [len(s.split()) for s in re.split(r"(?<=[.:])\s+", text.strip()) if s]
        return any(n[i] < 8 and n[i - 1] < 8 and n[i - 2] < 8 for i in range(2, len(n)))
    check("no chapter has three short sentences in a row",
          not any(short_run(s["narr"]) for s in seen),
          ", ".join(s["title"] for s in seen if short_run(s["narr"])))

    # The last chapter waits for the reader to choose an indicator.
    last = chapter("Your turn")
    check("the last chapter waits, with no indicator chosen",
          last and last["next"] == "Skip and finish" and last["overlay"] == "none",
          str(last and (last["next"], last["overlay"])))
    d.execute_script("document.getElementById('sbtab-overlay').click();")
    time.sleep(0.6)
    d.execute_script("document.querySelector('#ov-chips button:nth-child(3)').click();")
    time.sleep(2)
    fin = d.execute_script("""
      return { btn: document.getElementById('tour-next').textContent.trim(),
               nextLbl: document.getElementById('tour-next-lbl').textContent.trim(),
               done: document.querySelectorAll('#tour-prog .seg.done').length,
               task: document.getElementById('tour-strip').classList.contains('task-done'),
               ov: document.getElementById('ov').value };
    """)
    check("choosing an indicator completes the last chapter",
          fin["task"] and fin["btn"] == "Finish" and fin["ov"] != "none" and fin["nextLbl"] == "", str(fin))
    check("every earlier chapter is marked done",
          fin["done"] == total - 1, f"{fin['done']} of {total - 1}")

    d.execute_script("document.getElementById('tour-prev').click();")
    time.sleep(2.5)
    check("Previous steps back",
          d.execute_script("return document.getElementById('tour-ctr').textContent")
          .strip().startswith(f"Chapter {total - 1}"))

    # Back on the last chapter the map is cleared again for the task, so Next
    # reads "Skip and finish" and closes the tour.
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
           and not ("fonts.gstatic.com" in e["message"] and "404" in e["message"])
           # Map tiles and fonts from the internet, where the machine has none.
           and not re.search(r"arcgisonline|basemaps|tile|fonts\.googleapis|ERR_TUNNEL|ERR_NAME|ERR_PROXY", e["message"])]
    check("no severe console errors", not sev, str([e["message"][:170] for e in sev[:3]]))
finally:
    d.quit()

print()
print(("FAILED: " + "; ".join(fails)) if fails else "all checks passed")
sys.exit(1 if fails else 0)
