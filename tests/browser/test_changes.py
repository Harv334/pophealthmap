"""The level switch and the colour direction, checked in a real browser.

1. Picking a ward-only indicator while the map sits at LSOA level has to bring
   the map back to ward and shade it. It used to leave LSOA off, ward off and
   the badge reading LSOA, with nothing painted.

2. Dark has to mean the higher figure on every indicator, including the ones
   where higher is better, and both legends have to run the same way.
"""
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8902/index.html"
SETTLE = int(sys.argv[2]) if len(sys.argv) > 2 else 32

fails, passes = [], []


def check(name, ok, detail=""):
    (passes if ok else fails).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (("\n          " + str(detail)) if detail else ""))


o = Options()
o.add_argument("--headless=new")
o.add_argument("--window-size=1600,1000")
o.set_capability("goog:loggingPrefs", {"browser": "ALL"})
d = webdriver.Chrome(options=o)


def pick(key):
    d.execute_script(
        "var s = document.getElementById('ov');"
        "s.value = arguments[0]; s.dispatchEvent(new Event('change'));", key)


def state():
    return d.execute_script(
        "return {"
        "  level: (typeof currentDataLevel !== 'undefined') ? currentDataLevel : null,"
        "  tnw: (document.getElementById('tnw')||{}).checked,"
        "  tlsoa: (document.getElementById('tlsoa')||{}).checked,"
        "  wardOnMap: !!(typeof wLyr !== 'undefined' && wLyr && wLyr._map),"
        "  shaded: (function () {"
        "     if (typeof wLyr === 'undefined' || !wLyr) return 0;"
        "     var f = {}; wLyr.eachLayer(function (l) {"
        "       var c = l.options && l.options.fillColor; if (c) f[c] = 1; });"
        "     return Object.keys(f).length; })(),"
        "  badge: (document.querySelector('#dl-seg button.active')||{}).dataset ?"
        "         document.querySelector('#dl-seg button.active').dataset.dl : null"
        "};")


try:
    d.get(URL + ("&" if "?" in URL else "?") + "t=1")
    time.sleep(SETTLE)

    # An LSOA-only indicator and a ward-only one, taken from the page's own config
    keys = d.execute_script(
        "var lsoaOnly = null, wardOnly = null;"
        "var opts = document.getElementById('ov').options;"
        "for (var i = 0; i < opts.length; i++) {"
        "  var k = opts[i].value; if (!k || k === 'none') continue;"
        "  var lv = ovLevels(k) || [];"
        "  if (!lsoaOnly && lv.length === 1 && lv[0] === 'lsoa') lsoaOnly = k;"
        "  if (!wardOnly && lv.length === 1 && lv[0] === 'ward') wardOnly = k;"
        "}"
        "return { lsoaOnly: lsoaOnly, wardOnly: wardOnly,"
        "         crime: ovLevels('crime_violence_12mo') };")
    print("indicators: LSOA-only=%s  ward-only=%s  crime levels=%s"
          % (keys["lsoaOnly"], keys["wardOnly"], keys["crime"]))

    # ---- 1. the level switch -------------------------------------------------
    print("\n== level switch ==")
    pick(keys["lsoaOnly"])
    time.sleep(6)
    a = state()
    check("an LSOA-only indicator moves the map to LSOA", a["level"] == "lsoa", str(a))

    pick("crime_violence_12mo")
    time.sleep(6)
    b = state()
    check("a ward-only indicator brings the level back to ward",
          b["level"] == "ward", str(b))
    check("and the ward layer is back on the map", b["tnw"] and b["wardOnMap"], str(b))
    check("and the wards are actually shaded", b["shaded"] >= 3,
          "distinct ward fill colours: %s" % b["shaded"])
    check("and the badge agrees with the level", b["badge"] == "ward", str(b["badge"]))

    # ---- 2. dark is the higher figure ---------------------------------------
    print("\n== colour direction ==")
    res = d.execute_script("""
      var out = [];
      var opts = document.getElementById('ov').options;
      var ramp = rampClasses();
      var darkest = ramp[ramp.length - 1], lightest = ramp[0];
      for (var i = 0; i < opts.length && out.length < 6; i++) {
        var k = opts[i].value;
        if (!k || k === 'none') continue;
        var cfg = OV_CFG[k];
        if (!cfg || !ovHasLevel(k, 'ward')) continue;
        var field = resolveOvField(k);
        var best = null, worst = null;
        for (var nm in WARD_DATA) {
          var v = parseFloat((WARD_DATA[nm].indicators || {})[field]);
          if (isNaN(v)) continue;
          if (!best  || v > best.v)  best  = { n: nm, v: v };
          if (!worst || v < worst.v) worst = { n: nm, v: v };
        }
        if (!best || !worst || best.v === worst.v) continue;
        out.push({ key: k, wh: !!cfg.wh,
                   highColour: ovColor(best.n, k), lowColour: ovColor(worst.n, k),
                   darkest: darkest, lightest: lightest });
      }
      return out;
    """)
    for r in res:
        ok = r["highColour"] == r["darkest"] and r["lowColour"] == r["lightest"]
        check("%s (wh=%s): highest value is darkest" % (r["key"][:34], r["wh"]), ok,
              "high=%s low=%s (darkest=%s)" % (r["highColour"], r["lowColour"], r["darkest"]))

    grad = d.execute_script(
        "_syncScaleGradient();"
        "var g = (document.getElementById('sc-grad')||{}).style;"
        "var m = (document.getElementById('mls-grad')||{}).style;"
        "return { sc: g ? g.background : '', mls: m ? m.background : '' };")
    ramp_ends = d.execute_script("var r = rampClasses(); return [r[0], r[r.length-1]];")
    light, dark = ramp_ends
    def to_rgb(h):
        h = h.lstrip('#')
        return "rgb(%d, %d, %d)" % (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    check("the sidebar key runs light on the left, dark on the right",
          grad["sc"].find(to_rgb(light)) < grad["sc"].find(to_rgb(dark)) and to_rgb(dark) in grad["sc"],
          grad["sc"][:110])
    check("and the map legend strip matches it", grad["mls"] == grad["sc"],
          "strip: %s" % grad["mls"][:80])

    sev = [e for e in d.get_log("browser") if e["level"] == "SEVERE"
           and "gstatic" not in e["message"] and "googleapis" not in e["message"]]
    check("no severe console errors", not sev, [e["message"][:120] for e in sev[:3]])
finally:
    d.quit()

print("\n%d passed, %d failed" % (len(passes), len(fails)))
if fails:
    print("Failed: " + ", ".join(fails))
sys.exit(1 if fails else 0)
