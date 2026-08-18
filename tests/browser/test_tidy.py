"""Tour in the status bar, exclusive level badge, VCSE wording, no blue rule,
collapsible panel."""
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8902/index.html"
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

    # ── 1. the tour moved to the status bar ──────────────────────────────────
    t = d.execute_script("""
      var b = document.getElementById('sbar-tour');
      var dd = document.getElementById('data-date');
      return { inRail: !!document.querySelector('.mode-btn[data-mode="tour"]'),
               inBar: !!b && !!b.closest('.sbar'),
               modes: [...document.querySelectorAll('.mode-btn')].map(x => x.dataset.mode),
               nextToDate: !!(b && dd && b.parentNode === dd.parentNode),
               onRight: b ? b.getBoundingClientRect().left > window.innerWidth / 2 : false };
    """)
    check("the tour is out of the top rail", not t["inRail"], str(t["modes"]))
    check("the rail is the six working modes, Custom area then Report builder",
          t["modes"] == ["explore", "query", "compare", "report", "csv", "directory"],
          str(t["modes"]))
    check("and the tour sits in the status bar beside the date",
          t["inBar"] and t["nextToDate"] and t["onRight"], str(t))
    d.execute_script("document.getElementById('sbar-tour').click();")
    time.sleep(2.5)
    check("it still starts the tour", d.execute_script("return PH_TOUR.running()"))
    d.execute_script("document.getElementById('tour-end').click();")
    time.sleep(1.5)

    # ── 2. the level badge is exclusive ──────────────────────────────────────
    def levels():
        return d.execute_script("""
          return { ward: document.getElementById('tnw').checked,
                   msoa: document.getElementById('tmsoa').checked,
                   lsoa: document.getElementById('tlsoa').checked,
                   active: (document.querySelector('#dl-seg button.active')||{}).dataset.dl };
        """)

    start = levels()
    check("it starts on Ward with only ward boundaries on",
          start["ward"] and not start["msoa"] and not start["lsoa"]
          and start["active"] == "ward", str(start))

    d.execute_script("""document.querySelector('#dl-seg button[data-dl="msoa"]').click();""")
    time.sleep(8)
    m = levels()
    check("choosing MSOA turns ward and LSOA off",
          m["msoa"] and not m["ward"] and not m["lsoa"] and m["active"] == "msoa", str(m))
    # The wording varies by whether an overlay is active and whether that
    # indicator has MSOA figures, so assert that the user is told something
    # about MSOA rather than pinning one of the three branches.
    _hint = d.execute_script("return document.getElementById('dl-hint').textContent")
    check("and it tells the user what the level change did",
          "MSOA" in _hint and len(_hint.strip()) > 20, _hint)

    d.execute_script("""document.querySelector('#dl-seg button[data-dl="lsoa"]').click();""")
    time.sleep(8)
    l = levels()
    check("choosing LSOA turns ward and MSOA off",
          l["lsoa"] and not l["ward"] and not l["msoa"] and l["active"] == "lsoa", str(l))

    d.execute_script("""document.querySelector('#dl-seg button[data-dl="ward"]').click();""")
    time.sleep(6)
    w = levels()
    check("and going back to Ward turns the other two off",
          w["ward"] and not w["msoa"] and not w["lsoa"] and w["active"] == "ward", str(w))
    check("the ward layer really is back on the map",
          d.execute_script("return document.querySelectorAll('path').length") > 600,
          str(d.execute_script("return document.querySelectorAll('path').length")))

    # ── 3. VCSE wording ──────────────────────────────────────────────────────
    d.execute_script("""[...document.querySelectorAll('.sb-tab')]
        .find(x => x.dataset.tab === 'vcse').click();""")
    time.sleep(1)
    v = d.execute_script("return document.getElementById('tab-vcse').innerText;")
    check("the CIC coverage sentence is gone",
          "CICs cover North West London only" not in v
          and "Charities cover all of London" not in v)
    check("and the tag filter says whose tags they are",
          "Charity Commission" in v and "not tagged" in v,
          [ln for ln in v.split("\n") if "Charity Commission" in ln][:1])

    # ── 4. no blue rule across the top ───────────────────────────────────────
    blue = d.execute_script("""
      var h = document.querySelector('.hdr');
      var cs = getComputedStyle(h);
      return { w: cs.borderTopWidth, colour: cs.borderTopColor,
               hdrTop: Math.round(h.getBoundingClientRect().top) };
    """)
    check("the blue rule across the top has gone",
          blue["w"] in ("0px", "medium") or blue["colour"] == "rgba(0, 0, 0, 0)",
          str(blue))
    check("and the header now starts at the very top", blue["hdrTop"] == 0, str(blue["hdrTop"]))

    # ── 5. the panel collapses on desktop ────────────────────────────────────
    before = d.execute_script("""
      return { sidebar: Math.round(document.getElementById('sidebar').getBoundingClientRect().width),
               map: Math.round(document.getElementById('map').getBoundingClientRect().width),
               menuShown: getComputedStyle(document.getElementById('hdr-menu')).display !== 'none' };
    """)
    check("the toggle is available on desktop too", before["menuShown"])
    d.execute_script("document.getElementById('hdr-menu').click();")
    time.sleep(1.5)
    after = d.execute_script("""
      return { sidebar: Math.round(document.getElementById('sidebar').getBoundingClientRect().width),
               map: Math.round(document.getElementById('map').getBoundingClientRect().width),
               collapsed: document.getElementById('sidebar').classList.contains('collapsed'),
               expanded: document.getElementById('hdr-menu').getAttribute('aria-expanded') };
    """)
    check("collapsing gives the width to the map",
          after["collapsed"] and after["sidebar"] == 0 and after["map"] > before["map"],
          f"map {before['map']} -> {after['map']}")
    check("and it says so for a screen reader", after["expanded"] == "false")

    # a mode that lives in the panel has to bring it back
    d.execute_script("""document.querySelector('.mode-btn[data-mode="query"]').click();""")
    time.sleep(2)
    check("choosing Query re-opens a collapsed panel",
          d.execute_script("""
            return !document.getElementById('sidebar').classList.contains('collapsed')
              && document.getElementById('tab-query').classList.contains('active');
          """))
    d.execute_script("""document.querySelector('.mode-btn[data-mode="explore"]').click();""")
    time.sleep(1)

    # and the choice survives a reload
    d.execute_script("document.getElementById('hdr-menu').click();")
    time.sleep(1.5)
    d.get(URL)
    time.sleep(SETTLE)
    check("the collapsed choice is remembered",
          d.execute_script("return document.getElementById('sidebar').classList.contains('collapsed')"))
    d.execute_script("document.getElementById('hdr-menu').click();")
    time.sleep(1.5)
    check("and expanding again restores the panel",
          d.execute_script("""
            return !document.getElementById('sidebar').classList.contains('collapsed')
              && document.getElementById('sidebar').getBoundingClientRect().width > 200;
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
