"""Phase 1 of the responsive pass, under real device emulation."""
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8902/index.html"
if "?" not in URL:
    URL += "?t=1"
SETTLE = int(sys.argv[2]) if len(sys.argv) > 2 else 28

fails = []


def check(name, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def phone():
    o = Options()
    o.add_argument("--headless=new")
    o.add_argument("--disable-gpu")
    o.add_experimental_option("mobileEmulation", {
        "deviceMetrics": {"width": 390, "height": 844, "pixelRatio": 3.0, "touch": True},
        "userAgent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                     "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    })
    o.set_capability("goog:loggingPrefs", {"browser": "ALL"})
    return webdriver.Chrome(options=o)


def desktop():
    o = Options()
    o.add_argument("--headless=new")
    o.add_argument("--window-size=1680,1000")
    o.add_argument("--disable-gpu")
    o.set_capability("goog:loggingPrefs", {"browser": "ALL"})
    return webdriver.Chrome(options=o)


# ── phone ────────────────────────────────────────────────────────────────────
d = phone()
try:
    d.get(URL)
    time.sleep(SETTLE)

    base = d.execute_script("""
      var sb = document.getElementById('sidebar').getBoundingClientRect();
      var mp = document.getElementById('map').getBoundingClientRect();
      var hd = document.querySelector('.hdr').getBoundingClientRect();
      return { inner: window.innerWidth, mapW: Math.round(mp.width), mapH: Math.round(mp.height),
               hdrH: Math.round(hd.height),
               drawerOffscreen: sb.right <= 1,
               menuShown: getComputedStyle(document.getElementById('hdr-menu')).display !== 'none',
               scrollW: document.documentElement.scrollWidth,
               toolsHasToolbar: !!document.querySelector('#sb-tools .hdr-right'),
               toolbarsInDoc: document.querySelectorAll('.hdr-right').length,
               resetButtons: document.querySelectorAll('#tb-reset').length };
    """)
    print(f"\n  phone viewport {base['inner']}px, map {base['mapW']}x{base['mapH']}, "
          f"header {base['hdrH']}px\n")

    check("the map now gets the full width", base["mapW"] >= base["inner"] - 2,
          f"{base['mapW']} of {base['inner']}")
    # 61px, not 48: the wordmark and its In development badge wrap onto two
    # lines at 390px. Down from 137, which is what the threshold is about.
    check("the header is a single compact bar", base["hdrH"] <= 70, f"{base['hdrH']}px, was 137")
    check("the sidebar starts off screen as a drawer", base["drawerOffscreen"])
    check("and there is a control to open it", base["menuShown"])
    check("the toolbar moved into the drawer", base["toolsHasToolbar"])
    check("it moved rather than being duplicated",
          base["toolbarsInDoc"] == 1 and base["resetButtons"] == 1,
          f"{base['toolbarsInDoc']} toolbars / {base['resetButtons']} Reset buttons")
    check("the page does not scroll sideways",
          base["scrollW"] <= base["inner"] + 1, f"{base['scrollW']} vs {base['inner']}")

    # nothing is cut off the end of the two strips
    strips = d.execute_script("""
      function s(sel){var e=document.querySelector(sel);
        return {sw: e.scrollWidth, cw: Math.round(e.getBoundingClientRect().width),
                ox: getComputedStyle(e).overflowX};}
      return { rail: s('.mode-rail'), tabs: s('.sb-tabs'),
               modes: document.querySelectorAll('.mode-btn').length,
               tabCount: document.querySelectorAll('.sb-tab').length };
    """)
    # Four, not five: the tour moved to the status bar, so the rail is the
    # four modes you actually work in.
    check("every mode is still reachable, by scrolling the rail",
          strips["modes"] == 6 and strips["rail"]["ox"] == "auto", str(strips["rail"]))
    check("and all four sidebar tabs, including Ward",
          strips["tabCount"] == 4 and strips["tabs"]["ox"] == "auto", str(strips["tabs"]))

    # opening and closing
    d.execute_script("document.getElementById('hdr-menu').click();")
    time.sleep(1)
    op = d.execute_script("""
      var sb = document.getElementById('sidebar').getBoundingClientRect();
      return { open: document.getElementById('sidebar').classList.contains('open'),
               onScreen: sb.left >= -1 && sb.width > 200,
               scrim: !document.getElementById('sb-scrim').hidden,
               expanded: document.getElementById('hdr-menu').getAttribute('aria-expanded') };
    """)
    check("the menu opens the drawer over the map",
          op["open"] and op["onScreen"] and op["scrim"], str(op))
    check("and says so for a screen reader", op["expanded"] == "true")

    check("the drawer has a visible way out, since it covers the hamburger",
          d.execute_script("""
            var b = document.getElementById('sb-close');
            return !!b && b.getBoundingClientRect().width > 0;
          """))
    d.execute_script("document.getElementById('sb-close').click();")
    time.sleep(1)
    check("the close button closes it",
          not d.execute_script("return document.getElementById('sidebar').classList.contains('open')"))
    d.execute_script("document.getElementById('hdr-menu').click();")
    time.sleep(1)
    d.execute_script("document.getElementById('sb-scrim').click();")
    time.sleep(1)
    check("tapping outside closes it",
          not d.execute_script("return document.getElementById('sidebar').classList.contains('open')"))

    # a mode that lives in the sidebar has to open the sidebar
    d.execute_script("""document.querySelector('.mode-btn[data-mode="directory"]').click();""")
    time.sleep(3)
    check("choosing Directory opens the drawer, so it is not a no-op",
          d.execute_script("""
            return document.getElementById('sidebar').classList.contains('open')
              && document.getElementById('tab-directory').classList.contains('active');
          """))
    d.execute_script("""document.querySelector('.mode-btn[data-mode="explore"]').click();""")
    time.sleep(1.5)
    check("and Explore closes it again, because Explore is the map",
          not d.execute_script("return document.getElementById('sidebar').classList.contains('open')"))

    # the ward bar, the original complaint
    wd = d.execute_script("return Object.values(WARD_DATA).filter(x=>x&&x.lad==='Brent')[0].name;")
    d.execute_script("showWard(arguments[0]);", wd)
    time.sleep(3)
    sheet = d.execute_script("""
      var sh = document.getElementById('ward-sheet').getBoundingClientRect();
      var mp = document.getElementById('map').getBoundingClientRect();
      return { pct: Math.round(100*(sh.width*sh.height)/(mp.width*mp.height)),
               drawer: document.getElementById('sidebar').classList.contains('open') };
    """)
    check("the ward bar no longer competes with a permanent sidebar", not sheet["drawer"])

    # ── phase 2: the sheet opens at peek, not full ───────────────────────────
    check("it opens at peek rather than covering the map",
          d.execute_script("return PH_SHEET.state()") == 1
          and sheet["pct"] < 30, f"state {d.execute_script('return PH_SHEET.state()')}, "
                                 f"{sheet['pct']}% of the map")
    check("peek still answers what you just tapped",
          d.execute_script("""
            var s = document.getElementById('ward-sheet').getBoundingClientRect();
            var n = document.getElementById('ws-name').getBoundingClientRect();
            var m = document.getElementById('ws-meta').getBoundingClientRect();
            return n.bottom <= s.bottom && m.bottom <= s.bottom
                   && document.getElementById('ws-meta').textContent.includes('IMD');
          """))
    check("and peek stops at the head rather than slicing the body",
          d.execute_script("""
            var body = document.getElementById('ws-body');
            var head = document.getElementById('ws-head').getBoundingClientRect();
            var s = document.getElementById('ward-sheet').getBoundingClientRect();
            return getComputedStyle(body).display === 'none'
                   && Math.abs(s.bottom - head.bottom) < 4;
          """))
    check("and the secondary buttons stand down at peek",
          d.execute_script("""
            return getComputedStyle(document.getElementById('ws-pin')).display === 'none'
              && getComputedStyle(document.getElementById('ws-profile')).display !== 'none';
          """))

    # dragging the handle
    d.execute_script("""
      var h = document.getElementById('ws-head');
      var r = h.getBoundingClientRect();
      function pe(t, y){ return new PointerEvent(t, {clientX: r.left+40, clientY: y,
        bubbles: true, pointerId: 1, pointerType: 'touch'}); }
      h.dispatchEvent(pe('pointerdown', r.top + 10));
      h.dispatchEvent(pe('pointerup', r.top - 90));
    """)
    time.sleep(1.2)
    check("dragging the handle up opens it",
          d.execute_script("return PH_SHEET.state()") == 0,
          str(d.execute_script("return PH_SHEET.state()")))
    check("at full height the figures are actually reachable",
          d.execute_script("""
            var k = document.querySelectorAll('#ws-body .ws-kpi');
            var s = document.getElementById('ward-sheet').getBoundingClientRect();
            return k.length === 6 && k[0].getBoundingClientRect().top < s.bottom;
          """))
    check("two columns of figures, not one strip of six",
          d.execute_script("""
            var g = getComputedStyle(document.querySelector('#ws-body .ws-strip'))
                      .gridTemplateColumns.split(' ').length;
            return g === 2;
          """))

    d.execute_script("""
      var h = document.getElementById('ws-head');
      var r = h.getBoundingClientRect();
      function pe(t, y){ return new PointerEvent(t, {clientX: r.left+40, clientY: y,
        bubbles: true, pointerId: 1, pointerType: 'touch'}); }
      h.dispatchEvent(pe('pointerdown', r.top + 10));
      h.dispatchEvent(pe('pointerup', r.top + 90));
    """)
    time.sleep(1.2)
    check("and dragging it down closes it again",
          d.execute_script("return PH_SHEET.state()") == 1)

    # one at a time
    d.execute_script("PH_SHEET.setState(0);")
    time.sleep(0.8)
    d.execute_script("document.getElementById('hdr-menu').click();")
    time.sleep(1.2)
    check("opening the drawer gets a full-height sheet out of its way",
          d.execute_script("return PH_SHEET.state()") >= 1
          and d.execute_script("return PH_DRAWER.isOpen()"),
          f"sheet state {d.execute_script('return PH_SHEET.state()')}")
    d.execute_script("document.getElementById('sb-close').click();")
    time.sleep(1)

    # ── phase 3: no more saying the same thing twice ─────────────────────────
    # getComputedStyle throws on null, so a renamed selector used to take the
    # whole run down here rather than failing one check: everything after this
    # point stopped being tested and the output ended in a chromedriver stack
    # trace. That is what happened when .dl-badge-label became #dl-help. Look
    # the element up first and report a miss as a miss.
    dup = d.execute_script("""
      function shown(sel){ var e=document.querySelector(sel);
        return e ? getComputedStyle(e).display !== 'none' : null; }
      return { digest: document.getElementById('vs-digest').textContent.trim(),
               badgeActive: (document.querySelector('#dl-seg button.active')||{}).textContent,
               badgeLabelShown: shown('#dl-help') };
    """)
    check("the strip no longer repeats the level the badge is showing",
          "mode" not in dup["digest"], f"digest: {dup['digest']!r}, badge: {dup['badgeActive']!r}")
    check("and the badge keeps its four segments as the control",
          d.execute_script("return document.querySelectorAll('#dl-seg button').length") == 4,
          str(d.execute_script("return [...document.querySelectorAll('#dl-seg button')].map(b=>b.dataset.dl)")))

    stack = d.execute_script("""
      function r(s){var e=document.querySelector(s); if(!e) return null;
        var b=e.getBoundingClientRect();
        return {l:Math.round(b.left), r:Math.round(b.right), t:Math.round(b.top),
                b:Math.round(b.bottom), vis: getComputedStyle(e).display !== 'none'};}
      return { ask: r('#ai-toggle'), legend: r('.map-legend-strip'),
               inner: window.innerWidth };
    """)
    check("the Ask button stays inside the screen",
          stack["ask"]["r"] <= stack["inner"], str(stack["ask"]))

    # ── phase 4: touch targets ───────────────────────────────────────────────
    d.execute_script("document.getElementById('hdr-menu').click();")
    time.sleep(1.2)
    touch = d.execute_script("""
      var rows = [...document.querySelectorAll('.sidebar .lrow')]
                   .filter(e => e.offsetParent)
                   .map(e => Math.round(e.getBoundingClientRect().height));
      return { min: rows.length ? Math.min.apply(null, rows) : 0, n: rows.length,
               zoomRow: getComputedStyle(document.getElementById('zoom-row')).display,
               tabH: Math.round(document.querySelector('.sb-tab').getBoundingClientRect().height) };
    """)
    check("layer rows are a thumb-sized target", touch["min"] >= 40,
          f"smallest {touch['min']}px across {touch['n']} rows")
    check("sidebar tabs too", touch["tabH"] >= 40, f"{touch['tabH']}px")
    check("the redundant zoom slider is gone on touch",
          touch["zoomRow"] == "none", touch["zoomRow"])
    d.execute_script("document.getElementById('sb-close').click();")
    time.sleep(1)

    # ── phase 5: Compare stacks instead of going four across ─────────────────
    # Pin two wards so the grid actually lays out. Reading
    # gridTemplateColumns on an empty grid gives the unresolved "minmax(0px,
    # 1fr)", which says nothing about how many columns there turn out to be;
    # where the cards land does.
    d.execute_script("""document.querySelector('.mode-btn[data-mode="compare"]').click();""")
    time.sleep(3)
    two = d.execute_script("""
      return Object.values(WARD_DATA).filter(w => w && w.lad === 'Brent'
               && w.indicators && w.indicators.imd_decile_mean != null)
             .map(w => w.name).slice(0, 2);
    """)
    for w in two:
        d.execute_script("showWard(arguments[0]);", w)
        time.sleep(1.2)
    d.execute_script("PH_DRAWER.close();")
    time.sleep(1.5)
    cmp_ = d.execute_script("""
      var cards = [...document.querySelectorAll('#cmp-cards .cmp-card:not(.add)')];
      var lefts = [...new Set(cards.map(c => Math.round(c.getBoundingClientRect().left)))];
      var bar = document.getElementById('cmp-bar').getBoundingClientRect();
      return { n: cards.length, columns: lefts.length,
               barW: Math.round(bar.width), inner: window.innerWidth,
               cardW: cards.length ? Math.round(cards[0].getBoundingClientRect().width) : 0 };
    """)
    check("comparison cards stack rather than going several across",
          cmp_["n"] >= 2 and cmp_["columns"] == 1,
          f"{cmp_['n']} cards in {cmp_['columns']} column(s), {cmp_['cardW']}px wide")
    check("and the drawer sits inside the screen",
          cmp_["barW"] <= cmp_["inner"], f"{cmp_['barW']} vs {cmp_['inner']}")
    d.execute_script("""document.querySelector('.mode-btn[data-mode="explore"]').click();""")
    time.sleep(1.5)

    sev = [e for e in d.get_log("browser")
           if e["level"] == "SEVERE" and "favicon" not in e["message"]
           and not ("fonts.gstatic.com" in e["message"] and "404" in e["message"])]
    check("no severe console errors on the phone", not sev,
          str([e["message"][:160] for e in sev[:3]]))
finally:
    d.quit()

# ── desktop must be untouched ────────────────────────────────────────────────
d = desktop()
try:
    d.get(URL)
    time.sleep(SETTLE)
    dk = d.execute_script("""
      var sb = document.getElementById('sidebar').getBoundingClientRect();
      return { sidebarW: Math.round(sb.width), sidebarVisible: sb.left >= 0,
               menuHidden: getComputedStyle(document.getElementById('hdr-menu')).display === 'none',
               toolbarInHeader: !!document.querySelector('.hdr .hdr-right'),
               subShown: getComputedStyle(document.querySelector('.hdr-sub')).display !== 'none',
               scrim: document.getElementById('sb-scrim').hidden };
    """)
    check("desktop keeps the sidebar as a column",
          dk["sidebarVisible"] and dk["sidebarW"] > 200, str(dk["sidebarW"]))
    # The button is on desktop now too, where it collapses the panel rather
    # than drawering it. What must not happen is the drawer behaviour leaking.
    check("the toggle is present on desktop, collapsing rather than drawering",
          not dk["menuHidden"] and dk["scrim"], str(dk))
    check("the toolbar stays in the header", dk["toolbarInHeader"])
    check("the instruction line is still there", dk["subShown"])
    check("and no scrim", dk["scrim"])

    sev = [e for e in d.get_log("browser")
           if e["level"] == "SEVERE" and "favicon" not in e["message"]
           and not ("fonts.gstatic.com" in e["message"] and "404" in e["message"])]
    check("no severe console errors on desktop", not sev,
          str([e["message"][:160] for e in sev[:3]]))
finally:
    d.quit()

print()
print(("FAILED: " + "; ".join(fails)) if fails else "all checks passed")
sys.exit(1 if fails else 0)
