"""The five fixes from the review pass, plus the loading screen pulse.

None of this was verifiable when it was written: headless Chrome would not
start. Everything here is the part that was reasoned about rather than seen.

  1. injectWardIMD runs again when the LSOA boundaries land, so the ward
     figures derived from the geometry stop being whatever ward_data.json's
     postcode voting produced.
  2. The ward profile refresh in _onLsoaImdReady fires at all. It tested
     currentWard, which is scoped to an IIFE much further down the file, so
     the typeof guard read 'undefined' every time.
  3. The screenshot dialog offers "Just <area>, framed" under a board focus,
     not only under a borough focus.
  4. The point-layer CSV no longer claims to be scoped to a borough.
  5. The loading screen keeps moving until the data lands.

Usage:
    python test_fixes.py                        # production
    python test_fixes.py http://localhost:8902/index.html 30
"""
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

URL = sys.argv[1] if len(sys.argv) > 1 else "https://pophealth.uk/index.html"
SETTLE = int(sys.argv[2]) if len(sys.argv) > 2 else 34
IGNORED = ("gstatic.com", "fonts.googleapis.com")

fails, passes = [], []


def check(name, ok, detail=""):
    (passes if ok else fails).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name
          + (("\n          " + str(detail)) if detail else ""))


def driver():
    o = Options()
    o.add_argument("--headless=new")
    o.add_argument("--window-size=1500,1000")
    o.set_capability("goog:loggingPrefs", {"browser": "ALL"})
    return webdriver.Chrome(options=o)


def url_with_t():
    return URL + ("&" if "?" in URL else "?") + "t=1"


# ---------------------------------------------------------------------------
# The loading screen. Throttled hard, so the data cannot beat the sweep and
# the wait after it is long enough to observe.
# ---------------------------------------------------------------------------
print("\n== loading screen ==")
d = driver()
try:
    d.execute_cdp_cmd("Network.enable", {})
    d.execute_cdp_cmd("Network.emulateNetworkConditions", {
        "offline": False,
        "latency": 200,
        "downloadThroughput": 180 * 1024,
        "uploadThroughput": 180 * 1024,
    })
    d.get(url_with_t())

    seen_waiting = False
    anim = ""
    overlay_up = False
    for _ in range(90):
        st = d.execute_script(
            "var m = document.getElementById('ldr-map');"
            "var o = document.getElementById('ldr');"
            "if (!m || !o) return null;"
            "return { waiting: m.classList.contains('waiting'),"
            "         anim: getComputedStyle(m).animationName,"
            "         dur: getComputedStyle(m).animationDuration,"
            "         gone: o.classList.contains('hidden') };")
        if st is None:
            break
        if not st["gone"]:
            overlay_up = True
        if st["waiting"]:
            seen_waiting = True
            anim = "%s / %s" % (st["anim"], st["dur"])
        if st["gone"]:
            break
        time.sleep(0.5)

    check("the loading overlay was actually up", overlay_up)
    check("the map takes the waiting class while the data is still coming",
          seen_waiting, "never pulsed; it would sit still for the whole wait")
    check("and the pulse is the ldr-breathe animation", "ldr-breathe" in anim, anim)
    check("the pulse is slower than 3Hz (WCAG 2.3.1)", anim.endswith("1.6s"), anim)

    for _ in range(150):
        if d.execute_script(
                "return document.getElementById('ldr').classList.contains('hidden')"):
            break
        time.sleep(1)
    end = d.execute_script(
        "var m = document.getElementById('ldr-map'), o = document.getElementById('ldr');"
        "return { waiting: m.classList.contains('waiting'),"
        "         hidden: o.classList.contains('hidden') };")
    check("the screen goes when the data lands", end["hidden"])
    check("and the pulse comes off before the fade", not end["waiting"],
          "the fade and the pulse would otherwise compound")
finally:
    d.quit()


