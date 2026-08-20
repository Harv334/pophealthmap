"""What the tour actually does to the map on a small screen."""
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

import sys
SIZE = (1600, 1000) if len(sys.argv)>1 else (1366, 768)   # a common laptop, smaller than the 1600x1000 usually tested

o = Options()
o.add_argument("--headless=new")
o.add_argument("--window-size=%d,%d" % SIZE)
d = webdriver.Chrome(options=o)
try:
    d.get("http://localhost:8902/index.html?t=1")
    time.sleep(32)

    api = d.execute_script(
        "return { report: (typeof PH_REPORT !== 'undefined') ? Object.keys(PH_REPORT) : null,"
        "         tour: (typeof PH_TOUR !== 'undefined') ? Object.keys(PH_TOUR) : null };")
    print("PH_REPORT api:", api["report"])
    print("PH_TOUR  api:", api["tour"])

    d.execute_script("PH_TOUR.start();")
    time.sleep(4)

    def snap(label):
        r = d.execute_script("""
          var strip = document.getElementById('tour-strip');
          var sheet = document.getElementById('ward-sheet');
          var mapEl = document.getElementById('map');
          var mb = mapEl.getBoundingClientRect();
          var obstruction = 0;
          [strip, sheet].forEach(function (el) {
            if (!el) return;
            var s = getComputedStyle(el);
            if (s.display === 'none' || s.visibility === 'hidden') return;
            var r = el.getBoundingClientRect();
            if (r.height > 0 && r.bottom > mb.top) obstruction += r.height;
          });
          // how much of the selected/pinned geometry sits in the clear part
          var sel = [];
          if (typeof wLyr !== 'undefined' && wLyr) {
            wLyr.eachLayer(function (l) {
              var w = l.options && l.options.weight;
              if (w && w >= 3) sel.push(l);
            });
          }
          var vis = sel.map(function (l) {
            var b = l.getBounds();
            var nw = map.latLngToContainerPoint(b.getNorthWest());
            var se = map.latLngToContainerPoint(b.getSouthEast());
            return { w: Math.round(se.x - nw.x), h: Math.round(se.y - nw.y),
                     top: Math.round(nw.y), bottom: Math.round(se.y) };
          });
          return { zoom: map.getZoom(), mapH: Math.round(mb.height),
                   obstruction: Math.round(obstruction),
                   clearH: Math.round(mb.height - obstruction),
                   chapter: (document.getElementById('tour-ctr')||{}).textContent,
                   title: (document.getElementById('tour-title')||{}).textContent,
                   highlighted: vis };
        """)
        print("\n%-22s zoom=%-4s map=%spx obstruction=%spx clear=%spx"
              % (label, r["zoom"], r["mapH"], r["obstruction"], r["clearH"]))
        print("   %s | %s" % ((r["chapter"] or "").strip(), (r["title"] or "").strip()))
        rb = d.execute_script(
            "var n = document.querySelectorAll('#rb-list .rb-item').length;"
            "var c = (document.getElementById('rb-count')||{}).textContent || '';"
            "var nm = (document.getElementById('rb-name')||{}).value || '';"
            "return { rows: n, count: c.trim(), name: nm };")
        if rb["rows"] or rb["count"]:
            print("   custom area: %s rows | %s | name=%r" % (rb["rows"], rb["count"], rb["name"]))
        for v in r["highlighted"]:
            fits = v["bottom"] <= r["clearH"] and v["top"] >= 0
            print("   highlighted ward %sx%spx  top=%s bottom=%s  %s"
                  % (v["w"], v["h"], v["top"], v["bottom"],
                     "inside the clear area" if fits else "*** under the panels ***"))
        return r

    for i in range(9):
        t = d.execute_script("return (document.getElementById('tour-title')||{}).textContent || '';")
        if "Click a ward" in t or "Compare" in t or "Custom area" in t:
            snap(t.strip()[:22])
        d.execute_script("document.getElementById('tour-next').click();")
        time.sleep(3.5)
finally:
    d.quit()
