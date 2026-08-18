"""Verify the selected-ward sheet and the Query builder in a real browser.

Headless Chrome against the local http.server on 8902. Everything asserted
here is read out of the live DOM, not out of the source.
"""
import json
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8902/index.html"
# Any query parameter suppresses the first-visit tour, which would
# otherwise drive the UI out from under every assertion below.
if "?" not in URL:
    URL += "?t=1"
SETTLE = int(sys.argv[2]) if len(sys.argv) > 2 else 26

opts = Options()
opts.add_argument("--headless=new")
opts.add_argument("--window-size=1600,1000")
opts.add_argument("--disable-gpu")
opts.set_capability("goog:loggingPrefs", {"browser": "ALL"})

fails = []

def _ignorable(msg):
    """Console noise this project does not own.

    The JetBrains Mono latin-ext woff2 404s: Google's own css2 response still
    points at a file gstatic no longer serves, verified with curl outside the
    browser. The middots that trigger that subset fall back to the next mono in
    the stack, so it is cosmetic, but it is a real 404 and it is theirs.
    """
    return "favicon" in msg or ("fonts.gstatic.com" in msg and "404" in msg)




def check(name, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   {detail}" if detail else ""))
    if not ok:
        fails.append(name)


d = webdriver.Chrome(options=opts)
try:
    d.get(URL)
    time.sleep(SETTLE)

    # ── baseline: the map still draws ────────────────────────────────────────
    paths = d.execute_script("return document.querySelectorAll('path').length")
    check("map still draws its wards", paths > 600, f"{paths} paths")

    sev = [e for e in d.get_log("browser") if e["level"] == "SEVERE"]
    sev = [e for e in sev if not _ignorable(e["message"])]
    check("no severe console errors on load", not sev,
          json.dumps([e["message"][:220] for e in sev[:4]]))

    # ── the sheet ────────────────────────────────────────────────────────────
    ward = d.execute_script("""
      var names = Object.values(WARD_DATA)
        .filter(w => w && w.indicators && w.indicators.imd_decile_mean != null
                     && w.indicators.census_population != null)
        .map(w => w.name);
      return names.find(n => (WARD_DATA[n] && WARD_DATA[n].lad) === 'Brent') || names[0];
    """)
    second = d.execute_script("""
      var n = Object.values(WARD_DATA)
        .filter(w => w && w.lad === 'Brent' && w.indicators
                     && w.indicators.imd_decile_mean != null
                     && w.indicators.census_social_rented_pct != null)
        .map(w => w.name);
      return n.find(x => x !== arguments[0]);
    """, ward)
    print(f"\n  wards under test: {ward} / {second}")
    d.execute_script("showWard(arguments[0]);", ward)
    time.sleep(2.5)

    st = d.execute_script("""
      var s = document.getElementById('ward-sheet');
      var mw = document.getElementById('map-wrap');
      return {
        open: s.classList.contains('open'),
        vis: s.getBoundingClientRect().height,
        name: document.getElementById('ws-name').textContent,
        meta: document.getElementById('ws-meta').textContent,
        insight: (document.querySelector('#ws-body .ws-insight')||{}).textContent || '',
        kpis: document.querySelectorAll('#ws-body .ws-kpi').length,
        nums: [...document.querySelectorAll('#ws-body .ws-k-num')].map(e => e.textContent.trim()),
        hasSheetCls: mw.classList.contains('has-sheet'),
        lift: getComputedStyle(mw).getPropertyValue('--sheet-lift').trim(),
      };
    """)
    check("sheet opens on ward select", st["open"] and st["vis"] > 100, f"height {st['vis']:.0f}px")

    # ── declutter: a ward click no longer replaces the sidebar too ───────────
    declutter = d.execute_script("""
      return {
        wardTab: document.getElementById('tab-ward').classList.contains('active'),
        layersTab: document.getElementById('tab-layers').classList.contains('active'),
        panelFilled: document.getElementById('wpanel').textContent.length > 200,
        openCats: document.querySelectorAll('#wpanel .cat-body.open').length,
        totalCats: document.querySelectorAll('#wpanel .cat-body').length,
      };
    """)
    # The default tab is Indicators now, not Layers. What matters is that a
    # ward click does not drag the sidebar onto the ward profile.
    check("a ward click leaves the sidebar where it was",
          not declutter["wardTab"], str(declutter))
    check("but the profile is filled in, ready for Open profile", declutter["panelFilled"])
    check("only the first category starts expanded",
          declutter["openCats"] == 1 and declutter["totalCats"] > 2, str(declutter))

    d.execute_script("document.getElementById('ws-profile').click();")
    time.sleep(0.8)
    check("Open profile is the way into the full panel",
          d.execute_script("return document.getElementById('tab-ward').classList.contains('active')"))
    d.execute_script("""[...document.querySelectorAll('.sb-tab')].find(t=>t.dataset.tab==='layers').click();""")
    time.sleep(0.4)
    d.execute_script("showWard(arguments[0]);", ward)
    time.sleep(1.5)

    # ── status labels: one quiet family, no amber ────────────────────────────
    tags = d.execute_script("""
      var out = {};
      var b = document.querySelector('.dev-badge');
      var s = document.querySelector('.mode-soon');
      var c = document.querySelector('.cov-tag');
      function read(e) {
        if (!e) return null;
        var cs = getComputedStyle(e);
        return { bg: cs.backgroundColor, colour: cs.color, tt: cs.textTransform,
                 weight: cs.fontWeight, text: e.textContent.trim() };
      }
      out.dev = read(b); out.soon = read(s); out.cov = read(c);
      return out;
    """)
    # .mode-soon is absent once every mode is built, which is the intended
    # end state rather than a missing element, so it is skipped when gone.
    present = {k: v for k, v in tags.items() if v}
    for name, t in present.items():
        check(f"{name} label is not an amber pill",
              t["bg"] not in ("rgb(253, 240, 213)", "rgb(255, 244, 217)")
              and t["colour"] != "rgb(138, 90, 0)", str(t))
    check("labels no longer shout in capitals",
          present and all(t["tt"] == "none" for t in present.values()),
          str([t["tt"] for t in present.values()]))
    check("labels share one weight",
          len({t["weight"] for t in present.values()}) == 1,
          str([t["weight"] for t in present.values()]))
    check("no uppercase IN DEVELOPMENT pill left anywhere",
          d.execute_script("return !document.body.innerHTML.includes('IN DEVELOPMENT')"))
    check("sheet names the ward", st["name"] == ward, st["name"])
    check("sheet meta has borough, population and decile",
          st["meta"].count("·") >= 2 and "residents" in st["meta"], st["meta"])
    check("insight sentence built from real data",
          "deprivation decile" in st["insight"] and len(st["insight"]) > 60,
          st["insight"][:150])
    check("six KPI cards", st["kpis"] == 6, str(st["kpis"]))
    check("KPI values are populated, not all dashes",
          sum(1 for n in st["nums"] if n and n != "—") >= 5, str(st["nums"]))
    check("map furniture lifted above the sheet",
          st["hasSheetCls"] and st["lift"].endswith("px") and float(st["lift"][:-2]) > 100,
          st["lift"])

    # attribution must stay visible: it is a licence condition, not decoration
    attr = d.execute_script("""
      var a = document.querySelector('.leaflet-control-attribution');
      var s = document.getElementById('ward-sheet').getBoundingClientRect();
      var r = a.getBoundingClientRect();
      return { bottomOfAttr: r.bottom, topOfSheet: s.top, w: r.width };
    """)
    check("OSM attribution not buried under the sheet",
          attr["bottomOfAttr"] <= attr["topOfSheet"] + 2 and attr["w"] > 20,
          f"attr bottom {attr['bottomOfAttr']:.0f} vs sheet top {attr['topOfSheet']:.0f}")

    # ── three-state cycling ──────────────────────────────────────────────────
    states = []
    for _ in range(4):
        states.append(d.execute_script("""
          var s = document.getElementById('ward-sheet');
          return (s.classList.contains('collapsed') ? 'collapsed'
                : s.classList.contains('peek') ? 'peek' : 'full');
        """))
        d.execute_script("document.getElementById('ws-head').click();")
        time.sleep(0.45)
    check("head click cycles full > peek > collapsed > full",
          states == ["full", "peek", "collapsed", "full"], str(states))

    # ── pin to compare ───────────────────────────────────────────────────────
    d.execute_script("document.getElementById('ws-pin').click();")
    time.sleep(0.6)
    pin = d.execute_script("""
      return {
        label: document.getElementById('ws-pin').textContent.trim(),
        cols: document.querySelectorAll('#ws-body .ws-ptable thead th').length,
        rows: document.querySelectorAll('#ws-body .ws-ptable tbody tr').length,
        filled: [...document.querySelectorAll('#ws-body .ws-ptable tbody td')]
                  .filter(td => td.textContent.trim() && td.textContent.trim() !== '—').length,
        pinLayer: [...document.querySelectorAll('path')].some(p =>
                    p.getAttribute('stroke') === '#005EB8'
                    && (p.getAttribute('stroke-dasharray') || '').replace(/\\s/g,'') === '5,4'),
      };
    """)
    check("pin button flips to pinned", "Pinned" in pin["label"], pin["label"])
    check("pin table renders indicator rows and a ward column",
          pin["cols"] == 2 and pin["rows"] == 6, f"{pin['cols']} cols / {pin['rows']} rows")
    check("pin table carries real values", pin["filled"] >= 8, str(pin["filled"]))
    check("pinned ward outlined on the map", pin["pinLayer"])

    # buttons inside the head must not also resize the sheet
    after = d.execute_script("""
      var s = document.getElementById('ward-sheet');
      return s.classList.contains('peek') || s.classList.contains('collapsed');
    """)
    check("head buttons do not trigger the resize cycle", not after)

    # ── query builder ────────────────────────────────────────────────────────
    # ── compare mode ─────────────────────────────────────────────────────────
    check("Compare is no longer marked Soon",
          d.execute_script("""return !document.querySelector('.mode-btn[data-mode="compare"] .mode-soon')"""))

    d.execute_script("""document.querySelector('.mode-btn[data-mode="compare"]').click();""")
    time.sleep(1.2)
    c = d.execute_script("""
      return {
        pane: document.getElementById('tab-compare').classList.contains('active'),
        rail: document.querySelector('.mode-btn[data-mode="compare"]').getAttribute('aria-selected'),
        strip: getComputedStyle(document.querySelector('.sb-tabs')).display === 'none',
        count: document.getElementById('cmp-count').textContent.trim(),
        pins: document.querySelectorAll('#cmp-pins .cmp-pin').length,
        metricsOn: document.querySelectorAll('#cmp-metrics .q-chip.on').length,
        addMetric: !!document.getElementById('cmp-add-metric'),
      };
    """)
    check("Compare mode opens its own pane", c["pane"] and c["rail"] == "true" and c["strip"], str(c))
    check("four is the pin ceiling, per the design file", c["count"].endswith("of 4"), c["count"])
    check("one ward already pinned carries over from the sheet", c["pins"] == 1, str(c["pins"]))
    check("three metrics on by default, as designed", c["metricsOn"] == 3, str(c["metricsOn"]))
    check("an add-metric control is offered", c["addMetric"])

    # clicking a ward on the map while in Compare pins it
    d.execute_script("showWard(arguments[0]);", second)
    time.sleep(1.5)
    p2 = d.execute_script("""
      return {
        pins: document.querySelectorAll('#cmp-pins .cmp-pin').length,
        stillCompare: document.getElementById('tab-compare').classList.contains('active'),
        barOpen: document.getElementById('cmp-bar').classList.contains('open'),
        cards: document.querySelectorAll('#cmp-cards .cmp-card:not(.add)').length,
        sheetOpen: document.getElementById('ward-sheet').classList.contains('open'),
        colours: [...document.querySelectorAll('#cmp-pins .cmp-spine')]
                   .map(s => getComputedStyle(s).backgroundColor),
      };
    """)
    check("a map click in Compare pins the ward", p2["pins"] == 2, str(p2["pins"]))
    check("and does not drop out of Compare", p2["stillCompare"])
    check("the comparison drawer opens at two pins", p2["barOpen"] and p2["cards"] == 2, str(p2))
    check("the one-ward sheet gives way to the comparison", not p2["sheetOpen"])
    check("each pin gets its own colour",
          len(set(p2["colours"])) == 2, str(p2["colours"]))

    quick = d.execute_script("return document.getElementById('cmp-quick').textContent.trim()")
    check("quick view is written from the figures",
          "pinned wards" in quick and "%" in quick and len(quick) > 60, quick[:160])

    marks = d.execute_script("""
      return {
        hi: document.querySelectorAll('#cmp-cards .v.hi').length,
        lo: document.querySelectorAll('#cmp-cards .v.lo').length,
        rows: document.querySelectorAll('#cmp-cards .cmp-card:not(.add) .cmp-c-row').length,
        legend: document.getElementById('cmp-legend').textContent.trim(),
      };
    """)
    check("highest and lowest are marked on each metric",
          marks["hi"] >= 1 and marks["lo"] >= 1, str(marks))
    check("three metrics x two wards = six rows", marks["rows"] == 6, str(marks["rows"]))
    check("legend says colour tracks the indicator, not the size of the number",
          "not the size of the number" in marks["legend"]
          and "better" in marks["legend"] and "worse" in marks["legend"],
          marks["legend"][:120])

    # the pin colour on the card matches the outline on the map
    match = d.execute_script("""
      var sw = [...document.querySelectorAll('#cmp-cards .cmp-swatch')]
                 .map(s => getComputedStyle(s).backgroundColor);
      var paths = [...document.querySelectorAll('path')]
                 .map(p => p.getAttribute('stroke')).filter(Boolean);
      return { swatches: sw, hasBlue: paths.includes('#005EB8'), hasPink: paths.includes('#AE2573') };
    """)
    check("pinned wards outlined on the map in their card colours",
          match["hasBlue"] and match["hasPink"], str(match))

    # toggling a metric changes the comparison
    d.execute_script("""document.querySelector('#cmp-metrics [data-metric="3"]').click();""")
    time.sleep(0.6)
    check("toggling a metric adds a row to every card",
          d.execute_script("return document.querySelectorAll('#cmp-cards .cmp-card:not(.add) .cmp-c-row').length") == 8)

    # unpinning from the pane
    d.execute_script("""document.querySelector('#cmp-pins [data-unpin]').click();""")
    time.sleep(0.6)
    check("unpinning from the pane works",
          d.execute_script("return document.querySelectorAll('#cmp-pins .cmp-pin').length") == 1)

    d.execute_script("""document.querySelector('.mode-btn[data-mode="explore"]').click();""")
    time.sleep(0.6)
    check("leaving Compare closes the drawer and restores the strip",
          d.execute_script("""
            return !document.getElementById('cmp-bar').classList.contains('open')
              && getComputedStyle(document.querySelector('.sb-tabs')).display !== 'none'
              && document.getElementById('tab-layers').classList.contains('active');
          """))

    check("Query is not a sidebar tab any more",
          d.execute_script("return !document.querySelector('.sb-tab[data-tab=\\\"query\\\"]')"))
    check("Query sits in the top mode rail, after Explore",
          d.execute_script("""
            var b = [...document.querySelectorAll('.mode-btn')].map(x => x.dataset.mode);
            return b[0] === 'explore' && b[1] === 'query';
          """),
          d.execute_script("return [...document.querySelectorAll('.mode-btn')].map(x=>x.textContent.trim()).join(' | ')"))
    check("Query is not marked Soon",
          d.execute_script("""
            return !document.querySelector('.mode-btn[data-mode="query"] .mode-soon');
          """))

    d.execute_script("""
      document.querySelector('.mode-btn[data-mode="query"]').click();
    """)
    time.sleep(1.0)
    q = d.execute_script("""
      return {
        paneActive: document.getElementById('tab-query').classList.contains('active'),
        railOn: document.querySelector('.mode-btn[data-mode="query"]').getAttribute('aria-selected'),
        modePanelHidden: document.getElementById('mode-panel').hidden,
        tabStripHidden: getComputedStyle(document.querySelector('.sb-tabs')).display === 'none',
        chips: document.querySelectorAll('#q-services .q-chip').length,
        chipsOn: document.querySelectorAll('#q-services .q-chip.on').length,
        boroughs: document.querySelectorAll('#q-scope-sel option').length,
        scope: document.getElementById('q-scope-sel').value,
        conds: document.querySelectorAll('#q-conds [data-cond]').length,
        indOpts: document.querySelectorAll('#q-sort option').length,
        groups: [...document.querySelectorAll('#q-sort optgroup')].map(g => g.label),
      };
    """)
    check("query pane activates from the rail", q["paneActive"])
    check("rail marks Query selected, not Explore", q["railOn"] == "true", str(q["railOn"]))
    check("no placeholder card for Query", q["modePanelHidden"] is True)
    check("sidebar tab strip hidden in Query mode", q["tabStripHidden"])
    check("service chips rendered", q["chips"] == 9, str(q["chips"]))
    check("example seeds two services", q["chipsOn"] == 2, str(q["chipsOn"]))
    check("borough menu populated", q["boroughs"] > 30, str(q["boroughs"]))
    check("example scopes to Brent", q["scope"] == "Brent", q["scope"])
    check("example seeds two conditions", q["conds"] == 2, str(q["conds"]))
    check("indicator menu populated", q["indOpts"] > 80, str(q["indOpts"]))
    check("indicator menu grouped, ward and borough Fingertips kept apart",
          "Local Health (ward)" in q["groups"] and "Fingertips (borough level)" in q["groups"],
          str(q["groups"]))

    d.execute_script("document.getElementById('q-run').click();")
    time.sleep(4.0)
    r = d.execute_script("""
      return {
        bar: document.getElementById('q-resultbar').textContent.trim(),
        rows: document.querySelectorAll('#q-results .q-row').length,
        first: (document.querySelector('#q-results .q-row .q-nm')||{}).textContent || '',
        firstVal: (document.querySelector('#q-results .q-row .q-v')||{}).textContent || '',
        matchLayer: !!document.querySelector('path[stroke="#003087"]'),
      };
    """)
    check("query returns matching wards", r["rows"] > 0, f"{r['rows']} rows / bar: {r['bar']}")
    check("result bar counts wards and services",
          "ward" in r["bar"] and "gp practices" in r["bar"] and "pharmacies" in r["bar"], r["bar"])
    check("every result row is in Brent",
          d.execute_script("""
            return [...document.querySelectorAll('#q-results .q-row .q-nm small')]
              .every(s => s.textContent.startsWith('Brent'));
          """))
    check("rows carry a sort value", r["firstVal"] not in ("", "—"), r["firstVal"])
    check("matching wards outlined on the map", r["matchLayer"])

    # conditions are actually applied
    applied = d.execute_script("""
      var names = [...document.querySelectorAll('#q-results .q-row')].map(r => r.dataset.ward);
      return names.every(function (n) {
        var i = WARD_DATA[n].indicators;
        return parseFloat(i.imd_decile_mean) <= 3 && parseFloat(i.census_disability_any_pct) >= 5;
      });
    """)
    check("every result satisfies both conditions", applied)

    # borough-level caution fires for the named Fingertips series
    caution = d.execute_script("""
      var sel = document.querySelector('#q-conds [data-key="0"]');
      var opt = [...sel.options].find(o => /^ft_[a-z]/.test(o.value));
      sel.value = opt.value;
      sel.dispatchEvent(new Event('change', {bubbles:true}));
      return { key: opt.value, text: document.getElementById('q-caution').textContent.trim() };
    """)
    check("borough-level Fingertips key raises the caveat",
          "borough level" in caution["text"], f"{caution['key']}: {caution['text'][:110]}")

    ward_ft = d.execute_script("""
      var sel = document.querySelector('#q-conds [data-key="0"]');
      var opt = [...sel.options].find(o => /^ft_\\d+$/.test(o.value));
      sel.value = opt.value;
      sel.dispatchEvent(new Event('change', {bubbles:true}));
      return { key: opt.value, text: document.getElementById('q-caution').textContent.trim() };
    """)
    check("ward-level Fingertips key does not raise it",
          "borough level" not in ward_ft["text"],
          f"{ward_ft['key']}: {ward_ft['text'][:110] or '(no caution)'}")

    # the assistant's own test of the same thing
    lvl = d.execute_script("""
      return {
        named: PH_ASSISTANT.isBoroughLevel('ft_life_expectancy_male'),
        numeric: PH_ASSISTANT.isBoroughLevel('ft_93283'),
        label: PH_ASSISTANT.labelFor('imd_decile_mean').slice(0, 40),
      };
    """)
    check("assistant agrees: named ft_ is borough, numeric ft_ is ward",
          lvl["named"] is True and lvl["numeric"] is False, str(lvl))

    # clicking a result row selects that ward on the map and in the sheet
    d.execute_script("document.querySelector('#q-results .q-row').click();")
    time.sleep(2.0)
    picked = d.execute_script("""
      return { selW: selW, sheetName: document.getElementById('ws-name').textContent };
    """)
    check("clicking a result selects that ward",
          picked["selW"] and picked["selW"] == picked["sheetName"], str(picked))
    stay = d.execute_script("""
      return {
        pane: document.getElementById('tab-query').classList.contains('active'),
        rows: document.querySelectorAll('#q-results .q-row').length,
        rail: document.querySelector('.mode-btn[data-mode="query"]').getAttribute('aria-selected'),
      };
    """)
    check("the result list survives clicking through it",
          stay["pane"] and stay["rows"] > 0 and stay["rail"] == "true", str(stay))

    # leaving Query has to put the sidebar somewhere and give the strip back
    d.execute_script("document.querySelector('.mode-btn[data-mode=\"explore\"]').click();")
    time.sleep(0.6)
    left = d.execute_script("""
      return {
        query: document.getElementById('tab-query').classList.contains('active'),
        layers: document.getElementById('tab-layers').classList.contains('active'),
        strip: getComputedStyle(document.querySelector('.sb-tabs')).display !== 'none',
        rail: document.querySelector('.mode-btn[data-mode="explore"]').getAttribute('aria-selected'),
      };
    """)
    check("Explore leaves Query cleanly",
          not left["query"] and left["layers"] and left["strip"] and left["rail"] == "true", str(left))

    # and a ward clicked outside Query still opens its profile as before
    d.execute_script("document.querySelector('.mode-btn[data-mode=\"query\"]').click();")
    time.sleep(0.5)
    d.execute_script("document.querySelector('.mode-btn[data-mode=\"explore\"]').click();")
    time.sleep(0.5)
    d.execute_script("showWard(arguments[0]);", ward)
    time.sleep(1.2)
    check("a normal ward click leaves the sidebar alone",
          d.execute_script("""
            return !document.getElementById('tab-ward').classList.contains('active')
              && document.getElementById('ward-sheet').classList.contains('open');
          """))
    # collapse it, pick a different ward, and it should come back readable
    d.execute_script("""
      var h = document.getElementById('ws-head'), s = document.getElementById('ward-sheet');
      for (var i = 0; i < 3 && !s.classList.contains('collapsed'); i++) h.click();
    """)
    time.sleep(0.6)
    collapsed = d.execute_script(
        "return document.getElementById('ward-sheet').classList.contains('collapsed')")
    d.execute_script("showWard(arguments[0]);", second)
    time.sleep(1.2)
    reopened = d.execute_script("""
      var s = document.getElementById('ward-sheet');
      return { collapsed: s.classList.contains('collapsed'),
               peek: s.classList.contains('peek'),
               name: document.getElementById('ws-name').textContent };
    """)
    check("a collapsed sheet reopens fully on the next ward",
          collapsed and not reopened["collapsed"] and not reopened["peek"]
          and reopened["name"] == second, f"was collapsed={collapsed} then {reopened}")
    d.execute_script("showWard(arguments[0]);", ward)
    time.sleep(1.2)

    # deselect puts the sheet away and drops the lift
    d.execute_script("deselectWard();")
    time.sleep(0.6)
    gone = d.execute_script("""
      var mw = document.getElementById('map-wrap');
      return {
        open: document.getElementById('ward-sheet').classList.contains('open'),
        cls: mw.classList.contains('has-sheet'),
        lift: getComputedStyle(mw).getPropertyValue('--sheet-lift').trim(),
      };
    """)
    check("deselect hides the sheet and restores the map furniture",
          not gone["open"] and not gone["cls"] and gone["lift"] in ("0px", "0"), str(gone))

    # ── the AI panel ─────────────────────────────────────────────────────────
    d.execute_script("var t=document.getElementById('ai-toggle'); if(t) t.click();")
    time.sleep(1.2)
    ai = d.execute_script("""
      var p = document.getElementById('ai-panel');
      if (!p) return null;
      var lim = document.getElementById('ai-limit');
      var cau = p.querySelector('.ai-caution');
      var form = document.getElementById('ai-form');
      var log = document.getElementById('ai-log');
      return {
        open: p.classList.contains('open'),
        limitText: lim ? lim.textContent.trim() : null,
        limitInHead: !!(lim && lim.closest('#ai-head')),
        cautionText: cau ? cau.textContent.trim() : null,
        cautionBelowForm: !!(cau && form && (cau.compareDocumentPosition(form) & 2)),
        cautionInLog: !!(cau && log && log.contains(cau)),
        noteGone: !p.querySelector('.ai-note'),
        introMentionsCap: (log.textContent || '').includes('10 a day'),
      };
    """)
    if ai is None:
        check("AI panel present", False, "no #ai-panel (endpoint not configured?)")
    else:
        check("AI panel opens", ai["open"])
        check("the remaining count sits beside the panel title",
              ai["limitInHead"] and ai["limitText"], str(ai["limitText"]))
        check("it counts down rather than restating the policy",
              ai["limitText"] == "10 of 10 left today", str(ai["limitText"]))
        check("the cap is stated once, in the opening message", ai["introMentionsCap"])
        check("the old standalone limit note is gone", ai["noteGone"])
        check("the verification caution is at the bottom, below the input",
              ai["cautionBelowForm"] and not ai["cautionInLog"], str(ai))
        check("the caution still says what it needs to",
              "independently verified" in (ai["cautionText"] or ""), ai["cautionText"])
    d.execute_script("var c=document.getElementById('ai-close'); if(c) c.click();")
    time.sleep(0.4)

    # ── modes are exclusive: nothing a mode draws outlives it ────────────────
    def marks():
        return d.execute_script("""
          var s = [...document.querySelectorAll('path')].map(p => p.getAttribute('stroke'));
          return {
            queryHighlight: s.filter(x => x === '#003087').length,
            pinBlue: s.filter(x => x === '#005EB8').length,
            pinPink: s.filter(x => x === '#AE2573').length,
            cmpBar: document.getElementById('cmp-bar').classList.contains('open'),
            sheet: document.getElementById('ward-sheet').classList.contains('open'),
          };
        """)

    # Earlier checks changed the seeded conditions to exercise the Fingertips
    # caveats, so start from a clean query rather than inheriting that state.
    d.execute_script("""document.querySelector('.mode-btn[data-mode="query"]').click();""")
    time.sleep(1.0)
    d.execute_script("document.getElementById('q-clear').click();")
    time.sleep(0.5)
    d.execute_script("""
      var s = document.getElementById('q-scope-sel');
      s.value = 'Brent'; s.dispatchEvent(new Event('change', {bubbles:true}));
    """)
    time.sleep(0.3)
    d.execute_script("document.getElementById('q-run').click();")
    time.sleep(4.0)
    inQuery = marks()
    check("Query draws its matches while it is open", inQuery["queryHighlight"] > 0, str(inQuery))

    d.execute_script("""document.querySelector('.mode-btn[data-mode="compare"]').click();""")
    time.sleep(1.2)
    inCompare = marks()
    check("switching to Compare takes the query highlight off the map",
          inCompare["queryHighlight"] == 0, str(inCompare))
    check("and puts Compare's own marks on", inCompare["pinBlue"] > 0, str(inCompare))

    d.execute_script("""document.querySelector('.mode-btn[data-mode="explore"]').click();""")
    time.sleep(1.0)
    inExplore = marks()
    check("Explore drops the query highlight and the comparison drawer",
          inExplore["queryHighlight"] == 0 and not inExplore["cmpBar"], str(inExplore))
    # Pins survive into Explore on purpose: pinning starts on the ward sheet,
    # and a "Pin to compare" that marks nothing would be a button doing nothing.
    check("pins stay marked in Explore, where they are made",
          inExplore["pinBlue"] > 0, str(inExplore))
    check("but Query hides them, because Query owns the outlines then",
          inQuery["pinBlue"] == 0 and inQuery["pinPink"] == 0, str(inQuery))

    # ...but the state behind them survives, so reopening restores rather than resets
    d.execute_script("""document.querySelector('.mode-btn[data-mode="query"]').click();""")
    time.sleep(1.2)
    back = d.execute_script("""
      return {
        highlight: [...document.querySelectorAll('path')]
                     .filter(p => p.getAttribute('stroke') === '#003087').length,
        rows: document.querySelectorAll('#q-results .q-row').length,
      };
    """)
    check("reopening Query restores its highlight without re-running it",
          back["highlight"] > 0 and back["rows"] > 0, str(back))

    d.execute_script("""document.querySelector('.mode-btn[data-mode="compare"]').click();""")
    time.sleep(1.2)
    backC = d.execute_script("""
      return { pins: document.querySelectorAll('#cmp-pins .cmp-pin').length,
               marks: [...document.querySelectorAll('path')]
                        .filter(p => p.getAttribute('stroke') === '#005EB8').length };
    """)
    check("reopening Compare restores the pin list and its outlines",
          backC["pins"] > 0 and backC["marks"] > 0, str(backC))

    # the sidebar tabs are filters and are not mode state
    d.execute_script("""document.querySelector('.mode-btn[data-mode="explore"]').click();""")
    time.sleep(0.6)
    d.execute_script("""[...document.querySelectorAll('.sb-tab')].find(t=>t.dataset.tab==='overlay').click();""")
    time.sleep(0.5)
    d.execute_script("""document.querySelector('.mode-btn[data-mode="query"]').click();""")
    time.sleep(0.8)
    d.execute_script("""document.querySelector('.mode-btn[data-mode="explore"]').click();""")
    time.sleep(0.8)
    check("a sidebar tab is a filter, not a mode: Overlay survives the round trip",
          d.execute_script("""
            return document.getElementById('tab-overlay').classList.contains('active');
          """),
          d.execute_script("""
            return [...document.querySelectorAll('.tab-pane')].filter(p=>p.classList.contains('active')).map(p=>p.id).join(',');
          """))

    sev2 = [e for e in d.get_log("browser") if e["level"] == "SEVERE"]
    sev2 = [e for e in sev2 if not _ignorable(e["message"])]
    check("no severe console errors through the whole run", not sev2,
          json.dumps([e["message"][:220] for e in sev2[:4]]))

finally:
    d.quit()

print()
if fails:
    print(f"{len(fails)} FAILED: " + "; ".join(fails))
    sys.exit(1)
print("all checks passed")
