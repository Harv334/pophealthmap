"""First-visit tour, Reset, the borough lock, and the removed bivariate control."""
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8902/index.html"
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
    # ── a brand new visitor ──────────────────────────────────────────────────
    d.get(BASE)
    d.execute_script("try { localStorage.clear(); } catch (e) {}")
    d.get(BASE)
    time.sleep(SETTLE)

    first = d.execute_script("""
      return { running: PH_TOUR.running(), at: PH_TOUR.at(),
               open: document.getElementById('tour-strip').classList.contains('open'),
               seen: (function(){ try { return localStorage.getItem('phTourSeen'); }
                                  catch(e){ return null; } })() };
    """)
    check("a new visitor lands in the tour", first["running"] and first["at"] == 0, str(first))
    check("and the visit is remembered", first["seen"] == "1", str(first["seen"]))

    check("the tour has no separate 'start the tour?' box in front of it",
          d.execute_script("""
            return !document.querySelector('#mode-panel:not([hidden])');
          """))
    check("it can still be ended from its own header",
          d.execute_script("return !!document.getElementById('tour-end')"))

    # ── a returning visitor ──────────────────────────────────────────────────
    d.get(BASE)
    time.sleep(SETTLE)
    again = d.execute_script("return PH_TOUR.running();")
    check("a returning visitor is not shown it again", not again)

    # ── a shared link is not hijacked ────────────────────────────────────────
    d.execute_script("try { localStorage.clear(); } catch (e) {}")
    d.get(BASE + ("&" if "?" in BASE else "?") + "b=Brent")
    time.sleep(SETTLE)
    shared = d.execute_script("""
      return { running: PH_TOUR.running(),
               seen: (function(){ try { return localStorage.getItem('phTourSeen'); }
                                  catch(e){ return null; } })() };
    """)
    check("a link carrying state shows that state, not the tour", not shared["running"], str(shared))
    check("and it does not burn the one first visit", shared["seen"] is None, str(shared["seen"]))

    # ── the tour has a chapter for the Ask panel ─────────────────────────────
    # Found by title, not by position: the tour has grown from six chapters to
    # eleven, and counting positions broke this test every time it did.
    d.execute_script("""document.getElementById('sbar-tour').click();""")
    time.sleep(2.5)
    total = d.execute_script("return PH_TOUR.length")
    check("the tour has a chapter for every tool", total == 10, str(total))
    found = None
    for i in range(total):
        t = d.execute_script("return document.getElementById('tour-title').textContent.trim()")
        if "in plain English" in t or t.startswith("Asking it a question"):
            found = d.execute_script("""
              return { narr: document.getElementById('tour-narr').textContent,
                       panel: !!document.querySelector('#ai-panel.open') };
            """)
            break
        if i < total - 1:
            d.execute_script("document.getElementById('tour-next').click();")
            time.sleep(2.2)
    check("a chapter about asking it a question", found is not None)
    if found:
        check("and it opens the panel it is describing", found["panel"])
        check("it states the cap and that the model gets no data",
              "10 questions a day" in found["narr"]
              and ("never receives" in found["narr"] or "never sees" in found["narr"]))
    d.execute_script("document.getElementById('tour-end').click();")
    time.sleep(1.5)

    # ── bivariate is gone ────────────────────────────────────────────────────
    biv = d.execute_script("""
      return { sel: !!document.getElementById('ov2'),
               legend: !!document.getElementById('biv-legend'),
               text: document.body.innerText.includes('Bivariate') };
    """)
    check("the bivariate control is removed", not biv["sel"] and not biv["legend"] and not biv["text"],
          str(biv))
    check("and nothing threw on load without it",
          not [e for e in d.get_log("browser") if e["level"] == "SEVERE"
               and "favicon" not in e["message"]
               and not ("fonts.gstatic.com" in e["message"] and "404" in e["message"])])

    # ── Reset clears the selection ───────────────────────────────────────────
    ward = d.execute_script("""
      return Object.values(WARD_DATA).filter(w => w && w.lad === 'Brent')[0].name;
    """)
    # Select it the way a user does. showWard fills the panel and raises the
    # bar but has never set selW; the map click handler does. This assertion
    # used to pass on selW left over from the tour earlier in the run, which
    # the tour now clears after itself, so it was leaning on a side effect.
    d.execute_script("""
      var target = null;
      wLyr.eachLayer(function (l) {
        if (l.feature && l.feature.properties.WD24NM === arguments0) target = l;
      });
      if (target) target.fire('click');
    """.replace('arguments0', JSON.stringify(ward)) if False else """
      var want = arguments[0], target = null;
      wLyr.eachLayer(function (l) {
        if (l.feature && l.feature.properties.WD24NM === want) target = l;
      });
      if (target) target.fire('click');
    """, ward)
    time.sleep(2.5)
    check("a ward is selected before Reset",
          d.execute_script("return !!selW && document.getElementById('ward-sheet').classList.contains('open')"),
          str(d.execute_script("return selW;")))
    d.execute_script("document.getElementById('tb-reset').click();")
    time.sleep(2.5)
    after = d.execute_script("""
      var mw = document.getElementById('map-wrap');
      return { selW: selW,
               sheet: document.getElementById('ward-sheet').classList.contains('open'),
               lift: getComputedStyle(mw).getPropertyValue('--sheet-lift').trim(),
               popup: !!document.querySelector('.leaflet-popup') };
    """)
    check("Reset clears the ward and its bar",
          not after["selW"] and not after["sheet"], str(after))
    check("and the map furniture drops back down",
          after["lift"] in ("0px", "0"), after["lift"])
    check("and no popup is left open", not after["popup"])

    # ── the borough lock ─────────────────────────────────────────────────────
    d.execute_script("""
      var s = document.getElementById('borough-focus');
      s.value = 'Brent'; s.dispatchEvent(new Event('change', {bubbles:true}));
    """)
    time.sleep(3)
    lock = d.execute_script("""
      var b = map.options.maxBounds;
      return { hasBounds: !!b,
               minZoom: map.getMinZoom(),
               inside: wardInFocus(Object.values(WARD_DATA).filter(w => w.lad === 'Brent')[0].name),
               outside: wardInFocus(Object.values(WARD_DATA).filter(w => w.lad === 'Camden')[0].name) };
    """)
    check("panning is bounded to the focused borough", lock["hasBounds"], str(lock["hasBounds"]))
    check("and it cannot be zoomed out past it", lock["minZoom"] > 0, str(lock["minZoom"]))
    check("wards inside the borough stay interactive", lock["inside"] is True)
    check("wards outside it do not", lock["outside"] is False)

    # a click on an out-of-borough ward must not select it
    d.execute_script("""
      var outside = Object.values(WARD_DATA).filter(w => w.lad === 'Camden')[0].name;
      window.__outside = outside;
      var target = null;
      wLyr.eachLayer(function (l) {
        if (l.feature && l.feature.properties.WD24NM === outside) target = l;
      });
      if (target) target.fire('click');
    """)
    time.sleep(1.5)
    clicked = d.execute_script("return { selW: selW, want: window.__outside };")
    check("clicking a ward outside the borough does nothing",
          clicked["selW"] != clicked["want"], str(clicked))

    # and releasing the focus restores everything
    d.execute_script("""
      var s = document.getElementById('borough-focus');
      s.value = 'all'; s.dispatchEvent(new Event('change', {bubbles:true}));
    """)
    time.sleep(2.5)
    freed = d.execute_script("""
      return { bounds: !!map.options.maxBounds, minZoom: map.getMinZoom(),
               outside: wardInFocus(window.__outside) };
    """)
    check("clearing the focus unlocks the map again",
          not freed["bounds"] and freed["minZoom"] == 0 and freed["outside"] is True,
          str(freed))

    sev = [e for e in d.get_log("browser")
           if e["level"] == "SEVERE"
           and "favicon" not in e["message"]
           and not ("fonts.gstatic.com" in e["message"] and "404" in e["message"])]
    check("no severe console errors throughout", not sev, str([e["message"][:180] for e in sev[:3]]))
finally:
    d.quit()

print()
print(("FAILED: " + "; ".join(fails)) if fails else "all checks passed")
sys.exit(1 if fails else 0)