# ---------------------------------------------------------------------------
# The fixes, on a normal connection.
# ---------------------------------------------------------------------------
print("\n== the five fixes ==")
d = driver()
try:
    d.get(url_with_t())
    time.sleep(SETTLE)

    # Select a ward BEFORE the LSOA boundaries are asked for. That is the case
    # fix 2 is about: the profile has LSOA rows it cannot fill in yet.
    # A real click on a ward layer, not showWard directly: selW is set by the
    # layer's own click handler, and selW is what the fix reads.
    d.execute_script(
        "var t = null;"
        "if (typeof wLyr !== 'undefined' && wLyr) {"
        "  wLyr.eachLayer(function (l) { if (!t) t = l; }); }"
        "if (t) t.fire('click', { latlng: t.getBounds().getCenter() });")
    time.sleep(1)
    before = d.execute_script(
        "var w = WARD_DATA_BY_CODE, k = Object.keys(w);"
        "var withDecile = k.filter(function (c) {"
        "  return w[c].indicators.imd_worst_decile != null; }).length;"
        "return { selW: (typeof selW !== 'undefined') ? selW : null,"
        "         withDecile: withDecile,"
        "         lsoaLoaded: (typeof LSOA_IMD !== 'undefined' && !!LSOA_IMD) };")
    check("a ward is selected before the LSOA data is fetched",
          bool(before["selW"]), "selW=%s" % before["selW"])
    check("imd_worst_decile is absent while the LSOA data is missing",
          before["withDecile"] == 0,
          "wards carrying it beforehand: %s" % before["withDecile"])

    # Pull the boundaries in, the way the LSOA toggle does.
    d.execute_script("if (typeof ensureLsoaImd === 'function') ensureLsoaImd();")
    for _ in range(90):
        if d.execute_script(
                "return (typeof LSOA_IMD !== 'undefined' && !!LSOA_IMD && !!LSOA_IMD.features)"):
            break
        time.sleep(1)
    time.sleep(5)

    after = d.execute_script(
        "var w = WARD_DATA_BY_CODE, k = Object.keys(w);"
        "var withDecile = k.filter(function (c) {"
        "  return w[c].indicators.imd_worst_decile != null; }).length;"
        "var strays = k.filter(function (c) {"
        "  return w[c].indicators.imd_lsoa_count != null; }).length;"
        "var byWard = {};"
        "LSOA_IMD.features.forEach(function (f) {"
        "  var p = f.properties || {}; if (!p.ward) return;"
        "  if (!byWard[p.ward]) byWard[p.ward] = { t: 0, c: 0 };"
        "  byWard[p.ward].t += 1;"
        "  if (p.imd_decile === 1 || p.imd_decile === 2) byWard[p.ward].c += 1; });"
        "var mismatch = 0, compared = 0, examples = [];"
        "Object.keys(byWard).forEach(function (nm) {"
        "  var rec = WARD_DATA[nm]; if (!rec || !rec.indicators) return;"
        "  compared++;"
        "  if (rec.indicators.total_lsoa_count !== byWard[nm].t) {"
        "    mismatch++;"
        "    if (examples.length < 3) examples.push("
        "      nm + ' states ' + rec.indicators.total_lsoa_count + ', geometry has ' + byWard[nm].t); } });"
        "return { withDecile: withDecile, strays: strays, compared: compared,"
        "         mismatch: mismatch, examples: examples, n: k.length };")

    check("injectWardIMD re-runs, so the deciles now exist",
          after["withDecile"] > 600,
          "wards carrying imd_worst_decile: %d of %d" % (after["withDecile"], after["n"]))
    check("the ward LSOA counts agree with the geometry the map draws",
          after["compared"] > 600 and after["mismatch"] == 0,
          "%d wards compared, %d disagree%s"
          % (after["compared"], after["mismatch"],
             (": " + "; ".join(after["examples"])) if after["examples"] else ""))
    check("the redundant imd_lsoa_count is gone", after["strays"] == 0,
          "wards still carrying it: %s" % after["strays"])

    # Fix 3: the screenshot dialog under a board focus.
    icb = d.execute_script(
        "var sel = document.getElementById('icb-focus');"
        "if (!sel) return null;"
        "var o = Array.prototype.slice.call(sel.options).filter(function (x) {"
        "  return x.value && x.value !== 'all'; });"
        "if (!o.length) return null;"
        "sel.value = o[0].value; sel.dispatchEvent(new Event('change'));"
        "return o[0].text;")
    time.sleep(3)
    if not icb:
        check("an ICB focus can be set", False, "no icb-focus options found")
    else:
        lbl = d.execute_script(
            "return (typeof focusLabel === 'function') ? focusLabel() : null;")
        check("a board focus is active", bool(lbl), "focusLabel()=%s" % lbl)
        d.execute_script(
            "var b = document.getElementById('tb-png');"
            "if (b) b.click();")
        time.sleep(2)
        dlg = d.execute_script(
            "var t = document.body.innerText || '';"
            "return t.split('\\n').filter(function (l) {"
            "  return l.indexOf('framed') >= 0 || l.indexOf('This view') >= 0; });")
        joined = " | ".join(dlg)
        check("the screenshot dialog offers to frame the focused board",
              any("Just" in x and "framed" in x for x in dlg),
              "options shown: %s" % (joined or "(dialog did not open)"))
        check("and it names the board rather than a borough",
              bool(lbl) and any(lbl in x for x in dlg),
              "expected %r among: %s" % (lbl, joined))
        d.execute_script(
            "var b = Array.prototype.slice.call(document.querySelectorAll('button'))"
            "  .filter(function (x) { return /cancel|close/i.test(x.textContent); });"
            "if (b.length) b[0].click();")

    # Fix 4: the CSV dialog copy.
    src = d.page_source
    check("the CSV dialog no longer says 'the current borough focus'",
          "scoped to the current borough focus" not in src)
    check("and says what it actually does",
          "scoped to the area you have focused, board or borough" in src)

    # Fix 5: the report failure path exists.
    check("the ward report has a failure path",
          "could not be built" in src,
          "a throw would leave the tab on the holding page")

    errs = [e for e in d.get_log("browser")
            if e["level"] == "SEVERE" and not any(s in e["message"] for s in IGNORED)]
    check("no severe console errors", not errs,
          "\n          ".join(e["message"][:180] for e in errs[:5]))
finally:
    d.quit()

print("\n%d passed, %d failed" % (len(passes), len(fails)))
if fails:
    print("Failed: " + ", ".join(fails))
sys.exit(1 if fails else 0)
