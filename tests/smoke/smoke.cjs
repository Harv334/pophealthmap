/* Smoke tests for the live page: the things most likely to break silently
   when index.html changes. Run against a served copy of the site:

     python3 -m http.server 8792            # from the repo root
     node tests/smoke/smoke.cjs [base-url]

   Needs the playwright and axe-core npm packages. Prints PASS/FAIL per check
   and exits non-zero if any check fails. The North East checks are skipped
   when data/regions/north-east/ is not present (it is unpacked from the
   region-data release in CI). The output area checks are skipped when
   data/map/oa/index.json is not present. */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const AXE = fs.readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8');
const BASE = (process.argv[2] || 'http://localhost:8792').replace(/\/$/, '');
const ROOT = path.resolve(__dirname, '..', '..');
const HAS_NE = fs.existsSync(path.join(ROOT, 'data/regions/north-east/ward_data.json'));
const HAS_OA = fs.existsSync(path.join(ROOT, 'data/map/oa/index.json'));
const HAS_ENG = fs.existsSync(path.join(ROOT, 'data/regions/england/ward_data.json'));
const SETTLE = +(process.env.SMOKE_SETTLE_MS || 9000);

let failed = 0;
const ok = (cond, name, detail) => {
  console.log((cond ? 'PASS ' : 'FAIL ') + name + (detail && !cond ? '  -- ' + detail : ''));
  if (!cond) failed++;
};
const DESK = { width: 1400, height: 900 };
const PHONE = { width: 390, height: 844, isMobile: true, hasTouch: true };
const SIDEWAYS = { width: 844, height: 390, isMobile: true, hasTouch: true };

async function open(browser, url, vp) {
  const { isMobile, hasTouch, ...viewport } = vp;
  const page = await browser.newPage({ viewport, isMobile: !!isMobile, hasTouch: !!hasTouch });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto(BASE + '/' + url);
  await page.waitForTimeout(url.startsWith('index') ? SETTLE : 1500);
  return { page, errors };
}
// WCAG 2.2 AA, the standard the accessibility statement claims, plus axe's
// best-practice rules.
const AXE_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice'];
async function axeViolations(page) {
  await page.addScriptTag({ content: AXE });
  const r = await page.evaluate(tags => axe.run(document, {
    runOnly: tags, resultTypes: ['violations'] }), AXE_TAGS);
  // Map markers (GP practices, charities and their groups) sit where the
  // places are, so they crowd and overlap wherever places do. WCAG 2.2 lets a
  // target be smaller when the same thing can be done with another control
  // that is big enough (2.5.8, Equivalent), and every marker's place can be
  // found in the Directory, the charity list and the search box. So
  // target-size is not counted for map markers here; it is for everything else.
  r.violations.forEach(v => {
    if (v.id === 'target-size') v.nodes = v.nodes.filter(n => !/leaflet-marker-icon/.test(n.html.slice(0, 200)));
  });
  // Name the first element for each rule, so a failure says what to fix.
  return r.violations.filter(v => v.nodes.length).map(v => v.id + ' (' + v.nodes.length + ': ' + v.nodes[0].target.join(' ') + ')');
}
async function checkAxe(page, name) {
  const v = await axeViolations(page);
  ok(v.length === 0, 'accessibility: ' + name, v.join(', '));
}
// The 404 page asks for its files from https://pophealth.uk/ (see the page),
// so those requests are answered from the working copy being tested.
const routeSite = page => page.route('https://pophealth.uk/**', r => {
  const u = new URL(r.request().url());
  const f = path.join(ROOT, u.pathname === '/' ? 'index.html' : decodeURIComponent(u.pathname));
  return fs.existsSync(f) ? r.fulfill({ path: f }) : r.fulfill({ status: 404, body: '' });
});
/* Touch targets on a phone (workplan 2.10): axe's target-size rule, and our
   own floor of 44px for the controls people tap most, counting the
   invisible ::after hit area that the small round and square ones carry. */
async function checkTargets(page, name) {
  if (!(await page.evaluate(() => typeof axe !== 'undefined'))) await page.addScriptTag({ content: AXE });
  const r = await page.evaluate(() => axe.run(document, { runOnly: ['target-size'], resultTypes: ['violations'] }));
  const v = r.violations.flatMap(x => x.nodes.map(n => n.target.join(' ')));
  ok(v.length === 0, 'phone: touch targets big enough (axe target-size): ' + name, v.slice(0, 5).join(', '));
}
const hitSizes = (page, sel) => page.evaluate(sel => [...document.querySelectorAll(sel)].filter(el => el.getClientRects().length).map(el => {
  const r = el.getBoundingClientRect(), a = getComputedStyle(el, '::after');
  let w = r.width, h = r.height;
  if (a.content !== 'none' && a.position === 'absolute') {
    w -= (parseFloat(a.left) || 0) + (parseFloat(a.right) || 0);
    h -= (parseFloat(a.top) || 0) + (parseFloat(a.bottom) || 0);
  }
  return { id: el.id || el.getAttribute('aria-label') || el.textContent.trim().slice(0, 12), w: Math.round(w), h: Math.round(h) };
}), sel);
const rectOf = (page, sel) => page.evaluate(sel => {
  const e = document.querySelector(sel);
  if (!e || !e.getClientRects().length) return null;
  const r = e.getBoundingClientRect();
  return { l: r.left, t: r.top, r: r.right, b: r.bottom, w: r.width, h: r.height };
}, sel);
const overlaps = (a, b) => !!(a && b && a.l < b.r && a.r > b.l && a.t < b.b && a.b > b.t);
const setOv = (page, k) => page.evaluate(k => {
  const s = document.getElementById('ov'); s.value = k; s.dispatchEvent(new Event('change'));
}, k);

/* One icon set (workplan 2.9). Text that people read may hold arrows and
   symbols in a sentence, but nothing drawn as an icon: no emoji, no carets,
   crosses, ticks or glyph arrows, in the page or in ::before and ::after.
   Buttons carry no arrow characters at all, because their icons are SVG. */
const glyphCheck = page => page.evaluate(() => {
  const ICON = /[\u00D7\u2190-\u2191\u2193-\u21FF\u2300-\u23FF\u25A0-\u25FF\u2600-\u27BF\u2900-\u297F\u29C9\u2B00-\u2BFF]|[\u{1F300}-\u{1FAFF}]/u;
  const found = [];
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n; (n = walk.nextNode());) {
    const el = n.parentElement;
    if (!el || el.closest('script, style, noscript, svg')) continue;
    const t = n.nodeValue;
    if (ICON.test(t) || (el.closest('button, [role="button"]') && /[\u2190-\u21FF]/.test(t))) found.push(t.trim().slice(0, 40));
  }
  document.querySelectorAll('body *').forEach(el => {
    ['::before', '::after'].forEach(p => {
      const c = getComputedStyle(el, p).content;
      if (c && c !== 'none' && c !== 'normal' && ICON.test(c)) found.push(p + ' ' + c);
    });
  });
  return found;
});
/* Nothing on screen below 11px (workplan 2.9). The printable reports are
   separate documents, set for paper, and are not checked here. */
const smallText = page => page.evaluate(() => {
  const out = [];
  document.querySelectorAll('body *').forEach(el => {
    if (el.closest('svg, script, style, noscript') || !el.getClientRects().length) return;
    if (![...el.childNodes].some(n => n.nodeType === 3 && n.nodeValue.trim())) return;
    const px = parseFloat(getComputedStyle(el).fontSize);
    if (px < 11) out.push((el.id || el.className || el.tagName) + ' ' + px + 'px');
  });
  return out;
});

(async () => {
  const opts = process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {};
  const browser = await chromium.launch(opts);

  // London, desktop
  {
    const { page, errors } = await open(browser, 'index.html?t=1', DESK);
    ok(await page.evaluate(() => { let n = 0; wLyr.eachLayer(() => n++); return n; }) > 600, 'London wards drawn');
    await checkAxe(page, 'London on open');
    // One icon set and fewer component styles (workplan 2.9).
    {
      const src = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');
      const css = src.slice(src.indexOf('<style>'), src.indexOf('</style>'));
      const root = css.slice(css.indexOf(':root {'), css.indexOf('}', css.indexOf(':root {')));
      const radiusTokens = root.match(/--r-[\w-]+(?=\s*:)/g) || [];
      const shadowTokens = root.match(/--shadow[\w-]*(?=\s*:)/g) || [];
      const R = '(?:0|50%|var\\(--r-(?:sm|md|pill)\\))';
      const radiusOk = new RegExp('^' + R + '(?: ' + R + '){0,3}(?: !important)?$');
      const badRadius = (css.match(/border-radius\s*:\s*[^;}]+/g) || []).map(d => d.replace(/border-radius\s*:\s*/, '').trim())
        .filter(v => !radiusOk.test(v) && v !== '50% 50% 50% 0');
      const badShadow = (css.match(/box-shadow\s*:\s*[^;}]+/g) || []).map(d => d.replace(/box-shadow\s*:\s*/, '').trim())
        .filter(v => !/^(none|var\(--shadow(-sm|-lg)?\))( !important)?$/.test(v));
      const badSize = (css.match(/font-size\s*:\s*[\d.]+px/g) || []).filter(d => parseFloat(d.split(':')[1]) < 11);
      ok(radiusTokens.length === 3 && !badRadius.length, 'the stylesheet uses three corner tokens and no other corners',
        radiusTokens.join(', ') + ' / ' + badRadius.slice(0, 5).join(' | '));
      // The same for style attributes and style strings built in script,
      // leaving out the printable reports' own stylesheets and the gate in
      // the head, which is written before the stylesheet exists.
      let rest = src.slice(src.indexOf('</style>'));
      ['const REPORT_CSS = `', 'const WARD_ONEPAGE_CSS = `'].forEach(m => {
        const i = rest.indexOf(m); if (i !== -1) rest = rest.slice(0, i) + rest.slice(rest.indexOf('`;', i + m.length));
      });
      const Rv = '(?:0|50%|var\\(--r-(?:sm|md|pill)(?:, ?\\d+px)?\\))';
      const inlineOk = new RegExp('^' + Rv + '(?: ' + Rv + '){0,3}(?: !important)?$');
      const badInline = (rest.match(/border-radius\s*:\s*[^;"'`}]+/g) || []).map(d => d.replace(/border-radius\s*:\s*/, '').trim())
        .filter(v => !inlineOk.test(v) && !/\$\{/.test(v));
      ok(!badInline.length, 'style attributes and script-built styles use the corner tokens too', badInline.slice(0, 5).join(' | '));
      ok(shadowTokens.length === 3 && !badShadow.length, 'the stylesheet uses three shadows and no others',
        shadowTokens.join(', ') + ' / ' + badShadow.slice(0, 5).join(' | '));
      ok(!badSize.length, 'no font size below 11px in the stylesheet', badSize.join(', '));
    }
    const ui = await page.evaluate(() => {
      const tabs = [...document.querySelectorAll('.sb-tab')];
      const ms = document.getElementById('map-controls-sec');
      return {
        tabs: tabs.map(t => getComputedStyle(t).fontSize + (t.scrollWidth > t.clientWidth ? ' cut' : '')),
        icons: [...document.querySelectorAll('#tb-reset, #tb-link, #tb-cb, #tb-png')].map(b => {
          const u = b.querySelector('svg.ic use'); return u ? u.getAttribute('href') : 'none'; }),
        style: ms.querySelector('.sb-lbl').textContent.trim(),
        borders: [...ms.querySelectorAll('#border-weight label')].map(l => l.textContent.trim()).join(','),
        zoom: !!document.getElementById('zoom-slider'),
      };
    });
    ok(ui.tabs.every(t => t === '13px'), 'sidebar tabs are 13px and sized to their words', ui.tabs.join(', '));
    ok(ui.icons.every(h => /^#i-/.test(h)), 'the header buttons draw their icons from the one set', ui.icons.join(', '));
    ok(ui.style === 'Map style' && ui.borders === 'Thin,Medium,Thick' && !ui.zoom,
      'Map style offers Thin, Medium and Thick borders, and no zoom slider', JSON.stringify(ui));
    const bw = await page.evaluate(() => {
      const w = () => { let x = null; wLyr.eachLayer(l => { if (x == null && l.feature.properties.WD24NM !== selW) x = l.options.weight; }); return x; };
      const thin = w();
      document.querySelector('#border-weight input[value="6"]').click();
      const thick = w();
      document.querySelector('#border-weight input[value="1"]').click();
      return { thin, thick, back: w() };
    });
    ok(bw.thick > bw.thin && bw.back === bw.thin, 'Thick borders draw heavier ward outlines, and Thin puts them back', JSON.stringify(bw));
    // Background: Map or Satellite. Satellite swaps the street tiles for Esri
    // imagery with place names above the colours, credits it, and rides in Copy link.
    const bg = await page.evaluate(() => {
      const urls = () => { const u = []; map.eachLayer(l => { if (l._url) u.push(l._url); }); return u.join(' '); };
      const lbl = [...document.querySelectorAll('#basemap-choice label')].map(l => l.textContent.trim()).join(',');
      document.querySelector('#basemap-choice input[value="sat"]').click();
      _writeUrlState();
      const sat = { urls: urls(), credit: document.querySelector('.leaflet-control-attribution').textContent,
        link: location.search, pane: map.getPane('satLabels') && map.getPane('satLabels').style.pointerEvents };
      const btnOn = document.getElementById('basemap-btn').getAttribute('aria-pressed');
      document.querySelector('#basemap-choice input[value="map"]').click();
      _writeUrlState();
      const btnOff = document.getElementById('basemap-btn').getAttribute('aria-pressed');
      // The button on the map does the same, and the Map style choice follows it.
      document.getElementById('basemap-btn').click();
      const viaBtn = { urls: urls(), radio: document.querySelector('#basemap-choice input:checked').value };
      document.getElementById('basemap-btn').click();
      return { lbl, sat, back: urls(), link: location.search, btnOn, btnOff, viaBtn };
    });
    ok(bg.lbl === 'Map,Satellite' && /World_Imagery/.test(bg.sat.urls) && /World_Boundaries_and_Places/.test(bg.sat.urls) &&
       /Imagery/.test(bg.sat.credit) && /bg=sat/.test(bg.sat.link) && bg.sat.pane === 'none',
      'Satellite shows Esri imagery with place names, credits it and goes in the link', JSON.stringify(bg.sat).slice(0, 300));
    ok(!/World_Imagery|World_Boundaries/.test(bg.back) && /cartocdn|World_Light_Gray/.test(bg.back) && !/bg=sat/.test(bg.link),
      'Map puts the street background back', bg.back);
    ok(bg.btnOn === 'true' && bg.btnOff === 'false' && /World_Imagery/.test(bg.viaBtn.urls) && bg.viaBtn.radio === 'sat',
      'the Satellite button on the map and the Map style choice stay in step', JSON.stringify({ on: bg.btnOn, off: bg.btnOff, radio: bg.viaBtn.radio }));
    await checkAxe(page, 'Map style with the background choice');
    let g = await glyphCheck(page);
    ok(!g.length, 'no glyph or emoji icons on the opening screen', g.slice(0, 5).join(' | '));
    g = await smallText(page);
    ok(!g.length, 'nothing on the opening screen is smaller than 11px', g.slice(0, 5).join(' | '));
    // The level hint says the boundaries are showing only once they are drawn.
    await page.route('**/msoa_boundaries.json', async r => { await new Promise(x => setTimeout(x, 1500)); r.continue(); });
    await page.click('#dl-seg button[data-dl="msoa"]');
    await page.waitForTimeout(300);
    const early = await page.evaluate(() => ({ drawn: !!(msoaLyr && map.hasLayer(msoaLyr)), hint: document.getElementById('dl-hint').classList.contains('show') }));
    await page.waitForFunction(() => msoaLyr && map.hasLayer(msoaLyr), null, { timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(400);
    const late = await page.evaluate(() => document.getElementById('dl-hint').classList.contains('show') ? document.getElementById('dl-hint').textContent : '');
    ok(!early.drawn && !early.hint && /^The MSOA boundaries are shown/.test(late), 'the level hint waits until the boundaries are drawn', JSON.stringify([early, late]));
    await page.unroute('**/msoa_boundaries.json');
    // Reset forgets the level the reader chose, so indicators open at their own again.
    await page.click('#tb-reset');
    await page.waitForTimeout(600);
    ok(await page.evaluate(() => _readerLevel === null && currentDataLevel === 'ward'), 'Reset forgets the chosen level');
    await page.click('#sbtab-overlay');
    await page.click('#ov-chips button');
    await page.waitForTimeout(2500);
    ok(await page.evaluate(() => curOv && curOv !== 'none'), 'key indicator chip sets an indicator');
    ok(await page.evaluate(() => /\d/.test(document.getElementById('sc-lo').textContent)), 'legend shows numbers');
    // One hover card, and the LSOA's decile given once when the map is
    // coloured by the decile (workplan 2.9).
    const tip = await page.evaluate(() => {
      if (document.getElementById('ov').value !== 'imd_decile' || !lsoaLyr) return null;
      let l = null; lsoaLyr.eachLayer(x => { if (!l && x.feature.properties.imd_decile) l = x; });
      l.fire('mouseover', { latlng: l.getBounds().getCenter() });
      const el = l.getTooltip().getElement();
      const cs = el ? getComputedStyle(el) : null;
      const out = { text: el ? el.innerText : '', n: (el ? el.innerText.match(/Deprivation decile/g) || [] : []).length,
        bg: cs && cs.backgroundColor, radius: cs && cs.borderRadius, name: !!(el && el.querySelector('.tip-nm')) };
      l.closeTooltip();
      return out;
    });
    ok(!!tip && tip.n === 1 && tip.name && tip.bg === 'rgb(255, 255, 255)' && tip.radius === '10px',
      'the LSOA hover card is the one card style and gives the decile once', JSON.stringify(tip));
    await page.click('#ov-about');
    ok(await page.evaluate(() => /How to read it/.test(document.getElementById('ov-info-panel').innerText)), 'indicator card opens');
    // The card leads with what a general reader needs (P55): the period, how
    // to read it and the source, with the analyst's rows in a closed
    // Technical details.
    const icLay = await page.evaluate(() => {
      const p = document.getElementById('ov-info-panel'), d = p.querySelector('details.ic-tech');
      const top = [...p.querySelectorAll(':scope > dl.ic dt')].map(e => e.textContent);
      return { top, closed: !!d && !d.open, sum: d ? d.querySelector('summary').textContent : '',
        tech: d ? [...d.querySelectorAll('dt')].map(e => e.textContent) : [], seen: /Published at|Method/.test(p.innerText) };
    });
    ok(icLay.closed && icLay.sum === 'Technical details' && icLay.top.indexOf('Period') === 0 && icLay.top.indexOf('How to read it') !== -1 &&
       icLay.top.indexOf('Source') !== -1 && icLay.tech.indexOf('Method') !== -1 && icLay.tech.indexOf('Published at') !== -1 && !icLay.seen,
      'the indicator card shows the period, how to read it and the source, with the rest in a closed Technical details', JSON.stringify(icLay));
    await checkAxe(page, 'indicator and card');
    // Indicator descriptions and cards (workplan 2.3): one control opens the
    // card and a button at its top closes it; no card or description carries
    // internal codes, "=", "i.e.", dashes or title-case categories; Census
    // cards describe the method the pipeline uses; periods and dates are
    // written out in words.
    const c23 = await page.evaluate(() => {
      const panel = document.getElementById('ov-info-panel');
      const top = panel.firstElementChild;
      const out = {
        controls: !document.getElementById('ov-info') && document.getElementById('ov-about').textContent.trim() === 'About this indicator',
        closeAtTop: !!(top && top.querySelector('.ic-x')),
        bad: [], census: [], ft: [],
        periods: [fmtPeriod('2019 - 23'), fmtPeriod('2016/17 - 20/21'), fmtPeriod('Aug 2025 to Jul 2026'), fmtPeriod('2024/25')].join(' | '),
        ptal: PH_IND.label('ptal_score'),
        vintage: !document.getElementById('data-vintage') && !!document.querySelector('#ov-theme-view #core20-toggle'),
      };
      const BAD = /File_7|LSOA bridge|CLA01|NOMIS|=|\bi\.e\.|\be\.g\.|[\u2013\u2014]|→|pop-weighted|Offences \(|Public Order|Criminal Damage|Against the Person|Against Society|Deprivation domain|Domain score|'(Very )?(Good|Bad)'/;
      Object.keys(OV_META).forEach(k => {
        const d = document.createElement('div');
        d.innerHTML = indicatorCardHTML(k);
        const t = d.querySelector('.ic-t');
        if (t) t.remove();               // the publisher's own title, kept as published
        const text = d.textContent;
        [text, OV_META[k].desc || '', OV_PLAIN[k] || ''].forEach(x => {
          const m = x.match(BAD);
          if (m) out.bad.push(k + ': ' + m[0]);
        });
        const dd = name => { const dt = [...d.querySelectorAll('dt')].find(e => e.textContent === name); return dt ? dt.nextElementSibling.textContent : ''; };
        if (/\(TS\d+A?\)/.test(OV_META[k].src)) {
          // The card describes the Census figures that are loaded: summed
          // counts when the ward file says census_method 'counts' (and, for
          // LAs, when the LA file carries Census figures), and otherwise the
          // population-weighted averages an older data build made. Checked
          // in the state the data is in, then in the other state.
          const realMeta = WARD_META, realBd = BOROUGH_DATA;
          const states = [[censusWardCounts(), censusLaCounts()]];
          states.push([!states[0][0], !states[0][1]]);
          states.forEach(([wc, lc], i) => {
            if (i) {
              WARD_META = Object.assign({}, realMeta, { census_method: wc ? 'counts' : 'weighted' });
              BOROUGH_DATA = lc ? { E09000001: { census_no_car_pct: 40 } } : {};
            }
            try {
              const c = document.createElement('div');
              c.innerHTML = indicatorCardHTML(k);
              const dd2 = name => { const dt = [...c.querySelectorAll('dt')].find(e => e.textContent === name); return dt ? dt.nextElementSibling.textContent : ''; };
              const m = dd2('Method'), pub = dd2('Published at');
              const right = k === 'census_population' ? /sum the residents of the output areas/.test(m)
                : (wc ? /ward figure is calculated from the counts of the output areas/.test(m) &&
                        /Ward figures are calculated from the counts of the output areas in each ward/.test(pub)
                      : /ward figure is an average of the output areas that best fit the ward under the ONS lookup, weighted by their residents/.test(m) &&
                        /Ward figures are an average of the output areas in each ward, weighted by population/.test(pub)) &&
                  (lc ? /LA figure is calculated from the counts of the output areas in its wards/.test(m) &&
                        /LA\b.{0,40} figures are calculated (in the same way, from|from the counts of) the output areas in each LA/.test(pub)
                      : /LA figure is an average of its ward figures, weighted by population/.test(m) &&
                        /LA\b.{0,40} figures are an average of the ward figures, weighted by population/.test(pub));
              if (!right || /(add up|sum) the LSOAs/.test(m + ' ' + pub) ||
                  (wc && lc && /average|weight/.test(m + ' ' + pub))) out.census.push(k + (i ? ' (other state)' : ''));
            } finally { WARD_META = realMeta; BOROUGH_DATA = realBd; }
          });
        }
        if (/^ft_\d+$/.test(k) && FT_INFO[k.slice(3)]) {
          const u = dd('Updates');
          if (!/\b\d{1,2} (January|February|March|April|May|June|July|August|September|October|November|December) \d{4}\b/.test(u) ||
              /\d{2}\/\d{2}\/\d{4}/.test(u) || (u.match(/\./g) || []).length !== 1) out.ft.push(k);
        }
      });
      return out;
    });
    ok(c23.controls && c23.closeAtTop, 'one About this indicator control, and the card closes from its top', JSON.stringify(c23));
    await page.click('#ov-info-panel .ic-x');
    ok(await page.evaluate(() => document.getElementById('ov-info-panel').hidden &&
      document.getElementById('ov-about').getAttribute('aria-expanded') === 'false'), 'the close button closes the card');
    ok(!c23.bad.length, 'cards and descriptions carry no codes, "=", "i.e.", dashes or title-case categories', c23.bad.slice(0, 5).join(' | '));
    ok(!c23.census.length, 'every Census card describes the method the pipeline uses', c23.census.join(', '));
    ok(!c23.ft.length, 'Local Health cards say when OHID last updated them in one sentence, with the date in words', c23.ft.join(', '));
    ok(c23.periods === '2019 to 2023 | 2016/17 to 2020/21 | August 2025 to July 2026 | 2024/25', 'periods are written out by one helper', c23.periods);
    ok(c23.ptal === 'Public transport access (PTAL, 2014)', 'PTAL carries its year in its label', c23.ptal);
    ok(c23.vintage, 'data periods and Core20 have left the Indicators home', JSON.stringify(c23.vintage));
    await page.click('#ov-themes .ov-theme');
    await page.waitForTimeout(300);
    ok(await page.evaluate(() => !document.getElementById('ov-theme-view').hidden), 'theme list opens');
    await checkAxe(page, 'theme list');
    // Core20 sits in the Deprivation theme and nowhere else.
    const c20 = await page.evaluate(() => {
      const shown = () => !document.getElementById('ov-c20').hidden;
      const first = shown();
      document.getElementById('ov-back').click();
      [...document.querySelectorAll('#ov-themes .ov-theme')].find(b => /Deprivation/.test(b.textContent)).click();
      return [first, shown()];
    });
    ok(!c20[0] && c20[1], 'the Core20 control shows in the Deprivation theme only', JSON.stringify(c20));
    await checkAxe(page, 'Deprivation theme with Core20');
    await page.keyboard.press('Escape');
    await setOv(page, 'ft_93283');
    await page.waitForTimeout(2500);
    ok(await page.evaluate(() => _activeLevel()) === 'msoa', 'MSOA indicator opens at MSOA level');
    ok(await page.evaluate(() => Object.keys(MSOA_DATA).some(c => MSOA_DATA[c].ft_93250_vs)), 'England comparisons present in MSOA data');
    ok(await page.evaluate(() => /Figures end in/.test(ovAgeNote('ft_93227'))), 'old figures flagged');
    ok(await page.evaluate(() => /lockdown/i.test(ovCaution('census_public_transport_pct'))), 'Census travel caution');
    // Census figures open at LSOA, the level ONS publishes them at, with
    // every level from output area to local authority on offer.
    await setOv(page, 'census_bad_health_pct');
    await page.waitForTimeout(2000);
    ok(await page.evaluate(() => _activeLevel() === 'lsoa' &&
      ['borough', 'ward', 'msoa', 'lsoa'].every(l => ovHasLevel('census_bad_health_pct', l))), 'Census indicator offers every level');
    // The data build writes each LA's Census percentages, calculated from the
    // summed counts of its output areas, into the LA file. The map uses that
    // figure when it is there and works one out from the wards only when it
    // is not; a missing figure reads "No figure", never a dash.
    const laCen = await page.evaluate(() => {
      const code = _laCodes()[0];
      const k = 'census_no_car_pct';
      const before = BOROUGH_DATA[code];
      const fromWards = laAggregate(k)[code];
      BOROUGH_DATA[code] = Object.assign({}, before || {}, { [k]: 12.34 });
      const own = boroughOvValue(code, k);
      // Ask's LA lookup gives the same figure as the map, read from the LA
      // file, rather than its own average of the wards.
      const ward = Object.values(WARD_DATA).find(w => w && w.lad_code === code);
      const askLa = window.PH_ASSISTANT && ward ? PH_ASSISTANT.areasFor('la')[ward.lad] : null;
      const ask = askLa ? askLa.indicators[k] : undefined;
      const askTool = window.PH_ASSISTANT && ward ? PH_ASSISTANT.runTool('get_area', { name: ward.lad, level: 'la' }) : null;
      if (before) BOROUGH_DATA[code] = before; else delete BOROUGH_DATA[code];
      // Whichever state the LA file is in, the map shows its own Census
      // figure when it has one and the ward average when it has none, and
      // the card says which (see censusLaCounts).
      const has = Object.values(BOROUGH_DATA).some(d => Object.keys((d && (d.indicators || d)) || {}).some(x => /^census_.+_pct$/.test(x)));
      const live = boroughOvValue(code, k);
      const liveRight = before && before[k] != null ? live === before[k] : live === fromWards;
      return { own, fromWards, has, state: censusLaCounts() === has, liveRight, ask, askCode: askLa && askLa.lad_code,
        askTool: JSON.stringify(askTool || '').indexOf('12.34') !== -1, none: [fmtNum(null), fmtOv(undefined, k)] };
    });
    ok(laCen.own === 12.34 && typeof laCen.fromWards === 'number', 'an LA Census figure from the data build is used ahead of the ward average', JSON.stringify(laCen));
    ok(laCen.state && laCen.liveRight, 'the LA Census figure on the map, and what the card says about it, follow the LA file', JSON.stringify(laCen));
    ok(laCen.ask === 12.34 && laCen.askTool, 'Ask gives the same LA figure as the map, from the LA file', JSON.stringify(laCen));
    ok(laCen.none.every(t => t === 'No figure'), 'a missing figure reads "No figure"', laCen.none.join(' | '));
    // The local authority level is called LA everywhere, and the places that
    // explain the levels introduce it once as "local authority (LA)".
    const la = await page.evaluate(() => {
      const help = document.getElementById('dl-help').title;
      const card = (() => { const d = document.createElement('div'); d.innerHTML = indicatorCardHTML('census_bad_health_pct'); return d.textContent; })();
      const words = [...document.querySelectorAll('[title], [aria-label], option, optgroup, button, label, .sb-lbl, .cb-help, .q-hint')]
        .map(e => [e.getAttribute('title'), e.getAttribute('aria-label'), e.getAttribute('label'), e.textContent].filter(Boolean).join(' ')).join(' | ');
      return {
        button: document.querySelector('#dl-seg button[data-dl="borough"]').textContent.trim(),
        name: _levelName('borough'),
        focus: document.getElementById('borough-focus-label').textContent,
        helpIntro: (help.match(/local authority \(LA\)/g) || []).length,
        helpFive: /five sizes of area/.test(help),
        helpDash: /[\u2013\u2014]/.test(help),
        cardIntro: (card.match(/local authority \(LA, the council area\)/gi) || []).length,
        oldNames: (words.match(/Borough\/council|\bBoroughs?\b|Focus by borough|\bLocal authorit(y|ies)\b/g) || []),
      };
    });
    ok(la.button === 'LA' && la.name === 'LA' && la.focus === 'Focus by LA', 'the local authority level is called LA', JSON.stringify(la));
    ok(la.helpIntro === 1 && la.helpFive && !la.helpDash, 'Area level help introduces local authority (LA) once and counts five sizes', JSON.stringify(la));
    ok(la.cardIntro === 1, 'indicator card introduces local authority (LA, the council area) once, in plain words', JSON.stringify(la));
    ok(la.oldNames.length === 0, 'no control names the level Borough or Local authority', la.oldNames.join(', '));
    // One name for each thing (workplan 2.1): every indicator is named from
    // IND_NAMES wherever it appears, Compare and Query offer each measure
    // once, and the renamed controls say what they now say.
    const names = await page.evaluate(() => {
      const bad = [];
      document.querySelectorAll('#ov option').forEach(o => {
        if (o.value !== 'none' && o.textContent.split(' · ')[0] !== PH_IND.label(o.value)) bad.push('option ' + o.value);
      });
      CATS.forEach(c => c.fields.forEach(f => { if (f.l !== PH_IND.label(f.k)) bad.push('CATS ' + f.k); }));
      const words = [...document.querySelectorAll('[title], [aria-label], [placeholder], option, optgroup, button, label, h1, h2, h3, h4, .sb-lbl, .ov-theme')]
        .filter(e => !e.closest('#wpanel'))
        .map(e => [e.getAttribute('title'), e.getAttribute('aria-label'), e.getAttribute('placeholder'), e.getAttribute('label'), e.textContent].filter(Boolean).join(' ')).join(' | ');
      const heads = [...document.querySelectorAll('h1, h2, h3, h4, .sb-lbl, optgroup, .ov-theme')]
        .filter(e => !e.closest('#wpanel'))
        .map(e => (e.getAttribute('label') || '') + ' ' + (e.getAttribute('title') || '') + ' ' + e.textContent)
        .filter(t => /&/.test(t.replace(/A&E/g, '')));
      const card = (() => { const d = document.createElement('div'); d.innerHTML = indicatorCardHTML('ft_93250'); return d.textContent; })();
      return {
        bad, heads,
        old: (words.match(/Soc rent|Borough\/council|\bBoards?\b|\b[Mm]etrics?\b|VCSE|Point layers|Top & bottom/g) || []),
        card: /^Deaths from all causes/.test(card) && /Technical details\s*Official title: Mortality from all causes/.test(card),
        controls: [document.getElementById('ws-profile').textContent, document.getElementById('ws-pdf').textContent,
          document.getElementById('sbtab-vcse').textContent, document.querySelector('.mode-btn[data-mode="report"] .ml-short').textContent,
          document.querySelector('#icb-row .lname').firstChild.textContent.trim()].join(' | '),
      };
    });
    ok(names.bad.length === 0, 'every indicator is named from the one label table', names.bad.slice(0, 5).join(', '));
    ok(names.old.length === 0, 'no old names (Soc rent, Boards, metric, VCSE, Borough/council)', names.old.join(', '));
    ok(names.heads.length === 0, 'no heading outside the Area profile uses "&"', names.heads.slice(0, 3).join(' / '));
    ok(names.card, 'indicator card leads with the display label, with the official title under Technical details');
    ok(names.controls === 'Open full profile | Open printable report | Charities | Custom | Integrated care boards (ICBs)',
      'profile, report, Charities, Custom and ICB names', names.controls);
    await page.click('.mode-btn[data-mode="compare"]');
    await page.waitForTimeout(800);
    const cmp = await page.evaluate(() => {
      const opts = [...document.querySelectorAll('#cmp-add-metric option')].filter(o => o.value);
      const vals = opts.map(o => o.value), texts = opts.map(o => o.textContent);
      return {
        n: opts.length,
        twins: vals.filter(k => PH_IND.sameAs(k) && vals.indexOf(PH_IND.sameAs(k)) !== -1),
        repeats: texts.filter((t, i) => texts.indexOf(t) !== i),
        chips: [...document.querySelectorAll('#cmp-metrics span.q-chip')].map(c => c.textContent),
        levels: [...document.querySelectorAll('#cmp-levels .q-chip')].map(c => c.textContent),
      };
    });
    ok(cmp.n > 50 && !cmp.twins.length && !cmp.repeats.length, 'no indicator appears twice in Compare',
      cmp.n + ' options; twins ' + cmp.twins.join(', ') + '; repeats ' + cmp.repeats.join(', '));
    ok(cmp.chips.indexOf('Social renting') !== -1 && !cmp.levels.some(l => /board/i.test(l)),
      'Compare names indicators and ICBs by their one name', cmp.chips.join(', ') + ' / ' + cmp.levels.join(', '));
    // Help, hints, tool intros and messages (workplan 2.2): no alert boxes,
    // no "click" in text both phones and desktops show, help buttons named
    // for their topic, each tool opening with one sentence, and buttons that
    // wait for something to act on rather than raising an alert.
    const noAlert = f => !/\balert\s*\(/.test(fs.readFileSync(path.join(ROOT, f), 'utf8'));
    ok(noAlert('index.html') && noAlert('data/map/assistant.js'), 'no alert boxes left');
    const w22 = await page.evaluate(() => {
      const sentences = t => (t.match(/[.!?](\s|$)/g) || []).length;
      const shown = [...document.querySelectorAll('[title], [aria-label], [placeholder]')]
        .filter(e => !e.closest('#wpanel, #tour-strip'))
        .map(e => [e.getAttribute('title'), e.getAttribute('aria-label'), e.getAttribute('placeholder')].filter(Boolean).join(' '))
        .concat(['tab-query', 'tab-compare', 'tab-report'].map(id => document.getElementById(id).textContent));
      const intros = [document.querySelector('#tab-query .q-sub').firstChild.textContent,
        document.getElementById('cmp-sub').textContent, document.querySelector('#tab-report .rb-hint').textContent]
        .map(t => t.replace(/\s+/g, ' ').trim());
      return {
        helps: [...document.querySelectorAll('.lhelp')].map(b => b.getAttribute('aria-label') || b.textContent.trim())
          .filter(n => !n || n === '?' || /^explain/i.test(n)),
        click: shown.filter(t => /\bclick/i.test(t.replace(/double-click/gi, ''))),
        intros, oneSentence: sentences(intros[0]) === 1 && sentences(intros[1]) === 1 && sentences(intros[2]) <= 2,
        dashes: intros.filter(t => /[–—]/.test(t)),
        waiting: ['cmp-open', 'cmp-csv', 'q-csv', 'rb-report', 'rb-csv'].filter(id => !document.getElementById(id).disabled),
      };
    });
    ok(!w22.helps.length, 'every help button is named for its topic', w22.helps.join(', '));
    ok(!w22.click.length, 'no shared text says "click"', w22.click.slice(0, 3).join(' | '));
    // The Area profile's empty state is shown on phones too, before anything
    // is selected and again after Deselect.
    const w22ph = await page.evaluate(() => {
      const txt = () => { const e = document.querySelector('#wpanel .ph'); return e ? e.textContent.trim() : ''; };
      const before = txt();
      deselectWard(); const afterWard = txt();
      deselectLSOA(); const afterLsoa = txt();
      return [before, afterWard, afterLsoa];
    });
    ok(w22ph.every(t => t && !/\bclick/i.test(t)), 'the Area profile empty state says "select", not "click"', w22ph.join(' | '));
    ok(w22.oneSentence && !w22.dashes.length, 'Query, Compare and Custom area each open with one sentence', w22.intros.join(' | '));
    ok(!w22.waiting.length, 'tool buttons wait until there is something to act on', 'enabled: ' + w22.waiting.join(', '));
    await page.click('.mode-btn[data-mode="explore"]');
    await page.waitForTimeout(500);
    // Fuel poverty is published by LSOA, so it opens there, with wards on offer.
    await setOv(page, 'fuel_poverty_pct');
    await page.waitForTimeout(2000);
    ok(await page.evaluate(() => _activeLevel() === 'lsoa' && ovHasLevel('fuel_poverty_pct', 'ward')), 'fuel poverty opens at LSOA level',
       await page.evaluate(() => _activeLevel()));
    // A ward clicked opens its sheet.
    await setOv(page, 'crime_rate_per_1000');
    await page.waitForTimeout(2000);
    // The period beside the chosen indicator comes from the data.
    const vint = await page.evaluate(() => [document.getElementById('ov-cur-meta').textContent, fmtPeriod(WARD_META.crime_period)]);
    ok(!!vint[1] && vint[0].indexOf(vint[1]) !== -1, 'the indicator\'s period line is read from the data', vint.join(' / '));
    // The map's name follows the level and the indicator, and a level the
    // indicator lacks is refused in words that say which ones will work.
    ok(await page.evaluate(() => document.getElementById('map').getAttribute('aria-label')) ===
      'Map of London by ward, coloured by Crime rate per 1,000 residents.', 'map name follows the level and indicator',
      await page.evaluate(() => document.getElementById('map').getAttribute('aria-label')));
    // Area levels respect the reader (workplan 2.4). A level the indicator has
    // no figures for is greyed out but still focusable, its tooltip says why,
    // and pressing it says which levels will work in the hint beside the
    // buttons rather than in the middle of the map.
    const grey = await page.evaluate(() => {
      const b = document.querySelector('#dl-seg button[data-dl="msoa"]');
      return { aria: b.getAttribute('aria-disabled'), disabled: b.disabled, tab: b.tabIndex, title: b.title,
        others: [...document.querySelectorAll('#dl-seg button')].filter(x => x.getAttribute('aria-disabled') === 'true').map(x => x.dataset.dl).join(',') };
    });
    ok(grey.aria === 'true' && !grey.disabled && grey.tab >= 0 && /^msoa,lsoa(,oa)?$/.test(grey.others) &&
      grey.title === 'Crime rate per 1,000 residents is only available for LAs and wards, so it cannot be shown by MSOA.',
      'a level the indicator lacks is greyed out, focusable and says why', JSON.stringify(grey));
    await page.focus('#dl-seg button[data-dl="lsoa"]');
    await page.keyboard.press('Enter');
    await page.waitForTimeout(300);
    const gate = await page.evaluate(() => ({ lv: currentDataLevel, hint: document.getElementById('dl-hint').textContent,
      shown: document.getElementById('dl-hint').classList.contains('show'), role: document.getElementById('dl-hint').getAttribute('role') }));
    ok(gate.lv === 'ward' && gate.shown && gate.role === 'status' &&
      /^Crime rate per 1,000 residents is only available for LAs and wards, so it cannot be shown by LSOA\. Choose LA or Ward, or pick a different indicator\.$/.test(gate.hint),
      'pressing a greyed level says which levels will work, beside the buttons', JSON.stringify(gate));
    // Once the reader has chosen Ward, an indicator with ward figures stays at
    // Ward; one without moves and says so; Clear and leaving Custom area both
    // go back to the level the reader was at.
    const lvState = () => page.evaluate(() => ({ lv: currentDataLevel,
      hint: document.getElementById('dl-hint').classList.contains('show') ? document.getElementById('dl-hint').textContent : '' }));
    await page.click('#dl-seg button[data-dl="ward"]');
    await setOv(page, 'census_bad_health_pct');
    await page.waitForTimeout(1500);
    const kept = await lvState();
    ok(kept.lv === 'ward', 'once Ward is chosen, Bad or very bad health stays at Ward', JSON.stringify(kept));
    await setOv(page, 'imd_decile');
    await page.waitForTimeout(3000);
    const forced = await lvState();
    ok(forced.lv === 'lsoa' && /^Deprivation decile \(IMD 2025\) has no figures for wards, so the map is showing it by LSOA\. It will go back to wards when you choose an indicator that has them\.$/.test(forced.hint),
      'a forced move to another level is announced in the hint', JSON.stringify(forced));
    await page.click('#ov-clear');
    await page.waitForTimeout(800);
    const cleared = await lvState();
    ok(cleared.lv === 'ward' && /^The map is back to wards, the level you chose\.$/.test(cleared.hint), 'Clear goes back to the level the reader chose', JSON.stringify(cleared));
    await setOv(page, 'crime_rate_per_1000');
    await page.waitForTimeout(1000);
    await page.click('.mode-btn[data-mode="report"]');
    await page.waitForTimeout(2500);
    const custom = await page.evaluate(() => ({ lv: currentDataLevel, drawn: !!(lsoaLyr && map.hasLayer(lsoaLyr)) }));
    await page.click('.mode-btn[data-mode="explore"]');
    await page.waitForTimeout(800);
    ok(custom.lv === 'lsoa' && custom.drawn && await page.evaluate(() => currentDataLevel === 'ward' && curOv === 'crime_rate_per_1000'),
      'Custom area shows LSOAs, and leaving it goes back to the level before', JSON.stringify(custom));
    ok(!/[–—]/.test([grey.title, gate.hint, forced.hint, cleared.hint].join(' ')), 'level hints and tooltips have no dashes');
    // Presentation view hides the header and the side panel, and Esc leaves it.
    // It sits behind More in the header (workplan 2.11). A selected ward's
    // sheet is hidden while it lasts and comes back afterwards.
    await page.evaluate(() => { const l = wLyr.getLayers().find(l => l.feature.properties.WD24NM === 'Lansbury'); if (l) l.fire('click'); });
    await page.waitForTimeout(800);
    await page.click('#tb-more');
    await page.waitForTimeout(200);
    await page.click('#tb-png');
    await page.waitForTimeout(400);
    await page.click('text=This view');
    await page.waitForTimeout(800);
    const pres = await page.evaluate(() => ({ on: document.body.classList.contains('presenting'),
      hidden: ['.hdr', '#sidebar'].every(s => getComputedStyle(document.querySelector(s)).display === 'none'),
      overlays: ['#ward-sheet', '#cmp-bar'].filter(s => document.querySelector(s).getClientRects().length),
      h: map.getSize().y }));
    await page.keyboard.press('Escape');
    await page.waitForTimeout(500);
    const after = await page.evaluate(() => ({ on: document.body.classList.contains('presenting'),
      shown: getComputedStyle(document.getElementById('sidebar')).display !== 'none', banner: !!document.getElementById('screenshot-done'),
      sheet: document.getElementById('ward-sheet').classList.contains('open') && document.getElementById('ward-sheet').getClientRects().length > 0 }));
    ok(pres.on && pres.hidden && !pres.overlays.length && pres.h > 700 && !after.on && after.shown && !after.banner && after.sheet,
      'Presentation view hides the panels and Esc leaves it', JSON.stringify([pres, after]));
    // A ward chosen while Compare has the map is the one the sheet shows on
    // the way back to Explore, matching the profile, the link and the title.
    await page.click('.mode-btn[data-mode="compare"]');
    await page.waitForTimeout(800);
    await page.evaluate(() => { const l = wLyr.getLayers().find(l => l.feature.properties.WD24NM === 'Stonebridge'); if (l) l.fire('click'); });
    await page.waitForTimeout(600);
    await page.click('.mode-btn[data-mode="explore"]');
    await page.waitForTimeout(800);
    const back = await page.evaluate(() => ({ name: document.getElementById('ws-name').textContent, selW, title: document.title }));
    ok(back.name === back.selW && back.title.indexOf(back.selW) === 0, 'the ward sheet follows a ward chosen in Compare', JSON.stringify(back));
    // The Compare quick view gives an average decile as an average decile, to
    // one decimal place, and never turns it into a share of England (workplan
    // 1.11): "the most deprived 23%" from 2.3 overstated it.
    await page.click('.mode-btn[data-mode="compare"]');
    await page.waitForTimeout(600);
    await page.evaluate(() => { const l = wLyr.getLayers().find(l => l.feature.properties.WD24NM === 'Lansbury'); if (l) l.fire('click'); });
    await page.waitForTimeout(600);
    const qv = await page.evaluate(() => document.getElementById('cmp-quick').innerHTML);
    ok(/have an average deprivation decile of <strong>2\.3<\/strong> or lower, where 1 is the most deprived tenth of England\./.test(qv) &&
       !/deprived <strong>\d+%|deprived \d+%/.test(qv),
      'the Compare quick view gives the average deprivation decile, not a percentage', qv);
    await page.click('.mode-btn[data-mode="explore"]');
    await page.waitForTimeout(600);
    await page.evaluate(() => { const b = document.getElementById('ws-unpin-all'); if (b) b.click(); });
    await page.waitForTimeout(300);
    const wpt = await page.evaluate(() => { let c = null; wLyr.eachLayer(l => { if (!c && /Bethnal|Hoxton|Stratford|Peckham/.test(l.feature.properties.WD24NM)) c = l.getBounds().getCenter(); });
      if (!c) { wLyr.eachLayer(l => { if (!c) c = l.getBounds().getCenter(); }); } const p = map.latLngToContainerPoint(c); const r = document.getElementById('map').getBoundingClientRect(); return [r.left + p.x, r.top + p.y]; });
    await page.mouse.click(wpt[0], wpt[1]);
    await page.waitForTimeout(1500);
    ok(await page.evaluate(() => !!selW), 'clicking a ward selects it');
    // The hover card is drawn above the key, not under it (workplan 2.10).
    {
      const lg = await rectOf(page, '#map-legend-strip');
      let tip = null;
      for (const f of [0.5, 0.35, 0.65, 0.2, 0.8]) {
        await page.mouse.move(lg.l + lg.w * f, lg.t - 8);
        await page.mouse.move(lg.l + lg.w * f + 3, lg.t - 8);
        await page.waitForTimeout(500);
        if ((tip = await rectOf(page, '.leaflet-tooltip'))) break;
      }
      ok(!!tip && tip.b <= lg.t, 'a hover card just above the key is lifted clear of it', JSON.stringify({ tip, lg }));
      await page.mouse.move(5, 5);
    }
    await checkAxe(page, 'ward selected');
    await page.click('#sbtab-layers');
    const pcn = await page.evaluate(() => document.querySelectorAll('#pcn-focus option').length);
    ok(pcn > 50, 'PCN list loaded', pcn + ' options');
    // "Summary" (workplan 2.5): first in the sheet and the profile,
    // leading with the ward's ranking, with a case for each kind of ward.
    const plain = await page.evaluate(async () => {
      const out = {};
      for (const w of ['Stonebridge', 'Holloway', 'Barnes', 'Walbrook', 'Bishopsgate']) {
        showWard(w);
        await new Promise(r => setTimeout(r, 400));
        const body = document.getElementById('ws-body'), prof = document.getElementById('wpanel');
        const sheetBlock = body.firstElementChild, profBlock = prof.querySelector('.ws-insight');
        out[w] = {
          sheetFirst: !!sheetBlock && !!sheetBlock.querySelector('.ws-insight-h') && sheetBlock.querySelector('.ws-insight-h').textContent === 'Summary',
          profFirst: !!profBlock && profBlock.querySelector('.ws-insight-h').textContent === 'Summary' &&
            !!(profBlock.compareDocumentPosition(prof.querySelector('.keystats')) & Node.DOCUMENT_POSITION_FOLLOWING),
          sheet: sheetBlock.querySelector('p').textContent,
          prof: profBlock ? profBlock.querySelector('p').textContent : '',
          profH: profBlock ? profBlock.querySelector('.ws-insight-h').tagName : '',
        };
      }
      const tiles = [...document.querySelectorAll('#ws-body .ws-kpi .ws-k-lbl')].map(e => e.textContent);
      showWard('Stonebridge');
      await new Promise(r => setTimeout(r, 400));
      const lead = document.querySelector('#ws-body .ws-kpi .ws-k-lbl').textContent,
        leadNum = document.querySelector('#ws-body .ws-kpi .ws-k-num').textContent,
        meta = document.getElementById('ws-meta').textContent;
      return { out, tiles, lead, leadNum, meta, cols: getComputedStyle(document.querySelector('#ws-body .ws-strip')).gridTemplateColumns.split(' ').length };
    });
    const P = plain.out;
    ok(Object.values(P).every(x => x.sheetFirst && x.profFirst && x.sheet === x.prof),
      '"Summary" comes first in the ward sheet and the profile, and they match', JSON.stringify(P).slice(0, 300));
    ok(/^Stonebridge is more deprived than 99% of the wards in London, and \d+ of its \d+ neighbourhoods \(LSOAs\) are among the most deprived fifth of England\./.test(P.Stonebridge.sheet),
      'Stonebridge leads with its ranking and its Core20 count', P.Stonebridge.sheet);
    // P56: no "median" in the summary, a typical ward explained once, and
    // the sheet leads with the ranking rather than the average decile.
    ok(!/median/.test(P.Stonebridge.sheet) && /than in a typical London ward \(\d+\.\d%\)/.test(P.Stonebridge.sheet) &&
       /can expect to live about [\d.]+ years, [\d.]+ years (less|more) than in a typical London ward/.test(P.Stonebridge.sheet) &&
       /A typical ward is the middle one when all of them are put in order\.$/.test(P.Stonebridge.sheet),
      'the summary compares with a typical ward and says what one is', P.Stonebridge.sheet);
    ok(plain.lead === 'More deprived than' && plain.leadNum === '99%' && /more deprived than 99% of the wards in London/.test(plain.meta) &&
       !/decile/.test(plain.meta),
      'the ward sheet leads with its ranking, not the average decile', JSON.stringify([plain.lead, plain.leadNum, plain.meta]));
    ok(/^Holloway is (more|less) deprived than \d+% of the wards in London\./.test(P.Holloway.sheet) && !/deprived fifth/.test(P.Holloway.sheet),
      'Holloway, with no Core20 LSOAs, has no Core20 clause', P.Holloway.sheet);
    ok(/^Barnes is less deprived than \d+% of the wards in London/.test(P.Barnes.sheet), 'a Richmond ward says "less deprived than"', P.Barnes.sheet);
    ok(/^Bishopsgate is home to only 345 residents/.test(P.Bishopsgate.sheet) && !/median|deprived than/.test(P.Bishopsgate.sheet),
      'Bishopsgate opens with its residents and makes no comparison with the median', P.Bishopsgate.sheet);
    ok(/^Walbrook\b/.test(P.Walbrook.sheet) && /no Census figures of its own/.test(P.Walbrook.sheet) && !/median|deprived than|can expect/.test(P.Walbrook.sheet) && P.Walbrook.prof === P.Walbrook.sheet,
      'Walbrook, with no resident count, is neither ranked nor compared, in the sheet and the profile', P.Walbrook.sheet);
    ok(Object.values(P).every(x => x.profH === 'H3'), 'the profile\'s "Summary" is a sibling of its section headings (h3)', Object.values(P).map(x => x.profH).join(','));
    ok(!Object.values(P).some(x => /[–—]/.test(x.sheet)), 'no dashes in the ward summaries');
    ok(plain.tiles.length === 6 && plain.cols === 3 && !plain.tiles.some(t => /Population|Avg|\+|\((?!all\)|limited a lot\))/.test(t)),
      'six spelled-out tiles, three to a row, without the population', plain.cols + ' columns: ' + plain.tiles.join(' | '));
    await page.click('#ws-profile');
    await page.waitForTimeout(500);
    await checkAxe(page, 'Area profile with its summary');
    const [rep] = await Promise.all([page.waitForEvent('popup', { timeout: 20000 }).catch(() => null), page.click('#ws-pdf')]);
    if (rep) {
      await rep.waitForFunction(() => !!document.querySelector('.report'), null, { timeout: 30000 }).catch(() => {});
      const r = await rep.evaluate(() => {
        const s = document.querySelector('.report .keypoints.plain');
        return { h: s ? s.querySelector('h2').textContent : '', beforeKpis: !!s && s.nextElementSibling === document.querySelector('.report .kpis'),
          text: s ? s.querySelector('p').textContent : '' };
      });
      ok(r.h === 'Summary' && r.beforeKpis && r.text === P.Stonebridge.sheet,
        'the printable report opens with the same "Summary" summary', JSON.stringify(r).slice(0, 300));
      await rep.close();
    } else ok(false, 'the printable report opens');
    // The Area profile and the printable reports look finished (workplan 2.6).
    await page.evaluate(() => showWard('Lansbury'));
    await page.waitForTimeout(800);
    const prof = await page.evaluate(() => {
      const p = document.getElementById('wpanel');
      const ink = getComputedStyle(document.documentElement).getPropertyValue('--ink').trim();
      const probe = document.createElement('span'); probe.style.color = ink; document.body.appendChild(probe);
      const inkRgb = getComputedStyle(probe).color; probe.remove();
      const sv = [...p.querySelectorAll('.sv')].map(e => getComputedStyle(e));
      // The heading's words, without its icon and arrow.
      const heads = [...p.querySelectorAll('h3.cat-h .cat-hdr-l')].map(h => h.lastChild.textContent.trim());
      const pills = [...p.querySelectorAll('.sr-meta .gpill')].map(e => e.textContent.trim());
      const health = document.getElementById('pc-health');
      const visible = el => [...el.querySelectorAll('.sr')].filter(r => getComputedStyle(r).display !== 'none').length;
      const shownBefore = visible(health), btn = health.querySelector('.sr-all');
      btn.click();
      const shownAfter = visible(health), total = health.querySelectorAll('.sr').length;
      btn.click();
      const rowsIn = id => [...document.getElementById(id).querySelectorAll('.sr .sl')].map(e => e.firstChild.textContent);
      return {
        mono: sv.some(c => /mono/i.test(c.fontFamily)), green: sv.filter(c => c.color !== inkRgb).length,
        weight: sv.every(c => c.fontWeight === '600'), tnum: sv.every(c => /tabular-nums/.test(c.fontVariantNumeric)),
        heads, pills: [...new Set(pills)], oldPills: pills.filter(t => /^(ward|lsoa|msoa|oa|LA)$/.test(t)),
        name: p.querySelector('.wname').textContent, key: p.querySelector('.prof-key').textContent,
        captions: p.querySelectorAll('.cat-cap').length,
        shownBefore, shownAfter, total, expanded: btn.getAttribute('aria-expanded'),
        imd: rowsIn('pc-imd2025'), work: rowsIn('pc-economy'),
        cols: getComputedStyle(p.querySelector('.keystats')).gridTemplateColumns.split(' ').length,
        dashes: /[–—]/.test(p.innerText),
        // Every row's figure, against the hover card's rounding of the same value.
        // Only the number is compared: fmtOv adds % to any key ending _rate.
        mism: [...p.querySelectorAll('.sr[data-k]')].map(r => {
          const k = r.dataset.k, v = (WARD_DATA.Lansbury.indicators || {})[resolveOvField(k)];
          const num = t => (String(t).match(/-?[\d,]*\.?\d+/) || [''])[0];
          const a = num(r.querySelector('.sv').textContent), b = num(fmtOv(v, k));
          return a === b ? '' : k + ' ' + a + ' vs ' + b;
        }).filter(Boolean),
        keyLbls: [...p.querySelectorAll('.keystat-lbl')].map(e => e.textContent),
        workCap: (document.querySelector('#pc-economy .cat-cap') || {}).textContent || '',
        ratio: ([...p.querySelectorAll('.sr[data-k]')].find(r => ovIsStdRatio(r.dataset.k)) || { textContent: '' }).textContent,
        popRows: p.querySelectorAll('.sr[data-k="census_population"]').length,
      };
    });
    ok(!prof.mism.length, 'every Lansbury profile row is rounded as the hover card rounds it', prof.mism.slice(0, 5).join(' | '));
    ok(prof.keyLbls.indexOf('Bad or very bad health') !== -1 && prof.keyLbls.indexOf('Good or very good health') !== -1 && prof.popRows === 0,
      'the key figures use the indicators\' own names and the population appears once', prof.keyLbls.join(' | ') + ' / rows ' + prof.popRows);
    ok(!prof.mono && prof.green === 0 && prof.weight && prof.tnum, 'profile figures are Inter 600, tabular, in the ink colour', JSON.stringify(prof).slice(0, 200));
    ok(['Health and care', 'People and communities', 'Work, money and education', 'Housing and transport',
        'Environment and access', 'Crime and safety'].every(h => prof.heads.indexOf(h) !== -1) && !prof.heads.some(h => /&/.test(h)),
      'profile section headings say "and"', prof.heads.join(' | '));
    ok(prof.oldPills.length === 0 && ['estimated from LSOAs', 'added up from LSOAs', 'whole-LA figure'].every(t => prof.pills.indexOf(t) !== -1) &&
       prof.pills.indexOf('estimated from MSOAs') === -1,
      'profile marks estimates with worded pills, not level codes, and no MSOA pill under the OHID estimates heading', prof.pills.join(', ') + ' / ' + prof.oldPills.join(', '));
    ok(/average of the smaller LSOAs in the ward/.test(prof.key) && !/larger areas and weighted/.test(prof.key) &&
       /claimant rate from the Office for National Statistics/.test(prof.key) && /Office for Health Improvement and Disparities \(OHID\)/.test(prof.key) &&
       !/Department for Work and Pensions/.test(prof.key) && !/Department for Work and Pensions/.test(prof.workCap) && /\(England is 100\)/.test(prof.ratio),
      'the profile key calls LSOAs smaller, names one claimant source and spells out OHID, and a ratio carries its anchor', prof.key.slice(0, 200) + ' / ' + prof.ratio);
    ok(prof.captions >= 6 && /output areas/.test(prof.key) && !/~/.test(prof.name), 'each section has a caption, the key covers output areas, no "~"', prof.name);
    ok(prof.shownBefore === 8 && prof.shownAfter === prof.total && prof.total > 10, 'a long section shows eight rows and then all of them',
      prof.shownBefore + ' then ' + prof.shownAfter + ' of ' + prof.total);
    ok(prof.imd.indexOf('Deprivation score by ward (IMD 2025)') !== -1 && prof.work.indexOf('Claimant rate, working age') !== -1 &&
       prof.work.indexOf('Out-of-work benefit claimants') !== -1 && prof.work.indexOf('Unemployment (Census 2021)') !== -1,
      'profile shows the IMD score, and the claimant rate and unemployment measures under Work', prof.imd.join(', ') + ' / ' + prof.work.join(', '));
    ok(prof.cols === 2 && !prof.dashes, 'profile key figures two by two, and no dashes in the profile');
    await checkAxe(page, 'Area profile sections');
    g = await glyphCheck(page);
    ok(!g.length, 'no glyph or emoji icons in the ward sheet and the Area profile', g.slice(0, 5).join(' | '));
    ok(await page.evaluate(() => [...document.querySelectorAll('#wpanel .cat-hdr .cat-ico')].every(i => i.tagName === 'svg' && /^#i-/.test(i.querySelector('use').getAttribute('href')))),
      'the Area profile sections take their icons from the one set');
    g = await smallText(page);
    ok(!g.length, 'nothing in the ward sheet or the Area profile is smaller than 11px', g.slice(0, 5).join(' | '));
    const [rep2] = await Promise.all([page.waitForEvent('popup', { timeout: 20000 }).catch(() => null), page.click('#ws-pdf')]);
    if (rep2) {
      await rep2.waitForFunction(() => !!document.querySelector('.report'), null, { timeout: 30000 }).catch(() => {});
      const r2 = await rep2.evaluate(() => {
        const t = document.body.innerText;
        const blk = [...document.querySelectorAll('.locblock')].find(b => /Parks and green spaces/i.test(b.querySelector('h4').textContent));
        return {
          lang: document.documentElement.lang,
          parks: blk ? [...blk.querySelectorAll('li:not(.more)')].map(l => l.textContent) : [],
          pcodes: t.match(/\bP\d{1,2}\b/g) || [],
          counts: [...new Set((t.match(/\b\d[\d,]* (?:[A-Z]\w+ )?wards\b/g) || []).map(m => m.split(' ')[0]))],
          dashes: (t.match(/[–—]/g) || []).length,
          png: document.querySelectorAll('.chart-dl-wrap').length && [...document.querySelectorAll('.chart-dl-wrap')].every(w => /SVG/.test(w.textContent) && /PNG/.test(w.textContent)),
          // The key figure and the bar for the same indicator show one value.
          badKpi: (() => { const k = [...document.querySelectorAll('.kpi')].find(e => e.querySelector('.kpi-lbl').textContent === 'Bad or very bad health'); return k ? k.querySelector('.kpi-val').textContent.trim() : ''; })(),
          badBar: (() => { const t = [...document.querySelectorAll('svg.ch-compare text')]; const i = t.findIndex(e => e.textContent === 'Bad or very bad health'); return i === -1 ? '' : (t.slice(i + 1).find(e => /\d/.test(e.textContent)) || {}).textContent || ''; })(),
          // The bar for a descriptive row is the neutral colour, whichever
          // side of the median it falls (direction comes from OV_CFG).
          careFill: (() => { const t = [...document.querySelectorAll('svg.ch-compare text')].find(e => e.textContent === 'Unpaid carers');
            let n = t && t.nextElementSibling, seen = 0;
            while (n && !(n.tagName === 'rect' && ++seen === 2)) n = n.nextElementSibling;
            return n ? n.getAttribute('fill') : ''; })(),
          mail: (() => { const a = document.querySelector('.footer a[href^="mailto:"]'); return a ? decodeURIComponent(a.getAttribute('href')) + ' | ' + a.textContent : ''; })(),
        };
      });
      ok(r2.badKpi && r2.badKpi === r2.badBar, 'the report\'s key figure and bar chart give the same value', r2.badKpi + ' / ' + r2.badBar);
      ok(r2.parks.some(p => /Bartlett Park/.test(p)) && r2.parks.every(p => / ha( in all)?$/.test(p)), 'the Lansbury report lists real parks', r2.parks.join(' | '));
      ok(r2.lang === 'en-GB' && r2.pcodes.length === 0 && r2.counts.length === 1 && r2.dashes === 0,
        'the report declares en-GB, has no "P95", gives one ward count and no dashes', JSON.stringify(r2));
      ok(r2.png, 'every report chart offers SVG and PNG');
      ok(r2.careFill.toUpperCase() === '#7A8794', 'the report draws unpaid care as descriptive, in the neutral colour', r2.careFill);
      ok(/^mailto:contact@pophealth\.uk\?subject=Problem with a figure in the report for Lansbury ward&body=/.test(r2.mail) &&
         /Link to the map view: http/.test(r2.mail) && /please describe it here/.test(r2.mail) && / \| contact@pophealth\.uk$/.test(r2.mail),
        'the report footer says how to report a problem with a figure, by a pre-filled email', r2.mail);
      await rep2.close();
    } else ok(false, 'the Lansbury report opens');
    // The LSOA profile gives the published decile band rather than "Assessment".
    const lp = await page.evaluate(async () => {
      await ensureLsoaImd();
      showLSOA(LSOA_IMD.features.find(f => f.properties.ward === 'Lansbury'));
      const t = document.getElementById('wpanel').innerText;
      return { assess: /Assessment/.test(t), band: /(most|least) deprived LSOAs in England/.test(t),
        pills: document.querySelectorAll('#wpanel .sr .gpill').length, amp: /&/.test([...document.querySelectorAll('#wpanel h3')].map(h => h.textContent).join(' ')) };
    });
    ok(!lp.assess && lp.band && lp.pills === 0 && !lp.amp, 'LSOA profile gives the decile band, no level pills and no "&"', JSON.stringify(lp));
    // P60: counts on the LSOA card are whole numbers, PTAL has one decimal,
    // and small labels are in sentence case rather than capitals.
    const typo = await page.evaluate(() => {
      const rows = [...document.querySelectorAll('#wpanel .sr')].map(r => r.textContent);
      const tt = sel => { const e = document.querySelector(sel); return e ? getComputedStyle(e).textTransform : 'none'; };
      return { rows: rows.filter(t => /^(Stations within 1 km|Bus stops within 800 m)/.test(t)), ptal: indDecimals('ptal_score'),
        wardStations: indDecimalsAt('rail_stations_1km', 'ward'), tt: [tt('.keystat-lbl'), tt('.rb-group h3')] };
    });
    ok(typo.rows.every(t => !/\d\.\d+$/.test(t.trim())) && typo.ptal === 1 && typo.wardStations === 1 && typo.tt.every(t => t === 'none'),
      'LSOA counts are whole numbers, PTAL has one decimal and the small labels are not in capitals', JSON.stringify(typo));
    // The figures an analyst challenges first (workplan 1.11).
    const fig = await page.evaluate(() => {
      const vals = _bivIds('ward').map(id => _bivValue('ward', id, 'census_bad_health_pct')).filter(x => x != null);
      return {
        fmt: [fmtOv(7.3, 'ft_suicide_rate'), fmtOv(12.34, 'census_bad_health_pct'), fmtOv(101.34, 'ft_93227'), fmtOv(2.345, 'imd_decile_mean'), fmtOv(85.27, 'crime_rate_per_1000')],
        desc: ['ft_93089', 'census_car_to_work_pct', 'census_active_travel_pct', 'census_provides_unpaid_care_pct'].filter(k => !OV_CFG[k].descriptive),
        ctx: [ovContext('ward', 'census_bad_health_pct', Math.max(...vals)), ovContext('ward', 'census_bad_health_pct', Math.min(...vals))],
      };
    });
    ok(fig.fmt.join('|') === '7.3 per 100,000|12.3%|101.3|2.3|85.3',
      'figures carry their own unit: "%" only on percentages, a rate per 100,000 says so, a ratio has none, and a mean decile has one place', fig.fmt.join(' | '));
    ok(!fig.desc.length, 'fertility, car to work, walk or cycle to work and unpaid care are descriptive', fig.desc.join(', '));
    ok(/^Within London: higher than 99% of wards$/.test(fig.ctx[0]) && /^Within London: lower than 99% of wards$/.test(fig.ctx[1]) && !/100%/.test(fig.ctx.join(' ')),
      'hover context stops at 99%, never 100%', fig.ctx.join(' | '));
    await setOv(page, 'ft_93089');
    await page.waitForTimeout(1500);
    const fert = await page.evaluate(() => [document.getElementById('rank-best-hdr').textContent, document.getElementById('rank-worst-hdr').textContent]);
    ok(/^Highest 10/.test(fert[0]) && /^Lowest 10/.test(fert[1]), 'fertility ranks as Highest and Lowest, not Best and Worst', fert.join(' / '));
    // Every indicator card ends with a way to report a problem with the
    // figure: an email to us, already filled in, and nothing else.
    const icRep = await page.evaluate(() => {
      const panel = document.getElementById('ov-info-panel');
      const a = panel && panel.querySelector('a.ic-report');
      if (!a) return null;
      a.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
      return { text: a.textContent, href: decodeURIComponent(a.getAttribute('href')), cards: Object.keys(OV_META).filter(k => !/class="ic-more ic-report"/.test(indicatorCardHTML(k))).length };
    });
    ok(!!icRep && icRep.text === 'Report a problem with this figure' && icRep.cards === 0 &&
       icRep.href.indexOf('mailto:contact@pophealth.uk?subject=Problem with a figure: General fertility rate&body=') === 0 &&
       /Indicator: General fertility rate\r\nIndicator key: ft_93089\r\nLevel: (MSOA|Ward)\r\nArea: .+\r\nLink to this view: http[^\r]*ov=ft_93089/.test(icRep.href) &&
       /What looks wrong \(please describe it here\):/.test(icRep.href),
      'every indicator card has "Report a problem with this figure", a pre-filled email naming the indicator, its key, level, area and link',
      JSON.stringify(icRep));
    await setOv(page, 'none');
    await page.waitForTimeout(800);
    await checkAxe(page, 'LSOA profile');
    // The screenshot dialog and the level toast are built on the Data
    // credits dialog's frame and the page's own tokens (workplan 2.9).
    await page.click('#tb-more');
    await page.click('#tb-png');
    await page.waitForTimeout(300);
    const dlg = await page.evaluate(() => {
      const d = document.querySelector('dialog.ed-dlg');
      return d ? { open: d.open, credits: d.classList.contains('credits-dlg'), font: getComputedStyle(d).fontFamily,
        opts: d.querySelectorAll('.ed-opt').length, name: d.querySelector('#ed-h').textContent } : null;
    });
    ok(!!dlg && dlg.open && dlg.credits && /Inter/.test(dlg.font) && dlg.opts >= 2,
      'the screenshot dialog is a native dialog on the Data credits frame', JSON.stringify(dlg));
    await checkAxe(page, 'screenshot dialog');
    await page.keyboard.press('Escape');
    await page.waitForTimeout(200);
    ok(await page.evaluate(() => !document.querySelector('dialog.ed-dlg') && !_screenshotState), 'Escape closes the screenshot dialog');
    ok(await page.evaluate(() => { _levelToast('<b>Test</b>'); const t = document.getElementById('_level-toast');
      return t.classList.contains('toast') && t.getAttribute('role') === 'status' && !t.getAttribute('style'); }),
      'the level toast is the page\'s own card, and is read out');
    // A blocked report window gets a note beside the button, with a link that
    // opens the report in this tab when the reader chooses to.
    await page.evaluate(() => showWard('Lansbury'));
    await page.waitForTimeout(800);
    await page.evaluate(() => { window.open = () => null; });
    await page.click('#ws-pdf');
    await page.waitForTimeout(400);
    const blocked = await page.evaluate(() => { const n = document.querySelector('#ward-sheet > .ph-note'); return n ? n.textContent : ''; });
    ok(/Your browser blocked the report window\. Open the report in this tab instead\./.test(blocked), 'blocked report offers to open in this tab', blocked);
    ok(errors.length === 0, 'no page errors (London desktop)', errors.join(' | '));
    if (blocked) {
      await Promise.all([page.waitForNavigation({ timeout: 30000 }).catch(() => {}), page.click('#ward-sheet > .ph-note .ph-note-open')]);
      await page.waitForTimeout(1000);
      const rep = await page.evaluate(() => ({ title: document.title, back: [...document.querySelectorAll('button')].some(b => b.textContent === 'Back to the map') })).catch(e => ({ title: e.message }));
      ok(/Ward report/.test(rep.title) && rep.back, 'the report opens in this tab with a way back to the map', JSON.stringify(rep));
    }
    await page.close();
  }

  // Search, the header and getting around (workplan 2.11).
  {
    const { page, errors } = await open(browser, 'index.html?t=1', DESK);
    const DASH = /[–—]/;
    // The header: Copy link in view, Presentation view and the palette
    // behind More, the panel button saying what it will do in step with
    // aria-expanded, and the wider search box.
    const hdr = await page.evaluate(() => {
      const vis = id => { const e = document.getElementById(id); return !!(e && e.offsetParent); };
      const m = document.getElementById('hdr-menu');
      const s = document.getElementById('hdr-search-input');
      return { link: vis('tb-link'), more: vis('tb-more'), png: vis('tb-png'), cb: vis('tb-cb'),
        name: m.getAttribute('aria-label'), exp: m.getAttribute('aria-expanded'), text: m.innerText.trim(),
        width: s.offsetWidth, ph: s.placeholder };
    });
    ok(hdr.link && hdr.more && !hdr.png && !hdr.cb, 'Copy link is in the header, Presentation view and the palette are behind More', JSON.stringify(hdr));
    ok(hdr.name === 'Hide panel' && hdr.exp === 'true' && hdr.text === 'Hide panel', 'the panel button says Hide panel while the panel shows', JSON.stringify(hdr));
    ok(hdr.width >= 270 && hdr.ph === 'Postcode, ward, GP or charity', 'the header search is wide, with a plain placeholder', JSON.stringify(hdr));
    await page.click('#hdr-menu');
    await page.waitForTimeout(400);
    const hid = await page.evaluate(() => { const m = document.getElementById('hdr-menu');
      return [m.getAttribute('aria-label'), m.getAttribute('aria-expanded'), m.innerText.trim(), document.getElementById('sidebar').classList.contains('collapsed')].join('|'); });
    await page.click('#hdr-menu');
    await page.waitForTimeout(400);
    ok(hid === 'Show panel|false|Show panel|true', 'hiding the panel turns the button into Show panel, with aria-expanded false', hid);
    await page.click('#tb-more');
    await page.waitForTimeout(200);
    const more = await page.evaluate(() => ({ open: !document.getElementById('tb-more-menu').hidden,
      exp: document.getElementById('tb-more').getAttribute('aria-expanded'), png: !!document.getElementById('tb-png').offsetParent }));
    await checkAxe(page, 'More open');
    await page.keyboard.press('Escape');
    const moreShut = await page.evaluate(() => document.getElementById('tb-more-menu').hidden &&
      document.getElementById('tb-more').getAttribute('aria-expanded') === 'false' && document.activeElement.id === 'tb-more');
    ok(more.open && more.exp === 'true' && more.png && moreShut, 'More opens, and Esc closes it back to its button', JSON.stringify(more));

    // Typing an LA offers it first, and choosing it focuses it, fits it
    // tightly, names it in the tab title and takes the lists with it.
    await page.click('#hdr-search-input');
    await page.waitForFunction(() => typeof LSOA_IMD !== 'undefined' && LSOA_IMD && LSOA_IMD.features, null, { timeout: 20000 }).catch(() => {});
    await page.fill('#hdr-search-input', 'Tower Hamlets');
    await page.waitForTimeout(600);
    const sr = await page.evaluate(() => {
      const rows = [...document.querySelectorAll('#hdr-search-results .sr-row')];
      const t = r => (r.querySelector('.sr-type') || {}).textContent || '';
      return { first: rows.length ? t(rows[0]) + ':' + rows[0].querySelector('.sr-name').textContent : '',
        lsoas: rows.filter(r => t(r) === 'lsoa').length, more: (document.querySelector('#hdr-search-results .sr-more') || {}).textContent || '',
        wardsBeforeLsoas: rows.findIndex(r => t(r) === 'ward') < rows.findIndex(r => t(r) === 'lsoa') };
    });
    ok(sr.first === 'LA:Tower Hamlets', 'searching an LA offers the LA first', JSON.stringify(sr));
    ok(sr.lsoas === 5 && /^Show all \d+ LSOAs$/.test(sr.more) && sr.wardsBeforeLsoas, 'at most five LSOAs, below the wards, with Show all', JSON.stringify(sr));
    await checkAxe(page, 'search results');
    await page.click('#hdr-search-results .sr-more');
    await page.waitForTimeout(400);
    const all = await page.evaluate(() => ({ shown: document.getElementById('hdr-search-results').classList.contains('show'),
      lsoas: [...document.querySelectorAll('#hdr-search-results .sr-row .sr-type')].filter(e => e.textContent === 'lsoa').length }));
    ok(all.shown && all.lsoas > 5, 'Show all lists every LSOA and keeps the list open', JSON.stringify(all));
    await page.fill('#hdr-search-input', 'Tower Hamlets');
    await page.waitForTimeout(500);
    await page.keyboard.press('Enter');
    await page.waitForTimeout(2500);
    const th = await page.evaluate(() => {
      const m = wLyr.getLayers().filter(l => ladInFocus(l.feature.properties.LAD));
      const bb = L.featureGroup(m).getBounds();
      const a = map.latLngToContainerPoint(bb.getSouthWest()), b = map.latLngToContainerPoint(bb.getNorthEast());
      const sz = map.getSize();
      return { focus: focusLabel(), title: document.title, fill: Math.max((b.x - a.x) / sz.x, (a.y - b.y) / sz.y) };
    });
    ok(th.focus === 'Tower Hamlets' && th.title === 'Tower Hamlets | PopHealth Map', 'choosing the LA focuses it and names it in the tab title', JSON.stringify(th));
    ok(th.fill > 0.75, 'Focus by LA fits the LA tightly', JSON.stringify(th));
    await setOv(page, 'imd_score_ward');
    await page.waitForTimeout(2000);
    const rank = await page.evaluate(() => ({ title: document.getElementById('rank-title').textContent, unit: document.getElementById('rank-unit').textContent,
      out: [...document.querySelectorAll('#rank-best .rank-row, #rank-worst .rank-row')].filter(r => !wardInFocus(r.dataset.id)).length }));
    ok(rank.title === 'Highest and lowest 10 wards in Tower Hamlets' && /^out of \d+$/.test(rank.unit) && rank.out === 0,
      'the highest and lowest lists follow the focus and say so', JSON.stringify(rank));

    // A ward names itself and its LA in the tab title, and the Ask button has
    // its own space above the key and the map credits.
    await page.evaluate(() => { const s = document.getElementById('borough-focus'); s.value = 'all'; s.dispatchEvent(new Event('change')); });
    await page.waitForTimeout(800);
    await page.evaluate(() => { const l = wLyr.getLayers().find(l => l.feature.properties.WD24NM === 'Stonebridge'); if (l) l.fire('click'); });
    await page.waitForTimeout(1500);
    const ward = await page.evaluate(() => {
      const r = e => e.getBoundingClientRect();
      const ask = r(document.getElementById('ai-toggle')), strip = r(document.getElementById('map-legend-strip')),
        cred = r(document.querySelector('.leaflet-control-attribution'));
      const hit = (a, b) => a.width && b.width && a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
      return { title: document.title, ask: !!document.getElementById('ai-toggle').offsetParent,
        clear: !hit(ask, strip) && !hit(ask, cred) && ask.bottom <= Math.min(strip.top || 1e9, cred.top) };
    });
    ok(ward.title === 'Stonebridge, Brent | PopHealth Map', 'the tab title names the selected ward and its LA', ward.title);
    // An LSOA picked from the search, with an indicator that has no LSOA
    // figures, is outlined on its own; focusing a different LA afterwards
    // lets go of it, so the profile and the title follow the map.
    await setOv(page, 'crime_rate_per_1000');
    await page.waitForTimeout(1500);
    await page.evaluate(() => _searchPickResult({ type: 'lsoa', _code: 'E01000907', label: 'Camden 001A', sub: '' }));
    await page.waitForFunction(() => typeof selLSOA !== 'undefined' && selLSOA === 'E01000907', null, { timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(800);
    const pick = await page.evaluate(() => ({ sel: selLSOA, outline: !!(_searchLsoaOutline && map.hasLayer(_searchLsoaOutline)) }));
    ok(pick.sel === 'E01000907' && pick.outline, 'an LSOA picked from the search is outlined when the level stays at ward', JSON.stringify(pick));
    await page.evaluate(() => _searchPickResult({ type: 'LA', label: 'Tower Hamlets', _key: 'Tower Hamlets' }));
    await page.waitForTimeout(1200);
    const foc = await page.evaluate(() => ({ sel: selLSOA, title: document.title, outline: !!_searchLsoaOutline }));
    ok(foc.sel === null && foc.title === 'Tower Hamlets | PopHealth Map' && !foc.outline,
      'focusing another LA lets go of an LSOA outside it', JSON.stringify(foc));
    await page.evaluate(() => { const b = document.getElementById('borough-focus'); b.value = 'all'; b.dispatchEvent(new Event('change')); });
    await page.waitForTimeout(800);
    ok(!ward.ask || ward.clear, 'the Ask button sits above the key and the map credits', JSON.stringify(ward));

    // Copy link writes the level whenever it is not the one the indicator
    // opens at, and Reset puts the layers back as the map opens.
    await setOv(page, 'ft_93283');
    await page.waitForTimeout(2500);
    await page.click('#dl-seg button[data-dl="ward"]');
    await page.waitForTimeout(800);
    const lvl = await page.evaluate(() => { _writeUrlState(); return new URLSearchParams(location.search).get('lvl'); });
    await page.click('#dl-seg button[data-dl="msoa"]');
    await page.waitForTimeout(1500);
    const lvl2 = await page.evaluate(() => { _writeUrlState(); return new URLSearchParams(location.search).get('lvl'); });
    ok(lvl === 'ward' && lvl2 === null, 'Copy link writes the level only when it differs from where the indicator opens', lvl + ' / ' + lvl2);
    // With an indicator drawn, no text people can see or hear says "overlay"
    // or "choropleth" (workplan 2.1), and the MSOA note under the scale says
    // "select" rather than "hover" or "click" (2.2).
    const ovw = await page.evaluate(() => {
      const t = [document.body.innerText, document.getElementById('sr-live') ? document.getElementById('sr-live').textContent : '']
        .concat([...document.querySelectorAll('[title], [aria-label]')].map(e => (e.getAttribute('title') || '') + ' ' + (e.getAttribute('aria-label') || '')))
        .concat([...document.querySelectorAll('[aria-live]')].map(e => e.textContent)).join(' ');
      const meta = document.getElementById('sc-meta');
      return { hits: (t.match(/[^.]{0,40}\b(overlay|choropleth)[^.]{0,40}/gi) || []).slice(0, 4), note: meta ? meta.textContent : '' };
    });
    ok(!ovw.hits.length, 'no visible or announced text says "overlay" or "choropleth"', ovw.hits.join(' | '));
    ok(/Select an MSOA/.test(ovw.note) && !/\b(hover|click)/i.test(ovw.note), 'the MSOA note under the scale works on any device', ovw.note.slice(0, 160));
    await page.evaluate(() => ['tg', 'tph', 'ticb', 'tb'].forEach(id => { const e = document.getElementById(id); if (e) { e.checked = true; e.dispatchEvent(new Event('change')); } }));
    await page.waitForTimeout(1500);
    await page.click('#tb-reset');
    await page.waitForTimeout(1500);
    const reset = await page.evaluate(() => ({ on: ['tg', 'th', 'tgr', 'td', 'tph', 'vcse-tab-toggle', 'ticb', 'tb', 'tpcn'].filter(id => { const e = document.getElementById(id); return e && e.checked; }),
      wards: document.getElementById('tnw').checked, title: document.title, gp: !!(typeof gpL !== 'undefined' && gpL && map.hasLayer(gpL)) }));
    ok(!reset.on.length && reset.wards && !reset.gp && !/\|/.test(reset.title), 'Reset clears the point and boundary layers and the selection', JSON.stringify(reset));

    // Indicator search: everyday words, "1 match", and a council-only
    // indicator says where its figures are instead of finding nothing.
    await page.click('#sbtab-overlay');
    const ind = {};
    // Suicide rather than smoking: adult smoking is left out until a refresh
    // rebuilds it at its headline age (FT_WITHHELD).
    for (const q of ['suicide', 'pollution', 'arson', 'flu', 'no2', 'zzqx']) {
      await page.fill('#ov-search', q);
      await page.waitForTimeout(400);
      ind[q] = await page.evaluate(() => document.getElementById('ov-results').innerText.replace(/\s+/g, ' ').trim());
    }
    await page.fill('#ov-search', '');
    ok(!/No indicator matches/.test(ind.suicide) && /Suicide rate is only published for whole LAs/.test(ind.suicide) && /full profile/.test(ind.suicide) && /plan to add it to the LA map/.test(ind.suicide),
      'a council-only indicator says where its figures are', ind.suicide);
    ok(/Nitrogen dioxide/.test(ind.pollution), 'indicator search knows everyday words', ind.pollution.slice(0, 120));
    ok(/^1 match\b/.test(ind.arson), 'one result reads "1 match"', ind.arson.slice(0, 60));
    ok(/only published for whole LAs/.test(ind.flu) && !/\bmatch(es)?\b/.test(ind.flu.replace(/No indicator matches/, '')),
      '"flu" finds the council-only note and no word inside "fluent"', ind.flu.slice(0, 160));
    ok(/Nitrogen dioxide/.test(ind.no2), '"no2" still finds nitrogen dioxide', ind.no2.slice(0, 120));
    ok(/clear the search to browse/.test(ind.zzqx), 'a search with no matches says what to do next', ind.zzqx);
    const words = await page.evaluate(() => [...document.querySelectorAll('.hdr [title], .hdr [aria-label], .hdr [placeholder], #rank-panel')]
      .map(e => [e.getAttribute('title'), e.getAttribute('aria-label'), e.getAttribute('placeholder'), e.id === 'rank-panel' ? e.textContent : ''].join(' ')).join(' '));
    ok(!DASH.test(words + Object.values(ind).join(' ') + th.title + ward.title), 'the new header, list and search words have no dashes');
    ok(errors.length === 0, 'no page errors (search and header)', errors.join(' | '));
    await page.close();
  }
  // Nothing in the header overlaps at the widths people use.
  for (const w of [1200, 1360, 1400]) {
    const { isMobile, hasTouch, ...vp } = DESK;
    const page = await browser.newPage({ viewport: { ...vp, width: w } });
    await page.goto(BASE + '/index.html?t=1');
    await page.waitForTimeout(2500);
    const r = await page.evaluate(() => {
      const els = [...document.querySelectorAll('.hdr button, .hdr input, .hdr h1, .hdr .dev-badge, .hdr .hdr-tag')].filter(e => e.offsetParent);
      const over = [];
      for (let i = 0; i < els.length; i++) for (let j = i + 1; j < els.length; j++) {
        if (els[i].contains(els[j]) || els[j].contains(els[i])) continue;
        const a = els[i].getBoundingClientRect(), b = els[j].getBoundingClientRect();
        if (a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1) over.push((els[i].id || els[i].className) + ' x ' + (els[j].id || els[j].className));
      }
      const tag = document.querySelector('.hdr .hdr-tag');
      return { over, wide: document.documentElement.scrollWidth > window.innerWidth, tall: document.querySelector('.hdr').offsetHeight > 60, tag: !!(tag && tag.offsetParent) };
    });
    ok(!r.over.length && !r.wide && !r.tall, 'nothing in the header overlaps at ' + w + 'px', JSON.stringify(r));
    ok(r.tag === (w >= 1360), 'the header tagline is ' + (w >= 1360 ? 'shown' : 'hidden') + ' at ' + w + 'px');
    await page.close();
  }

  // The site mark (workplan 1.2): four rounded squares in four blues, about
  // 20px beside the name in the app header, the pages' header and the tab,
  // and the same drawing in every icon. It adds nothing to the header's
  // height or the heading's name.
  {
    const favMark = html => {
      const m = html.match(/<link rel="icon" type="image\/svg\+xml" href="data:image\/svg\+xml,([^"]+)">/);
      return m ? decodeURIComponent(m[1]).replace(/'/g, '"') : '';
    };
    const sizes = [];
    // Each square's place, corner and colour, so two drawings can be compared.
    let appRects = '';
    for (const [w, h, phone, maxH] of [[1200, 800, false, 47], [1400, 900, false, 47], [390, 844, true, 43]]) {
      const page = await browser.newPage({ viewport: { width: w, height: h }, isMobile: phone, hasTouch: phone });
      await page.goto(BASE + '/index.html?t=1');
      await page.waitForTimeout(2500);
      const r = await page.evaluate(() => {
        const svg = document.querySelector('.hdr h1.hdr-logo > svg.hdr-mark');
        const h1 = document.querySelector('.hdr h1');
        const rect = e => { const b = e.getBoundingClientRect(); return { l: b.left, t: b.top, r: b.right, b: b.bottom, w: b.width, h: b.height }; };
        const others = [...document.querySelectorAll('.hdr button, .hdr input, .hdr .hdr-tag')].filter(e => e.offsetParent).map(rect);
        const s = svg && rect(svg), n = h1 && rect(h1);
        return {
          svg: s, hidden: svg && svg.getAttribute('aria-hidden'), rects: svg ? svg.querySelectorAll('rect').length : 0,
          fills: svg ? [...svg.querySelectorAll('rect')].map(r => r.getAttribute('fill')) : [],
          mark: svg ? [...svg.querySelectorAll('rect')].map(r => [r.getAttribute('x') || 0, r.getAttribute('y') || 0, r.getAttribute('rx'), r.getAttribute('fill')].join(':')).join(' ') : '',
          name: h1 ? h1.textContent.trim() : '',
          centred: s && n ? Math.abs((s.t + s.b) / 2 - (n.t + n.b) / 2) : 99,
          hit: s ? others.filter(o => s.l < o.r && s.r > o.l && s.t < o.b && s.b > o.t).length : 0,
          hdr: document.querySelector('.hdr').offsetHeight,
        };
      });
      ok(r.svg && Math.round(r.svg.w) === 20 && Math.round(r.svg.h) === 20 && r.hidden === 'true' && r.rects === 4 && r.centred <= 1 && !r.hit,
        'the site mark sits 20px beside the name in the app header at ' + w + 'px', JSON.stringify(r).slice(0, 300));
      ok(new Set(r.fills).size === 4 && r.fills.every(f => /^#[0-9A-F]{6}$/i.test(f)), 'the site mark is four shades of blue', r.fills.join(' '));
      ok(r.hdr <= maxH, 'the mark leaves the app header ' + maxH + 'px or less at ' + w + 'px', r.hdr + 'px');
      ok(/^PopHealth Map\s*Beta$/.test(r.name), 'the header heading still reads PopHealth Map with the Beta badge at ' + w + 'px', r.name);
      appRects = r.mark;
      await page.close();
    }
    for (const p of ['index.html', 'methodology.html', 'accessibility.html', 'privacy.html', '404.html']) {
      const html = await (await fetch(BASE + '/' + p)).text();
      const page = await browser.newPage();
      await page.setContent('<body>' + favMark(html) + '<div id="b">' + ((html.match(/<a class="brand"[^>]*>(<svg[\s\S]*?<\/svg>)/) || [])[1] || '') + '</div></body>');
      const [fav, brand] = await page.evaluate(() => {
        const m = s => s ? [...s.querySelectorAll('rect')].map(r => [r.getAttribute('x') || 0, r.getAttribute('y') || 0, r.getAttribute('rx'), r.getAttribute('fill')].join(':')).join(' ') : '';
        return [m(document.querySelector('body > svg')), m(document.querySelector('#b svg'))];
      });
      await page.close();
      ok(fav && fav === appRects, p + ': the tab icon is the header mark', fav + ' / ' + appRects);
      if (p !== 'index.html') ok(brand === appRects, p + ': the page header shows the same mark', brand);
      ok(/<link rel="icon" type="image\/png" sizes="32x32" href="(https:\/\/pophealth\.uk\/)?favicon-32\.png">/.test(html) &&
         /<link rel="apple-touch-icon" href="(https:\/\/pophealth\.uk\/)?apple-touch-icon\.png">/.test(html), p + ': PNG favicon and touch icon linked');
    }
    // The printable reports carry the same mark in their masthead: a ward
    // report as opened from the sheet, and the drawing the LSOA and Custom
    // area reports share with it.
    {
      const { page } = await open(browser, 'index.html?t=1', DESK);
      const marks = s => s ? [...s.querySelectorAll('rect')].map(r => [r.getAttribute('x') || 0, r.getAttribute('y') || 0, r.getAttribute('rx'), r.getAttribute('fill')].join(':')).join(' ') : '';
      const shared = await page.evaluate(m => {
        const d = document.createElement('div'); d.innerHTML = _reportMasthead('LSOA report');
        const svg = d.querySelector('.masthead svg');
        return { rects: new Function('s', 'return (' + m + ')(s)')(svg), w: svg && svg.getAttribute('width'), vb: svg && svg.getAttribute('viewBox') };
      }, marks.toString());
      ok(shared.rects === appRects && shared.vb === '0 0 20 20', 'reports: the masthead mark is the header mark, scaled', JSON.stringify(shared) + ' / ' + appRects);
      await page.evaluate(() => showWard('Lansbury'));
      await page.waitForTimeout(800);
      const [rep] = await Promise.all([page.waitForEvent('popup', { timeout: 20000 }).catch(() => null), page.click('#ws-pdf')]);
      if (rep) {
        await rep.waitForFunction(() => !!document.querySelector('.masthead svg'), null, { timeout: 30000 }).catch(() => {});
        const got = await rep.evaluate(m => new Function('s', 'return (' + m + ')(s)')(document.querySelector('.masthead svg')), marks.toString());
        ok(got === appRects, 'reports: a generated ward report shows the header mark in its masthead', got);
        await rep.close();
      } else ok(false, 'reports: the ward report opens to check its mark');
      await page.close();
    }
    // The PNGs exist at the sizes they claim, and the manifest names them.
    const pngSize = f => { const b = fs.readFileSync(path.join(ROOT, f)); return b.slice(1, 4).toString() === 'PNG' ? [b.readUInt32BE(16), b.readUInt32BE(20)] : null; };
    for (const [f, s] of [['favicon-32.png', 32], ['apple-touch-icon.png', 180], ['icon-192.png', 192], ['icon-512.png', 512], ['icon-maskable-512.png', 512]]) {
      const d = fs.existsSync(path.join(ROOT, f)) && pngSize(f);
      ok(d && d[0] === s && d[1] === s, f + ' is a ' + s + 'px PNG', JSON.stringify(d));
    }
    const man = JSON.parse(fs.readFileSync(path.join(ROOT, 'site.webmanifest'), 'utf8'));
    const icons = man.icons || [];
    ok(man.id === '/' && man.lang === 'en-GB' && man.start_url === '/', 'manifest: id, lang en-GB and start address', JSON.stringify({ id: man.id, lang: man.lang }));
    ok(icons.some(i => i.purpose === 'maskable' && i.src === 'icon-maskable-512.png' && i.sizes === '512x512') &&
       icons.some(i => (i.purpose || 'any') === 'any' && i.sizes === '512x512') && icons.some(i => i.sizes === '192x192'),
       'manifest: 192 and 512 icons, and a maskable 512', JSON.stringify(icons));
    ok(icons.every(i => { const d = pngSize(i.src); return d && i.sizes === d[0] + 'x' + d[1]; }), 'manifest: every icon is the size it says');
    // The link preview: 1200x630, as the tags say, and described in words.
    const og = pngSize('og.png');
    ok(og && og[0] === 1200 && og[1] === 630, 'og.png is 1200 by 630', JSON.stringify(og));
    for (const p of ['index.html', 'methodology.html', 'accessibility.html', 'privacy.html']) {
      const html = await (await fetch(BASE + '/' + p)).text();
      const meta = k => (html.match(new RegExp('<meta (?:property|name)="' + k.replace(/[:.]/g, '\\$&') + '" content="([^"]*)"')) || [])[1];
      ok(meta('og:image') === 'https://pophealth.uk/og.png' && meta('og:image:width') === '1200' && meta('og:image:height') === '630' &&
         meta('twitter:image') === 'https://pophealth.uk/og.png', p + ': preview image tags match og.png');
      const alt = meta('og:image:alt') || '';
      ok(/map of London wards/i.test(alt) && alt === meta('twitter:image:alt') && !/[–—]/.test(alt),
        p + ': the preview image is described the same way for both cards', alt);
    }
  }

  // Data export (workplan 2.7): headings with label, unit and period, figures
  // to each indicator's own decimal places, a notes file to cite, and a
  // preview built from the rows inside the panel.
  {
    const { page, errors } = await open(browser, 'index.html?t=1', DESK);
    await page.click('.mode-btn[data-mode="csv"]');
    await page.waitForTimeout(600);
    const empty = await page.evaluate(() => ({
      off: ['cb-download', 'cb-notes', 'cb-preview'].every(id => document.getElementById(id).disabled),
      count: document.getElementById('cb-count').textContent,
    }));
    ok(empty.off && /Choose at least one indicator/.test(empty.count),
      'export: the buttons wait for an indicator and the bar says why', JSON.stringify(empty));
    // Step 1 stays open, because more than one size can be chosen. Answering
    // step 2 folds it to a line, and Change opens it again.
    await page.selectOption('#cb-scope', 'Tower Hamlets');
    await page.waitForTimeout(300);
    const fold = await page.evaluate(() => [1, 2].map(n => {
      const st = document.getElementById('cb-step-' + n);
      return st.classList.contains('folded') && !st.querySelector('.cb-chg').hidden &&
        getComputedStyle(st.querySelector('.cb-step-body')).display === 'none' ? st.querySelector('h3').textContent : '';
    }));
    ok(fold[0] === '' && /Tower Hamlets/.test(fold[1]), 'export: step 2 folds once answered and step 1 stays open', JSON.stringify(fold));
    await page.click('#cb-step-2 .cb-chg');
    ok(await page.evaluate(() => !document.getElementById('cb-step-2').classList.contains('folded') &&
      document.activeElement === document.getElementById('cb-scope')), 'export: Change opens the step again');
    // The last size of area cannot be switched off.
    await page.click('#cb-level button[data-lvl="ward"]');
    ok(await page.evaluate(() => PH_CSV.levels().join() === 'ward' &&
      document.querySelector('#cb-level button[data-lvl="ward"]').getAttribute('aria-pressed') === 'true'),
      'export: the last size of area stays chosen');
    const ex = await page.evaluate(() => {
      const before = PH_CSV.columns().length;
      PH_CSV.pick('ft_93283');
      const one = before === 0 ? PH_CSV.notes() : '';
      PH_CSV.pick('ft_suicide_rate'); PH_CSV.pick('crime_total');
      const o = PH_CSV.build();
      const col = i => o.table.slice(1).map(r => r[i]).filter(v => v !== '');
      const le = o.header.indexOf('Life expectancy at birth, women (years, 2019 to 2023)');
      const groupOf = k => { const b = document.querySelector('#cb-cols input[data-col="' + k + '"]');
        return b ? b.closest('.cb-grp').querySelector('.cb-grp-hd').getAttribute('data-grp') : ''; };
      const sorted = [...document.querySelectorAll('#cb-cols .cb-grp-body')].every(g => {
        const l = [...g.querySelectorAll('.l')].map(e => e.textContent);
        return l.every((t, i) => !i || l[i - 1].localeCompare(t, 'en-GB', { numeric: true, sensitivity: 'base' }) <= 0);
      });
      return {
        header: o.header, le, leVals: le === -1 ? [] : col(le),
        smoke: o.header.find(h => /^Suicide rate/.test(h)) || '',
        crime: o.header.find(h => /^Total recorded crime/.test(h)) || '',
        crimeVals: col(o.header.findIndex(h => /^Total recorded crime/.test(h))),
        ptal: groupOf('ptal_score'), no2: groupOf('no2_ugm3'), sorted,
        shared: [fmtOv(81.0723, 'ft_93283'), PH_IND.decimals('ft_93283'), PH_IND.decimals('crime_total')],
        notes: PH_CSV.notes(), one, before,
        dashes: /[\u2013\u2014]/.test(o.header.join(' ') + PH_CSV.notes()),
      };
    });
    ok(ex.le !== -1 && ex.leVals.length > 10 && ex.leVals.every(v => /^\d+\.\d$/.test(v)),
      'export: life expectancy is headed "Life expectancy at birth, women (years, 2019 to 2023)" with one decimal place',
      ex.header.join(' | ') + ' / ' + ex.leVals.slice(0, 3).join(', '));
    ok(/\(LA figure\)$/.test(ex.smoke) && /\(\w+ \d{4} to \w+ \d{4}\)$/.test(ex.crime) && ex.crimeVals.every(v => /^\d+$/.test(v)),
      'export: an LA-wide column says "(LA figure)", and counts are whole numbers', ex.smoke + ' / ' + ex.crime);
    ok(ex.shared[0] === '81.1' && ex.shared[1] === 1 && ex.shared[2] === 0,
      'export: decimal places are shared with the hover card', JSON.stringify(ex.shared));
    ok(ex.ptal === 'Transport' && ex.no2 === 'Environment and access' && ex.sorted,
      'export: PTAL under Transport, air quality under Environment, each group in alphabetical order', ex.ptal + ' / ' + ex.no2);
    ok(/How to cite it/.test(ex.notes) && /PopHealth Map \(\d{4}\) Data export: ward figures for Tower Hamlets/.test(ex.notes) &&
       (ex.notes.match(/\n   Source: /g) || []).length === 3 && (ex.notes.match(/\n   Geography: /g) || []).length === 3 &&
       ex.notes.indexOf('Life expectancy at birth, women (years, 2019 to 2023)') !== -1 && !ex.dashes,
      'export: the notes give a citation and the source and geography of each column, with no dashes', ex.notes.slice(0, 200));
    ok(ex.before !== 0 || (/one column for the indicator you chose/.test(ex.one) && !/each of the one/.test(ex.one)),
      'export: the notes read naturally with one indicator chosen', (ex.one.match(/and (one|a) column[^.]*\./) || [''])[0]);
    ok(/\. Source\n/.test(ex.notes), 'export: the notes name the Source column in sentence case');
    // The preview is built from the row arrays, inside the panel.
    await page.click('#cb-preview');
    await page.waitForTimeout(300);
    const pv = await page.evaluate(() => {
      const o = PH_CSV.build();
      const th = [...document.querySelectorAll('#cb-prev thead th')].map(e => e.textContent);
      const rows = [...document.querySelectorAll('#cb-prev tbody tr')].map(r => [...r.children].map(c => c.textContent));
      return { shown: !document.getElementById('cb-prev').hidden && document.getElementById('cb-scroll').hidden,
        same: JSON.stringify(th) === JSON.stringify(o.header) && rows.length === Math.min(10, o.rows) &&
          rows.every((r, i) => JSON.stringify(r) === JSON.stringify(o.table[i + 1])),
        expanded: document.getElementById('cb-preview').getAttribute('aria-expanded') };
    });
    ok(pv.shown && pv.same && pv.expanded === 'true', 'export: the preview shows inside the panel and its columns line up with the file', JSON.stringify(pv));
    await checkAxe(page, 'Data export with its preview');
    await page.click('#cb-preview');
    // The download bar stays at the foot of the panel while the list scrolls.
    const bar = await page.evaluate(() => {
      // Each click repaints the list, so open the groups one at a time.
      for (let h, n = 0; n < 30 && (h = document.querySelector('#cb-cols .cb-grp-hd:not(.open)')); n++) h.click();
      const sc = document.getElementById('cb-scroll'); sc.scrollTop = sc.scrollHeight;
      const a = document.querySelector('#tab-csv .cb-acts').getBoundingClientRect(), p = document.getElementById('tab-csv').getBoundingClientRect();
      return { scrolls: sc.scrollHeight > sc.clientHeight, pinned: Math.abs(a.bottom - p.bottom) < 2 && a.top > 0 && !sc.contains(document.getElementById('cb-download')) };
    });
    ok(bar.scrolls && bar.pinned, 'export: the download bar is pinned below the scrolling list', JSON.stringify(bar));
    await checkAxe(page, 'Data export with every group open');
    // Quicker ways to choose: search, a box for the whole group, and ready-made sets.
    await page.click('#cb-clear');
    await page.fill('#cb-find', 'pollution');
    await page.waitForTimeout(300);
    const find = await page.evaluate(() => ({
      shown: [...document.querySelectorAll('#cb-cols .cb-col .l')].map(e => e.textContent),
      all: document.getElementById('cb-q-all').textContent,
    }));
    await page.click('#cb-q-all');
    const fromFind = await page.evaluate(() => PH_CSV.columns().sort().join());
    ok(find.shown.length >= 2 && find.shown.every(t => /PM|NO|nitrogen|particulate|air/i.test(t)) &&
       /^Choose all/.test(find.all) && fromFind.split(',').length === find.shown.length,
      'export: searching narrows the list and one button chooses every match', JSON.stringify(find));
    await page.fill('#cb-find', '');
    await page.click('#cb-clear');
    await page.click('#cb-cols input[data-grpall="Health"]');
    const grp = await page.evaluate(() => {
      const box = document.querySelector('#cb-cols input[data-grpall="Health"]');
      const g = box.closest('.cb-grp');
      return { n: PH_CSV.columns().length, checked: box.checked, label: box.getAttribute('aria-label') || '',
        every: [...g.querySelectorAll('input[data-col]')].every(b => b.checked) };
    });
    await page.click('#cb-cols .cb-grp:has(input[data-grpall="Health"]) input[data-col]');
    const part = await page.evaluate(() => document.querySelector('#cb-cols input[data-grpall="Health"]').indeterminate);
    ok(grp.n > 5 && grp.checked && grp.every && /Health/.test(grp.label) && part,
      'export: the box beside a group chooses all of it, and shows when only part is chosen', JSON.stringify(grp));
    await page.click('#cb-clear');
    await page.click('#cb-q-key');
    const key = await page.evaluate(() => PH_CSV.columns().length);
    ok(key >= 8, 'export: Key indicators chooses a ready-made set', String(key));
    // More than one size of area makes a ZIP with a CSV and notes for each.
    await page.click('#cb-level button[data-lvl="borough"]');
    await page.click('#cb-level button[data-lvl="lsoa"]');
    await page.waitForFunction(() => PH_CSV.levels().length === 3 && !/Loading/i.test(document.getElementById('cb-count').textContent),
      null, { timeout: 30000 }).catch(() => {});
    const multi = await page.evaluate(() => {
      const files = PH_CSV.files();
      return { levels: PH_CSV.levels().join(), count: document.getElementById('cb-count').textContent,
        btn: document.getElementById('cb-download').textContent, notes: document.getElementById('cb-notes').hidden,
        pressed: [...document.querySelectorAll('#cb-level button[aria-pressed="true"]')].length,
        names: files.map(f => f.name), csvs: files.filter(f => /\.csv$/.test(f.name)).map(f => f.text.split('\n').length) };
    });
    const zip = await page.evaluate(async () => {
      const b = new Uint8Array(await PH_CSV.zip().arrayBuffer());
      const u32 = i => (b[i] | b[i + 1] << 8 | b[i + 2] << 16 | b[i + 3] << 24) >>> 0;
      const end = b.length - 22;
      return { start: u32(0) === 0x04034b50, end: u32(end) === 0x06054b50, files: b[end + 10] | b[end + 11] << 8 };
    });
    ok(multi.pressed === 3 && /ward,lsoa,borough|ward,borough,lsoa/.test(multi.levels) && /three files/.test(multi.count) &&
       /ZIP/.test(multi.btn) && multi.notes && multi.names.length === 6 && multi.csvs.every(n => n > 2) &&
       multi.names.some(n => /^pophealth-lsoa/.test(n)) && multi.names.some(n => /-notes\.txt$/.test(n)),
      'export: three sizes of area make three files, downloaded together as a ZIP with their notes', JSON.stringify(multi));
    ok(zip.start && zip.end && zip.files === 6, 'export: the ZIP holds the six files', JSON.stringify(zip));
    await page.click('#cb-preview');
    await page.waitForTimeout(300);
    ok(await page.evaluate(() => document.querySelectorAll('#cb-prev table').length === 3), 'export: the preview shows a table for each size of area');
    await checkAxe(page, 'Data export with three sizes of area');
    await page.click('#cb-preview');
    ok(errors.length === 0, 'no page errors (Data export)', errors.join(' | '));
    await page.close();
  }

  // Charities, the Directory and the PCN list (workplan 2.12). postcodes.io is
  // answered locally, so the "Near E1 6AN" list does not depend on the network.
  {
    const PC = { postcode: 'E1 6AN', latitude: 51.520014, longitude: -0.072097, admin_ward: 'Spitalfields & Banglatown',
      admin_district: 'Tower Hamlets', region: 'London', codes: { admin_ward: 'E05009333' } };
    const { isMobile, hasTouch, ...viewport } = DESK;
    const page = await browser.newPage({ viewport });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('https://api.postcodes.io/**', r => r.fulfill({ status: 200, contentType: 'application/json',
      body: JSON.stringify({ status: 200, result: PC }) }));
    await page.goto(BASE + '/index.html?t=1');
    await page.waitForTimeout(SETTLE);
    await page.click('.mode-btn[data-mode="directory"]');
    await page.waitForFunction(() => document.querySelectorAll('#dir-types [data-dtype]').length > 3, null, { timeout: 30000 }).catch(() => {});
    const first = await page.evaluate(() => ({
      prompt: !!document.querySelector('#dir-list .dir-prompt'), rows: document.querySelectorAll('#dir-list .dir-item').length,
      sub: document.getElementById('dir-sub').textContent,
      chips: [...document.querySelectorAll('#dir-types [data-dtype]')].map(c => c.getAttribute('data-dtype')),
    }));
    ok(first.prompt && first.rows === 0 && first.chips.length >= 5, 'directory: opens on a search prompt with type filters', JSON.stringify(first));
    ok(/^Search [\d,]+ GP practices, pharmacies, hospitals, dental practices and charities across London by name, address or postcode\.$/.test(first.sub),
      'directory: the heading is built from the live count and the region', first.sub);
    await checkAxe(page, 'Directory first screen');
    await page.fill('#dir-q', 'E1 6');
    await page.waitForTimeout(700);
    const e16 = await page.evaluate(() => [...document.querySelectorAll('#dir-list .dir-item .dir-sub')].map(e => e.textContent));
    ok(e16.length > 3 && !e16.some(t => /\bSE1\b/.test(t)) && e16.every(t => /\bE1 6/.test(t)),
      'directory: "E1 6" finds E1 6 postcodes and no SE1 ones', e16.slice(0, 4).join(' / '));
    await page.fill('#dir-q', 'E1 6AN');
    await page.waitForTimeout(700);
    await page.click('#dir-near-go');
    await page.waitForTimeout(1000);
    const dnear = await page.evaluate(() => ({
      head: document.getElementById('dir-count').innerText,
      d: [...document.querySelectorAll('#dir-list .dir-item .dir-sub')].slice(0, 30).map(e => e.textContent.split(' · ')[0]),
    }));
    const metres = t => /km$/.test(t) ? parseFloat(t) * 1000 : /^under/.test(t) ? 0 : parseFloat(t);
    // A charity placed only by its postcode sector reads "in E1 6" rather
    // than a distance, so the order is checked over the distances.
    const dists = l => l.filter(t => !/^in [A-Z]/.test(t));
    ok(/^Near E1 6AN, nearest first/.test(dnear.head) && dnear.d.length === 30 && dists(dnear.d).every((t, i, l) => !i || metres(t) >= metres(l[i - 1])),
      'directory: a full postcode lists the places nearest to it, nearest first', JSON.stringify(dnear).slice(0, 200));
    await checkAxe(page, 'Directory near a postcode');
    await page.click('#dir-near-x');
    await page.fill('#dir-q', '');
    await page.click('#dir-types [data-dtype="vcse"]');
    await page.waitForTimeout(700);
    const az = await page.evaluate(() => {
      const names = [...document.querySelectorAll('#dir-list .dir-nm')].map(e => e.textContent);
      return {
        names, n: +document.querySelector('#dir-types [data-dtype="vcse"] .dir-chip-n').textContent.replace(/,/g, ''),
        first: /^[^\p{L}\p{N}]/u.test(names[0] || ''),
        order: names.every((x, i) => !i || phCompareNames(names[i - 1], x) <= 0),
        // A whole word of six or more capitals is the register's shouting, not an acronym.
        caps: names.filter(x => /(^|[\s(])\p{Lu}{6,}(?=[\s),.]|$)/u.test(x)),
      };
    });
    ok(az.names.length > 100 && !az.first && az.order && !az.caps.length,
      'directory: names sort past leading punctuation and are shown in title case', az.names.slice(0, 3).join(' | ') + ' / ' + az.caps.slice(0, 3).join(' | '));
    const tc = await page.evaluate(() => [phTitle('BRICK LANE YMCA CIC'), phTitle('Croydon Gp Super Network PCN'),
      phTitle("ST MARY'S NHS SURGERY"), phTitle('UK GP TRUST')]);
    ok(tc.join(' | ') === "Brick Lane YMCA CIC | Croydon GP Super Network PCN | St Mary's NHS Surgery | UK GP Trust",
      'title case keeps acronyms and lower-cases after an apostrophe', tc.join(' | '));
    // One charity count: the Charities tab explains it, and the Directory's
    // charities are the ones on the map plus the London-wide ones.
    await page.click('.mode-btn[data-mode="explore"]');
    await page.click('#sbtab-vcse');
    // P59: with the layer off, the tab does not say the charities are on the map.
    await page.uncheck('#vcse-tab-toggle');
    await page.waitForTimeout(300);
    const vOff = await page.evaluate(() => ({ about: document.getElementById('vcse-about').textContent, count: document.getElementById('vcse-count').textContent }));
    ok(!/^Showing/.test(vOff.about.trim()) && /Tick Show charities to put them on the map\./.test(vOff.about) && !/on the map/.test(vOff.count),
      'with Show charities unticked, the Charities tab does not say they are on the map', JSON.stringify(vOff));
    await page.check('#vcse-tab-toggle');
    await page.waitForFunction(() => document.querySelectorAll('.vcse-ring').length > 3, null, { timeout: 30000 }).catch(() => {});
    const ch = await page.evaluate(() => {
      const t = document.getElementById('vcse-about').textContent;
      const num = re => { const m = t.match(re); return m ? +m[1].replace(/,/g, '') : NaN; };
      const ring = document.querySelector('.leaflet-marker-icon.vcse-cluster .vcse-ring');
      return { t, pinned: num(/Showing ([\d,]+) registered charities/), wide: num(/The Directory also lists ([\d,]+)/),
        nopin: /more without a dot/.test(t) ? num(/It lists ([\d,]+) more without a dot/) : 0,
        tab: +document.getElementById('vcse-tab-count').textContent.replace(/,/g, ''),
        key: !document.getElementById('vcse-key').hidden && document.querySelectorAll('#vcse-key li').length,
        ring: ring ? getComputedStyle(ring).backgroundImage : '' };
    });
    ok(ch.pinned === ch.tab && ch.pinned + ch.wide + ch.nopin === az.n && !/[–—]/.test(ch.t),
      'charities: the tab and the Directory agree on one count, explained once', ch.t + ' / Directory ' + az.n);
    ok(ch.key === 11 && /conic-gradient/.test(ch.ring), 'charities: a key to the dots, and groups drawn as a ring of causes',
      ch.key + ' / ' + ch.ring.slice(0, 40));
    await checkAxe(page, 'Charities tab with its key');
    // The tour names the Directory without a count of its own; that check
    // runs with the tour checks below, on the chapter as it is shown.
    // The PCN list has no placeholder and no "Gp".
    await page.click('#sbtab-layers');
    const pcn = await page.evaluate(() => [...document.querySelectorAll('#pcn-focus option')].map(o => o.textContent));
    ok(pcn.length > 50 && !pcn.some(t => /not allocated|\bGp\b|\bKwh\b/.test(t)),
      'PCN list: no placeholder and no "Gp"', pcn.filter(t => /alloc|Gp|Kwh/.test(t)).join(', '));
    // A postcode search lists what is near it in the ward sheet.
    await page.click('#sbtab-overlay');
    await page.fill('#hdr-search-input', 'E1 6AN');
    await page.waitForTimeout(1200);
    await page.click('#sr-pc-row');
    await page.waitForFunction(() => document.querySelectorAll('#ws-body .ws-near-item').length >= 6, null, { timeout: 30000 }).catch(() => {});
    const near = await page.evaluate(() => {
      const n = document.querySelector('#ws-body .ws-near');
      return n ? { h: n.querySelector('.ws-near-h').textContent,
        cols: [...n.querySelectorAll('.ws-near-col h4')].map(e => e.textContent),
        d: [...n.querySelectorAll('.ws-near-col')].map(c => [...c.querySelectorAll('.ws-near-d')].map(e => e.textContent)) } : null;
    });
    ok(!!near && near.h === 'Near E1 6AN' && near.cols.join() === 'GP practices,Pharmacies,Registered charities nearby' &&
       near.d.every(l => l.length === 3 && dists(l).every((t, i, d) => !i || metres(t) >= metres(d[i - 1]))),
      'a postcode search lists the nearest GP practices, pharmacies and charities by distance', JSON.stringify(near));
    // Review H15: the charities column holds charities that serve the area,
    // says where they come from, and gives a sector, not a distance, for one
    // placed only by its postcode sector.
    const nearV = await page.evaluate(() => {
      const col = [...document.querySelectorAll('#ws-body .ws-near-col')].find(c => /charities/.test(c.querySelector('h4').textContent));
      const vcse = (typeof VCSE_ALL !== 'undefined' ? VCSE_ALL : []);
      const byName = {};
      vcse.forEach(r => { byName[phTitle(r.n)] = r; });
      const items = col ? [...col.querySelectorAll('li')].map(li => ({ n: li.querySelector('.ws-near-item').textContent,
        d: li.querySelector('.ws-near-d').textContent })) : [];
      return { note: col ? (col.querySelector('.ws-near-vnote') || {}).textContent || '' : '',
        items: items.map(it => ({ ...it, nr: byName[it.n] ? byName[it.n].nr : null, sec: byName[it.n] ? (!byName[it.n].a && !!byName[it.n].pc) : null })) };
    });
    ok(/^From the Charity Commission register\. A charity listed here may not offer help to the public, so check its website first\.$/.test(nearV.note) &&
       nearV.items.length === 3 && nearV.items.every(it => it.nr !== false) &&
       nearV.items.every(it => it.sec === null || (it.sec ? /^in [A-Z0-9]+ \d$/.test(it.d) : !/^in /.test(it.d))),
      'the near list\'s charities serve the area, carry the register note, and give a sector for sector-placed ones', JSON.stringify(nearV));
    await checkAxe(page, 'ward sheet with its near list');
    await page.evaluate(() => showWard('Lansbury'));
    await page.waitForTimeout(500);
    ok(await page.evaluate(() => !document.querySelector('#ws-body .ws-near')), 'the near list goes when another ward is chosen');
    ok(errors.length === 0, 'no page errors (charities, Directory, PCN)', errors.join(' | '));
    await page.close();
  }

  // Output areas: census indicators can be shown by output area once a borough is chosen.
  if (HAS_OA) {
    const { page, errors } = await open(browser, 'index.html?t=1', DESK);
    const k = await page.evaluate(() => OA_INDEX && OA_INDEX.keys.find(k => document.querySelector('#ov option[value="' + k + '"]')));
    ok(!!k, 'output area index loaded');
    if (k) {
      await setOv(page, k);
      await page.waitForTimeout(1000);
      ok(await page.evaluate(() => !document.querySelector('#dl-seg button[data-dl="oa"]').hidden), 'output area button shown for a census indicator');
      await page.click('#dl-seg button[data-dl="oa"]');
      await page.waitForFunction(() => PH_OA.loadedCount() === Object.keys(OA_INDEX.lads).length, null, { timeout: 30000 }).catch(() => {});
      ok(await page.evaluate(() => _activeLevel() === 'oa' && PH_OA.shownCount() === Object.values(OA_INDEX.lads).reduce((a, l) => a + l.count, 0)),
         'every output area drawn at once',
         await page.evaluate(() => _activeLevel() + ' ' + PH_OA.shownCount()));
      // An output area is titled by its place, with its code beneath.
      await page.evaluate(() => map.setView([51.494, -0.094], 15));
      await page.waitForTimeout(1200);
      const mc = await page.evaluate(() => { const r = document.getElementById('map').getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; });
      await page.mouse.move(mc[0], mc[1]); await page.mouse.move(mc[0] + 3, mc[1] + 2);
      await page.waitForTimeout(700);
      const tip = await page.evaluate(() => [...document.querySelectorAll('.leaflet-tooltip')].map(t => t.innerText).join(' | '));
      ok(/Small area in .+ ward, .+\nOutput area E00\d+/.test(tip), 'output area card names its place, with the code beneath', tip);
      await checkAxe(page, 'output areas');
      await setOv(page, 'imd_decile');
      await page.waitForTimeout(1500);
      ok(await page.evaluate(() => _activeLevel() !== 'oa' && PH_OA.shownCount() === 0), 'output areas cleared for a non-census indicator');
    }
    ok(errors.length === 0, 'no page errors with output areas', errors.join(' | '));
    await page.close();
  } else console.log('SKIP output area checks (no data/map/oa)');

  // Two indicators
  {
    const { page, errors } = await open(browser, 'index.html?ov=imd_decile&ov2=no2_ugm3&t=1', DESK);
    await page.waitForTimeout(2500);
    const cols = await page.evaluate(() => { const c = new Set(); lsoaLyr && lsoaLyr.eachLayer(l => c.add(l.options.fillColor)); return c.size; });
    ok(await page.evaluate(() => bivOn()), 'two-indicator map restores from link');
    ok(cols >= 8, 'two-indicator map uses the nine colours', cols + ' colours');
    const bivName = await page.evaluate(() => { const m = document.getElementById('map-legend-strip'); return { l: m.getAttribute('aria-label'), d: m.getAttribute('aria-describedby') || '' }; });
    ok(/Average deprivation decile|Deprivation decile/i.test(bivName.l) && /compared with .*nitrogen dioxide/i.test(bivName.l) && !/mls-min/.test(bivName.d),
      'the two-indicator key is named for both indicators and not described by the hidden scale', JSON.stringify(bivName));
    await page.click('#sbtab-overlay');
    await checkAxe(page, 'two indicators');
    // The key in words (review P1): corners from each indicator's own words,
    // low, middle and high on each axis, a swatch for a missing figure, the
    // caution about cause, and thirds that never split or push up a tie.
    const bk = await page.evaluate(() => {
      const leg = document.getElementById('biv-legend');
      const gw = leg.querySelector('.biv-gw');
      const vals = [5, 0, 0, 0, 0, 0, 0, 1, 2, 3, 4, 6].sort((a, b) => a - b);
      const t = bivThirds(vals);
      const cls = v => v < t[0] ? 0 : v < t[1] ? 1 : 2;
      const allTied = bivThirds([1, 1, 1, 1, 1, 1, 2, 3, 4]);
      const code = Object.keys(LSOA_DATA).find(c => bivFill('lsoa', c) !== BIV_MISSING);
      return {
        corners: [...leg.querySelectorAll('.biv-c')].map(e => e.textContent),
        axes: [...leg.querySelectorAll('.biv-yl span, .biv-xl span')].map(e => e.textContent),
        img: gw && gw.getAttribute('role') === 'img' && /nine colours/.test(gw.getAttribute('aria-label')),
        miss: !!leg.querySelector('.biv-miss-sw') && /Missing one of the two figures/.test(leg.textContent),
        note: /not proof that one causes the other/.test(leg.textContent),
        zeros: vals.filter(v => v === 0).map(cls), ok: t.ok, tied: allTied.ok,
        hover: code ? bivThirdsLine('lsoa', code) : '',
        missEdge: bivEdge({ fillColor: BIV_MISSING, fillOpacity: 0.9, weight: 0.5 }).dashArray,
      };
    });
    ok(bk.corners.indexOf('More deprived, dirtier air') !== -1 && bk.corners.indexOf('Less deprived, cleaner air') !== -1,
      'two-indicator corners are named from each indicator\'s own words', bk.corners.join(' | '));
    ok(bk.axes.join(' ') === 'High Middle Low Low Middle High' && bk.img && bk.miss && bk.note,
      'two-indicator key marks low, middle and high, is one described image, has a missing swatch and the caution', JSON.stringify(bk));
    ok(new Set(bk.zeros).size === 1 && bk.ok && bk.tied === false && bk.missEdge === '3 3',
      'thirds keep tied figures together, an unsplittable indicator is flagged, missing figures are dashed', JSON.stringify(bk));
    ok(/third/.test(bk.hover) && /deprived third/.test(bk.hover), 'two-indicator hover names the thirds', bk.hover);
    ok(errors.length === 0, 'no page errors (two indicators)', errors.join(' | '));
    await page.close();
  }

  // Data and methodology polish (review P2 to P11).
  {
    const { page, errors } = await open(browser, 'index.html?t=1', DESK);
    const dm = await page.evaluate(async () => {
      await Promise.all([ensureLsoaData(), ensureMsoaData(), ensureLsoaImd(), ensureWardDetail()]);
      const st = Object.values(WARD_DATA_BY_CODE).find(w => w.name === 'Stonebridge');
      const asbIn = Object.values(WARD_DATA_BY_CODE).filter(w => w.crime_by_category).every(w =>
        w.indicators.crime_total === Object.entries(w.crime_by_category).filter(([c]) => c !== 'anti-social-behaviour').reduce((a, [, v]) => a + v, 0));
      const card = k => indicatorCardHTML(k).replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
      const fit = Object.values(WARD_DATA_BY_CODE).find(w => w.name === 'Farringdon Without');
      return {
        covid: /COVID-19 raised deaths/.test(card('ft_93250')) && /COVID-19 raised deaths/.test(card('ft_93283')) &&
          /2020\/21, was the first year of the pandemic/.test(card('ft_93227')) && !/COVID/.test(card('census_bad_health_pct')),
        crime: asbIn && /not counting anti-social behaviour/.test(card('crime_total')),
        jsa: indLabel('ft_93098') === 'Long-term Jobseeker’s Allowance claimants' && /Universal Credit/.test(card('ft_93098')) &&
          !Object.values(MSOA_DATA).some(r => r.ft_93098 === 0 && !r.ft_93098_vs) && OV_CFG.ft_93098.min >= 0,
        fit: wardFitNote(fit), fitNone: wardFitNote(st),
        names: [indLabel('imd_score_ward'), indLabel('imd_score'), indSameAs('ft_93097')],
        inc: [st.indicators.income_score, fmtOv(st.indicators.income_score, 'income_score'), OV_CFG.income_score.lo, OV_CFG.health_score.hi,
          ovContext('ward', 'income_score', st.indicators.income_score, st)],
        ratio: fmtOvTip(126, 'ft_93250'), vs: VS_ENGLAND_TEXT.S,
        plain: Object.values(OV_PLAIN).filter(t => /where 100 is average/.test(t)).length,
        oa: /about 125 households/.test(card('census_no_car_pct')),
        est: EST_HTML.indexOf('>Estimated from 2011 areas<') !== -1,
      };
    });
    ok(dm.covid, 'death, life expectancy and admissions cards say their periods take in the pandemic');
    ok(dm.crime, 'the crime total leaves out anti-social behaviour, and its card says so');
    ok(dm.jsa, 'long-term claimants are named for Jobseeker\'s Allowance, with no bare zeros');
    ok(/slightly different area|noticeably different area/.test(dm.fit) && dm.fitNone === '', 'a ward whose LSOAs hold a different population is marked', dm.fit);
    ok(dm.names[0] !== dm.names[1] && dm.names[2] === 'claimant_rate_pct', 'the two deprivation scores are named apart and the claimant rates are paired', dm.names.join(' | '));
    ok(dm.inc[0] > 1 && /%$/.test(dm.inc[1]) && dm.inc[2] === 'Less deprived' && dm.inc[3] === 'More deprived' && /more deprived than/.test(dm.inc[4]),
      'income deprivation reads as a percentage, and deprivation keys and hovers say less or more deprived', dm.inc.join(' | '));
    ok(dm.ratio === '126.0 (England is 100)' && dm.vs === 'Not clearly different from England' && dm.plain === 0,
      'hover ratios say England is 100, and similar reads not clearly different', dm.ratio + ' / ' + dm.vs);
    ok(dm.oa && dm.est, 'Census cards carry the output area caution, and the 2011 label has one name');
    // Ward rankings of a Local Health figure say there is no interval (P3), and
    // the ward report places the ward among wards (P11) and marks a poor fit (P6).
    await setOv(page, 'ft_93250');
    await page.waitForTimeout(1500);
    await page.evaluate(() => _setDataLevel('ward'));
    await page.waitForTimeout(1500);
    const rk = await page.evaluate(() => {
      renderTopBottom();
      let html = '';
      _generateWardReport({ sink: h => { html = h; } }, 'Farringdon Without', {});
      const d = new DOMParser().parseFromString(html, 'text/html');
      return { notes: document.getElementById('rank-notes').textContent,
        dots: [...d.querySelectorAll('.legend')].map(e => e.textContent).find(t => /Each dot places/.test(t)) || '',
        fit: (d.querySelector('.fit-note') || {}).textContent || '' };
    });
    ok(/no interval is available/.test(rk.notes), 'ward rankings of a Local Health figure say no interval is available', rk.notes);
    ok((!rk.dots || /places the ward among the [\d,]+ London wards/.test(rk.dots)) && /different area/.test(rk.fit),
      'the ward report places the ward among wards and marks a poor LSOA fit', JSON.stringify(rk));
    ok(errors.length === 0, 'no page errors (data and methodology polish)', errors.join(' | '));
    await page.close();
  }

  // The tour, the Indicators name and the Ask panel (workplan 1.4 to 1.6).
  // Six chapters in plain words; the last completes when the reader chooses
  // any indicator, on a desktop from the tab and on a phone through the menu;
  // and the tour, the Ask panel and the privacy page describe the same route
  // for a question: through this site's server to Anthropic's Claude model,
  // 10 a day, with a reply to a clarifying question free.
  const ASK_SAME = [/this site's server/, /Anthropic's Claude model/, /10 questions a day/,
    /narrow a question down does not use one up/];
  for (const [vpName, vp] of [['desktop', DESK], ['phone', PHONE], ['landscape', SIDEWAYS]]) {
    const { page, errors } = await open(browser, 'index.html?t=1', vp);
    const ui = await page.evaluate(() => ({
      heading: document.querySelector('#tab-overlay .sb-lbl span').textContent.trim(),
      chip: document.getElementById('map-colour-chip').textContent.trim(),
      tab: document.getElementById('sbtab-overlay').textContent.trim(),
      titles: [...document.querySelectorAll('#dl-seg button')].map(b => b.title),
      body: document.body.innerText,
    }));
    ok(ui.heading === 'Indicators' && ui.chip === 'Indicators' && ui.tab === 'Indicators',
      vpName + ': the Indicators panel, its tab and the phone chip share the name "Indicators"', JSON.stringify([ui.heading, ui.chip, ui.tab]));
    ok(!/colour the map/i.test(ui.body) && ui.titles.every(t => !/^Colour the map/.test(t)) && ui.titles.some(t => /^Show figures by ward, the [\d,]+ areas councillors are elected for\.$/.test(t)),
      vpName + ': no control is called "Colour the map", and the level buttons say what they show', ui.titles.join(' | '));
    const ch = await page.evaluate(async () => {
      PH_TOUR.start();
      const out = [];
      for (let i = 0; i < PH_TOUR.length; i++) {
        await new Promise(r => setTimeout(r, 400));
        const narr = document.getElementById('tour-narr'), ask = document.getElementById('ai-toggle');
        out.push({ t: document.getElementById('tour-title').textContent, c: document.getElementById('tour-ctr').textContent,
          n: narr.innerText, spot: document.querySelectorAll('.tour-spot').length,
          ch: narr.clientHeight, sh: narr.scrollHeight,
          ask: !!ask && getComputedStyle(ask).display !== 'none' && ask.getBoundingClientRect().width > 0 });
        if (i < PH_TOUR.length - 1) document.getElementById('tour-next').click();
      }
      return out;
    });
    ok(ch.length === 6 && ch[0].c === 'Chapter 1 of 6' &&
       ch.map(c => c.t).join('|') === 'What PopHealth Map is|Choose an indicator|Select a place|The tools|What the figures cannot tell you|Your turn',
      vpName + ': the tour is six chapters, in order', ch.map(c => c.t).join(' | '));
    const all = ch.map(c => c.t + ' ' + c.n).join(' ');
    ok(!/[–—]/.test(all) && !/\bclick/i.test(all) && !/never receives|choropleth|overlay/i.test(all),
      vpName + ': the tour has no dashes, no "click" and no claim that the data is never sent');
    const short = ch.filter(c => {
      const s = c.n.split(/(?<=[.:])\s+/).map(x => x.split(/\s+/).filter(Boolean).length);
      return s.some((_, i) => i >= 2 && s[i] < 8 && s[i - 1] < 8 && s[i - 2] < 8);
    });
    ok(!short.length, vpName + ': no chapter has three short sentences in a row', short.map(c => c.t).join(', '));
    ok(/local authorities \(LAs\)/.test(ch[0].n) && /\b\d{2,3} indicators\b/.test(ch[0].n) && /\b\d{2} local authorities/.test(ch[0].n) && /\b\d{3} wards\b/.test(ch[0].n)
       && !/borough/i.test(all),
      vpName + ': the first chapter counts indicators, LAs and wards from the data and names the level LA', ch[0].n);
    ok(/postcode/.test(ch[2].n) && /a panel opens with/.test(ch[2].n) && !/along the bottom|at the top centre|on the right/.test(ch[2].n),
      vpName + ': the place chapter mentions postcode search and names no side for the panel, which moves with the screen', ch[2].n.slice(0, 120));
    // On a phone the Ask button waits until the tour ends, because the strip
    // is what is being read (workplan 1.5).
    if (vp.isMobile) ok(ch.every(c => !c.ask), vpName + ': the Ask button is hidden throughout the tour', ch.map(c => c.ask).join(','));
    // Held sideways, the strip gives the narration about 200px, so a chapter
    // is read rather than scrolled through a slot (most fit outright).
    if (vpName === 'landscape') {
      ok(ch.every(c => c.ch >= Math.min(c.sh, 180)) && ch.filter(c => c.sh <= c.ch + 2).length >= 4,
        'landscape: the tour narration has at least 180px on every chapter, and most chapters fit without scrolling',
        ch.map(c => c.t + ' ' + c.ch + '/' + c.sh).join(' | '));
    }
    ok(['Query', 'Compare', 'Custom area', 'Data export', 'Directory', 'Ask about this data'].every(w => ch[3].n.includes(w)) &&
       ASK_SAME.every(re => re.test(ch[3].n)) && /personal/.test(ch[3].n) && ch[3].spot >= 2,
      vpName + ': the tools chapter names every tool and describes Ask as the panel does', ch[3].n);
    const dirSentence = (ch[3].n.match(/Directory[^.]*\./) || [''])[0];
    ok(dirSentence && !/\d/.test(dirSentence), vpName + ': the tour gives no charity or Directory count of its own', dirSentence);
    ok(/Key indicators|Start here/.test(ch[5].n) && !!vp.isMobile === /menu button first/.test(ch[5].n),
      vpName + ': the last chapter names a group on the Indicators tab, and on a phone says to open the menu first', ch[5].n);
    // Do the last chapter as a reader would.
    const before = await page.evaluate(() => ({ next: document.getElementById('tour-next').textContent, ov: document.getElementById('ov').value }));
    ok(before.next === 'Skip and finish' && before.ov === 'none', vpName + ': the last chapter waits, with no indicator chosen', JSON.stringify(before));
    if (vp.isMobile) {
      await page.tap('#hdr-menu'); await page.waitForTimeout(600);
      await page.tap('#sbtab-overlay'); await page.waitForTimeout(300);
      await page.tap('#ov-chips button:nth-child(3)');
    } else {
      await page.click('#sbtab-overlay'); await page.waitForTimeout(300);
      await page.click('#ov-chips button:nth-child(3)');
    }
    await page.waitForTimeout(1500);
    const after = await page.evaluate(() => ({ next: document.getElementById('tour-next').textContent, ov: document.getElementById('ov').value,
      done: document.getElementById('tour-strip').classList.contains('task-done'),
      said: (document.querySelector('#tour-narr .tour-task-done') || {}).innerText || '',
      drawer: PH_DRAWER.isOpen() }));
    ok(after.done && after.next === 'Finish' && after.ov !== 'none' && /coloured by/.test(after.said) && !after.drawer,
      vpName + ': choosing a key indicator completes the last chapter', JSON.stringify(after));
    await page.click('#tour-next');
    await page.waitForTimeout(800);
    const end = await page.evaluate(() => ({ run: PH_TOUR.running(), ov: document.getElementById('ov').value, spots: document.querySelectorAll('.tour-spot, .tour-pulse').length }));
    ok(!end.run && end.ov === after.ov && !end.spots, vpName + ': Finish closes the tour and keeps the indicator the reader chose', JSON.stringify(end));
    // The Ask panel's opening message, in the same terms.
    await page.click('#ai-toggle');
    await page.waitForTimeout(400);
    const intro = await page.evaluate(() => {
      const m = document.querySelector('#ai-log .ai-intro');
      return { text: m ? m.innerText : '', paras: m ? m.querySelectorAll('p').length : 0,
        caution: (document.getElementById('ai-caution') || {}).innerText || '' };
    });
    ok(intro.paras === 2 && ASK_SAME.every(re => re.test(intro.text)) && /personal/.test(intro.text) && /local authority \(LA\)/.test(intro.text) &&
       !/[–—]/.test(intro.text + intro.caution) && /check any figure/.test(intro.caution),
      vpName + ': the Ask panel opens with two paragraphs that describe the same route as the tour', intro.text);
    ok(errors.length === 0, 'no page errors (tour, ' + vpName + ')', errors.join(' | '));
    await page.close();
  }

  // First visit (review round 2, H22 to H28 and onboarding-02): the map opens
  // on Indicators, names its region in a header button that lists the
  // others, finds LAs on other regions' maps, starts the tour for campaign
  // links but not over a shared view, keeps a bare address until the view
  // changes, says when the LSOAs are loading, and writes its key in words.
  {
    const { page, errors } = await open(browser, 'index.html?utm_source=linkedin&utm_medium=social&fbclid=IwAR0abc', DESK);
    const first = await page.evaluate(() => ({ tour: PH_TOUR.running(), url: location.search,
      tab: document.querySelector('.sb-tab.active').id, sel: document.getElementById('sbtab-overlay').getAttribute('aria-selected'),
      pane: document.getElementById('tab-overlay').classList.contains('active') && !document.getElementById('tab-layers').classList.contains('active') }));
    ok(first.tour, 'a campaign link (utm_*, fbclid) still gets the tour on a first visit', JSON.stringify(first));
    ok(first.tab === 'sbtab-overlay' && first.sel === 'true' && first.pane, 'the map opens on the Indicators tab', JSON.stringify(first));
    await page.evaluate(() => PH_TOUR.end());
    await page.waitForTimeout(1500);
    const q0 = await page.evaluate(() => location.search);
    ok(!/[?&](L|z|c)=/.test(q0), 'an untouched map keeps a bare address, without the opening layers or position', q0);
    await page.evaluate(() => map.setZoom(map.getZoom() + 1));
    await page.waitForTimeout(1200);
    const q1 = await page.evaluate(() => location.search);
    ok(/[?&]z=/.test(q1) && /[?&]c=/.test(q1), 'the position is written once the map has moved', q1);
    // The first key indicator keeps the wards on the map and says what is loading.
    await page.route('**/lsoa_imd.js', async r => { await new Promise(x => setTimeout(x, 2500)); r.continue(); });
    await setOv(page, 'imd_decile');
    await page.waitForTimeout(400);
    const loading = await page.evaluate(() => ({ hint: document.getElementById('dl-hint').classList.contains('show') ? document.getElementById('dl-hint').textContent : '',
      wards: !!(wLyr && map.hasLayer(wLyr)), lsoa: !!(lsoaLyr && map.hasLayer(lsoaLyr)) }));
    ok(/^Loading [\d,]+ LSOAs, which takes a few seconds the first time\.$/.test(loading.hint) && loading.wards && !loading.lsoa,
      'the first LSOA indicator keeps the wards drawn and says the LSOAs are loading', JSON.stringify(loading));
    await page.waitForFunction(() => lsoaLyr && map.hasLayer(lsoaLyr), null, { timeout: 30000 }).catch(() => {});
    await page.waitForTimeout(800);
    const loaded = await page.evaluate(() => ({ wards: !!(wLyr && map.hasLayer(wLyr)), hint: document.getElementById('dl-hint').textContent }));
    ok(!loaded.wards, 'the wards make way once the LSOAs are drawn', JSON.stringify(loaded));
    await page.unroute('**/lsoa_imd.js');
    // The region button in the header.
    const hb = await page.evaluate(() => { const b = document.getElementById('hdr-region-btn');
      return { name: b.getAttribute('aria-label'), text: b.innerText.trim(), exp: b.getAttribute('aria-expanded'),
        label: document.querySelector('label[for="region-picker"]').textContent }; });
    await page.click('#hdr-region-btn');
    await page.waitForTimeout(300);
    const menu = await page.evaluate(() => { const m = document.getElementById('hdr-region-menu');
      return { shown: !m.hidden && m.getBoundingClientRect().height > 100, links: [...m.querySelectorAll('a')].map(a => a.textContent + '=' + a.getAttribute('href')),
        current: (m.querySelector('a[aria-current="page"]') || {}).textContent, focus: document.activeElement && document.activeElement.textContent }; });
    ok(hb.name === 'London, change region' && hb.text === 'London' && hb.label === 'Region' && menu.shown &&
       menu.links.some(l => /^North East=.*\?r=north-east$/.test(l)) && menu.current === 'London' && menu.focus === 'London',
      'the header names the region in a button that lists the other regions, and the Layers select is called Region', JSON.stringify([hb, menu]));
    await checkAxe(page, 'region list open');
    await page.keyboard.press('Escape');
    await page.waitForTimeout(200);
    ok(await page.evaluate(() => document.getElementById('hdr-region-menu').hidden && document.activeElement.id === 'hdr-region-btn'),
      'Escape closes the region list and returns to its button');
    // A search for a place on another region's map.
    const search = async q => { await page.fill('#hdr-search-input', q); await page.waitForTimeout(900);
      return page.evaluate(() => ({ text: document.getElementById('hdr-search-results').innerText,
        row: (document.querySelector('#hdr-search-results .sr-region') || {}).dataset || null })); };
    const leeds = await search('Leeds'), manc = await search('Manchester'), none = await search('zzqxv');
    ok(/Leeds is on the Yorkshire and The Humber map\. Open Yorkshire and The Humber/.test(leeds.text) && leeds.row && leeds.row.region === 'yorkshire-and-the-humber' &&
       /Manchester is on the North West map\. Open the North West/.test(manc.text),
      'a search for an LA on another map says which map, and offers to open it', JSON.stringify([leeds, manc]));
    ok(/^We could not find that in London\. Try a postcode, or choose another region from the region button at the top\.$/.test(none.text.trim()),
      'a search that finds nothing says where it looked and how to change region', none.text);
    await page.fill('#hdr-search-input', '');
    // The key under the scale is written in words, for every level.
    const BAD = /TS0\d|CLA01|bridge|→|pop-weighted|best-fit|NOMIS|Fingertips \(|Ward\/LSOA|·/;
    const keys = [];
    for (const [k, lv] of [['imd_decile', ''], ['ft_93283', 'msoa'], ['ft_93283', 'ward'], ['ft_93283', 'borough'], ['census_bad_health_pct', 'lsoa'],
                           ['census_bad_health_pct', 'ward'], ['claimant_rate_pct', ''], ['imd_score_ward', ''], ['crime_rate_per_1000', ''], ['census_population', 'ward']]) {
      await setOv(page, k);
      await page.waitForTimeout(1200);
      if (lv) { await page.evaluate(lv => _setDataLevel(lv), lv); await page.waitForTimeout(1500); }
      keys.push(await page.evaluate(k => ({ k, lv: currentDataLevel, meta: document.getElementById('sc-meta').innerText,
        mid: document.getElementById('sc-mid').textContent, min: document.getElementById('mls-min').textContent, max: document.getElementById('mls-max').textContent,
        label: document.getElementById('mls-label').innerText }), k));
    }
    ok(keys.every(x => x.meta && !BAD.test(x.meta) && !/[–—]/.test(x.meta)), 'the key under the scale has no source codes, arrows or dots', keys.map(x => x.k + '@' + x.lv + ': ' + x.meta).join(' | '));
    ok(/^Published by OHID \(Local Health\) for MSOAs, 2019 to 2023\. Ward figures are our estimates, blended from the MSOAs each ward overlaps\./.test(keys[2].meta) &&
       /^Calculated for each ward from MHCLG/.test(keys[7].meta) && /^Published by ONS for LSOAs, /.test(keys[6].meta) && /^Counted from police\.uk crime locations/.test(keys[8].meta),
      'the key says who publishes the figures and how the level drawn was made from them', [keys[2].meta, keys[6].meta, keys[7].meta, keys[8].meta].join(' | '));
    ok(keys.every(x => x.mid === '') && keys[0].min === 'Most deprived' && keys[0].max === 'Least deprived' &&
       keys.filter(x => x.k === 'census_bad_health_pct').every(x => /\d%$/.test(x.min) && !/ %/.test(x.min)) &&
       keys.filter(x => x.k === 'census_population').every(x => /^\D*[\d,]+ people$/.test(x.min)) &&
       !/per 1,000 residents\s*per 1,000/.test(keys[8].label) && !keys.some(x => /score|decile/.test(x.min + x.max) && x.k !== 'imd_decile'),
      'the key has no unlabelled middle number, words for the decile ends, whole counts and no doubled units', JSON.stringify(keys.map(x => [x.k, x.mid, x.min, x.max, x.label])));
    ok(errors.length === 0, 'no page errors (first visit)', errors.join(' | '));
    await page.close();
  }
  // A shared view still opens without the tour, and ?tour=1 forces it.
  for (const [url, want] of [['index.html?ov=imd_score_ward', false], ['index.html?ov=imd_score_ward&tour=1', true], ['index.html?ref=producthunt', true]]) {
    const { page } = await open(browser, url, DESK);
    const run = await page.evaluate(() => PH_TOUR.running());
    ok(run === want, url + (want ? ' opens the tour' : ' opens without the tour'), String(run));
    await page.close();
  }
  // On a slow line the tour does not take over the visitor's first click
  // (onboarding-02): the wards land after they have started using the page.
  {
    const page = await browser.newPage({ viewport: { width: DESK.width, height: DESK.height } });
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    await page.route(/ward_(core|data)\.json/, async r => { await new Promise(x => setTimeout(x, 6000)); r.continue(); });
    await page.goto(BASE + '/index.html');
    await page.waitForTimeout(2500);
    // The loading screen covers the page, but the keyboard still reaches the
    // search box behind it.
    await page.focus('#hdr-search-input');
    await page.keyboard.type('Stone');
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => typeof wLyr !== 'undefined' && wLyr && wLyr.getLayers().length > 600, null, { timeout: 40000 }).catch(() => {});
    await page.waitForTimeout(2500);
    const slow1 = await page.evaluate(() => PH_TOUR.running());
    await page.evaluate(() => { const l = wLyr.getLayers().find(l => l.feature.properties.WD24NM === 'Stonebridge'); if (l) l.fire('click'); });
    await page.waitForTimeout(1200);
    const slow2 = await page.evaluate(() => ({ tour: PH_TOUR.running(), ward: selW }));
    ok(!slow1 && !slow2.tour && slow2.ward === 'Stonebridge', 'on a slow load the tour stays away once the visitor has started, and a ward click opens the ward', JSON.stringify([slow1, slow2]));
    ok(errors.length === 0, 'no page errors (slow first visit)', errors.join(' | '));
    await page.close();
  }
  // The key on a phone keeps its ends whole (H28).
  for (const w of [390, 320]) {
    const { page } = await open(browser, 'index.html?ov=census_bad_health_pct&t=1', { width: w, height: 760, isMobile: true, hasTouch: true });
    const fit = await page.evaluate(() => {
      const s = document.getElementById('map-legend-strip'), m = document.getElementById('map').getBoundingClientRect(), r = s.getBoundingClientRect();
      const end = id => { const e = document.getElementById(id); return e.scrollWidth <= e.clientWidth + 1 && e.getBoundingClientRect().right <= m.right; };
      return { min: end('mls-min'), max: end('mls-max'), inMap: r.left >= m.left && r.right <= m.right, name: getComputedStyle(document.getElementById('mls-label')).fontSize };
    });
    ok(fit.min && fit.max && fit.inMap, w + 'px phone: the key\'s end values are not clipped and the key stays on the map', JSON.stringify(fit));
    await page.close();
  }

  // Phone
  {
    const { page, errors } = await open(browser, 'index.html?ov=imd_decile&t=1', PHONE);
    const r = await page.evaluate(() => {
      const a = document.getElementById('map-legend-strip').getBoundingClientRect();
      const b = document.querySelector('.leaflet-control-attribution').getBoundingClientRect();
      return { overlap: a.bottom > b.top && a.top < b.bottom, footer: document.querySelector('footer.sbar').parentNode.id };
    });
    ok(!r.overlap, 'phone: legend clear of the map credits');
    ok(r.footer === 'sidebar', 'phone: footer in the drawer');
    // Give the map room on phones (workplan 2.10): the credits on one line,
    // nothing floating over them, and the "Indicators" chip over the map.
    {
      const cr = await page.evaluate(() => {
        const a = document.querySelector('.leaflet-control-attribution');
        return { text: a.innerText, h: a.getBoundingClientRect().height };
      });
      const attr = await rectOf(page, '.leaflet-control-attribution');
      const covered = [];
      for (const s of ['#map-legend-strip', '#ai-toggle', '#map-colour-chip', '#dl-badge']) if (overlaps(attr, await rectOf(page, s))) covered.push(s);
      ok(cr.h <= 20 && /OpenStreetMap/.test(cr.text) && /Esri|CARTO/.test(cr.text) && !/Leaflet|Data credits/.test(cr.text) && !covered.length,
        'phone: the map credits are one line, keep the tile and OpenStreetMap credit, and nothing covers them', JSON.stringify(cr) + ' ' + covered.join(','));
      const chip = await rectOf(page, '#map-colour-chip');
      ok(!!chip && chip.h >= 44 && !overlaps(chip, await rectOf(page, '#ai-toggle')) && !overlaps(chip, await rectOf(page, '#map-legend-strip')),
        'phone: the "Indicators" chip floats over the map, clear of the Ask button and the key', JSON.stringify(chip));
      await page.touchscreen.tap(chip.l + 20, chip.t + 20);
      await page.waitForTimeout(600);
      ok(await page.evaluate(() => PH_DRAWER.isOpen() && document.getElementById('tab-overlay').classList.contains('active')),
        'phone: the "Indicators" chip opens the Indicators tab');
      // The search prompt fits its box in the drawer, measured in the box's
      // own font (an input's scrollWidth ignores its placeholder).
      const phFit = await page.evaluate(() => {
        const i = document.getElementById('hdr-search-input'), cs = getComputedStyle(i);
        const ctx = document.createElement('canvas').getContext('2d');
        ctx.font = cs.fontStyle + ' ' + cs.fontWeight + ' ' + cs.fontSize + ' ' + cs.fontFamily;
        const need = ctx.measureText(i.placeholder).width;
        const room = i.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
        return { ph: i.placeholder, need: Math.round(need), room: Math.round(room), aria: i.getAttribute('aria-label') };
      });
      ok(phFit.room > 0 && phFit.need <= phFit.room && !!phFit.aria, 'phone: the search prompt fits its box', JSON.stringify(phFit));
      await page.evaluate(() => PH_DRAWER.close());
      await page.waitForTimeout(400);
      const lg = await rectOf(page, '#map-legend-strip');
      const lgName = await page.evaluate(() => [document.getElementById('map-legend-strip').getAttribute('aria-label'),
        document.getElementById('mls-label').firstChild.textContent.trim()]);
      ok(lgName[0] === 'About this indicator: ' + lgName[1], 'phone: the key is a button named after its indicator', lgName.join(' / '));
      await page.touchscreen.tap(lg.l + 30, lg.t + lg.h / 2);
      await page.waitForTimeout(700);
      ok(await page.evaluate(() => PH_DRAWER.isOpen() && document.getElementById('tab-overlay').classList.contains('active') &&
          !document.getElementById('ov-info-panel').hidden &&
          document.querySelector('#ov-info-panel dl.ic').getBoundingClientRect().height > 100),
        'phone: a tap on the key opens the indicator card');
      await page.evaluate(() => PH_DRAWER.close());
      await page.waitForTimeout(400);
      await checkTargets(page, 'the map');
      const hits = await hitSizes(page, '.leaflet-control-zoom a, #dl-seg button, #dl-help, #hdr-menu, #hdr-search-btn, #ai-toggle, #map-colour-chip');
      ok(hits.length >= 8 && hits.every(x => x.w >= 44 && x.h >= 44), 'phone: the map controls have 44px touch targets',
        hits.filter(x => x.w < 44 || x.h < 44).map(x => x.id + ' ' + x.w + 'x' + x.h).join(', '));
    }
    const box = await page.evaluate(() => { const r = document.getElementById('map').getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; });
    await page.touchscreen.tap(box[0], box[1]);
    await page.waitForTimeout(1200);
    ok(await page.evaluate(() => !document.getElementById('area-peek').hidden), 'phone: tapping an area shows its card');
    await checkAxe(page, 'phone with area card');
    await page.click('#hdr-menu'); await page.waitForTimeout(400);
    // The drawer: its button says it will hide the panel, and Reset, Copy
    // link and More share one row under the search box.
    const drawer = await page.evaluate(() => {
      const m = document.getElementById('hdr-menu');
      const tops = ['tb-reset', 'tb-link', 'tb-more'].map(id => Math.round(document.getElementById(id).getBoundingClientRect().top));
      return { name: m.getAttribute('aria-label'), exp: m.getAttribute('aria-expanded'), row: tops.every(t => t === tops[0]) };
    });
    ok(drawer.name === 'Hide panel' && drawer.exp === 'true' && drawer.row, 'phone: the drawer button and the tools row', JSON.stringify(drawer));
    await checkAxe(page, 'phone drawer');
    await checkTargets(page, 'the drawer');
    g = await glyphCheck(page);
    ok(!g.length, 'phone: no glyph or emoji icons in the drawer', g.slice(0, 5).join(' | '));
    // The four tools wait behind More, so the tabs and their content start
    // straight under the search row, and the drawer scrolls as one column
    // (workplan 1.10): the search row and the tabs stay at the top, and the
    // open tab gets the rest of the drawer, at least 650px, with Map style and
    // the footer links after it rather than pinned below it.
    const drawerOf = () => page.evaluate(() => {
      const sb = document.getElementById('sidebar'), r = s => document.querySelector(s).getBoundingClientRect();
      const pane = document.querySelector('#sidebar > .tab-pane.active');
      return {
        tools: Math.round(r('#sb-tools').height),
        room: Math.round(r('#sidebar').bottom - r('#sidebar > .sb-tabs').bottom),
        own: pane.scrollHeight - pane.clientHeight,                 // 0: the pane does not scroll on its own
        order: r('#map-controls-sec').top >= r('#sidebar > .tab-pane.active').bottom - 1 && r('#sidebar footer.sbar').top >= r('#map-controls-sec').bottom - 1,
        sticky: ['#sb-tools', '#sidebar > .sb-tabs'].map(s => getComputedStyle(document.querySelector(s)).position),
        scrolls: getComputedStyle(sb).overflowY,
      };
    });
    const dr = Object.assign(await drawerOf(), await page.evaluate(() => ({
      inMenu: ['tb-reset', 'tb-link', 'tb-cb', 'tb-png'].every(id => document.getElementById(id).parentNode.id === 'sb-more-menu'),
      menuHidden: document.getElementById('sb-more-menu').hidden,
    })));
    ok(dr.tools <= 64 && dr.room >= 650 && dr.own <= 1 && dr.order && dr.sticky.join() === 'sticky,sticky' && dr.scrolls === 'auto' && dr.inMenu && dr.menuHidden,
      'phone: the drawer is one scrolling column, with the search row and tabs at the top and at least 650px for the open tab', JSON.stringify(dr));
    // Scrolled to the end, the search row and tabs are still at the top and
    // Map style and the footer links are on screen.
    {
      await page.evaluate(() => document.querySelector('.sb-tab[data-tab="layers"]').click());
      await page.waitForTimeout(300);
      const end = await page.evaluate(async () => {
        const sb = document.getElementById('sidebar');
        sb.scrollTop = sb.scrollHeight;
        await new Promise(r => setTimeout(r, 200));
        const r = s => document.querySelector(s).getBoundingClientRect();
        const out = { scrolled: sb.scrollTop > 0, tools: Math.round(r('#sb-tools').top), tabs: Math.round(r('#sidebar > .sb-tabs').top),
          foot: r('#sidebar footer.sbar').bottom <= innerHeight + 1, style: r('#map-controls-sec').top >= r('#sidebar > .sb-tabs').bottom };
        // A newly opened tab starts at its top.
        document.querySelector('.sb-tab[data-tab="overlay"]').click();
        await new Promise(r => setTimeout(r, 200));
        out.reset = sb.scrollTop === 0;
        return out;
      });
      ok(end.scrolled && end.tools === 0 && end.tabs > 0 && end.tabs <= 70 && end.foot && end.style && end.reset,
        'phone: scrolled to its end the drawer keeps search and tabs at the top, and a new tab opens at its top', JSON.stringify(end));
    }
    // 16px in every field on a touch screen, so iOS does not zoom into one.
    const small = await page.evaluate(() => [...document.querySelectorAll('input:not([type=checkbox]):not([type=radio]):not([type=range]), select, textarea')]
      .filter(e => parseFloat(getComputedStyle(e).fontSize) < 16).map(e => e.id || e.className || e.tagName));
    ok(!small.length, 'phone: every input, select and text box is 16px, so iOS does not zoom', small.slice(0, 6).join(', '));
    await page.click('#sb-more');
    await page.waitForTimeout(200);
    const menu = await hitSizes(page, '#sb-more-menu button');
    ok(menu.length === 4 && menu.every(x => x.h >= 44) && await page.evaluate(() => document.getElementById('sb-more').getAttribute('aria-expanded') === 'true'),
      'phone: More opens Reset, Copy link, Colour-blind palette and Screenshot, each 44px tall', JSON.stringify(menu));
    await checkAxe(page, 'phone drawer with More open');
    await page.keyboard.press('Escape');
    ok(await page.evaluate(() => document.getElementById('sb-more-menu').hidden && PH_DRAWER.isOpen()),
      'phone: Escape closes the More menu and leaves the panel open');
    await page.click('#sb-more');
    await page.waitForTimeout(200);
    await page.click('#tb-png');
    await page.waitForTimeout(500);
    const sheet = await page.evaluate(() => {
      const d = document.querySelector('dialog.ed-dlg'); const r = d && d.getBoundingClientRect();
      return d ? { drawer: PH_DRAWER.isOpen(), bottom: Math.round(r.bottom), width: Math.round(r.width), vh: innerHeight, vw: innerWidth } : null;
    });
    ok(!!sheet && !sheet.drawer && sheet.bottom === sheet.vh && sheet.width === sheet.vw,
      'phone: the screenshot dialog closes the drawer and rises from the bottom of the screen', JSON.stringify(sheet));
    await page.keyboard.press('Escape');
    await page.waitForTimeout(300);
    // Ask opens as a full-screen sheet on a phone (workplan 2.10).
    if (await page.evaluate(() => !!document.getElementById('ai-toggle'))) {
      // The tapped area's card is still up from earlier, and the button waits under it.
      await page.evaluate(() => { if (typeof PH_PEEK !== 'undefined') PH_PEEK.hide(); });
      await page.click('#ai-toggle');
      await page.waitForTimeout(400);
      const ask = await rectOf(page, '#ai-panel');
      const vp = page.viewportSize();
      ok(!!ask && ask.l === 0 && ask.t === 0 && Math.round(ask.w) === vp.width && Math.round(ask.h) === vp.height,
        'phone: Ask opens as a full-screen sheet', JSON.stringify(ask));
      await checkTargets(page, 'Ask');
      await page.click('#ai-close');
      await page.waitForTimeout(300);
    }
    ok(errors.length === 0, 'no page errors (phone)', errors.join(' | '));
    await page.close();
  }

  // Phone: tapping a ward shows its figure; one tap opens the sheet, one puts it back.
  {
    const { page, errors } = await open(browser, 'index.html?ov=crime_rate_per_1000&t=1', PHONE);
    const m = await page.evaluate(() => { const r = document.getElementById('map').getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; });
    await page.touchscreen.tap(m[0], m[1]);
    await page.waitForTimeout(1200);
    ok(await page.evaluate(() => /\d/.test(document.getElementById('ws-fig').innerText)), 'phone: tapped ward shows its figure');
    const t = async () => { const h = await page.evaluate(() => { const r = document.querySelector('.ws-title').getBoundingClientRect(); return [r.left + 20, r.top + 10]; }); await page.touchscreen.tap(h[0], h[1]); await page.waitForTimeout(600); return page.evaluate(() => PH_SHEET.state()); };
    const a = await t(), b2 = await t();
    ok(a === 0 && b2 === 1, 'phone: one tap opens the ward sheet, one tap puts it back', a + ',' + b2);
    // The grab handle drags (workplan 1.10): a real touch swipe on it, which
    // the browser would otherwise take as a scroll, moves the sheet with the
    // finger and settles it one state up or down; a tap on it still toggles.
    {
      const cdp = await page.context().newCDPSession(page);
      const grabAt = () => page.evaluate(() => { const g = document.getElementById('ws-grab'), r = g.getBoundingClientRect();
        return { x: r.left + r.width / 2, y: r.top + r.height / 2, ta: getComputedStyle(g).touchAction, inHead: !!g.closest('#ws-head') }; });
      const swipe = async dy => {
        const g = await grabAt();
        await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: g.x, y: g.y }] });
        for (let i = 1; i <= 8; i++) {
          await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: g.x, y: g.y + dy * i / 8 }] });
          await page.waitForTimeout(16);
        }
        const mid = await page.evaluate(() => Math.round(document.getElementById('ward-sheet').getBoundingClientRect().height));
        await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
        await page.waitForTimeout(600);
        return { mid, state: await page.evaluate(() => PH_SHEET.state()) };
      };
      const g0 = await grabAt();
      const h0 = await page.evaluate(() => Math.round(document.getElementById('ward-sheet').getBoundingClientRect().height));
      const up = await swipe(-200), down = await swipe(160);
      const g1 = await grabAt();
      await page.touchscreen.tap(g1.x, g1.y);
      await page.waitForTimeout(600);
      const tapped = await page.evaluate(() => PH_SHEET.state());
      ok(g0.ta === 'none' && g0.inHead && up.mid > h0 + 100 && up.state === 0 && down.state === 1 && tapped === 0,
        'phone: swiping the ward sheet\'s handle drags it open and closed, and a tap on it still toggles',
        JSON.stringify({ g0, h0, up, down, tapped }));
      await page.evaluate(() => PH_SHEET.setState(1));
      await page.waitForTimeout(400);
    }
    await checkAxe(page, 'phone ward sheet');
    // The open sheet stops at about 60% of the screen, a pin drops it to
    // peek with a note, and the comparison opens at peek and then as a table
    // with the areas as columns (workplan 2.10).
    {
      await page.evaluate(() => { showWard('Lansbury'); PH_SHEET.setState(0); });
      await page.waitForTimeout(700);
      const sh = await rectOf(page, '#ward-sheet');
      const vh = page.viewportSize().height;
      ok(!!sh && sh.h <= vh * 0.61, 'phone: the open ward sheet takes at most about 60% of the screen', JSON.stringify(sh));
      // The Ask button waits while the sheet is fully open, rather than
      // floating over the strip of map above it, and the sheet's own row
      // offers "Ask about this ward" instead (workplan 1.10).
      const askShown = () => page.evaluate(() => { const t = document.getElementById('ai-toggle');
        return !!t && getComputedStyle(t).display !== 'none' && t.getBoundingClientRect().width > 0; });
      const hasAsk = await page.evaluate(() => !!document.getElementById('ai-toggle'));
      if (hasAsk) {
        ok(!(await askShown()), 'phone: the Ask button is hidden while the ward sheet is fully open');
        const wa = await page.evaluate(() => { const b = document.getElementById('ws-ask'), r = b.getBoundingClientRect();
          return { shown: !b.hidden && r.width > 0, text: b.textContent.trim(), w: Math.round(r.width), h: Math.round(r.height) }; });
        ok(wa.shown && wa.text === 'Ask about this ward' && wa.h >= 44, 'phone: the open sheet offers "Ask about this ward"', JSON.stringify(wa));
      }
      const attr = await rectOf(page, '.leaflet-control-attribution');
      ok(!!attr && attr.b <= sh.t && !overlaps(attr, await rectOf(page, '#map-legend-strip')) && !overlaps(attr, await rectOf(page, '#ai-toggle')),
        'phone: the credits sit on the sheet\'s edge, one line, covered by nothing', JSON.stringify({ attr, sh }));
      // And at peek, where the sheet is as tall as its head, which wraps
      // with a long indicator name.
      await page.evaluate(() => { showWard('Stonebridge'); PH_SHEET.setState(1); });
      await page.waitForTimeout(900);
      const shP = await rectOf(page, '#ward-sheet'), attrP = await rectOf(page, '.leaflet-control-attribution');
      ok(!!attrP && !!shP && attrP.b <= shP.t + 1, 'phone: the credits clear the sheet at peek too', JSON.stringify({ attrP, shP }));
      if (hasAsk) {
        const askP = await rectOf(page, '#ai-toggle');
        ok((await askShown()) && !!askP && askP.b <= shP.t, 'phone: the Ask button is back above the sheet at peek', JSON.stringify({ askP, shP }));
        // "Ask about this ward" opens the panel with the ward named in the
        // box, ready to send, and sends nothing by itself.
        await page.evaluate(() => PH_SHEET.setState(0));
        await page.waitForTimeout(500);
        await page.tap('#ws-ask');
        await page.waitForTimeout(500);
        const asked = await page.evaluate(() => ({ open: document.getElementById('ai-panel').classList.contains('open'),
          box: (document.getElementById('ai-input') || {}).value || '', sent: document.querySelectorAll('#ai-log .ai-msg.ai-user, #ai-log .ai-user').length,
          state: PH_SHEET.state() }));
        ok(asked.open && asked.box === 'Tell me about Stonebridge' && !asked.sent && asked.state === 1,
          'phone: "Ask about this ward" opens Ask with the ward named, and the sheet drops to its head', JSON.stringify(asked));
        await page.click('#ai-close');
        await page.waitForTimeout(300);
      }
      await page.evaluate(() => { showWard('Lansbury'); PH_SHEET.setState(0); });
      await page.waitForTimeout(700);
      await checkTargets(page, 'the open ward sheet');
      const sheetHits = await hitSizes(page, '#ws-size, #ws-close, #ws-pin, #ws-profile');
      ok(sheetHits.length === 4 && sheetHits.every(x => x.w >= 44 && x.h >= 44), 'phone: the sheet\'s buttons have 44px touch targets', JSON.stringify(sheetHits));
      await page.click('#ws-pin');
      await page.waitForTimeout(600);
      const p1 = await page.evaluate(() => ({ state: PH_SHEET.state(), note: document.getElementById('ws-pinnote').hidden ? '' : document.getElementById('ws-pinnote').textContent }));
      ok(p1.state === 1 && p1.note === 'Pinned. Select another ward to add it.', 'phone: pinning drops the sheet to peek and says what to do next', JSON.stringify(p1));
      await page.evaluate(() => showWard('Stonebridge'));
      await page.waitForTimeout(500);
      ok(await page.evaluate(() => document.getElementById('ws-pinnote').hidden && getComputedStyle(document.getElementById('ws-pin')).display !== 'none'),
        'phone: the next ward opens at peek with its own Pin button');
      await page.click('#ws-pin');
      await page.waitForTimeout(500);
      await page.click('#ws-see-cmp');
      await page.waitForTimeout(800);
      const tb = await page.evaluate(() => {
        const bar = document.getElementById('cmp-bar');
        return { open: bar.classList.contains('open'), peek: bar.classList.contains('peek'),
          cols: [...bar.querySelectorAll('.cmp-table thead th')].map(t => t.textContent.trim()),
          rows: bar.querySelectorAll('.cmp-table tbody tr').length,
          cards: getComputedStyle(document.getElementById('cmp-cards')).display };
      });
      ok(tb.open && !tb.peek && tb.cols.length === 3 && /Lansbury/.test(tb.cols[1]) && /Stonebridge/.test(tb.cols[2]) && tb.rows >= 2 && tb.cards === 'none',
        'phone: See comparison opens a table with the two wards as columns', JSON.stringify(tb));
      ok((await rectOf(page, '#cmp-bar')).h <= vh * 0.61, 'phone: the comparison takes at most about 60% of the screen');
      await checkAxe(page, 'phone comparison table');
      await checkTargets(page, 'the comparison table');
      await page.click('#cmp-bar-less');
      await page.waitForTimeout(400);
      const pk = await page.evaluate(() => ({ peek: document.getElementById('cmp-bar').classList.contains('peek'),
        names: document.getElementById('cmp-peek').innerText.replace(/\s+/g, ' ').trim(),
        see: getComputedStyle(document.getElementById('cmp-bar-see')).display !== 'none',
        table: getComputedStyle(document.getElementById('cmp-tablewrap')).display }));
      ok(pk.peek && /Lansbury/.test(pk.names) && /Stonebridge/.test(pk.names) && pk.see && pk.table === 'none',
        'phone: the comparison peeks with only the names and See comparison', JSON.stringify(pk));
      await page.evaluate(() => { document.getElementById('cmp-bar-close').click(); PH_COMPARE.clear(); document.querySelector('.mode-btn[data-mode="explore"]').click(); });
      await page.waitForTimeout(400);
    }
    // GP practices are small dots with a 44px box to tap (workplan 2.10).
    {
      await page.evaluate(() => { PH_DRAWER.close(); const t = document.getElementById('tg'); if (!t.checked) t.click(); map.setView([51.515, -0.07], 16, { animate: false }); });
      await page.waitForTimeout(1500);
      const gp = await page.evaluate(() => [...document.querySelectorAll('.leaflet-marker-icon.mk-hit')].slice(0, 5).map(e => {
        const r = e.getBoundingClientRect(), d = e.querySelector('.mk').getBoundingClientRect();
        return [Math.round(r.width), Math.round(r.height), Math.round(d.width)];
      }));
      ok(gp.length > 0 && gp.every(x => x[0] >= 44 && x[1] >= 44 && x[2] <= 12), 'phone: a GP practice is a small dot with a 44px touch target', JSON.stringify(gp));
    }
    ok(errors.length === 0, 'no page errors (phone ward)', errors.join(' | '));
    await page.close();
  }
  {
    const { page, errors } = await open(browser, 'index.html?ov=ft_93250&t=1', PHONE);
    const m = await page.evaluate(() => { const r = document.getElementById('map').getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; });
    await page.touchscreen.tap(m[0], m[1]);
    await page.waitForTimeout(1200);
    ok(await page.evaluate(() => { const e = document.getElementById('area-peek'); return !e.hidden && /\d/.test(e.innerText); }), 'phone: tapped MSOA shows its card');
    ok(errors.length === 0, 'no page errors (phone MSOA)', errors.join(' | '));
    await page.close();
  }

  // A phone on its side (workplan 2.10): the sheet and the tour stand down
  // the right of the map, and the key, the credits, the level switch and the
  // Ask button move left of them rather than under them.
  {
    const LAND = { width: 844, height: 390, isMobile: true, hasTouch: true };
    const { page, errors } = await open(browser, 'index.html?ov=crime_rate_per_1000&t=1', LAND);
    const clear = async panel => {
      const p = await rectOf(page, panel), hit = [];
      for (const s of ['#map-legend-strip', '.leaflet-control-attribution', '#ai-toggle', '#dl-badge', '.leaflet-control-zoom'])
        if (overlaps(p, await rectOf(page, s))) hit.push(s);
      return hit;
    };
    await page.evaluate(() => showWard('Lansbury'));
    await page.waitForTimeout(800);
    const mp = await rectOf(page, '#map');
    let sh = await rectOf(page, '#ward-sheet');
    ok(!!sh && sh.r >= mp.r - 10 && sh.w <= 380 && sh.t <= mp.t + 10, 'landscape: the ward sheet stands at the right of the map', JSON.stringify({ sh, mp }));
    // The drawer on its side: one scrolling column still leaves the open tab
    // at least 250px under the search row and the tabs (workplan 1.10).
    {
      await page.evaluate(() => PH_DRAWER.open());
      await page.waitForTimeout(500);
      const d = await page.evaluate(() => {
        const r = s => document.querySelector(s).getBoundingClientRect();
        const pane = document.querySelector('#sidebar > .tab-pane.active');
        return { room: Math.round(r('#sidebar').bottom - r('#sidebar > .sb-tabs').bottom), own: pane.scrollHeight - pane.clientHeight };
      });
      ok(d.room >= 250 && d.own <= 1, 'landscape: the drawer gives the open tab at least 250px, in one scrolling column', JSON.stringify(d));
      await page.evaluate(() => PH_DRAWER.close());
      await page.waitForTimeout(400);
    }
    let hit = await clear('#ward-sheet');
    ok(!hit.length, 'landscape: nothing on the map sits under the ward sheet', hit.join(', '));
    await page.evaluate(() => PH_SHEET.setState(0));
    await page.waitForTimeout(600);
    sh = await rectOf(page, '#ward-sheet');
    ok(sh.b >= mp.b - 10 && sh.t <= mp.t + 10, 'landscape: the open sheet runs the full height of the map', JSON.stringify(sh));
    await checkTargets(page, 'landscape with the sheet open');
    await page.evaluate(() => { deselectWard(); PH_TOUR.start(); });
    await page.waitForTimeout(800);
    const tr = await rectOf(page, '#tour-strip');
    // A little wider than the ward panel, and up into the row above the
    // map, so its narration has room (see the tour checks above).
    ok(!!tr && tr.r >= mp.r - 10 && tr.h >= mp.h - 20 && tr.w <= 460 && tr.w <= mp.w * 0.55, 'landscape: the tour stands down the right of the map', JSON.stringify({ tr, mp }));
    hit = await clear('#tour-strip');
    ok(!hit.length, 'landscape: nothing on the map sits under the tour', hit.join(', '));
    await page.evaluate(() => PH_TOUR.end());
    await page.waitForTimeout(600);
    ok(await page.evaluate(() => document.getElementById('map-wrap').style.getPropertyValue('--side-r') === '0px'),
      'landscape: the furniture goes back when the panels close');
    if (await page.evaluate(() => !!document.getElementById('ai-toggle'))) {
      await page.click('#ai-toggle');
      await page.waitForTimeout(400);
      const ask = await rectOf(page, '#ai-panel');
      ok(!!ask && ask.t === 0 && Math.round(ask.w) === 844 && Math.round(ask.h) === 390, 'landscape: Ask opens as a full-screen sheet', JSON.stringify(ask));
      await page.click('#ai-close');
    }
    ok(errors.length === 0, 'no page errors (landscape)', errors.join(' | '));
    await page.close();
  }

  // Failure and loading states (workplan 2.8). Files are blocked with
  // page.route; each case has to end in a plain message, never an endless
  // loader, a blank map or a developer's note.
  const DASH = /[\u2013\u2014]/;
  const noteText = page => page.evaluate(() => document.getElementById('load-note').hidden ? '' : document.getElementById('load-note-text').textContent);
  {
    // The core half of the ward figures blocked: the whole file stands in, so
    // the map opens with every figure and nothing to apologise for. Then the
    // LSOA figures blocked: one retry, the shared message, and Try again.
    const browserCtx = await browser.newContext({ viewport: DESK });
    const page = await browserCtx.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    let blockLsoa = true;
    await page.route(/data\/ward_core\.json/, r => r.abort());
    await page.route(/lsoa_data\.json/, r => blockLsoa ? r.abort() : r.continue());
    await page.route(/workers\.dev/, () => {});   // the Ask service never answers
    await page.goto(BASE + '/index.html?t=1');
    await page.waitForTimeout(SETTLE);
    const ld = await page.evaluate(() => ({
      name: document.querySelector('#ldr .ldr-name').textContent,
      line: document.querySelector('#ldr .ldr-line').textContent,
      gone: document.getElementById('ldr').classList.contains('hidden'),
      wards: Object.keys(WARD_DATA_BY_CODE).length,
    }));
    ok(ld.name === 'PopHealth Map' && /^Explore health, deprivation and local services/.test(ld.line) && !DASH.test(ld.line),
      'loading screen: the site name and one sentence', JSON.stringify(ld));
    ok(ld.gone && ld.wards > 600 && (await noteText(page)) === '',
      'ward_core.json blocked: the whole ward file stands in and nothing is reported', JSON.stringify(ld));
    await setOv(page, 'census_bad_health_pct');
    await page.waitForFunction(() => !document.getElementById('load-note').hidden, null, { timeout: 20000 }).catch(() => {});
    const t = await noteText(page);
    ok(/the LSOA figures could not be loaded/.test(t) && /try again/.test(t) && !DASH.test(t),
      'lsoa_data.json blocked: a plain message within 20 seconds', t);
    await checkAxe(page, 'a file that could not be loaded');
    blockLsoa = false;
    await page.click('#load-note-retry');
    await page.waitForFunction(() => Object.keys(LSOA_DATA).length > 1000, null, { timeout: 20000 }).catch(() => {});
    ok(await page.evaluate(() => Object.keys(LSOA_DATA).length > 1000 && document.getElementById('load-note').hidden),
      'Try again loads the LSOA figures and puts the message away');
    // An Ask request with no answer is given up after 30 seconds.
    await page.click('#ai-toggle');
    await page.waitForTimeout(300);
    await page.clock.install();
    await page.fill('#ai-input', 'Which ward is most deprived?');
    await page.keyboard.press('Enter');
    await page.waitForTimeout(800);
    await page.clock.fastForward(31000);
    await page.waitForTimeout(800);
    const ask = await page.evaluate(() => { const m = document.querySelector('.ai-msg.ai-error'); return m ? m.textContent : ''; });
    ok(/took too long/.test(ask) && !DASH.test(ask), 'an Ask request is given up after 30 seconds with a plain message', ask);
    ok(errors.length === 0, 'no page errors (blocked files)', errors.join(' | '));
    await browserCtx.close();
  }
  {
    // Both ward files blocked: the outlines, and a banner with Try again.
    const page = await browser.newPage({ viewport: DESK });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route(/ward_(core|data)\.json/, r => r.abort());
    await page.goto(BASE + '/index.html?t=1');
    await page.waitForFunction(() => !document.getElementById('load-note').hidden, null, { timeout: 20000 }).catch(() => {});
    const t = await noteText(page);
    ok(/could not be loaded, so only the area outlines are showing/.test(t) && !DASH.test(t) &&
       await page.evaluate(() => document.getElementById('ldr').classList.contains('fading') && wLyr.getLayers().length > 600 &&
         !document.getElementById('load-note-retry').hidden),
      'ward figures blocked: outlines drawn, with a banner and Try again', t);
    ok(errors.length === 0, 'no page errors (ward figures blocked)', errors.join(' | '));
    await page.close();
  }
  {
    // The same failure on a phone: the banner sits clear of the level badge
    // and the zoom buttons.
    const { isMobile, hasTouch, ...viewport } = PHONE;
    const page = await browser.newPage({ viewport, isMobile, hasTouch });
    await page.route(/ward_(core|data)\.json/, r => r.abort());
    await page.goto(BASE + '/index.html?t=1');
    await page.waitForFunction(() => !document.getElementById('load-note').hidden, null, { timeout: 20000 }).catch(() => {});
    await page.waitForTimeout(500);
    const note = await rectOf(page, '#load-note'), badge = await rectOf(page, '#dl-badge'), zoom = await rectOf(page, '.leaflet-control-zoom');
    ok(!!note && (!badge || !overlaps(note, badge)) && (!zoom || !overlaps(note, zoom)),
      'phone: the failure banner is clear of the level badge and the zoom buttons', JSON.stringify({ note, badge, zoom }));
    await page.close();
  }
  {
    // wards.js blocked: the application cannot start, so the loading screen
    // says so and offers a reload, rather than breathing for ever.
    const page = await browser.newPage({ viewport: DESK });
    await page.route(/data\/map\/wards\.js/, r => r.abort());
    await page.goto(BASE + '/index.html?t=1');
    await page.waitForFunction(() => !document.getElementById('ldr-msg').hidden, null, { timeout: 20000 }).catch(() => {});
    const m = await page.evaluate(() => ({ text: document.getElementById('ldr-msg').hidden ? '' : document.getElementById('ldr-msg-text').textContent,
      btn: document.getElementById('ldr-reload').textContent, focus: document.activeElement && document.activeElement.id }));
    ok(/failed to load/.test(m.text) && /reload the page/.test(m.text) && !DASH.test(m.text) && m.btn === 'Reload the page' && m.focus === 'ldr-reload',
      'wards.js blocked: the loading screen says so within 20 seconds and offers a reload', JSON.stringify(m));
    await page.close();
  }

  // Phone: with an LA focused the map still drags. The focus used to hold
  // the map to the LA and a small margin, which on a phone screen left it
  // nowhere to go, so it looked locked whichever indicator was chosen.
  {
    const { page, errors } = await open(browser, 'index.html?t=1', PHONE);
    const cdp = await page.context().newCDPSession(page);
    const swipe = async () => {
      const st = await page.evaluate(() => { const r = document.getElementById('map').getBoundingClientRect(); const c = map.getCenter();
        return { x: r.left + r.width / 2, y: r.top + r.height * 0.35, c: [c.lat, c.lng] }; });
      await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: st.x, y: st.y }] });
      for (let i = 1; i <= 8; i++) await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: st.x - 12 * i, y: st.y - 8 * i }] });
      await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
      await page.waitForTimeout(700);
      const c2 = await page.evaluate(() => { const c = map.getCenter(); return [c.lat, c.lng]; });
      return Math.abs(c2[0] - st.c[0]) + Math.abs(c2[1] - st.c[1]) > 1e-5;
    };
    await page.evaluate(() => { const s = document.getElementById('borough-focus'); s.value = s.options[5].value; s.dispatchEvent(new Event('change')); });
    await page.waitForTimeout(3000);
    ok(await swipe(), 'phone: the map drags with an LA focused');
    await setOv(page, 'census_bad_health_pct');
    await page.waitForTimeout(3000);
    ok(await swipe(), 'phone: the map still drags after choosing an indicator with an LA focused');
    ok(errors.length === 0, 'no page errors (phone LA focus)', errors.join(' | '));
    await page.close();
  }

  // Compare drives the map: the map shows the size of area being compared,
  // and an LA chosen from the list is brought into view.
  {
    const { page, errors } = await open(browser, 'index.html?t=1&ov=census_bad_health_pct', DESK);
    await page.click('.mode-btn[data-mode="compare"]');
    await page.waitForTimeout(1200);
    const lv = [];
    for (const k of ['lsoa', 'borough', 'icb', 'ward']) {
      await page.click('#cmp-levels [data-cmplvl="' + k + '"]');
      await page.waitForTimeout(2000);
      lv.push(k + ':' + await page.evaluate(() => _activeLevel()));
    }
    ok(lv.join() === 'lsoa:lsoa,borough:borough,icb:borough,ward:ward', 'Compare switches the map to the size of area compared', lv.join());
    ok(await page.evaluate(() => !document.getElementById('tlsoa').checked), 'LSOA boundaries Compare switched on go off when it leaves LSOAs');
    await page.click('#cmp-levels [data-cmplvl="borough"]');
    await page.waitForTimeout(1500);
    const before = await page.evaluate(() => map.getZoom());
    const v = await page.evaluate(() => [...document.getElementById('cmp-add-area').options].map(o => o.value).filter(Boolean)[0]);
    await page.selectOption('#cmp-add-area', v);
    await page.waitForTimeout(2000);
    const fit = await page.evaluate(v => { let inView = false; const b = map.getBounds();
      BOROUGH_GJ.features.forEach(f => { if (f.properties.name === v) inView = b.contains(L.geoJSON(f).getBounds().getCenter()); });
      return { z: map.getZoom(), inView }; }, v);
    ok(fit.z > before && fit.inView, 'an LA chosen in Compare is brought into view', JSON.stringify({ before, ...fit }));
    // The bar above a tool is only the way back, so the tool's name appears
    // once, in the heading at the top of its pane.
    const names = {};
    for (const m of ['query', 'compare', 'report', 'csv', 'directory']) {
      await page.click('.mode-btn[data-mode="' + m + '"]');
      await page.waitForTimeout(600);
      names[m] = await page.evaluate(() => {
        const pane = document.querySelector('.tab-pane.active'), h = pane.querySelector('h2');
        const top = document.getElementById('sb-modebar').innerText.trim() + ' | ' + (h ? h.textContent.trim() : '');
        const n = h ? h.textContent.trim() : '';
        return { top, once: !!n && (document.getElementById('sb-modebar').innerText.split(n).length === 1) };
      });
    }
    ok(Object.values(names).every(v => v.once && /^Back to Explore \| /.test(v.top)) &&
       names.report.top === 'Back to Explore | Custom area' && names.csv.top === 'Back to Explore | Data export',
      'each tool is named once at the top of the panel', JSON.stringify(names));
    ok(await page.evaluate(() => !/sign-in/.test(document.querySelector('#ldr .ldr-line').textContent)),
      'the loading screen line has no sign-in phrase');
    // P77: a visible Feedback link in the footer, a pre-filled email.
    {
      const fb = await page.evaluate(() => { const a = document.getElementById('sbar-feedback');
        return a ? { t: a.textContent, href: decodeURIComponent(a.getAttribute('href')) } : null; });
      ok(!!fb && fb.t === 'Feedback' && /^mailto:contact@pophealth\.uk\?subject=Feedback on PopHealth Map&body=1\. What is your role/.test(fb.href) &&
         /\n3\. What would make it more useful to you\?/.test(fb.href),
        'the footer has a Feedback link, an email with three questions', JSON.stringify(fb));
    }
    // P61: the footer's version is the first released one in CHANGELOG.md,
    // and the data date is written in full.
    {
      const rel = (fs.readFileSync(path.join(ROOT, 'CHANGELOG.md'), 'utf8').match(/^## (\d+\.\d+\.\d+)\b/m) || [])[1];
      const ft = await page.evaluate(() => ({ ver: document.getElementById('site-ver').textContent.trim(),
        date: document.getElementById('data-date').textContent.trim() }));
      ok(!!rel && ft.ver === 'Version ' + rel, 'the footer version matches the latest release in CHANGELOG.md', ft.ver + ' / ' + rel);
      ok(!ft.date || /^Data updated \d{1,2} (January|February|March|April|May|June|July|August|September|October|November|December) \d{4}$/.test(ft.date),
        'the footer writes the data date in full', ft.date);
    }
    ok(errors.length === 0, 'no page errors (Compare and the map)', errors.join(' | '));
    await page.close();
  }

  // Copy link round trips: a link reopens the view it was copied from, with
  // the custom area after a reload, Compare's level, pins and indicators, the
  // charities layer, the PCN footprints and chosen network, and an open LSOA
  // profile. Each compares a snapshot of the state before and after.
  {
    const snap = page => page.evaluate(() => {
      _writeUrlState();
      const q = new URLSearchParams(location.search); q.delete('z'); q.delete('c');
      return {
        href: location.href, q: [...q.entries()].map(e => e.join('=')).sort().join('&'),
        tool: _urlTool(), lvl: currentDataLevel, ov: curOv,
        area: PH_REPORT.state(), cmp: _urlTool() === 'compare' ? PH_COMPARE.state() : null,
        ls: selLSOA, pcn: document.getElementById('pcn-focus').value,
        layers: _URL_LAYERS.filter(id => { const e = document.getElementById(id); return e && e.checked; }).join(','),
      };
    });
    const strip = s => { const o = Object.assign({}, s); delete o.href; return JSON.stringify(o); };
    const reopen = async (href) => {
      const p = await browser.newPage({ viewport: { width: DESK.width, height: DESK.height } });
      const errs = [];
      p.on('pageerror', e => errs.push(e.message));
      await p.goto(href);
      await p.waitForTimeout(SETTLE + 6000);
      return { p, errs };
    };

    // A custom area link keeps its area in the address bar, so a reload has it.
    const { page, errors } = await open(browser, 'index.html?mode=report&a=004243.004244&an=Test&t=1', DESK);
    await page.waitForTimeout(3000);
    const a1 = await snap(page);
    await page.reload();
    await page.waitForTimeout(SETTLE + 3000);
    const a2 = await snap(page);
    ok(a1.area.codes.length === 2 && a1.area.name === 'Test' && /[?&]a=004243\.004244/.test(a1.href) && /[?&]an=Test/.test(a1.href),
      'a custom area link keeps the area in the address bar once it is drawn', a1.href);
    ok(strip(a1) === strip(a2), 'a custom area link survives a reload', strip(a1) + ' / ' + strip(a2));

    // Compare at LSOA level, two pins and an extra indicator.
    await page.goto(BASE + '/index.html?t=1');
    await page.waitForTimeout(SETTLE);
    await page.click('.mode-btn[data-mode="compare"]');
    await page.waitForTimeout(800);
    await page.evaluate(() => PH_COMPARE.setLevel('lsoa'));
    await page.waitForFunction(() => typeof LSOA_IMD !== 'undefined' && LSOA_IMD && LSOA_IMD.features, null, { timeout: 30000 }).catch(() => {});
    await page.evaluate(() => { PH_COMPARE.pinFromLsoa({ code: 'E01004243' }); PH_COMPARE.pinFromLsoa({ code: 'E01004244' }); });
    await page.evaluate(() => document.querySelector('#cmp-metrics [data-metric="3"]').click());
    await page.waitForTimeout(800);
    const c1 = await snap(page);
    const c = await reopen(c1.href);
    const c2 = await snap(c.p);
    const cOpen = await c.p.evaluate(() => document.getElementById('cmp-bar').classList.contains('open') &&
      !/Nothing is pinned/.test(document.getElementById('cmp-pins').innerText));
    ok(c1.cmp.pins.length === 2 && c1.cmp.metrics.length === 4 && strip(c1) === strip(c2) && cOpen,
      'a Compare link reopens with its level, pins and indicators', strip(c1) + ' / ' + strip(c2));
    ok(c.errs.length === 0, 'no page errors (Compare link)', c.errs.join(' | '));
    await c.p.close();

    // Ward pins at the default level and indicators.
    await page.evaluate(() => PH_COMPARE.setLevel('ward'));
    await page.waitForTimeout(1500);
    await page.evaluate(() => wLyr.getLayers().slice(0, 2).forEach(l => l.fire('click')));
    await page.waitForTimeout(800);
    const w1 = await snap(page);
    const w = await reopen(w1.href);
    const w2 = await snap(w.p);
    ok(w1.cmp.pins.length === 2 && strip(w1) === strip(w2), 'a Compare link with pinned wards reopens with them', strip(w1) + ' / ' + strip(w2));
    await w.p.close();

    // The charities layer, the PCN footprints and chosen network, and an LSOA profile.
    await page.click('.mode-btn[data-mode="explore"]');
    await page.waitForTimeout(800);
    await page.evaluate(() => {
      ['vcse-tab-toggle', 'tpcn'].forEach(id => { const e = document.getElementById(id); e.checked = true; e.dispatchEvent(new Event('change')); });
      const s = document.getElementById('pcn-focus'); s.value = s.options[3].value; s.dispatchEvent(new Event('change'));
    });
    await page.waitForTimeout(2500);
    await page.evaluate(() => _searchPickResult({ type: 'lsoa', _code: 'E01000907', label: 'Camden 001A', sub: '' }));
    await page.waitForTimeout(4000);
    const l1 = await snap(page);
    const l = await reopen(l1.href);
    const l2 = await snap(l.p);
    const vc = await l.p.evaluate(() => !!(vcseCluster && vcseCluster._map));
    ok(/vcse-tab-toggle/.test(l1.layers) && /tpcn/.test(l1.layers) && l1.pcn && l1.ls === 'E01000907' && strip(l1) === strip(l2) && vc,
      'a link reopens the charities layer, the PCN chosen and the LSOA profile open', strip(l1) + ' / ' + strip(l2));
    ok(l.errs.length === 0, 'no page errors (layers link)', l.errs.join(' | '));
    await l.p.close();

    // A link from before the charities layer was named right still turns it on.
    const old = await reopen(BASE + '/index.html?L=tv&t=1');
    ok(await old.p.evaluate(() => document.getElementById('vcse-tab-toggle').checked), 'an older link with L=tv turns the charities layer on');
    await old.p.close();

    // A level the indicator has no figures at is not applied, so the key
    // never describes colours that are not drawn.
    await page.goto(BASE + '/index.html?ov=crime_rate_per_1000&lvl=lsoa&t=1');
    await page.waitForTimeout(SETTLE);
    const lv = await page.evaluate(() => ({ lvl: currentDataLevel, has: ovHasLevel('crime_rate_per_1000', 'lsoa'), land: _ovLandingLevel('crime_rate_per_1000') }));
    ok(!lv.has && lv.lvl === lv.land, 'a link level the indicator has no figures at falls back to where it opens', JSON.stringify(lv));
    ok(await page.evaluate(() => /comparison or custom area/.test(document.getElementById('tb-link').title)),
      'the Copy link tooltip names what the link carries');
    ok(errors.length === 0, 'no page errors (Copy link round trips)', errors.join(' | '));
    await page.close();
  }

  // The header search finds charities, pharmacies and dental practices on a
  // fresh page (their files are lazy, and the index used to be built before
  // they arrived and kept).
  {
    const { page, errors } = await open(browser, 'index.html?t=1', DESK);
    await page.click('#hdr-search-input');
    const find = async (q, type) => {
      await page.fill('#hdr-search-input', q);
      return page.waitForFunction(t => [...document.querySelectorAll('#hdr-search-results .sr-row .sr-type')]
        .some(e => e.textContent === t), type, { timeout: 45000 }).then(() => true, () => false);
    };
    ok(await find('Boots', 'pharmacy'), 'search: a fresh page finds a pharmacy (Boots)');
    ok(await find('Hackney Play Association', 'charity'), 'search: a fresh page finds a charity (Hackney Play Association)');
    ok(await find('Dental', 'dental'), 'search: a fresh page finds a dental practice');
    ok(errors.length === 0, 'no page errors (search lazy layers)', errors.join(' | '));
    await page.close();
  }

  // Hospitals: the checked list, the A&E line and flag, and no empty Trust row.
  {
    const { page, errors } = await open(browser, 'index.html?t=1', DESK);
    const data = await page.evaluate(() => ({
      n: HOSP.length, reg: HOSP.filter(d => d.t === 'NHS trust site').length,
      gone: HOSP.filter(d => /Samaritan|Barts Hospital at Home|^Private Hospitals$|Italian Hospital/.test(d.n)).map(d => d.n),
      western: HOSP.some(d => d.n === 'Western Eye Hospital' && d.a === 'NW1 5QH'),
      ae: HOSP.filter(d => d.ae === true).length,
      rl: (HOSP.find(d => d.n === 'Royal London Hospital') || {}).ae,
      guys: (HOSP.find(d => d.n === "Guy's Hospital") || {}).ae,
      qm: HOSP.filter(d => /^Queen Mary's Hospital, (Roehampton|Sidcup)$/.test(d.n)).length,
      note: (document.getElementById('hosp-ae-note') || {}).title || '',
    }));
    ok(data.reg === 0 && data.gone.length === 0 && data.western && data.qm === 2,
      'hospitals: typed from the checked list, closed sites and services left out, names told apart', JSON.stringify(data));
    ok(data.ae >= 20 && data.rl === true && data.guys === false, 'hospitals: the major hospitals with a 24-hour A&E are flagged', JSON.stringify(data));
    ok(/^Not every hospital has an A&E\. In an emergency call 999, and for urgent help that is not an emergency use NHS 111 online at 111\.nhs\.uk or call 111\.$/.test(data.note.trim()),
      'hospitals: the Layers row\'s help button says not every hospital has an A&E', data.note);
    const pop = await page.evaluate(() => {
      let m = null;
      hL.eachLayer(l => { if (/Royal London Hospital</.test(l.getPopup().getContent())) m = l; });
      const div = document.createElement('div');
      div.innerHTML = m ? m.getPopup().getContent() : '';
      return { keys: [...div.querySelectorAll('.pop-key')].map(e => e.textContent),
        text: div.textContent };
    });
    ok(!pop.keys.includes('Trust') && pop.keys.includes('A&E') && /Has a 24-hour A&E/.test(pop.text) && /In an emergency call 999/.test(pop.text),
      'hospitals: a popup has its type, its A&E and the 999 line, and no empty Trust row', JSON.stringify(pop));
    await page.click('.mode-btn[data-mode="directory"]');
    await page.waitForFunction(() => document.querySelector('#dir-types [data-dtype="hospital"]'), null, { timeout: 30000 }).catch(() => {});
    await page.click('#dir-types [data-dtype="hospital"]');
    await page.waitForTimeout(400);
    const dir = await page.evaluate(() => ({ note: !!document.getElementById('dir-hosp-note'),
      chip: !!document.querySelector('#dir-types [data-dae]') }));
    await page.click('#dir-types [data-dae]');
    await page.waitForTimeout(400);
    const aeRows = await page.evaluate(() => [...document.querySelectorAll('#dir-list .dir-item .dir-sub')].map(e => e.textContent));
    ok(dir.note && dir.chip && aeRows.length === data.ae && aeRows.every(t => /24-hour A&E/.test(t)),
      'directory: the hospital list starts with the 999 line and can be cut to hospitals with an A&E', JSON.stringify({ dir, n: aeRows.length, first: aeRows[0] }));
    ok(errors.length === 0, 'no page errors (hospitals)', errors.join(' | '));
    await page.close();
  }

  // GP practices, pharmacies and dentists a resident can use: no schemes,
  // hubs or offices among the GP practices, no appliance or online-only
  // pharmacies, and the dental layer says what an NHS contract means.
  {
    const { page, errors } = await open(browser, 'index.html?t=1', DESK);
    await page.evaluate(() => Promise.all([ensureLayerData('pharmacy'), ensureLayerData('dental')]));
    const s = await page.evaluate(() => ({
      gpBad: GPS.filter(d => /special allocation|\bSAS\b|PCN Ea Hub|federat|covid/i.test(d.n)).map(d => d.n),
      homeless: (GPS.find(d => d.code === 'E87772') || {}).kind || '',
      phBad: pharmData.filter(d => /fittleworth|online|chemist 2 home/i.test(d.name)).map(d => d.name),
      prison: dentalData.filter(d => /\bHMP\b/i.test(d.name)).map(d => d.name),
      note: (document.getElementById('dental-note') || {}).title || '',
    }));
    ok(!s.gpBad.length && s.homeless === 'Practice for people who are homeless',
      'GP practices: no schemes, hubs or offices, and the practices for people who are homeless are labelled', JSON.stringify(s));
    ok(!s.phBad.length && !s.prison.length, 'pharmacies and dentists: no appliance or online-only suppliers, and no prisons', JSON.stringify(s));
    ok(/A contract does not mean a practice is taking new NHS patients/.test(s.note), 'dental: the Layers row\'s help button says what an NHS contract means', s.note);
    ok(errors.length === 0, 'no page errors (service layers)', errors.join(' | '));
    await page.close();
  }

  // The LSOA report waits for the pharmacies, dental practices and charities,
  // as the ward report does, rather than counting none on a fresh page.
  {
    const { page, errors } = await open(browser, 'index.html?t=1', DESK);
    const [rep] = await Promise.all([
      page.context().waitForEvent('page'),
      page.evaluate(() => { generateLSOAReport('E01004243'); }),
    ]);
    const listed = await rep.waitForFunction(() => /Britannia Pharmacy/i.test(document.body.innerText), null, { timeout: 60000 })
      .then(() => true, () => false);
    ok(listed, 'LSOA report: a fresh page\'s report for E01004243 lists Britannia Pharmacy');
    await rep.close();
    ok(errors.length === 0, 'no page errors (LSOA report)', errors.join(' | '));
    await page.close();
  }

  /* Review items B2, B6, B7, H1, H8 and H10: one additivity rule, the
     highest and lowest lists, the blank-area notice, Local Health LA
     estimates, small wards in reports, and LSOA deprivation in Compare. */
  {
    const { page, errors } = await open(browser, 'index.html?t=1', DESK);
    // B2: Compare's LA figure is the map's, and so is Ask's, for every key.
    const add = await page.evaluate(async () => {
      PH_COMPARE.setLevel('borough');
      await new Promise(r => setTimeout(r, 800));
      const code = { 'Tower Hamlets': 'E09000030', 'Newham': 'E09000025' };
      const cmp = Object.keys(code).map(n => [n, PH_COMPARE.figures(n).crime_total, parseFloat(boroughOvValue(code[n], 'crime_total'))]);
      const areas = PH_ASSISTANT.areasFor('la'), bad = [];
      Object.values(areas).forEach(a => Object.keys(a.indicators).forEach(k => {
        const m = parseFloat(boroughOvValue(a.lad_code, k));
        if (!isNaN(m) && Math.abs(m - a.indicators[k]) > 0.006) bad.push(a.name + ' ' + k + ' ' + a.indicators[k] + ' vs ' + m);
      }));
      PH_COMPARE.setLevel('ward');
      return { cmp, bad: bad.slice(0, 5), nBad: bad.length, n: Object.keys(areas).length,
        sums: ['crime_total', 'crime_violence_12mo', 'claimant_count', 'census_population'].every(laSums) && !laSums('census_bad_health_pct') };
    });
    ok(add.cmp.every(([, c, m]) => c > 1000 && Math.abs(c - m) < 0.01), 'Compare\'s LA crime total is the map\'s figure (summed, not averaged)', JSON.stringify(add.cmp));
    ok(add.n >= 33 && add.nBad === 0, 'every Ask LA figure matches the map\'s', add.nBad + ': ' + add.bad.join(' | '));
    ok(add.sums, 'one additivity rule: crime counts and claimant_count are summed, rates are not');
    // H10: an LSOA's deprivation chip reads its own decile.
    const lsoa = await page.evaluate(async () => {
      PH_COMPARE.setLevel('lsoa');
      await new Promise(r => setTimeout(r, 800));
      const f = PH_COMPARE.figures('E01004243');
      PH_COMPARE.setLevel('ward');
      return { dec: f.imd_decile_mean, own: (LSOA_DATA['E01004243'] || {}).imd_decile };
    });
    ok(lsoa.own != null && lsoa.dec === lsoa.own, 'Compare at LSOA level reads the LSOA\'s own deprivation decile', JSON.stringify(lsoa));
    // B6: lists of buttons, small City wards left out with a note, ties said.
    await setOv(page, 'census_bad_health_pct');
    await page.waitForTimeout(1500);
    await page.click('#dl-seg [data-dl="ward"]');
    await page.waitForTimeout(2500);
    const rk = await page.evaluate(() => ({
      buttons: document.querySelectorAll('#rank-best ol.rank-list > li > button.rank-row[type="button"]').length,
      ids: [...document.querySelectorAll('#rank-best .rank-row, #rank-worst .rank-row')].map(b => b.dataset.id),
      hdr: document.getElementById('rank-best-hdr').textContent, notes: document.getElementById('rank-notes').textContent }));
    const smallIds = await page.evaluate(ids => ids.filter(id => wardIsSmall(WARD_DATA[id])), rk.ids);
    ok(rk.buttons === 10 && rk.hdr === 'Best 10 (lowest %)', 'the rank rows are buttons in an ordered list, and the header carries the unit', JSON.stringify(rk).slice(0, 200));
    ok(!smallIds.length && /City of London wards with fewer than 1,000 residents are left out/.test(rk.notes),
      'wards under 1,000 residents are left out of a percentage ranking, and the list says so', smallIds.join(', ') + ' | ' + rk.notes);
    await setOv(page, 'core20_lsoa_count');
    await page.waitForTimeout(2000);
    const cnt = await page.evaluate(() => [document.getElementById('rank-best-hdr').textContent, document.getElementById('rank-notes').textContent]);
    ok(/^Highest 10/.test(cnt[0]) && /wards in London have none, so this list shows ten of them in alphabetical order\./.test(cnt[1]),
      'a count ranks as Highest and Lowest, and a tie at zero is said', cnt.join(' / '));
    const clickRow = await page.evaluate(async () => {
      const b = document.querySelector('#rank-best .rank-row'); b.focus(); b.click();
      await new Promise(r => setTimeout(r, 800));
      return { focused: document.activeElement === b || document.activeElement.tagName !== 'BODY', ward: (document.getElementById('ws-name') || {}).textContent, id: b.dataset.id };
    });
    ok(clickRow.ward === clickRow.id, 'selecting a rank row still opens that ward', JSON.stringify(clickRow));
    // B7: the notice and the hover card give the reason the data shows.
    await setOv(page, 'ft_93227');
    await page.waitForTimeout(3000);
    const b7 = await page.evaluate(() => ({
      text: document.getElementById('mls-note').textContent,
      redrawn: Object.keys(MSOA_DATA).filter(msoaRedrawn2021).length,
      card: msoaNoFigure(Object.keys(MSOA_DATA).filter(msoaRedrawn2021)[0], 'ft_93227'),
      other: msoaNoFigure('E02000001', 'census_bad_health_pct') }));
    ok(/^Areas left blank have no figure\. OHID’s figures for these areas leave out the MSOAs that were redrawn in 2021/.test(b7.text) && b7.redrawn === 39,
      'the blank-area notice names the 39 MSOAs redrawn in 2021 for the hospital admissions series', JSON.stringify(b7));
    ok(/^No figure: this MSOA was redrawn in 2021/.test(b7.card) && b7.other === 'No figure', 'a blank MSOA\'s card says why, or "No figure"', JSON.stringify(b7));
    await setOv(page, 'ft_93105');
    await page.waitForTimeout(1500);
    ok(/^Areas left without colour have no figure\. OHID, the Office for Health Improvement and Disparities, withholds/.test(
      await page.evaluate(() => document.getElementById('mls-note').textContent)), 'other blanks are described as withheld');
    // H1: an LA map of a Local Health indicator says it is our estimate, and
    // its LA list leaves out the City.
    await setOv(page, 'ft_932831');
    await page.waitForTimeout(1500);
    await page.click('#dl-seg [data-dl="borough"]');
    await page.waitForTimeout(2500);
    const h1 = await page.evaluate(() => ({ est: (document.getElementById('sc-la-est') || {}).textContent || '',
      ids: [...document.querySelectorAll('#rank-best .rank-row, #rank-worst .rank-row')].map(b => b.dataset.id),
      twin: indSameAs('ft_life_expectancy_male'), wardEst: laIsWardEstimate('ft_932831') }));
    // Once the refresh has fetched OHID's own LA figures, the key carries no
    // estimate label; until then it says so and leaves the City out.
    ok((h1.wardEst ? (/^These LA figures are our estimates from ward figures/.test(h1.est) && !h1.ids.includes('E09000001'))
                   : !/estimates from ward figures/.test(h1.est)) && !h1.twin,
      'Local Health LA figures are labelled as estimates from wards, without the City in the lists, and OHID\'s LA series is offered too', JSON.stringify(h1).slice(0, 300));
    await page.click('#dl-seg [data-dl="ward"]');
    await page.waitForTimeout(1500);
    // H8: the report does not rank or compare a ward its Summary does not.
    const h8 = await page.evaluate(() => {
      const one = wn => { let html = ''; _generateWardReport({ sink: h => { html = h; } }, wn);
        return new DOMParser().parseFromString(html, 'text/html'); };
      const b = one('Bishopsgate'), w = one('Walbrook');
      return { bCaps: [...b.querySelectorAll('.kpi-ctx-cap')].map(e => e.textContent),
        wSub: w.querySelector('.hero .sub').textContent, wPanel: (w.querySelector('.no-census') || {}).textContent || '',
        wHo: [...w.querySelectorAll('table.ho th')].map(t => t.textContent).join('|') };
    });
    ok(h8.bCaps.length && h8.bCaps.every(c => c === 'Too few residents to compare'), 'a ward under 1,000 residents is not placed among other wards in its report', h8.bCaps.join(' | '));
    ok(!/deprived/.test(h8.wSub) && /^There are no Census figures for Walbrook/.test(h8.wPanel) && !/City of London ward median/.test(h8.wHo),
      'Walbrook\'s report has no rank, one sentence for its missing Census figures and no repeated City median', JSON.stringify(h8));
    ok(errors.length === 0, 'no page errors (review items B2 to H10)', errors.join(' | '));
    await page.close();
  }

  // Ask against the real Worker code (worker/src/index.js, run here in Node
  // with fake bindings) and a scripted model: tables are drawn as tables
  // (H21), an answer cut off at the output limit says so and the next
  // question still works (H20), a refusal is answered plainly, and a long
  // session stays under the Worker's 64 KB limit, or starts again with a
  // plain message if refused as too long (H18, H17).
  {
    const os = require('os');
    const { pathToFileURL } = require('url');
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'phworker-'));
    fs.copyFileSync(path.join(ROOT, 'worker/src/index.js'), path.join(tmp, 'worker.mjs'));
    const W = await import(pathToFileURL(path.join(tmp, 'worker.mjs')).href);
    const store = new Map();
    const storage = { get: async k => store.get(k), put: async (k, v) => { if (typeof k === 'object') Object.entries(k).forEach(([a, b]) => store.set(a, b)); else store.set(k, v); },
      deleteAll: async () => store.clear(), getAlarm: async () => 1, setAlarm: async () => {} };
    const obj = new W.AskCounter({ storage }, {});
    const env = { ANTHROPIC_API_KEY: 'sk-test', ALLOWED_ORIGINS: new URL(BASE).origin,
      ASK_COUNTER: { idFromName: n => n, get: () => ({ fetch: (u, i) => obj.fetch(new Request(u, i)) }) },
      ASK_BURST: { limit: async () => ({ success: true }) } };
    let ids = 0, force413 = false;
    const seen = [];
    const realFetch = globalThis.fetch, realLog = console.log;
    // The Worker logs each call's usage; not wanted in this output.
    console.log = (...a) => { if (!(typeof a[0] === 'string' && a[0].startsWith('{"q"'))) realLog(...a); };
    // The scripted model: what it says depends on the reader's last words.
    globalThis.fetch = async (url, init) => {
      const body = JSON.parse(init.body), msgs = body.messages, last = msgs[msgs.length - 1];
      const said = last.content.map(b => b.text || '').join(' ');
      const reply = (stop, content) => new Response(JSON.stringify({ model: 'claude-sonnet-5', stop_reason: stop, content,
        usage: { input_tokens: 1, output_tokens: 1 } }), { status: 200, headers: { 'content-type': 'application/json' } });
      if (last.content[0].type === 'tool_result') return reply('end_turn', [{ type: 'text', text: 'Done with the figures.' }]);
      if (/COMPARE/.test(said)) return reply('end_turn', [{ type: 'text', text: 'Here they are.\n\n| Ward | IMD score |\n|---|---|\n| Notting Dale | 41.2 |\n| **Stonebridge** | 38.5 |\n\n- Higher means more deprived.' }]);
      if (/CUTTOOL/.test(said)) return reply('max_tokens', [{ type: 'text', text: 'Let me compare those.' }, { type: 'tool_use', id: 'cut' + (++ids), name: 'compare_areas', input: {} }]);
      if (/CUTTEXT/.test(said)) return reply('max_tokens', [{ type: 'text', text: 'Brent: IMD score 30.3\nBromley: IMD' }]);
      if (/REFUSE/.test(said)) return reply('refusal', []);
      if (/PROFILE/.test(said)) return reply('tool_use', [{ type: 'tool_use', id: 'p' + (++ids), name: 'get_area', input: { name: 'Lansbury', level: 'ward' } },
        { type: 'tool_use', id: 'p' + (++ids), name: 'get_area', input: { name: 'Stonebridge', level: 'ward' } }]);
      return reply('end_turn', [{ type: 'text', text: 'Fine.' }]);
    };
    const { page, errors } = await open(browser, 'index.html?t=1', DESK);
    await page.route(/workers\.dev/, async route => {
      const r = route.request();
      const cors = { 'access-control-allow-origin': '*', 'access-control-allow-headers': 'content-type', 'content-type': 'application/json' };
      if (r.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: cors });
      const post = r.postData() || '';
      let res;
      if (force413) { force413 = false; res = { status: 413, text: '{"error":"Payload too large"}' }; }
      else {
        const out = await W.default.fetch(new Request('https://worker.test/', { method: 'POST', body: post,
          headers: { 'content-type': 'application/json', Origin: new URL(BASE).origin, 'CF-Connecting-IP': '10.9.' + (seen.length >> 8) + '.' + (seen.length & 255) } }), env);
        res = { status: out.status, text: await out.text() };
      }
      seen.push({ size: post.length, status: res.status, n: (JSON.parse(post).messages || []).length });
      return route.fulfill({ status: res.status, headers: cors, body: res.text });
    });
    await page.click('#ai-toggle');
    await page.waitForTimeout(300);
    const ask = async q => {
      const before = await page.evaluate(() => document.querySelectorAll('#ai-log .ai-msg').length);
      await page.fill('#ai-input', q);
      await page.keyboard.press('Enter');
      await page.waitForFunction(b => document.querySelectorAll('#ai-log .ai-msg').length >= b + 2 &&
        !document.querySelector('#ai-log .ai-thinking'), before, { timeout: 15000 }).catch(() => {});
      return page.evaluate(() => { const m = [...document.querySelectorAll('#ai-log .ai-msg.ai-bot')].pop();
        return { text: m.innerText, html: m.innerHTML, error: m.classList.contains('ai-error') }; });
    };
    const t = await ask('COMPARE Notting Dale and Stonebridge');
    const tbl = await page.evaluate(() => { const m = [...document.querySelectorAll('#ai-log .ai-msg.ai-bot')].pop();
      return { th: [...m.querySelectorAll('table th[scope="col"]')].map(e => e.textContent), rows: m.querySelectorAll('tbody tr').length,
        strong: !!m.querySelector('td strong'), li: m.querySelectorAll('ul li').length }; });
    ok(tbl.th.join('|') === 'Ward|IMD score' && tbl.rows === 2 && tbl.strong && tbl.li === 1 && !/\|/.test(t.text),
      'Ask: a Markdown table is drawn as a table with column headers, not pipes (H21)', JSON.stringify({ tbl, text: t.text }));
    await page.addScriptTag({ content: AXE });
    const axeAsk = await page.evaluate(tags => axe.run('#ai-panel', { runOnly: tags, resultTypes: ['violations'] })
      .then(r => r.violations.map(v => v.id + ' (' + v.nodes[0].target.join(' ') + ')')), AXE_TAGS);
    ok(axeAsk.length === 0, 'accessibility: the Ask panel with a table answer', axeAsk.join(', '));
    ok(await page.evaluate(() => PH_ASSISTANT.render('<img src=x onerror=alert(1)>\n| <b>a</b> | b |\n|---|---|\n| <i>c</i> | d |').indexOf('<img') === -1 &&
      PH_ASSISTANT.render('| <b>a</b> | b |\n|---|---|\n| c | d |').indexOf('&lt;b&gt;') !== -1), 'Ask: text in tables is escaped like the rest of the answer');
    const c1 = await ask('CUTTOOL compare everything');
    const c2 = await ask('and the next one?');
    ok(/Let me compare those\.\s+This answer was cut short, so please ask about fewer places at once\.$/.test(c1.text) && !c1.error &&
       c2.text === 'Fine.' && !c2.error, 'Ask: an answer cut off mid tool call says so, and the next question still works (H20)', JSON.stringify([c1.text, c2.text]));
    const c3 = await ask('CUTTEXT compare Brent and Bromley');
    ok(/Bromley: IMD\s+This answer was cut short/.test(c3.text), 'Ask: an answer cut off mid text keeps what was written and says it was cut short', c3.text);
    const rf = await ask('REFUSE this');
    const rf2 = await ask('and now?');
    ok(rf.text === 'I cannot answer that one. Please ask about the figures on the map.' && rf2.text === 'Fine.', 'Ask: a refusal gets a plain reply and the next question works', JSON.stringify([rf.text, rf2.text]));
    const profiles = [];
    for (let i = 0; i < 8; i++) profiles.push(await ask('PROFILE ' + i + ' Lansbury and Stonebridge'));
    const maxSize = Math.max(...seen.map(s => s.size)), bad = seen.filter(s => s.status !== 200);
    ok(profiles.every(p => p.text === 'Done with the figures.') && !bad.length && maxSize < 64 * 1024,
      'Ask: a long session of profile lookups stays under the Worker\'s 64 KB limit and every request is accepted (H18)',
      JSON.stringify({ maxSize, bad, last: profiles[profiles.length - 1].text }));
    ok(await page.evaluate(() => JSON.stringify(PH_ASSISTANT.runTool('list_indicators', {})).length) < 16 * 1024,
      'Ask: the indicator list fits in one tool result (under 16 KB)');
    force413 = true;
    const big = await ask('one more');
    const after = await ask('again');
    ok(big.text === 'That conversation has grown too long for me to follow, so I have cleared the earlier questions. Please ask again.' &&
       after.text === 'Fine.' && seen[seen.length - 1].n === 1,
      'Ask: a conversation refused as too long is cleared with a plain message, and the next question starts afresh', JSON.stringify([big.text, after.text, seen[seen.length - 1]]));
    ok(!DASH.test(big.text + c1.text + rf.text), 'Ask: the new messages have no dashes');
    ok(errors.length === 0, 'no page errors (Ask with the Worker)', errors.join(' | '));
    await page.close();
    globalThis.fetch = realFetch;
    console.log = realLog;
  }

  // H33, H38, H39: the printable reports on paper and on a phone.
  {
    const { page, errors } = await open(browser, 'index.html?t=1', DESK);
    const H = await page.evaluate(async () => {
      await Promise.all([ensureAllLayerData(), ensureLsoaImd(), ensureLsoaData(), ensureWardDetail(), ensureGreenspaceData()]);
      const ward = wn => { let h = ''; _generateWardReport({ sink: x => { h = x; } }, wn); return h; };
      let custom = '';
      const realOpen = window.open;
      window.open = () => ({ document: { open() {}, write(h) { custom += h; }, close() {} } });
      try {
        PH_REPORT.apply({ codes: ['E01004243', 'E01004244', 'E01004245'] });
        const b = document.getElementById('rb-report'); b.disabled = false; b.click();
      } finally { window.open = realOpen; }
      return { Lansbury: ward('Lansbury'), Bishopsgate: ward('Bishopsgate'), lsoa: _buildLSOAReportHtml('E01004243'), custom };
    });
    // On paper: two A4 pages, nothing under 8pt, no chart label cut off at
    // the left edge, and the English language row named as the Census does.
    for (const wn of ['Lansbury', 'Bishopsgate']) {
      const p = await browser.newPage({ viewport: { width: 1280, height: 900 } });
      await p.goto(BASE + '/robots.txt');
      await p.setContent(H[wn]);
      await p.waitForTimeout(600);
      await p.emulateMedia({ media: 'print' });
      const m = await p.evaluate(() => {
        let min = 99, where = '';
        for (const el of document.querySelectorAll('.report *')) {
          if (!el.getClientRects().length || ![...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())) continue;
          let px = parseFloat(getComputedStyle(el).fontSize);
          const svg = el.closest('svg');
          if (svg && svg.viewBox.baseVal && svg.viewBox.baseVal.width) px *= svg.getBoundingClientRect().width / svg.viewBox.baseVal.width;
          if (px < min) { min = px; where = el.textContent.slice(0, 30); }
        }
        const cut = [...document.querySelectorAll('.report svg text')].filter(t => t.getBBox().x < -0.5).map(t => t.textContent);
        const labels = [...document.querySelectorAll('svg.ch-compare text')].map(t => t.textContent.replace(/\s+/g, ''));
        return { pt: +(min * 0.75).toFixed(2), where, cut, zoom: getComputedStyle(document.querySelector('.report')).zoom,
          english: labels.includes('NoonewithEnglishasmainlanguage') && !/speaks English/.test(document.body.textContent) };
      });
      const pdf = await p.pdf({ format: 'A4', preferCSSPageSize: true });
      const pages = (pdf.toString('latin1').match(/\/Type\s*\/Page[^s]/g) || []).length;
      ok(pages === 2 && m.pt >= 8 && String(m.zoom) === '1', 'the ' + wn + ' report prints on two A4 pages with nothing under 8pt',
        pages + ' pages, smallest ' + m.pt + 'pt (' + m.where + '), zoom ' + m.zoom);
      ok(!m.cut.length, 'no chart label in the ' + wn + ' report starts left of the chart', m.cut.join(' | '));
      ok(m.english, 'the ' + wn + ' report names the language row "No one with English as main language"');
      await p.close();
    }
    {
      const p = await browser.newPage({ viewport: { width: 1280, height: 900 } });
      await p.goto(BASE + '/robots.txt');
      await p.setContent(H.lsoa);
      await p.waitForTimeout(600);
      const cut = await p.evaluate(() => [...document.querySelectorAll('.report svg text')].filter(t => t.getBBox().x < -0.5).map(t => t.textContent));
      ok(!cut.length, 'no chart label in the LSOA report starts left of the chart', cut.join(' | '));
      await p.close();
    }
    // On a phone each report fits the screen's width.
    for (const vp of [{ width: 390, height: 664 }, { width: 360, height: 740 }]) {
      for (const k of ['Lansbury', 'lsoa', 'custom']) {
        const p = await browser.newPage({ viewport: vp, isMobile: true, hasTouch: true });
        await p.goto(BASE + '/robots.txt');
        await p.setContent(H[k] || '<p>missing</p>');
        await p.waitForTimeout(400);
        const m = await p.evaluate(() => ({ vp: !!document.querySelector('meta[name="viewport"]'), iw: innerWidth, sw: document.documentElement.scrollWidth }));
        ok(m.vp && m.iw === vp.width && m.sw <= m.iw, 'phone ' + vp.width + 'px: the ' + k + ' report fits the screen', JSON.stringify(m));
        await p.close();
      }
    }
    ok(errors.length === 0, 'no page errors (report layout checks)', errors.join(' | '));
    await page.close();
  }

  // H30: a ward sheet fully open on a phone. The missing-figure note is a
  // line of the key, the floating controls do not pile up, the ward is in
  // the map left showing, and reading down the sheet the head drops to a line.
  for (const vp of [{ width: 390, height: 664 }, { width: 360, height: 740 }, { width: 390, height: 844 }, { width: 750, height: 342 }]) {
    const tag = vp.width + 'x' + vp.height;
    const { page, errors } = await open(browser, 'index.html?ov=ft_93227&t=1', { ...vp, isMobile: true, hasTouch: true });
    await page.waitForTimeout(1500);
    const FLOAT = ['#map-legend-strip', '.leaflet-control-zoom', '#dl-badge', '.basemap-ctl', '#map-colour-chip', '#ai-toggle', '.leaflet-control-attribution'];
    const piled = async extra => {
      const rs = {};
      for (const s of FLOAT.concat(extra || [])) {
        const r = await rectOf(page, s);
        if (r && await page.evaluate(s => getComputedStyle(document.querySelector(s)).visibility !== 'hidden', s)) rs[s] = r;
      }
      const ks = Object.keys(rs), hit = [];
      for (let i = 0; i < ks.length; i++) for (let j = i + 1; j < ks.length; j++) if (overlaps(rs[ks[i]], rs[ks[j]])) hit.push(ks[i] + ' x ' + ks[j]);
      return hit;
    };
    const note = await page.evaluate(() => { const n = document.getElementById('mls-note');
      return { inKey: !!n && n.parentNode.id === 'map-legend-strip', shown: !!n && !n.hidden && n.getClientRects().length > 0,
        old: !!document.getElementById('fingertips-banner'), text: n ? n.textContent : '' }; });
    ok(note.inKey && note.shown && !note.old && /^Areas left blank have no figure\./.test(note.text),
      'phone ' + tag + ': the missing-figure note is a line of the key, not a floating card', JSON.stringify(note).slice(0, 200));
    let hit = await piled();
    ok(!hit.length, 'phone ' + tag + ': the key, the zoom buttons, the level switch and the chips do not overlap', hit.join(', '));
    await page.evaluate(() => { showWard('Lansbury'); PH_SHEET.setState(0); });
    await page.waitForTimeout(1600);
    hit = await piled(['#ward-sheet']);
    ok(!hit.length, 'phone ' + tag + ': with the ward sheet fully open nothing floats over it or over each other', hit.join(', '));
    const seen = await page.evaluate(() => {
      let lyr = null; wLyr.eachLayer(l => { if (l.feature.properties.WD24NM === 'Lansbury') lyr = l; });
      const c = map.latLngToContainerPoint(lyr.getBounds().getCenter()), m = map.getContainer().getBoundingClientRect();
      const s = document.getElementById('ward-sheet').getBoundingClientRect(), x = m.left + c.x, y = m.top + c.y;
      return { inMap: x > m.left && x < m.right && y > m.top && y < m.bottom, underSheet: x > s.left && x < s.right && y > s.top && y < s.bottom };
    });
    ok(seen.inMap && !seen.underSheet, 'phone ' + tag + ': the chosen ward is in the map left showing, not under the sheet', JSON.stringify(seen));
    const head = await page.evaluate(async () => {
      const b = document.getElementById('ws-body'), h = document.getElementById('ws-head'), h0 = h.offsetHeight;
      if (b.scrollHeight - b.clientHeight < h0) return { skip: true };
      b.scrollTop = b.scrollHeight; await new Promise(r => setTimeout(r, 300));
      const h1 = h.offsetHeight, name = document.getElementById('ws-name').getBoundingClientRect().height > 0,
        size = document.getElementById('ws-size').getBoundingClientRect().height > 0;
      b.scrollTop = 0; await new Promise(r => setTimeout(r, 300));
      b.scrollTop = 0; await new Promise(r => setTimeout(r, 300));
      return { h0, h1, name, size, back: h.offsetHeight };
    });
    ok(head.skip || (head.h1 < 70 && head.name && head.size && head.back === head.h0),
      'phone ' + tag + ': reading down the open sheet, its head drops to one line, and comes back at the top', JSON.stringify(head));
    ok(errors.length === 0, 'no page errors (phone sheet ' + tag + ')', errors.join(' | '));
    await page.close();
  }

  // H31: a postcode on a phone marks the spot without a label under the
  // map's buttons; on a desktop the label opens clear of them, with a
  // Remove pin button big enough to tap.
  for (const vp of [PHONE, DESK]) {
    const { page, errors } = await open(browser, 'index.html?t=1', vp);
    const res = { postcode: 'E1 6AN', latitude: 51.5203, longitude: -0.0727, admin_ward: 'Spitalfields & Banglatown',
      codes: { admin_ward: 'E05009336' } };
    await page.evaluate(res => _pickPostcode({ kind: 'postcode', text: 'E1 6AN' }, res), res);
    await page.waitForTimeout(2500);
    const pop = await rectOf(page, '.leaflet-popup'), pin = await page.evaluate(() => !!document.querySelector('.search-pin'));
    if (vp === PHONE) {
      ok(pin && !pop, 'phone: a postcode drops its pin without a popup (the sheet says "Near")', JSON.stringify(pop));
    } else {
      const hit = [];
      for (const s of ['.leaflet-control-zoom', '.basemap-ctl', '#ward-sheet', '.dl-badge']) if (overlaps(pop, await rectOf(page, s))) hit.push(s);
      const btn = await page.evaluate(() => { const b = document.querySelector('.leaflet-popup .search-pin-clear');
        return b ? { tag: b.tagName, text: b.textContent, h: Math.round(b.getBoundingClientRect().height) } : null; });
      ok(!!pop && !hit.length && btn && btn.tag === 'BUTTON' && btn.text === 'Remove pin' && btn.h >= 44,
        'desktop: the postcode label opens clear of the map buttons and the sheet, with a 44px Remove pin', JSON.stringify({ pop, hit, btn }));
    }
    ok(errors.length === 0, 'no page errors (postcode pin)', errors.join(' | '));
    await page.close();
  }

  // H32: the Ask button and the chip stay off an open comparison.
  for (const [vp, wards] of [[PHONE, ['Lansbury', 'Bow West']], [{ width: 1280, height: 720 }, ['Lansbury', 'Bow West', 'Hornsey', 'Sudbury']], [DESK, ['Lansbury', 'Bow West', 'Hornsey', 'Sudbury']]]) {
    const { page, errors } = await open(browser, 'index.html?t=1', vp);
    for (const wn of wards) {
      await page.evaluate(wn => showWard(wn), wn);
      await page.waitForTimeout(500);
      await page.evaluate(() => document.getElementById('ws-pin').click());
      await page.waitForTimeout(500);
    }
    await page.evaluate(() => { const b = document.getElementById('cmp-bar-see'); if (b && b.getClientRects().length) b.click();
      if (!document.getElementById('cmp-bar').classList.contains('open')) document.getElementById('cmp-open').click(); });
    await page.waitForTimeout(1200);
    const bar = await rectOf(page, '#cmp-bar'), ask = await rectOf(page, '#ai-toggle'), chip = await rectOf(page, '#map-colour-chip');
    ok(!!bar && !overlaps(ask, bar) && !overlaps(chip, bar), 'the Ask button and the chip are clear of the open comparison at ' + vp.width + 'x' + vp.height,
      JSON.stringify({ bar, ask, chip }));
    ok(errors.length === 0, 'no page errors (comparison and Ask)', errors.join(' | '));
    await page.close();
  }

  // A region
  if (HAS_NE) {
    const { page, errors } = await open(browser, 'index.html?r=north-east&t=1', DESK);
    ok(await page.evaluate(() => { let n = 0; wLyr.eachLayer(() => n++); return n; }) > 300, 'North East wards drawn');
    ok(await page.evaluate(() => document.getElementById('borough-focus-label').textContent) === 'Focus by LA', 'region wording: the level is LA');
    ok(await page.evaluate(() => { const h = document.getElementById('dl-help').title;
      return (h.match(/local authority \(LA\)/g) || []).length === 1 && /(four|five) sizes of area/.test(h) && !/\bBorough\b/.test(h); }),
      'region Area level help introduces local authority (LA) once');
    // Ask is written for London's wards and LAs, so a region has no Ask
    // button, no panel and no "Ask about this ward" (workplan 1.6).
    const askNE = await page.evaluate(async () => {
      const w = Object.keys(WARD_DATA)[0];
      if (w) showWard(w);
      await new Promise(r => setTimeout(r, 600));
      const b = document.getElementById('ws-ask');
      return { toggle: !!document.getElementById('ai-toggle'), panel: !!document.getElementById('ai-panel'),
        sheetAsk: !!b && !b.hidden && b.getBoundingClientRect().width > 0, available: !!(window.PH_ASSISTANT && PH_ASSISTANT.available) };
    });
    ok(!askNE.toggle && !askNE.panel && !askNE.sheetAsk && !askNE.available, 'region: no Ask button or panel outside London', JSON.stringify(askNE));
    // B6: the most deprived ten LSOAs are the ten with the lowest national
    // rank, not the first ten in the file (which were all in Gateshead).
    await setOv(page, 'imd_decile');
    await page.waitForTimeout(3000);
    const neDec = await page.evaluate(() => ({
      lads: [...new Set([...document.querySelectorAll('#rank-worst .rank-row')].map(b =>
        (LSOA_IMD.features.find(f => f.properties.code === b.dataset.id) || { properties: {} }).properties.borough))],
      notes: document.getElementById('rank-notes').textContent }));
    ok(neDec.lads.length > 1 && /LSOAs in the North East are in decile 1, the most deprived tenth of England, so this list shows the ten with the lowest national rank\./.test(neDec.notes),
      'region: the lowest 10 LSOAs for deprivation are not all from one LA, and the tie is said', JSON.stringify(neDec));
    // "the North East" wherever the region follows "of", "in" or "across" (H29).
    await setOv(page, 'imd_score_ward');
    await page.waitForTimeout(1500);
    const neWords = await page.evaluate(async () => {
      const digest = document.getElementById('vs-digest').textContent;
      const w = Object.keys(WARD_DATA).find(n => { const i = WARD_DATA[n].indicators || {};
        return i.imd_decile_mean != null && i.census_population > 2000; });
      showWard(w);
      await new Promise(r => setTimeout(r, 600));
      const t = document.body.innerText + ' ' + [...document.querySelectorAll('option, [aria-label], [title]')]
        .map(e => e.textContent + ' ' + (e.getAttribute('aria-label') || '') + ' ' + (e.getAttribute('title') || '')).join(' ');
      return { bad: (t.match(/.{0,30}\b(of|in|across|outside) North East.{0,20}/g) || []).slice(0, 4), poss: (t.match(/North East's/g) || []).length,
        digest, sheet: (document.querySelector('#ws-body .ws-insight') || {}).innerText || '',
        hdr: document.getElementById('hdr-region-btn').getAttribute('aria-label') };
    });
    ok(!neWords.bad.length && !neWords.poss && /^All of the North East/.test(neWords.digest) && /of the wards in the North East/.test(neWords.sheet) &&
       neWords.hdr === 'North East, change region',
      'region: the wording says "the North East", with no possessive', JSON.stringify(neWords));
    // The tour, on the region's own counts and without Ask (H25).
    const neTour = await page.evaluate(async () => {
      PH_TOUR.start();
      const o = [];
      for (let i = 0; i < PH_TOUR.length; i++) {
        await new Promise(r => setTimeout(r, 500));
        o.push(document.getElementById('tour-narr').innerText);
        if (i < PH_TOUR.length - 1) document.getElementById('tour-next').click();
      }
      PH_TOUR.end();
      return { o, link: getComputedStyle(document.getElementById('sbar-tour')).display };
    });
    ok(neTour.o.length === 6 && /how the North East changes from one neighbourhood to the next\. You can look at its 12 local authorities \(LAs\)/.test(neTour.o[0]) &&
       !/London/.test(neTour.o.join(' ')) && !/Ask about this data/.test(neTour.o[3]) && neTour.link !== 'none',
      'region: the tour runs on the region\'s own counts, with Take a tour, and leaves out Ask where there is none', JSON.stringify(neTour).slice(0, 600));
    ok(errors.length === 0, 'no page errors (North East)', errors.join(' | '));
    await page.close();
    // A first visit to a region gets the tour too, since ?r= alone is not a shared view.
    {
      const { page: p2 } = await open(browser, 'index.html?r=north-east', DESK);
      ok(await p2.evaluate(() => PH_TOUR.running()), 'region: a first visit to ?r=north-east opens the tour');
      await p2.close();
    }
  } else {
    console.log('SKIP North East (no data/regions/north-east)');
  }

  // The England gate shows before the heavy load
  {
    const { page } = await open(browser, 'index.html?r=england', DESK);
    ok(await page.evaluate(() => /England map is large/.test(document.body.innerText)), 'England warning shows');
    ok(await page.evaluate(() => /around 40 MB of figures, and neighbourhood \(LSOA\) maps for the whole country are not available here/.test(document.body.innerText)),
      'the England warning says how much it loads and that there are no country-wide LSOA maps');
    await page.close();
  }

  // The England map on a phone (built by scripts/build_england.py, so only
  // where data/regions/england/ exists): drawn on a canvas, a Census
  // indicator opens at ward, LSOAs are refused until an LA is in focus, and
  // then only that LA's file loads.
  if (HAS_ENG) {
    const { page, errors } = await open(browser, 'index.html?r=england&go=1&t=1', PHONE);
    await page.waitForFunction(() => typeof wLyr !== 'undefined' && wLyr, null, { timeout: 120000 }).catch(() => {});
    await page.waitForTimeout(3000);
    const lsoaReq = [];
    page.on('request', r => { if (/lsoa_imd\.js|lsoa_data\.json|\/map\/lsoa\//.test(r.url())) lsoaReq.push(r.url().replace(/^.*\/data\/regions\/england\//, '')); });
    await setOv(page, 'census_bad_health_pct');
    await page.waitForTimeout(2500);
    await page.evaluate(() => document.querySelector('#dl-seg button[data-dl="lsoa"]').click());
    await page.waitForTimeout(1500);
    const e1 = await page.evaluate(() => ({ lvl: currentDataLevel, svg: document.querySelectorAll('.leaflet-overlay-pane path').length,
      canvas: document.querySelectorAll('.leaflet-overlay-pane canvas').length, hint: document.getElementById('dl-hint').textContent }));
    ok(e1.lvl === 'ward' && e1.canvas > 0 && e1.svg === 0 && !lsoaReq.length && /choose an LA under Focus by LA/.test(e1.hint),
      'England: a Census indicator opens at ward on a canvas, and LSOAs wait for an LA', JSON.stringify({ ...e1, lsoaReq }));
    const la = await page.evaluate(() => [...document.getElementById('borough-focus').options].map(o => o.value).filter(v => v && v !== 'all' && !/^icb:/.test(v))[0]);
    await page.evaluate(v => { const s = document.getElementById('borough-focus'); s.value = v; s.dispatchEvent(new Event('change')); }, la);
    await page.waitForTimeout(2000);
    await page.evaluate(() => document.querySelector('#dl-seg button[data-dl="lsoa"]').click());
    await page.waitForTimeout(5000);
    const e2 = await page.evaluate(la => ({ lvl: currentDataLevel, n: LSOA_IMD.features.length,
      all: LSOA_IMD.features.every(f => _sameLad(f.properties.borough, la)), onMap: map.hasLayer(lsoaLyr) }), la);
    ok(e2.lvl === 'lsoa' && e2.n > 0 && e2.all && e2.onMap && lsoaReq.length === 1 && /^map\/lsoa\/E\d{8}\.json$/.test(lsoaReq[0]),
      'England: with an LA in focus its LSOAs load from their own file', JSON.stringify({ ...e2, lsoaReq }));
    ok(errors.length === 0, 'no page errors (England on a phone)', errors.join(' | '));
    await page.close();
  } else {
    console.log('SKIP England map (no data/regions/england)');
  }

  // Only the served files are published (B11). Checked when the folder under
  // test is the assembled site (scripts/build_site.sh), which has .nojekyll.
  {
    const site = (await fetch(BASE + '/.nojekyll')).ok;
    if (site) {
      const codes = {};
      for (const p of ['HANDOVER.md', 'README.md', 'DOMAIN.md', 'fetch_all_data.py', 'index.html', 'data/ward_core.json']) {
        codes[p] = (await fetch(BASE + '/' + p)).status;
      }
      ok(codes['HANDOVER.md'] === 404 && codes['README.md'] === 404 && codes['DOMAIN.md'] === 404 &&
        codes['fetch_all_data.py'] === 404 && codes['index.html'] === 200 && codes['data/ward_core.json'] === 200,
        'site: internal notes are not published, the page and its data are', JSON.stringify(codes));
    } else console.log('SKIP site contents (not the assembled _site)');
  }

  // The ward sheet header between 900 and 1180px (B13): the name keeps room
  // to be read and the close button stays inside the sheet.
  for (const [w, h] of [[1024, 768], [920, 700]]) {
    const { page } = await open(browser, 'index.html?t=1', { width: w, height: h });
    await page.evaluate(() => { const l = wLyr.getLayers().find(l => l.feature.properties.WD24NM === 'Spitalfields & Banglatown'); if (l) l.fire('click'); });
    await page.waitForTimeout(800);
    const r = await page.evaluate(() => {
      const s = document.getElementById('ward-sheet').getBoundingClientRect();
      const n = document.getElementById('ws-name').getBoundingClientRect();
      const c = document.getElementById('ws-close').getBoundingClientRect();
      return { name: Math.round(n.width), inside: c.left >= s.left && c.right <= s.right && c.width > 0 };
    });
    ok(r.name >= 150 && r.inside, 'ward sheet at ' + w + 'x' + h + ': the name is readable and Deselect is inside the sheet', JSON.stringify(r));
    await page.close();
  }

  // Keyboard and screen reader checks (B15, H24, H35, H36, H37).
  {
    const { page, errors } = await open(browser, 'index.html?t=1', DESK);
    const act = () => page.evaluate(() => {
      const a = document.activeElement;
      return { body: !a || a === document.body, id: a ? a.id : '', cls: a ? String(a.className) : '', ov: a && a.dataset ? a.dataset.ov || '' : '', tag: a ? a.tagName : '' };
    });
    // Query: Add a condition is a button that takes focus, Enter adds a row and
    // focus goes to its indicator, and the results are a list of buttons.
    await page.click('.mode-btn[data-mode="query"]');
    await page.waitForTimeout(800);
    await page.focus('#q-add-cond');
    const qa = await act();
    const before = await page.evaluate(() => document.querySelectorAll('.q-cond').length);
    await page.keyboard.press('Enter');
    await page.waitForTimeout(300);
    const after = await page.evaluate(() => document.querySelectorAll('.q-cond').length);
    const qf = await act();
    ok(qa.id === 'q-add-cond' && qa.tag === 'BUTTON' && after === before + 1 && /q-f-ind/.test(qf.cls),
      'Query: Add a condition takes focus, Enter adds a condition and focuses its indicator', JSON.stringify([qa, before, after, qf]));
    await page.click('#q-run');
    await page.waitForTimeout(1500);
    const qr = await page.evaluate(() => ({
      btns: document.querySelectorAll('#q-results ol.q-list > li > button.q-row').length,
      divs: document.querySelectorAll('#q-results div.q-row').length }));
    ok(qr.btns > 0 && qr.divs === 0, 'Query: the results are an ordered list of buttons', JSON.stringify(qr));
    await page.click('.mode-btn[data-mode="explore"]');
    await page.waitForTimeout(600);

    // Every layer checkbox is named by its own label (H37).
    const lab = await page.evaluate(() => [...document.querySelectorAll('.lrow input[type="checkbox"]')]
      .filter(i => !(i.labels && i.labels.length && i.labels[0].control === i && i.labels[0].textContent.trim()))
      .map(i => i.id).concat([...document.querySelectorAll('label.lrow, .lrow > label')]
        .filter(l => !(l.control && l.control.type === 'checkbox')).map(l => 'label:' + (l.id || l.textContent.trim().slice(0, 30)))));
    ok(lab.length === 0, 'Layers: every layer checkbox has its label as its name, ICBs and PCNs included', lab.join(', '));

    // Focus is not dropped to the top of the page (H36).
    await page.click('#sbtab-overlay');
    await page.waitForTimeout(400);
    const lost = [];
    const chip = await page.evaluate(() => { const c = [...document.querySelectorAll('#ov-chips .ov-chip')].find(b => !b.classList.contains('on')); if (c) c.focus(); return !!c; });
    if (chip) { await page.keyboard.press('Enter'); await page.waitForTimeout(500); const a = await act(); if (a.body || !a.ov) lost.push('key indicator chip ' + JSON.stringify(a)); }
    const th = await page.evaluate(() => { const t = document.querySelector('#ov-themes .ov-theme, .ov-theme'); if (t) t.click(); return !!t; });
    await page.waitForTimeout(400);
    const row = th && await page.evaluate(() => { const r = [...document.querySelectorAll('.ov-tv-row')].find(b => !b.classList.contains('on') && b.getClientRects().length); if (r) r.focus(); return !!r; });
    if (row) { await page.keyboard.press('Enter'); await page.waitForTimeout(500); const a = await act(); if (a.body) lost.push('theme row'); }
    const clr = await page.evaluate(() => { const c = document.getElementById('ov-clear'); if (c && c.getClientRects().length) { c.focus(); return true; } return false; });
    if (clr) { await page.keyboard.press('Enter'); await page.waitForTimeout(500); const a = await act(); if (a.body) lost.push('Clear'); }
    await page.evaluate(() => { const l = wLyr.getLayers().find(l => l.feature.properties.WD24NM === 'Stepney Green'); if (l) l.fire('click'); });
    await page.waitForTimeout(800);
    await page.focus('#ws-close');
    await page.keyboard.press('Enter');
    await page.waitForTimeout(500);
    { const a = await act(); if (a.body) lost.push('Deselect this ward'); }
    const live = await page.evaluate(() => document.getElementById('sr-live').textContent);
    await page.evaluate(() => { const l = wLyr.getLayers().find(l => l.feature.properties.WD24NM === 'Stepney Green'); if (l) l.fire('click'); });
    await page.waitForTimeout(800);
    await page.focus('#ws-profile');
    await page.keyboard.press('Enter');
    await page.waitForTimeout(500);
    const prof = await page.evaluate(() => { const a = document.activeElement; return a && a.matches('#wpanel h2.wname'); });
    if (!prof) lost.push('Open full profile');
    await page.click('.mode-btn[data-mode="compare"]');
    await page.waitForTimeout(800);
    // Placed just above an indicator that is not yet shown, so one ArrowDown
    // lands on it.
    const hasAdd = await page.evaluate(() => {
      const s = document.getElementById('cmp-add-metric');
      if (!s) return false;
      // By key: an option's name carries its unit, a chip's does not.
      const on = PH_COMPARE.state().metrics;
      const t = [...s.options].findIndex((o, i) => i > 0 && on.indexOf(o.value) === -1);
      if (t < 1) return false;
      s.selectedIndex = t - 1;
      s.focus();
      return true;
    });
    if (hasAdd) {
      const n0 = await page.evaluate(() => document.querySelectorAll('#cmp-metrics [data-metric].on').length);
      await page.keyboard.press('ArrowDown');
      await page.waitForTimeout(400);
      const n1 = await page.evaluate(() => document.querySelectorAll('#cmp-metrics [data-metric].on').length);
      const a1 = await act();
      await page.keyboard.press('Enter');
      await page.waitForTimeout(400);
      const n2 = await page.evaluate(() => document.querySelectorAll('#cmp-metrics [data-metric].on').length);
      const a2 = await act();
      ok(n1 === n0 && a1.id === 'cmp-add-metric' && n2 === n0 + 1 && a2.id === 'cmp-add-metric',
        'Compare: an arrow key in Add an indicator only chooses, Enter adds and focus stays on the select', JSON.stringify([n0, n1, a1.id, n2, a2.id]));
    } else console.log('SKIP Compare add an indicator (no select)');
    await page.click('.mode-btn[data-mode="explore"]');
    await page.waitForTimeout(600);
    ok(lost.length === 0, 'focus stays put after a key indicator, a theme row, Clear, Deselect and Open full profile', lost.join(', '));
    ok(/No area is selected/.test(live), 'Deselect this ward says no area is selected', live);

    // The region select only chooses with an arrow key (H24).
    await page.click('#sbtab-layers');
    await page.waitForTimeout(400);
    const pickShown = await page.evaluate(() => { const w = document.getElementById('region-picker-wrap'); return !!(w && !w.hidden && w.getClientRects().length); });
    if (pickShown) {
      const url0 = page.url();
      const v0 = await page.evaluate(() => document.getElementById('region-picker').value);
      await page.focus('#region-picker');
      await page.keyboard.press('ArrowDown');
      await page.waitForTimeout(1200);
      const v1 = await page.evaluate(() => document.getElementById('region-picker').value);
      ok(page.url() === url0 && v1 !== v0 && await page.evaluate(() => !document.getElementById('region-go')),
        'region select: an arrow key chooses without leaving the page, and there is no separate Open button', JSON.stringify([url0, page.url(), v0, v1]));
      await page.evaluate(v => { document.getElementById('region-picker').value = v; }, v0);
    } else console.log('SKIP region select (one region published)');

    // The tour's keys act only inside the tour strip (H35).
    await page.evaluate(() => { deselectWard(); PH_TOUR.start(); });
    await page.waitForTimeout(800);
    const ctr = () => page.evaluate(() => document.getElementById('tour-ctr').textContent);
    const c0 = await ctr();
    const said = await page.evaluate(() => new Promise(r => setTimeout(() => r(document.getElementById('sr-live').textContent), 200)));
    await page.focus('#sbtab-overlay');
    await page.keyboard.press('ArrowRight');
    await page.waitForTimeout(500);
    await page.evaluate(() => document.activeElement && document.activeElement.blur());
    await page.keyboard.press('Escape');
    await page.waitForTimeout(400);
    const c1 = await ctr();
    const still = await page.evaluate(() => PH_TOUR.running());
    await page.focus('#tour-next');
    await page.keyboard.press('ArrowRight');
    await page.waitForTimeout(600);
    const c2 = await ctr();
    ok(c1 === c0 && still && c2 !== c0, 'tour: arrow keys and Escape outside the strip leave it alone; inside it they step', JSON.stringify([c0, c1, still, c2]));
    ok(/Guided tour, chapter 1 of/.test(said), 'tour: the chapter is announced', said);
    await page.evaluate(() => PH_TOUR.end());
    ok(errors.length === 0, 'keyboard checks: no page errors', errors.join(' | '));
    await page.close();
  }

  // Phone: Tab never enters the closed drawer, and never leaves it when open (H34).
  {
    const { page } = await open(browser, 'index.html?t=1', PHONE);
    const walk = async n => {
      const out = [];
      for (let i = 0; i < n; i++) {
        await page.keyboard.press('Tab');
        out.push(await page.evaluate(() => {
          const a = document.activeElement;
          if (!a || a === document.body) return 'body';
          return (document.getElementById('sidebar').contains(a) ? 'in:' : 'out:') + (a.id || a.className || a.tagName);
        }));
      }
      return out;
    };
    await page.focus('#hdr-menu');
    const closed = (await walk(20)).filter(x => x.startsWith('in:'));
    ok(closed.length === 0, 'phone: Tab from the menu button never lands in the closed drawer', closed.slice(0, 5).join(' '));
    await page.click('#hdr-menu');
    await page.waitForTimeout(500);
    const opened = (await walk(60)).filter(x => x.startsWith('out:'));
    ok(opened.length === 0, 'phone: Tab stays inside the open drawer', opened.slice(0, 5).join(' '));
    await page.keyboard.press('Escape');
    await page.waitForTimeout(400);
    const back = await page.evaluate(() => document.activeElement && document.activeElement.id);
    ok(back === 'hdr-menu', 'phone: closing the drawer puts focus back on the menu button', back);
    await page.close();
  }

  // The pages around the map share one frame (workplan 2.14): one stylesheet,
  // light even when the device is set to dark, the same header and footer,
  // titles in the form "Page | PopHealth Map", and no dashes.
  const FRAME = ['methodology.html', 'accessibility.html', 'privacy.html', '404.html'];
  const frames = {};
  for (const p of FRAME) {
    for (const [vpName, vp] of [['desktop', { viewport: { width: 1200, height: 900 } }],
                                ['phone', { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true }]]) {
      const page = await browser.newPage({ ...vp, colorScheme: 'dark' });
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      page.on('requestfailed', r => errors.push('failed to load ' + r.url()));
      await routeSite(page);
      await page.goto(BASE + '/' + p);
      await page.waitForTimeout(1500);
      const f = await page.evaluate(() => {
        const hdr = document.querySelector('header.site-hdr');
        const links = hdr ? [...hdr.querySelectorAll('a')] : [];
        return {
          title: document.title,
          bg: getComputedStyle(document.body).backgroundColor,
          sheets: [...document.querySelectorAll('link[rel="stylesheet"]')].map(l => l.getAttribute('href').replace(/^https:\/\/pophealth\.uk\//, '')),
          inlineStyle: document.querySelectorAll('style').length,
          hdrText: hdr ? hdr.innerText.replace(/\s+/g, ' ').trim() : '',
          hdrLinks: links.map(a => (a.getAttribute('href').replace(/^https:\/\/pophealth\.uk\//, '') || 'index.html')).join(' '),
          current: (hdr && hdr.querySelector('[aria-current="page"]') || {}).textContent || '',
          ftr: (document.querySelector('footer.site-ftr') || {}).innerText || '',
          skip: (document.querySelector('a.skip') || { getAttribute: () => '' }).getAttribute('href'),
          h1: document.querySelectorAll('main h1').length,
          dashes: /[–—]/.test(document.title + document.body.innerText),
          over: document.documentElement.scrollWidth - document.documentElement.clientWidth,
          relative: [...document.querySelectorAll('a[href], link[href]')].map(e => e.getAttribute('href'))
            .filter(h => !/^(https:\/\/|mailto:|#|data:)/.test(h)),
        };
      });
      if (vpName === 'desktop') {
        frames[p] = f;
        ok(/^\S.* \| PopHealth Map$/.test(f.title), p + ': title in the form "Page | PopHealth Map"', f.title);
        ok(f.bg === 'rgb(244, 247, 249)', p + ': light under a dark device setting', f.bg);
        ok(f.sheets.join(' ') === 'vendor/fonts.css pages.css' && f.inlineStyle === 0, p + ': the shared stylesheet only',
          f.sheets.join(' ') + ' / ' + f.inlineStyle + ' style blocks');
        ok(f.skip === '#main' && f.h1 === 1, p + ': skip link and one heading');
        ok(!f.dashes, p + ': no em or en dashes');
        await checkAxe(page, p);
      } else {
        ok(f.over <= 0, p + ': no sideways scroll on a phone', f.over + 'px');
      }
      ok(errors.length === 0, 'no page errors (' + p + ', ' + vpName + ')', errors.join(' | '));
      await page.close();
    }
  }
  {
    const ref = frames['methodology.html'];
    ok(ref.hdrText === 'PopHealth Map Methodology Accessibility About and privacy' &&
       ref.hdrLinks === 'index.html methodology.html accessibility.html privacy.html',
       'pages: header has the mark and the three pages', ref.hdrText + ' / ' + ref.hdrLinks);
    ok(FRAME.every(p => frames[p].hdrText === ref.hdrText && frames[p].hdrLinks === ref.hdrLinks && frames[p].ftr === ref.ftr),
       'pages: the same header and footer on all four',
       FRAME.map(p => p + ': ' + frames[p].hdrLinks).join(' | '));
    ok(frames['methodology.html'].current === 'Methodology' && frames['accessibility.html'].current === 'Accessibility' &&
       frames['privacy.html'].current === 'About and privacy' && frames['404.html'].current === '',
       'pages: the header marks the page you are on');
    ok(/contact@pophealth\.uk/.test(ref.ftr) && /not affiliated with the NHS/.test(ref.ftr), 'pages: footer gives the contact address',
       ref.ftr);
    // The app's status bar and the pages say who runs the site in the same words.
    const appNote = ((await (await fetch(BASE + '/index.html')).text()).match(/<span class="sbar-note">([^<]+)<\/span>/) || [])[1] || '';
    ok(/not affiliated with the NHS/.test(appNote) && ref.ftr.includes(appNote.trim()),
       'pages: the footer disclaimer matches the app\'s word for word', appNote + ' / ' + ref.ftr);
    ok(frames['404.html'].relative.length === 0, '404: every link is absolute', frames['404.html'].relative.join(' '));
  }
  // What each page says.
  {
    const texts = {};
    for (const p of ['accessibility.html', 'privacy.html', '404.html']) {
      const page = await browser.newPage();
      await routeSite(page);
      await page.goto(BASE + '/' + p);
      texts[p] = await page.evaluate(() => ({
        main: document.querySelector('main').innerText,
        storage: [...document.querySelectorAll('main table code')].map(c => c.textContent),
        contactParas: [...document.querySelectorAll('main p')].filter(e => /correction/.test(e.textContent)).length,
        ask: [...document.querySelectorAll('#ask ~ ul li')].map(li => li.textContent).join(' '),
        home: (document.querySelector('main a.btn') || {}).href || '',
      }));
      await page.close();
    }
    const a = texts['accessibility.html'].main;
    ok(AXE_TAGS.includes('wcag22aa') && /WCAG 2\.2 AA/.test(a) && !/WCAG 2\.[01]\b|version 2\.[01]\b/.test(a),
      'accessibility: WCAG 2.2 AA stated and tested');
    ok(/one light colour scheme/.test(a) && !/dark (mode|theme|scheme|colour scheme)/i.test(a),
      'accessibility: one light colour scheme, no dark scheme claimed');
    ok(!/\b1,400\b|\d+ shapes/.test(a), 'accessibility: no fixed shape counts');
    ok(/prepared on \d{1,2} \w+ 20\d\d and last reviewed on \d{1,2} \w+ 20\d\d/.test(a) &&
       (a.match(/\d{1,2} \w+ 20\d\d/g) || []).length === 2 && !/Last updated:/.test(a),
      'accessibility: dates merged into one sentence');
    ok(/local authority \(LA\)/.test(a) && !/borough/i.test(a), 'accessibility: the level is called LA');
    for (const crit of ['2.1.1', '1.1.1', '1.3.1']) {
      ok(a.includes(crit), 'accessibility: known failure against ' + crit + ' listed');
    }
    const pr = texts['privacy.html'];
    ok(pr.storage.includes('ph-england-ok'), 'privacy: ph-england-ok storage row');
    ok(pr.contactParas === 1, 'privacy: contact paragraph given once', pr.contactParas + ' paragraphs');
    ok(!/\bWorker\b/.test(pr.ask) && /Anthropic/.test(pr.ask) && /48 hours/.test(pr.ask) && /IP address/.test(pr.ask),
      'privacy: Ask bullets in plain terms, same claims');
    const prAsk = pr.ask.replace(/\s+/g, ' ');
    ok(ASK_SAME.every(re => re.test(prAsk)) && /personal information/.test(pr.main) && !/[\u2013\u2014]/.test(prAsk),
      'privacy: the Ask panel described in the same terms as the tour and the panel', prAsk);
    const nf = texts['404.html'];
    ok(/We could not find a page at this address/.test(nf.main) && nf.home === 'https://pophealth.uk/',
      '404: new copy, and the way back to the map is absolute');
  }

  // The methodology page is built around the reader (workplan 2.13): the
  // first screen says what the map covers and how to cite it, every heading
  // can be linked to, no ONS product code comes before the appendix, the
  // source table is live, and on a phone it is a stack of cards.
  // Each indicator card links to the row of the source table its figures
  // come from (workplan 2.13), and every row it can link to exists.
  {
    const { page } = await open(browser, 'index.html?t=1', DESK);
    const a = await page.evaluate(() => {
      const href = k => { const d = document.createElement('div'); d.innerHTML = indicatorCardHTML(k); const l = d.querySelector('a.ic-more'); return l ? l.getAttribute('href') : ''; };
      return { census: href('census_bad_health_pct'), crime: href('crime_score'), ptal: href('ptal_score'), core20: href('core20_lsoa_count'),
        all: [...new Set(Object.keys(OV_META).map(_methAnchor))],
        credits: [...document.querySelectorAll('a.credits-link')].map(l => l.getAttribute('href')) };
    });
    await page.close();
    ok(a.census === 'methodology.html#src-census' && a.crime === 'methodology.html#src-imd' && a.ptal === 'methodology.html#src-ptal' && a.core20 === 'methodology.html#src-core20',
      'indicator cards link to their source row', JSON.stringify(a).slice(0, 240));
    ok(a.credits.length && a.credits.every(h => h === 'methodology.html#licences'), 'Data credits links open the licences section', a.credits.join(' '));
    const { page: mp } = await open(browser, 'methodology.html', DESK);
    await mp.waitForFunction(() => document.querySelectorAll('#src-rows tr[id^="src-"]').length > 15, null, { timeout: 10000 }).catch(() => {});
    const missing = await mp.evaluate(ids => ids.filter(id => !document.getElementById(id)), a.all);
    ok(!missing.length, 'every source row a card links to exists on the methodology page', missing.join(', ') + ' of ' + a.all.join(', '));
    // The page describes the controls the map has, and how Census figures
    // are combined: from the summed counts of output areas once the data
    // build says so (indicators.js census_method), and otherwise as the
    // population-weighted average that built the figures on the map. Never
    // by adding up percentages.
    await mp.waitForFunction(() => document.querySelector('[data-cen]') && SOURCES[0].geo, null, { timeout: 5000 }).catch(() => {});
    const mt = await mp.evaluate(() => document.querySelector('main').innerHTML);
    const cenM = await mp.evaluate(async () => {
      const t = await (await fetch('data/map/indicators.js')).text();
      const d = JSON.parse(t.slice(t.indexOf('var PH_IND_STATS = ') + 19, t.lastIndexOf(';')));
      const vis = document.querySelector('#census-levels').nextElementSibling.innerText + ' ' +
        document.querySelector('#adding-up').nextElementSibling.innerText;
      const row = (document.getElementById('src-census') || {}).innerText || '';
      return { ward: d.census_method === 'counts', la: d.census_la_method === 'counts', vis, row };
    });
    ok(/About this indicator/.test(mt) && !/<strong>i<\/strong>/.test(mt) && !/<strong>About<\/strong> button/.test(mt),
      'methodology: names the About this indicator button, not controls that are gone');
    ok(!/Census[^.<]{0,80}added up|added up[^.<]{0,40}Census/.test(mt), 'methodology: Census figures are not said to be added up');
    ok((cenM.ward ? /calculated from the summed counts of the output areas\s+in the ward/.test(cenM.vis) && !/weighted by their residents/.test(cenM.vis)
                  : /average of the\s+percentages of the output areas in the ward, weighted by their residents/.test(cenM.vis) && !/summed counts/.test(cenM.vis)) &&
       (cenM.la ? /LA figures are\s+calculated from the summed counts/.test(cenM.vis) : /LA figures are an average of the ward figures,\s+weighted by population/.test(cenM.vis)) &&
       (cenM.ward && cenM.la ? /summed counts/.test(cenM.row) : /averaged here, weighted by population/.test(cenM.row)),
      'methodology: describes the Census method that built the figures on the map', JSON.stringify(cenM).slice(0, 400));
    await mp.close();
  }
  {
    const { page } = await open(browser, 'methodology.html', DESK);
    const m = await page.evaluate(() => {
      const main = document.querySelector('main');
      const app = document.getElementById('appendix');
      const r = document.createRange();
      r.setStartBefore(main.firstChild); r.setEndBefore(app);
      const before = r.toString();
      const cite = document.getElementById('cite');
      return {
        noId: [...main.querySelectorAll('h2, h3, h4')].filter(h => !h.id).map(h => h.textContent.trim()),
        toc: [...document.querySelectorAll('.toc a')].map(a => a.getAttribute('href')),
        tocBroken: [...document.querySelectorAll('.toc a')].filter(a => !document.querySelector(a.getAttribute('href'))).length,
        citeTop: cite ? cite.getBoundingClientRect().top : 1e9,
        intro: main.querySelector('p').textContent,
        codes: before.match(/\b[A-Z]{2,}\d{2}[A-Z0-9]*_\w+|\bCLA01\b|\bKS101EW\b|\bTS0\d\d\b|_BGC\b|egdpprac|epraccur|edispensary/g),
        dev: main.innerText.match(/\b(fetch\w*|pipeline|polygon|overlay|choropleth|manifest)\b/gi),
        dashes: /[–—]/.test(main.innerText),
        lastCol: [...document.querySelectorAll('#src-table thead th')].pop().textContent.trim(),
        rows: document.querySelectorAll('#src-rows tr[id^="src-"]').length,
        dwp: /DWP benefits/.test(document.getElementById('src-table').innerText),
        status: (document.querySelector('#src-census td:last-child') || {}).textContent || '',
        // "local authority (LA)" once, before the page first says LA alone.
        laIntro: (() => { const t = main.innerText, i = t.indexOf('local authority (LA)'), j = t.search(/\bLAs?\b/);
          return i >= 0 && t.split('local authority (LA)').length === 2 && j > i; })(),
        skip: (document.querySelector('a.skip') || {}).getAttribute && document.querySelector('a.skip').getAttribute('href'),
      };
    });
    ok(m.noId.length === 0, 'methodology: every heading has an id', m.noId.join(' | '));
    ok(m.toc.length >= 9 && m.tocBroken === 0, 'methodology: "On this page" links all land', m.toc.join(' '));
    ok(m.citeTop < DESK.height, 'methodology: How to cite starts on the first screen', 'top ' + Math.round(m.citeTop));
    ok(/map of England/.test(m.intro), 'methodology: intro says what the map covers', m.intro.slice(0, 80));
    ok(!m.codes, 'methodology: no ONS product code before the appendix', (m.codes || []).join(' '));
    ok(!m.dev, 'methodology: no developer words', (m.dev || []).join(' '));
    ok(!m.dashes, 'methodology: no em or en dashes');
    ok(m.lastCol === 'Last updated here' && m.rows > 15 && !m.dwp, 'methodology: live source table, DWP row dropped',
      m.lastCol + ' / ' + m.rows);
    ok(/^(Updated|Not updated|Partly updated)/.test(m.status), 'methodology: short status per source', m.status);
    ok(m.laIntro, 'methodology: introduces "local authority (LA)" once, at its first use');
    ok(m.skip === '#main', 'methodology: skip link');
    await page.close();

    const ph = await open(browser, 'methodology.html', PHONE);
    const s = await ph.page.evaluate(() => ({
      disp: getComputedStyle(document.querySelector('#src-rows tr')).display,
      label: getComputedStyle(document.querySelector('#src-rows td:nth-child(2)'), '::before').content,
      over: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    }));
    ok(s.disp === 'block' && /What it provides/.test(s.label), 'methodology: source table stacks as cards on a phone',
      s.disp + ' ' + s.label);
    ok(s.over <= 0, 'methodology: no sideways scroll on a phone', s.over + 'px');
    await checkAxe(ph.page, 'methodology.html (phone)');
    await ph.page.close();
  }

  // The source status note counts only sources that failed and have a row in
  // the table: custom and NaPTAN are skipped by design in London, and TfL and
  // PTAL in the regions, so they are not reported as stale (B8).
  {
    const real = JSON.parse(fs.readFileSync(path.join(ROOT, 'data/meta/manifest.json'), 'utf8'));
    const notes = {};
    for (const [name, patch] of [['skipped', { naptan: 'skipped', custom: 'skipped' }], ['failed', { naptan: 'skipped', crime: 'failed' }]]) {
      const m = JSON.parse(JSON.stringify(real));
      m.last_run = '2026-10-17T21:56:04+00:00';
      Object.keys(patch).forEach(k => { m.sources[k] = Object.assign({}, m.sources[k] || {}, { status: patch[k] }); });
      const page = await browser.newPage({ viewport: DESK });
      await page.route('**/data/meta/manifest.json', r => r.fulfill({ contentType: 'application/json', body: JSON.stringify(m) }));
      await page.goto(BASE + '/methodology.html');
      await page.waitForFunction(() => (document.getElementById('src-note') || {}).textContent, null, { timeout: 10000 }).catch(() => {});
      notes[name] = await page.evaluate(() => ({ note: document.getElementById('src-note').textContent,
        bad: [...document.querySelectorAll('#src-rows .status-bad')].map(e => e.closest('tr').id) }));
      await page.close();
    }
    ok(notes.skipped.note === 'Every source was updated on 17 October 2026.' && !notes.skipped.bad.length,
      'methodology: sources skipped by design are not reported as failures', JSON.stringify(notes.skipped));
    ok(/^One source could not be updated on 17 October 2026\. It is marked in the table above/.test(notes.failed.note) &&
       notes.failed.bad.join() === 'src-crime', 'methodology: a failed source is counted and marked, with the date in words', JSON.stringify(notes.failed));
  }

  // Licences travel with the figures (B9): any export or report holding a
  // TfL station or bus stop measure carries TfL's statements, and one with
  // air quality carries Defra's citation. Presentation view keeps the map's
  // credits in the picture (B10). The Ask panel promises no more than it can
  // keep (H19).
  {
    const { page, errors } = await open(browser, 'index.html?t=1', DESK);
    const lic = await page.evaluate(() => {
      const csvWith = k => { PH_CSV.setLevel('ward'); PH_CSV.pick(k); return PH_CSV.files().map(f => f.text).join('\n'); };
      const tfl = csvWith('bus_stops_800m');
      const no2 = csvWith('no2_ugm3');
      const notes = PH_CSV.notes();
      return { tfl, no2: no2, notes,
        plain: phCreditText(['imd_score']),
        report: reportProvenanceHtml('Test', ['rail_station_dist_m']),
        reportPlain: reportProvenanceHtml('Test', ['imd_score']) };
    });
    ok(/Powered by TfL Open Data/.test(lic.tfl) && /Geomni UK Map data/.test(lic.tfl) && /version 2\.0 of the Open Government Licence/.test(lic.tfl),
      'licences: a Data export CSV with a TfL measure carries TfL\'s statements', lic.tfl.split('\n')[1]);
    ok(/Crown copyright Defra via uk-air\.defra\.gov\.uk/.test(lic.no2) && /Powered by TfL Open Data/.test(lic.notes),
      'licences: air quality carries Defra\'s citation, and the notes carry the same lines');
    ok(/not affiliated with the NHS, with figures updated on \d{1,2} \w+ 20\d\d\. Contains public sector information licensed under the Open Government Licence v3\.0\.$/.test(lic.plain),
      'licences: every file carries the site and OGL credit', lic.plain);
    ok(/Powered by TfL Open Data/.test(lic.report) && !/TfL/.test(lic.reportPlain),
      'licences: a report footer names TfL only when it shows a TfL measure');
    const ask = await page.evaluate(() => {
      const b = document.getElementById('ai-toggle'); if (b) b.click();
      return new Promise(r => setTimeout(() => r({ caution: (document.getElementById('ai-caution') || {}).textContent || '',
        intro: ((document.querySelector('.ai-intro') || {}).textContent || '') }), 600));
    });
    ok(!/every figure in them comes from this map|built from the figures on this map/.test(ask.caution + ask.intro) &&
       /can still get things wrong/.test(ask.caution) && /can still make mistakes/.test(ask.intro),
      'Ask: the panel does not promise that every figure comes from the map', ask.caution);
    await page.keyboard.press('Escape');
    await setOv(page, 'imd_score_ward');
    await page.waitForTimeout(800);
    await page.click('#tb-more');
    await page.waitForTimeout(200);
    await page.click('#tb-png');
    await page.waitForTimeout(400);
    await page.click('text=This view');
    await page.waitForTimeout(800);
    const shot = await page.evaluate(() => {
      const attr = document.querySelector('.leaflet-control-attribution');
      const cap = document.getElementById('shot-caption');
      const banner = document.querySelector('.shot-banner');
      return { attr: !!attr && getComputedStyle(attr).visibility !== 'hidden' && attr.getClientRects().length > 0,
        zoom: [...document.querySelectorAll('.leaflet-control-zoom')].some(z => z.getClientRects().length && getComputedStyle(z).visibility !== 'hidden'),
        cap: cap ? cap.textContent : '', keep: banner ? /Keep the credit line/.test(banner.textContent) : false };
    });
    await page.waitForTimeout(4800);
    const later = await page.evaluate(() => {
      const banner = document.querySelector('.shot-banner'), done = document.getElementById('screenshot-done');
      const r = banner.getBoundingClientRect();
      return { mini: banner.classList.contains('mini'), h: Math.round(r.height), done: !!done && done.getClientRects().length > 0 };
    });
    await page.keyboard.press('Escape');
    await page.waitForTimeout(400);
    const gone = await page.evaluate(() => !document.getElementById('shot-caption') && !document.body.classList.contains('presenting'));
    ok(shot.attr && !shot.zoom && /Deprivation|deprivation/.test(shot.cap) && /pophealth\.uk/.test(shot.cap) &&
       /Crown copyright/.test(shot.cap) && /OpenStreetMap|Esri/.test(shot.cap) && shot.keep,
      'Presentation view keeps the attribution and adds a credit caption under the key', JSON.stringify(shot));
    ok(later.mini && later.h < 60 && later.done && gone, 'Presentation view: the instructions fade to a small Done button, and Esc still leaves',
      JSON.stringify([later, gone]));
    ok(errors.length === 0, 'no page errors (licences and Presentation view)', errors.join(' | '));
    await page.close();
  }

  // The statement and the privacy notice (B16, H16).
  {
    const texts = {};
    for (const p of ['accessibility.html', 'privacy.html']) {
      const page = await browser.newPage();
      await page.goto(BASE + '/' + p);
      texts[p] = await page.evaluate(() => document.querySelector('main').innerText.replace(/\s+/g, ' '));
      await page.close();
    }
    const a = texts['accessibility.html'], pr = texts['privacy.html'];
    ok(/last reviewed on 19 October 2026/.test(a) && !/Every control can be reached/.test(a) && !/the toolbar/i.test(a) &&
       /the pages around it: the Methodology page, the About and privacy page and this statement/.test(a),
      'accessibility: no false keyboard claims, and the review date is current');
    // P31 to P34 fixed the focus ring, the on and off states, the status
    // messages and the report's contrast, so only the rest are listed.
    ok(['2.1.1', '1.3.1'].every(c => a.includes(c)) && !['2.4.7', '4.1.2', '4.1.3', '1.4.3', '1.4.11'].some(c => a.includes(c)),
      'accessibility: the remaining known failures are listed, and the fixed ones are not');
    ok(/Your personal data and your rights/.test(pr) && /Harvey Seville is responsible/.test(pr) && /legitimate interest/.test(pr) &&
       /United States/.test(pr) && /background map does this whenever the map is open/.test(pr) && !/optional parts/.test(pr) &&
       /ph-ldr-swept/.test(pr) && !/devBannerDismissed/.test(pr) && /last updated on 19 October 2026/.test(pr),
      'privacy: controller, lawful basis, rights, transfers and storage rows as the site behaves');
  }

  // OHID's LA figures: a district's county figure is labelled as the
  // county's, an LA with none of them says so, adult smoking (stored as its
  // 18 to 64 breakdown) is left out, and the Census age bands are labelled by
  // the ages they cover. London has no districts, so the county and missing
  // cases run on a fixture served in place of the LA and ward files: Tower
  // Hamlets and Hackney carry a made-up county's figures, Barnet none.
  {
    const bd = JSON.parse(fs.readFileSync(path.join(ROOT, 'borough_data.json'), 'utf8'));
    const wd = JSON.parse(fs.readFileSync(path.join(ROOT, 'ward_data.json'), 'utf8'));
    const st = (() => { const t = fs.readFileSync(path.join(ROOT, 'data/map/indicators.js'), 'utf8');
      return JSON.parse(t.slice(t.indexOf('var PH_IND_STATS = ') + 19, t.lastIndexOf(';'))); })();
    const LAK = k => /^ft_/.test(k) && !/^ft_\d+$/.test(k);
    for (const c of ['E09000030', 'E09000012']) {
      bd[c].ft_from = {};
      Object.keys(bd[c]).filter(LAK).forEach(k => { bd[c].ft_from[k] = ['E10009999', 'Testshire']; bd[c][k] = bd.E09000030[k]; });
    }
    Object.keys(bd.E09000003).filter(LAK).forEach(k => delete bd.E09000003[k]);
    Object.values(wd.wards).forEach(w => {
      if (w.lad_code === 'E09000003') Object.keys(w.indicators).filter(LAK).forEach(k => delete w.indicators[k]);
      if (w.lad_code === 'E09000012') Object.keys(w.indicators).filter(LAK).forEach(k => { w.indicators[k] = bd.E09000030[k]; });
    });
    // The shipped files first, as they are.
    const real = await open(browser, 'index.html', DESK);
    const r0 = await real.page.evaluate(() => {
      showWard('Lansbury');
      const p = document.getElementById('wpanel');
      return { county: /county/i.test(p.innerText) || laAnyCountyFigure(), smokeRow: !!p.querySelector('[data-k="ft_smoking_prevalence_adults"]'),
        smokeOpt: !!document.querySelector('#ov option[value="ft_smoking_prevalence_adults"]'),
        smokeAsk: JSON.stringify(PH_ASSISTANT.runTool('list_indicators', {})).indexOf('ft_smoking_prevalence_adults') !== -1,
        smokeArea: 'ft_smoking_prevalence_adults' in (PH_ASSISTANT.runTool('get_area', { name: 'Lansbury', level: 'ward' }).indicators || {}),
        u: indLabel('census_under16_pct'), w: indLabel('census_working_age_pct'), den: indicatorFacts('claimant_rate_pct').den,
        exportSmoke: (() => { PH_CSV.setLevel('borough'); PH_CSV.pick('ft_smoking_prevalence_adults'); PH_CSV.pick('ft_suicide_rate'); return PH_CSV.build().header.join('|'); })() };
    });
    const bands = st.census_age_bands === '0-15';
    ok(!r0.county, 'London: no LA figure is called a county\'s');
    // Adult smoking is left out while the stored series is not at its
    // headline age, and comes back once the refresh has checked it.
    const smokeHeld = !st.ft_age_checked || (st.ft_age_mismatch || []).includes('ft_smoking_prevalence_adults');
    ok(smokeHeld ? (!r0.smokeRow && !r0.smokeOpt && !r0.smokeAsk && !r0.smokeArea && !/smok/i.test(r0.exportSmoke))
                 : (r0.smokeRow && r0.smokeAsk && r0.smokeArea && /Adults who smoke/.test(r0.exportSmoke)),
      smokeHeld ? 'adult smoking at the wrong age is left out of the profile, the picker, the export and Ask'
                : 'adult smoking at its headline age is back in the profile, the export and Ask', JSON.stringify(r0));
    ok(bands ? (r0.u === 'Aged under 16' && /16 to 64/.test(r0.w + r0.den))
             : (r0.u === 'Aged under 15' && r0.w === 'Aged 15 to 64' && r0.den === 'Residents aged 15 to 64 at the 2021 Census'),
      'the Census age bands are labelled as the ages indicators.js says they cover', [st.census_age_bands, r0.u, r0.w, r0.den].join(' / '));
    await real.page.close();

    const fx = await (async () => {
      const page = await browser.newPage({ viewport: { width: DESK.width, height: DESK.height } });
      const errors = []; page.on('pageerror', e => errors.push(e.message));
      await page.route(/\/borough_data\.json/, r => r.fulfill({ body: JSON.stringify(bd), contentType: 'application/json' }));
      await page.route(/\/ward_data\.json/, r => r.fulfill({ body: JSON.stringify(wd), contentType: 'application/json' }));
      await page.route(/ward_(core|rest)\.json/, r => r.fulfill({ status: 404, body: '' }));
      await page.goto(BASE + '/index.html');
      await page.waitForTimeout(SETTLE);
      return { page, errors };
    })();
    const r1 = await fx.page.evaluate(() => {
      showWard('Lansbury');
      const p = document.getElementById('wpanel');
      const out = { ctx: (p.querySelector('.sr-ctx') || {}).innerText || '', lh: (p.querySelector('.sr-lh .sr-sub-note') || {}).innerText || '',
        pills: [...p.querySelectorAll('.sr-ctx .gpill:not(.gpill-note)')].map(e => e.textContent), key: p.querySelector('.prof-key').innerText,
        e07: laFigCounty('E07000033', 'ft_suicide_rate') === null ? 'own' : 'county' };
      const bn = Object.values(WARD_DATA).find(w => w.lad_code === 'E09000003');
      showWard(bn.name);
      out.missing = (document.querySelector('#wpanel .sr-missing') || {}).innerText || '';
      PH_CSV.setLevel('borough'); PH_CSV.pick('ft_suicide_rate');
      const b = PH_CSV.build();
      out.head = b.header; out.th = (b.table.find(r => r[1] === 'Tower Hamlets') || []).slice(-1)[0];
      out.cam = (b.table.find(r => r[1] === 'Camden') || []).slice(-1)[0];
      return out;
    });
    ok(/^\s*Figures for the whole of Testshire/.test(r1.ctx) &&
       /OHID publishes these only for Testshire as a whole, which takes in Tower Hamlets and one other district, so every ward in those districts shows the same figure\. They describe the county rather than Tower Hamlets or this ward\./.test(r1.ctx) &&
       r1.pills.length && r1.pills.every(t => t === 'county figure'),
      'a district\'s county figures are titled, noted and marked as the county\'s', r1.ctx.slice(0, 300) + ' / ' + r1.pills.join(','));
    ok(/further down as OHID’s official figure for Testshire as a whole, the county Tower Hamlets is part of\./.test(r1.lh) && /county figure/.test(r1.key),
      'the life expectancy note and the key name the county', r1.lh.slice(-160));
    ok(/^OHID’s figures for the whole of Barnet, such as life expectancy.*are missing from this version of the map\. We are working on it\.$/.test(r1.missing.trim()),
      'an LA with none of OHID\'s figures says so in its profile', r1.missing);
    ok(r1.head.indexOf('Area OHID\'s LA figures describe') !== -1 && r1.th === 'Testshire' && r1.cam === 'Camden',
      'the Data export names the area OHID\'s LA figures describe', r1.head.join('|') + ' / ' + r1.th + ' / ' + r1.cam);
    ok(r1.e07 === 'county', 'a district in an LA file without ft_from is taken to carry its county\'s figure');
    await fx.page.click('.mode-btn[data-mode="compare"]');
    await fx.page.waitForTimeout(800);
    const r2 = await fx.page.evaluate(async () => {
      PH_COMPARE.clear(); PH_COMPARE.setLevel('borough');
      await new Promise(r => setTimeout(r, 400));
      const m = document.getElementById('cmp-add-metric');
      if (m) { m.value = 'ft_suicide_rate'; m.dispatchEvent(new Event('change', { bubbles: true })); }
      for (const n of ['Tower Hamlets', 'Hackney']) {
        const a = document.getElementById('cmp-add-area');
        a.value = n; a.dispatchEvent(new Event('change', { bubbles: true }));
        await new Promise(r => setTimeout(r, 200));
      }
      const marks = [...document.querySelectorAll('#cmp-cards .cmp-c-row')].filter(r => /Suicide/.test(r.innerText))
        .map(r => r.querySelector('.hi, .lo') ? 'arrow' : '');
      return { caution: document.getElementById('cmp-caution').innerText, marks,
        same: PH_COMPARE.figures('Tower Hamlets').ft_suicide_rate === BOROUGH_DATA.E09000030.ft_suicide_rate };
    });
    ok(/Suicide rate is OHID’s published figure, which for a district is its county’s\./.test(r2.caution) &&
       /The other figures are calculated from the wards in each LA, with counts added up and the rest weighted by population, so they are not published LA statistics\./.test(r2.caution) &&
       !/published for each LA, so here/.test(r2.caution),
      'Compare explains OHID\'s figures and the calculated ones in one note', r2.caution);
    ok(r2.same && r2.marks.length === 2 && r2.marks.every(x => !x),
      'Compare reads OHID\'s LA figure as published, and a tie gets no arrow', JSON.stringify(r2.marks));
    ok(!fx.errors.length, 'no page errors with district and missing-LA figures', fx.errors.join(' | '));
    await fx.page.close();
  }

  // Review round 2: OHID's notes (H2), ward figures weighted by residents and
  // their coverage (H3, H4), PTAL's gap (H5), England's 100 on ratio keys
  // (H6), no Core20 ward badge (H7) and Custom area's arithmetic (H9).
  {
    const { page, errors } = await open(browser, 'index.html', DESK);
    const files = await page.evaluate(async () => {
      const g = u => fetch(u).then(r => r.json());
      const [bd, wd, cnt] = await Promise.all([g('borough_data.json'), g('ward_data.json'), g('lsoa_census_counts.json')]);
      const W = Object.values(wd.wards);
      const byName = n => W.find(w => w.name === n) || {};
      const covered = W.filter(w => w.cover && Object.keys(w.cover).some(k => /^ft_\d+$/.test(k)));
      const notes = Object.values(bd).filter(r => r.ft_notes).map(r => r.ft_notes);
      return {
        hackney: (bd.E09000012 && bd.E09000012.ft_notes) || {},
        nLe: notes.filter(n => /provisional/i.test(n.ft_life_expectancy_male || '')).length,
        withheld: Object.values(bd).some(r => Object.keys(r.ft_notes || {}).some(k => /not published|cannot be calculated/i.test(r.ft_notes[k]) && typeof r[k] === 'number')),
        coverOk: covered.length > 0 && covered.every(w => Object.keys(w.cover).every(k => w.cover[k] >= 80 && w.cover[k] < 100 && w.indicators[k] != null)),
        ncg: byName('New Cross Gate').indicators ? byName('New Cross Gate').indicators.ft_932831 : 'missing',
        poplar: byName('Poplar').indicators ? byName('Poplar').indicators.ft_93259 : null,
        rv: byName('Royal Victoria').indicators ? byName('Royal Victoria').indicators.ptal_score : 'missing',
        c20: byName('Hornsey').core20_pop_pct,
        cnt: !!(cnt && cnt.keys && cnt.keys.indexOf('census_social_rented_pct') !== -1 && Object.keys(cnt.rows).length > 4000),
      };
    });
    ok(/Hackney and City of London combined/.test(files.hackney.ft_mmr_2_doses_age5 || '') && files.nLe >= 30 && !files.withheld,
      'borough_data.json carries OHID\'s notes, and no withheld value', JSON.stringify(files).slice(0, 300));
    ok(files.coverOk && files.ncg == null && Math.abs(files.poplar - 197.67) < 0.05,
      'ward Local Health figures are weighted by residents, marked when partly covered, and blank under 80%', JSON.stringify(files));
    ok(files.rv == null && typeof files.c20 === 'number' && files.cnt,
      'PTAL is blank for a ward mostly without figures, wards carry a Core20 share, and the Census counts ship', JSON.stringify(files));

    const r = await page.evaluate(async () => {
      await ensureWardDetail();
      const out = {};
      const hk = Object.values(WARD_DATA).find(w => w.lad_code === 'E09000012');
      showWard(hk.name);
      const pills = sel => [...document.querySelectorAll('#wpanel ' + sel)].map(e => e.closest('.sr').getAttribute('data-k') + '=' + e.textContent.replace(/\..*$/, ''));
      out.hk = pills('.sr-ctx .gpill-note');
      const part = Object.values(WARD_DATA).find(w => w.cover && Object.keys(w.cover).some(k => /^ft_\d+$/.test(k)));
      showWard(part.name);
      out.part = pills('.sr-lh ~ .sr .gpill-note, .sr-lh .gpill-note, .gpill-note');
      out.partNote = (document.querySelector('#wpanel .sr-lh .sr-sub-note') || {}).textContent || '';
      out.card = indicatorCardHTML('ft_life_expectancy_male');
      out.ptal = indicatorCardHTML('ptal_score');
      out.c20card = indicatorCardHTML('core20_lsoa_count');
      return out;
    });
    ok(r.hk.some(t => /^ft_mmr_2_doses_age5=combined figure/.test(t)) && r.hk.some(t => /^ft_life_expectancy_(fe)?male=provisional/.test(t)),
      'the profile marks Hackney\'s combined and provisional LA figures', r.hk.join(', '));
    ok(r.part.some(t => /based on \d\d% of residents/.test(t)) && /rests on that part of the ward only/.test(r.partNote),
      'a ward health figure on part of the ward says what share of residents it rests on', r.part.join(', ') + ' / ' + r.partNote.slice(-120));
    ok(/Provisional: (the Office for Health Improvement and Disparities \(OHID\)|OHID) based this on provisional death and population figures, for \d+ of the \d+ LAs\./.test(r.card),
      'the life expectancy card\'s Caution row gives OHID\'s note', r.card.replace(/<[^>]+>/g, ' ').slice(0, 400));
    ok(/LSOAs of 2011/.test(r.ptal) && /fewer than four in five of its residents is left blank/.test(r.ptal),
      'the PTAL card explains the 2011 LSOAs and the ward rule', r.ptal.replace(/<[^>]+>/g, ' ').slice(0, 500));
    ok(/NHS England’s own Core20 figures may still use the 2019 index/.test(r.c20card),
      'the Core20 card says which deciles it uses', r.c20card.replace(/<[^>]+>/g, ' ').slice(0, 300));

    // H6: a tick at England's 100 on both keys for a ratio, and none otherwise.
    await setOv(page, 'ft_93250');
    await page.waitForTimeout(1500);
    const eng = await page.evaluate(() => ({
      side: !!document.querySelector('#sc-grad .eng-tick'), strip: !!document.querySelector('#mls-grad .eng-tick'),
      label: (document.querySelector('#mls-grad .eng-tick') || {}).textContent || '',
    }));
    await setOv(page, 'imd_score_ward');
    await page.waitForTimeout(1200);
    const none = await page.evaluate(() => !document.querySelector('.eng-tick') && !(document.getElementById('mls-eng') || {}).textContent);
    ok(eng.side && eng.strip && eng.label === 'England' && none,
      'a standardised ratio\'s keys mark England\'s 100, and other indicators\' do not', JSON.stringify(eng) + ' / ' + none);
    const st = await page.evaluate(() => [
      _englandMarkState('ft_93250', { lo: 80, hi: 95, min: 70, max: 99 }),
      _englandMarkState('ft_93250', { lo: 101, hi: 140, min: 101, max: 150 }),
      _englandMarkState('ft_93250', { lo: 50, hi: 150, min: 40, max: 160 }),
      _englandMarkState('imd_score_ward', { lo: 50, hi: 150, min: 40, max: 160 }),
      _englandMarkState('ft_93239', { lo: 19.9, hi: 82.4, min: 5, max: 140 })]);
    ok(st[0].note === 'Every area shown is below England’s level of 100' && st[1].note === 'Every area shown is above England’s level of 100' &&
       st[2].pos === 50 && !st[2].note && st[3] === null &&
       st[4].pos === 100 && st[4].note === 'Most areas shown are below England’s level of 100',
      'when 100 is beyond every area, the key says so instead', JSON.stringify(st));

    // H7: no Core20 ward badge; the tile gives the count and the residents' share.
    const rep = await page.evaluate(async () => {
      await Promise.all([ensureAllLayerData(), ensureLsoaImd(), ensureLsoaData(), ensureWardDetail(), ensureGreenspaceData()]);
      let html = '';
      _generateWardReport({ sink: h => { html = h; } }, 'Hornsey', {});
      const d = new DOMParser().parseFromString(html, 'text/html');
      const kpi = [...d.querySelectorAll('.kpi')].map(k => k.textContent.replace(/\s+/g, ' ').trim()).find(t => /Core20|most deprived fifth/.test(t)) || '';
      return { kicker: (d.querySelector('.kicker') || {}).textContent || '', kpi };
    });
    ok(rep.kicker && !/Core20/i.test(rep.kicker) && /1 of 11/.test(rep.kpi) && /home to [\d.]+% of residents/.test(rep.kpi),
      'the ward report has no Core20 ward badge, and its tile gives the count and the residents\' share', JSON.stringify(rep));

    // H9: Custom area reports an average decile, sums the Census counts and
    // leaves out the address count.
    await page.click('.mode-btn[data-mode="report"]').catch(() => {});
    await page.waitForTimeout(1500);
    const rb = await page.evaluate(async () => {
      const codes = LSOA_IMD.features.filter(f => f.properties.ward === 'Lansbury').map(f => f.properties.code);
      PH_REPORT.apply({ name: 'Lansbury test', codes });
      for (let i = 0; i < 40 && !(PH_REPORT.aggregate().method || {}).census_social_rented_pct; i++) await new Promise(r => setTimeout(r, 250));
      for (let i = 0; i < 40 && PH_REPORT.aggregate().method.census_social_rented_pct !== 'counts'; i++) await new Promise(r => setTimeout(r, 250));
      const a = PH_REPORT.aggregate();
      const tiles = [...document.querySelectorAll('#rb-summary .rb-stat')].map(e => e.innerText.replace(/\s+/g, ' '));
      return { keys: Object.keys(a.indicators), m: a.method, dec: a.indicators.imd_decile_mean, tiles };
    });
    ok(rb.keys.indexOf('imd_decile') === -1 && rb.keys.indexOf('gb_total_uprn') === -1 && typeof rb.dec === 'number' &&
       rb.m.census_social_rented_pct === 'counts' && rb.m.census_population === 'summed' && rb.m.fuel_poverty_pct === 'households' &&
       rb.tiles.some(t => /^Average deprivation decile \(IMD 2025\) \d\.\d$/.test(t)),
      'Custom area gives an average decile to one place, sums the Census counts and weights fuel poverty by households', JSON.stringify(rb).slice(0, 400));
    ok(!errors.length, 'no page errors (review round 2 checks)', errors.join(' | '));
    await page.close();

    const meth = await open(browser, 'methodology.html', DESK);
    const mt = await meth.page.evaluate(() => document.body.innerText);
    ok(!/cover\s+every one/.test(mt) && /fewer than four in five of its residents, we show no figure for the ward/.test(mt) &&
       /Census day, 21 March 2021/.test(mt) && !/mid-2022/.test(mt) && /measures of households|summed counts\s+behind it, as a ward's is/.test(mt),
      'the methodology describes the ward coverage rule, the Census population and Custom area\'s arithmetic', '');
    await meth.page.close();
  }

  // Review round 2, polish (P35 to P50): tools, sharing, reports and downloads.
  {
    const fsx = require('fs');
    const { isMobile, hasTouch, ...viewport } = DESK;
    const page = await browser.newPage({ viewport });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('https://api.postcodes.io/**', r => r.fulfill({ status: 200, contentType: 'application/json',
      body: JSON.stringify({ status: 200, result: { postcode: 'M1 1AE', latitude: 53.4774, longitude: -2.2309,
        region: 'North West', admin_ward: 'Piccadilly', admin_district: 'Manchester', codes: {} } }) }));
    await page.goto(BASE + '/index.html?t=1');
    await page.waitForTimeout(SETTLE);
    ok(await page.evaluate(() => !document.querySelector('meta[property="og:url"]') && !!document.querySelector('link[rel="canonical"]')),
      'sharing: no og:url, so a shared link previews and opens as the address shared (P42)');

    // P36: a postcode on another region's map.
    await page.click('.mode-btn[data-mode="directory"]');
    await page.waitForFunction(() => document.querySelectorAll('#dir-types [data-dtype]').length > 3, null, { timeout: 30000 }).catch(() => {});
    await page.fill('#dir-q', 'M1 1AE');
    await page.waitForTimeout(700);
    await page.click('#dir-near-go');
    await page.waitForTimeout(800);
    const dn = await page.evaluate(() => ({ msg: (document.getElementById('dir-offer-msg') || {}).textContent,
      link: (document.querySelector('#dir-offer-msg a') || {}).href || '', near: !!PH_DIR.near() }));
    ok(dn.msg === 'M1 1AE is in the North West, which this map does not cover, so open the North West map to see the places near it.' &&
       /r=north-west/.test(dn.link) && !dn.near, 'directory: a postcode on another map links there rather than listing places 240 km away (P36)', JSON.stringify(dn));

    // P37: choosing LAs keeps the indicators already chosen.
    await page.click('.mode-btn[data-mode="csv"]');
    await page.waitForTimeout(800);
    const ex = await page.evaluate(async () => {
      PH_CSV.setLevel('ward');
      ['census_bad_health_pct', 'crime_rate_per_1000', 'imd_score_ward', 'census_over65_pct', 'claimant_rate_pct', 'ft_93283', 'ft_smoking_prevalence_adults']
        .forEach(k => PH_CSV.pick(k));
      const before = PH_CSV.columns().length;
      PH_CSV.setLevel('borough');
      for (let i = 0; i < 40 && /Loading/i.test(document.getElementById('cb-count').textContent); i++) await new Promise(r => setTimeout(r, 250));
      const at = { n: PH_CSV.columns().length, note: (document.getElementById('cb-dropped') || {}).textContent || '',
        marks: document.querySelectorAll('#cb-cols .cb-only').length, cols: PH_CSV.build().header.length };
      PH_CSV.setLevel('ward');
      return { before, at, after: PH_CSV.columns().length };
    });
    ok(ex.before === 7 && ex.at.n === 7 && ex.after === 7 && ex.at.marks > 0 &&
       /^\w+ of your indicators are not published for LAs, so the LA file leaves them out, and they will be back in the file when you choose another size of area\.$/.test(ex.at.note),
      'export: choosing LAs marks the indicators it lacks and keeps them chosen for the other sizes (P37)', JSON.stringify(ex));

    // P39, P41: Query's groups, its operators, and equals at the shown precision.
    await page.click('.mode-btn[data-mode="query"]');
    await page.waitForTimeout(600);
    await page.click('#q-add-cond');
    await page.waitForTimeout(300);
    const qg = await page.evaluate(() => {
      const s = document.querySelector('.q-f-ind'), g = {};
      s.querySelectorAll('optgroup').forEach(o => { g[o.label] = [...o.querySelectorAll('option')].map(x => x.value); });
      const where = k => Object.keys(g).find(l => g[l].includes(k)) || '';
      return { groups: Object.keys(g), claimant: where('claimant_rate_pct'), ptal: where('ptal_score'),
        ops: [...document.querySelector('.q-f[data-op]').options].map(o => o.textContent).join('|') };
    });
    ok(qg.claimant === 'Work, money and education' && qg.ptal === 'Transport' && qg.groups.indexOf('Health figures for each LA') !== -1 &&
       !qg.groups.some(g => /^(Crime|Local Health \(ward\)|Other)$/.test(g)) && qg.ops === 'at most|at least|equals',
      'query: indicators grouped by the picker\'s themes, with plain operator names (P39, P41)', JSON.stringify(qg));
    const shown = await page.evaluate(() => {
      const w = Object.values(WARD_DATA).find(w => w.name === 'Stonebridge');
      const v = Math.round(w.indicators.census_bad_health_pct * 10) / 10;
      PH_QUERY.apply({ services: [], scope: '', conds: [['census_bad_health_pct', 'eq', String(v)]] });
      return v;
    });
    await page.click('#q-run');
    await page.waitForTimeout(1500);
    const qr = await page.evaluate(() => [...document.querySelectorAll('.q-row')].map(b => b.getAttribute('data-ward')));
    ok(qr.includes('Stonebridge'), 'query: "equals ' + shown + '" finds Stonebridge, shown as ' + shown + '% (P41)', qr.slice(0, 8).join(', '));
    // P45: a byte order mark, headings with their period, and notes that start with the query.
    const [qd] = await Promise.all([page.waitForEvent('download', { timeout: 8000 }).catch(() => null), page.click('#q-csv')]);
    const qb = qd ? fsx.readFileSync(await qd.path()) : Buffer.from('');
    const [qn] = await Promise.all([page.waitForEvent('download', { timeout: 8000 }).catch(() => null), page.click('#q-notes')]);
    const qt = qn ? fsx.readFileSync(await qn.path(), 'utf8') : '';
    ok(qb[0] === 0xEF && qb[1] === 0xBB && qb[2] === 0xBF && /"Bad or very bad health \(%, 2021\)"/.test(qb.toString('utf8').split('\n')[0]) &&
       /^PopHealth Map: notes for your query\n\nWards anywhere in London, where bad or very bad health is exactly [\d.]+%, using figures updated on /.test(qt),
      'query: the CSV starts with a byte order mark, heads its columns with their period, and its notes start with the query (P45)',
      qb.toString('utf8').split('\n')[0] + ' / ' + qt.slice(0, 160));

    // P40: Compare at its cap, and a level change that clears the pins.
    await page.click('.mode-btn[data-mode="compare"]');
    await page.waitForTimeout(800);
    await page.evaluate(() => ['Lansbury', 'Stonebridge', 'Holloway', 'Bishopsgate']
      .forEach(n => wLyr.getLayers().find(l => l.feature.properties.WD24NM === n).fire('click')));
    await page.waitForTimeout(500);
    const selBefore = await page.evaluate(() => selW);
    await page.evaluate(() => wLyr.getLayers().find(l => l.feature.properties.WD24NM === 'Barnes').fire('click'));
    await page.waitForTimeout(400);
    const cap = await page.evaluate(() => ({ pins: PH_COMPARE.pinned(), sel: selW,
      note: (document.querySelector('#tab-compare .ph-note') || {}).textContent || '' }));
    ok(cap.pins.length === 4 && cap.pins.indexOf('Barnes') === -1 && cap.sel === selBefore &&
       /You have pinned four wards, which is as many as fit side by side\./.test(cap.note),
      'compare: a fifth ward is refused with the reason, and the outline stays where it was (P40)', JSON.stringify(cap));
    const [cc] = await Promise.all([page.waitForEvent('download', { timeout: 8000 }).catch(() => null), page.click('#cmp-csv')]);
    const ch = cc ? fsx.readFileSync(await cc.path(), 'utf8') : '';
    ok(ch.charCodeAt(0) === 0xFEFF && /\(%, 2021\)/.test(ch.split('\n')[0]), 'compare: the CSV has a byte order mark and periods in its headings (P45)', ch.split('\n')[0]);
    await page.evaluate(() => document.querySelector('#cmp-levels [data-cmplvl="borough"]').click());
    await page.waitForTimeout(500);
    const cl = await page.evaluate(() => ({ pins: PH_COMPARE.pinned().length,
      note: [...document.querySelectorAll('#tab-compare .ph-note')].map(n => n.textContent).join(' / ') }));
    ok(cl.pins === 0 && /Your four wards were cleared, because Compare works on one size of area at a time\./.test(cl.note),
      'compare: switching size says where the pins went (P40)', JSON.stringify(cl));

    // P45: the Custom area download is a ZIP of one-table files.
    await page.click('.mode-btn[data-mode="report"]');
    await page.waitForTimeout(1200);
    const rf = await page.evaluate(async () => {
      await ensureLsoaData();
      const codes = LSOA_IMD.features.filter(f => f.properties.ward === 'Lansbury').map(f => f.properties.code);
      PH_REPORT.apply({ name: 'Poplar & Bow  test', codes });
      await new Promise(r => setTimeout(r, 1500));
      return PH_REPORT.files().map(x => ({ name: x.name, head: x.text.split('\n')[0] }));
    });
    const [zd] = await Promise.all([page.waitForEvent('download', { timeout: 8000 }).catch(() => null), page.click('#rb-csv')]);
    ok(rf.length === 3 && rf[0].head === 'Indicator,Value,Unit,Period,How it was combined,Source (publisher)' &&
       /-lsoas\.csv$/.test(rf[1].name) && /-notes\.txt$/.test(rf[2].name) && !!zd && /^Poplar and Bow test-[\d-]+\.zip$/.test(zd.suggestedFilename()),
      'custom area: a ZIP of the figures, the LSOAs and the notes, named without the ampersand (P45)', JSON.stringify(rf) + ' ' + (zd && zd.suggestedFilename()));

    // P38: the two-indicator map across a level change.
    await page.click('.mode-btn[data-mode="explore"]');
    await page.waitForTimeout(400);
    await page.evaluate(() => { const o = document.getElementById('ov'); o.value = 'census_bad_health_pct'; o.dispatchEvent(new Event('change')); });
    await page.waitForTimeout(1200);
    await page.click('#dl-seg button[data-dl="ward"]');
    await page.waitForTimeout(1200);
    await page.evaluate(() => window._bivSet('crime_rate_per_1000'));
    await page.waitForTimeout(500);
    const b1 = await page.evaluate(() => document.querySelector('#dl-seg button[data-dl="lsoa"]').classList.contains('biv-lacks'));
    await page.click('#dl-seg button[data-dl="lsoa"]');
    await page.waitForTimeout(1500);
    const b2 = await page.evaluate(() => ({ ov2: curOv2, hint: (document.getElementById('dl-hint') || {}).textContent || '' }));
    await page.click('#dl-seg button[data-dl="ward"]');
    await page.waitForTimeout(1500);
    const b3 = await page.evaluate(() => curOv2);
    ok(b1 && b2.ov2 === 'none' && b3 === 'crime_rate_per_1000' &&
       b2.hint === 'Crime rate per 1,000 residents has no LSOA figures, so the map is showing Bad or very bad health on its own, and the comparison will come back when you choose Ward again.',
      'two indicators: a level the second lacks is greyed, leaving it says why, and coming back restores it (P38)', JSON.stringify([b1, b2, b3]));

    // P43: opening a tool is a step Back can undo.
    const h0 = await page.evaluate(() => history.length);
    await page.click('.mode-btn[data-mode="compare"]');
    await page.waitForTimeout(800);
    const h1 = await page.evaluate(() => ({ n: history.length, s: location.search }));
    await page.goBack();
    await page.waitForTimeout(SETTLE);
    const hb = await page.evaluate(() => ({ s: location.search, tool: document.querySelector('.mode-rail .mode-btn[aria-selected="true"]').dataset.mode }));
    ok(h1.n === h0 + 1 && /mode=compare/.test(h1.s) && hb.tool === 'explore' && !/mode=compare/.test(hb.s),
      'history: Back after opening Compare returns to the map, not the page before the site (P43)', JSON.stringify([h0, h1, hb]));

    // P48: Ctrl+P with the Area profile open prints the profile.
    await page.evaluate(() => { const l = wLyr.getLayers().find(l => l.feature.properties.WD24NM === 'Lansbury'); l.fire('click'); });
    await page.waitForTimeout(800);
    await page.click('#ws-profile');
    await page.waitForTimeout(600);
    await page.evaluate(() => window.dispatchEvent(new Event('beforeprint')));
    await page.emulateMedia({ media: 'print' });
    const pr = await page.evaluate(() => ({ map: getComputedStyle(document.getElementById('map-wrap')).display,
      hdr: getComputedStyle(document.querySelector('.hdr')).display, w: Math.round(document.getElementById('wpanel').getBoundingClientRect().width),
      open: [...document.querySelectorAll('#wpanel .cat-body')].every(b => getComputedStyle(b).display === 'block') }));
    const ppdf = await page.pdf({ format: 'A4' });
    const ppages = (ppdf.toString('latin1').match(/\/Type\s*\/Page[^s]/g) || []).length;
    await page.emulateMedia({ media: 'screen' });
    await page.evaluate(() => window.dispatchEvent(new Event('afterprint')));
    ok(pr.map === 'none' && pr.hdr === 'none' && pr.w > 600 && pr.open && ppages >= 2,
      'print: the Area profile prints full width with every section open (P48)', JSON.stringify(pr) + ', ' + ppages + ' pages');

    // P47: the sheet's Open printable report opens it without printing.
    const [rep] = await Promise.all([page.waitForEvent('popup', { timeout: 20000 }).catch(() => null), page.click('#ws-pdf')]);
    if (rep) {
      await rep.evaluate(() => { window.print = () => { window.__printed = (window.__printed || 0) + 1; }; }).catch(() => {});
      await rep.waitForTimeout(2500);
      ok((await rep.evaluate(() => window.__printed || 0)) === 0, 'report: the ward sheet\'s Open printable report does not print by itself (P47)');
      await rep.close();
    } else ok(false, 'report: the ward sheet\'s Open printable report opens (P47)');

    // P46, P49, P50: the reports' wording, and charts that explain themselves.
    const R = await page.evaluate(async () => {
      await Promise.all([ensureAllLayerData(), ensureLsoaImd(), ensureLsoaData(), ensureWardDetail(), ensureGreenspaceData()]);
      const ward = wn => { let h = ''; _generateWardReport({ sink: x => { h = x; } }, wn); return h; };
      return { Lansbury: ward('Lansbury'), lsoa: _buildLSOAReportHtml('E01004243') };
    });
    ok(/LSOAs are the small areas, of about 1,500 residents each, that official statistics are published for\. Core20 means the most deprived fifth of areas in England, which the NHS gives priority to\./.test(R.Lansbury) &&
       /LSOAs are the small areas/.test(R.lsoa) && /kpi-lbl">Disabled residents \(all\)</.test(R.Lansbury) &&
       /Parks and green spaces <span class="count">(\d+|[\d,]+ sites)</.test(R.Lansbury),
      'report: the footer explains LSOA and Core20, the tile matches the chart, and the parks count says what it counts (P49, P50)');
    const rp = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    await rp.goto(BASE + '/robots.txt');
    await rp.setContent(R.Lansbury);
    await rp.waitForTimeout(800);
    const [sd] = await Promise.all([rp.waitForEvent('download', { timeout: 8000 }).catch(() => null),
      rp.evaluate(() => [...document.querySelectorAll('.panel')].find(x => /Compared with/.test(x.textContent)).querySelector('.chart-dl').click())]);
    const sv = sd ? fsx.readFileSync(await sd.path(), 'utf8') : '';
    ok(/>Lansbury: compared with the London ward median,/.test(sv) && />Census 2021</.test(sv) && /better than the median,/.test(sv) &&
       /Source: ONS Census 2021\. PopHealth Map, pophealth\.uk/.test(sv),
      'report: a downloaded chart carries its title, area, key and source (P46)', sv.slice(0, 200));
    await rp.close();

    // P35: Reset after layers, a focus, a network and a selection.
    await page.click('.mode-btn[data-mode="explore"]').catch(() => {});
    await page.evaluate(() => { const b = document.getElementById('borough-focus'); b.value = 'Hackney'; b.dispatchEvent(new Event('change')); });
    await page.waitForTimeout(800);
    await page.evaluate(() => ['tg', 'tph', 'vcse-tab-toggle'].forEach(id => { const e = document.getElementById(id);
      if (e && !e.checked) { e.checked = true; e.dispatchEvent(new Event('change')); } }));
    await page.waitForTimeout(1500);
    await page.evaluate(() => { const s = document.getElementById('pcn-focus'); if (s && s.options.length > 1) { s.value = s.options[1].value; s.dispatchEvent(new Event('change')); } });
    await page.waitForTimeout(500);
    await page.evaluate(() => { const l = wLyr.getLayers().find(l => l.feature.properties.WD24NM === 'Dalston'); if (l) l.fire('click'); });
    await page.waitForTimeout(150);
    await page.click('#tb-reset');
    await page.waitForTimeout(2500);
    ok(!errors.length, 'no page errors (review round 2 polish checks, including Reset after layers, a focus and a selection, P35)', errors.join(' | '));
    await page.close();
  }

  // Review round 2, polish (P14 to P34): first visit, phones and accessibility.
  {
    const page = await browser.newPage({ viewport: DESK });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    // postcodes.io is answered locally, with the LSOA the postcode is in
    // taken from the boundaries once they have loaded (see below).
    const PCL = { code: '', name: '' };
    await page.route('https://api.postcodes.io/**', r => r.fulfill({ status: 200, contentType: 'application/json',
      body: JSON.stringify({ status: 200, result: { postcode: 'E1 6AN', latitude: 51.520014, longitude: -0.072097,
        admin_ward: 'Spitalfields & Banglatown', admin_district: 'Tower Hamlets', region: 'London', lsoa: PCL.name,
        codes: { admin_ward: 'E05009333', lsoa: PCL.code } } }) }));
    await page.goto(BASE + '/index.html?t=1');
    await page.waitForTimeout(SETTLE);
    // P14: the key indicators lead with a health figure anyone can read.
    const keys = await page.evaluate(() => [...document.querySelectorAll('#ov-chips [data-ov]')].map(b => b.getAttribute('data-ov')));
    ok(keys.join() === 'imd_decile,census_bad_health_pct,fuel_poverty_pct,no2_ugm3',
      'polish: the key indicators are the decile, bad health, fuel poverty and NO2, not a negative score', keys.join());
    // P34: the indicator card follows its button.
    ok(await page.evaluate(() => document.getElementById('ov-cur-btns').nextElementSibling.id === 'ov-info-panel'),
      'polish: the indicator card sits straight after About this indicator in the page order');
    // P18: the level hint goes when the next indicator is chosen.
    await setOv(page, 'no2_ugm3');
    await page.waitForFunction(() => document.getElementById('dl-hint').classList.contains('show') && /Nitrogen/.test(document.getElementById('dl-hint').textContent), null, { timeout: 30000 }).catch(() => {});
    await setOv(page, 'pm25_ugm3');
    await page.waitForTimeout(1500);
    const hint = await page.evaluate(() => { const h = document.getElementById('dl-hint'); return h.classList.contains('show') ? h.textContent : ''; });
    ok(!/Nitrogen/.test(hint), 'polish: the Area level hint never names the indicator before', hint);
    // P32: the level on show is pressed.
    const pr = await page.evaluate(() => ({ g: document.getElementById('dl-seg').getAttribute('role'),
      p: [...document.querySelectorAll('#dl-seg button')].map(b => (b.classList.contains('active') ? 'on' : 'off') + ':' + b.getAttribute('aria-pressed')) }));
    ok(pr.g === 'group' && pr.p.every(x => x === 'on:true' || x === 'off:false') && pr.p.filter(x => x === 'on:true').length === 1,
      'polish: the level buttons say which is pressed', JSON.stringify(pr));
    // P15 and P31: a postcode search names the reader's own LSOA and is announced.
    await setOv(page, 'imd_decile');
    await page.waitForFunction(() => lsoaLyr && map.hasLayer(lsoaLyr) && Object.keys(LSOA_DATA).length > 1000, null, { timeout: 40000 }).catch(() => {});
    const lsoa = await page.evaluate(() => {
      const f = LSOA_IMD.features.find(f => f.properties.ward === 'Spitalfields & Banglatown' && LSOA_DATA[f.properties.code] && lsoaOvValue(f.properties.code, 'imd_decile') != null);
      return f ? { code: f.properties.code, name: f.properties.name || f.properties.code, dec: String(lsoaOvValue(f.properties.code, 'imd_decile')) } : null;
    });
    if (lsoa) { PCL.code = lsoa.code; PCL.name = lsoa.name; }
    await page.fill('#hdr-search-input', 'E1 6AN');
    await page.waitForTimeout(1200);
    const said = await page.evaluate(() => document.getElementById('hdr-search-status').textContent);
    ok(said === 'Postcode E1 6AN, Spitalfields & Banglatown, Tower Hamlets. Press Enter to go there.', 'polish: the postcode found is announced', said);
    await page.click('#sr-pc-row');
    await page.waitForTimeout(1500);
    const fig = await page.evaluate(() => document.getElementById('ws-fig').innerText);
    ok(!!lsoa && fig === 'Your neighbourhood, ' + lsoa.name + ', is in decile ' + lsoa.dec + ' for Deprivation decile (IMD 2025).',
      'polish: after a postcode search the ward sheet gives the figure for the reader\'s own LSOA', fig + ' ' + JSON.stringify(lsoa));
    await page.evaluate(() => showWard('Lansbury'));
    await page.waitForTimeout(600);
    const fig2 = await page.evaluate(() => document.getElementById('ws-fig').innerText);
    ok(fig2 === 'Deprivation decile (IMD 2025) is published for LSOAs, so hover over the map to see each one.',
      'polish: without a postcode the ward sheet points to the map rather than saying "No figure"', fig2);
    // P16: the full profile is a wide reading view, and the button closes it.
    await page.click('#ws-profile');
    // The panel widens over a short transition; read it once it has settled.
    await page.waitForFunction(() => document.getElementById('sidebar').getBoundingClientRect().width >= 600, null, { timeout: 5000 }).catch(() => {});
    await page.waitForTimeout(1000);
    const wide = await page.evaluate(() => ({ w: document.getElementById('sidebar').getBoundingClientRect().width, b: document.getElementById('ws-profile').textContent,
      fs: getComputedStyle(document.querySelector('#wpanel .sr')).fontSize }));
    // The whole region framed first: an area cut off at the map's bottom
    // edge keeps its whole box, which axe counts as a neighbour of the footer
    // links below, and that depends on where the map is panned rather than
    // on the profile.
    await page.evaluate(() => map.fitBounds(wLyr.getBounds(), { padding: [40, 40], animate: false }));
    await page.waitForTimeout(800);
    await checkAxe(page, 'the full profile, wide');
    await page.click('#ws-profile');
    await page.waitForTimeout(800);
    const back = await page.evaluate(() => ({ w: document.getElementById('sidebar').getBoundingClientRect().width, b: document.getElementById('ws-profile').textContent,
      tab: document.querySelector('.sb-tab.active').dataset.tab }));
    ok(wide.w >= 600 && wide.b === 'Close full profile' && wide.fs === '13px' && back.w < 400 && back.b === 'Open full profile' && back.tab !== 'ward',
      'polish: the full profile opens wide in 13px text and its button closes it again', JSON.stringify([wide, back]));
    // P17: the Area level help is a card under its button, not a bar.
    await page.click('.dl-help');
    await page.waitForTimeout(300);
    const pop = await page.evaluate(() => { const p = document.querySelector('.help-pop'), b = document.querySelector('.dl-help').getBoundingClientRect();
      if (!p) return null; const r = p.getBoundingClientRect(); return { w: Math.round(r.width), right: Math.round(r.right), btn: Math.round(b.right), top: r.top >= b.bottom, fs: getComputedStyle(p).fontSize }; });
    ok(!!pop && pop.w <= 360 && Math.abs(pop.right - pop.btn) <= 2 && pop.top && pop.fs === '13px',
      'polish: the Area level help opens under its button at a readable width', JSON.stringify(pop));
    await page.keyboard.press('Escape');
    // P29: group headings in sentence case, no repeated tag on each row.
    await page.fill('#hdr-search-input', 'Brent');
    await page.waitForTimeout(800);
    const sr = await page.evaluate(() => ({ g: [...document.querySelectorAll('#hdr-search-results .sr-grp')].map(e => e.textContent),
      tags: [...document.querySelectorAll('#hdr-search-results .sr-row .sr-type')].filter(e => e.getBoundingClientRect().width > 2).length }));
    ok(sr.g.some(t => /^Wards \(\d+\)$/.test(t)) && sr.g.every(t => /^[A-Z][a-z]/.test(t) || /^(LAs|LSOAs|ICBs|GP practices) \(/.test(t)) && sr.tags === 0,
      'polish: search groups are headed in sentence case and the rows do not repeat the tag', JSON.stringify(sr));
    await page.fill('#hdr-search-input', '');
    await page.keyboard.press('Escape');
    // P34: the search box gets the 2px ring.
    await page.focus('#hdr-search-input');
    const ring = await page.evaluate(() => { const c = getComputedStyle(document.getElementById('hdr-search-input')); return c.outlineStyle + ' ' + c.outlineWidth; });
    ok(/^solid 2px$/.test(ring), 'polish: the header search box shows the same focus ring as other controls', ring);
    // P31: the indicator search says how many match.
    await page.click('#sbtab-overlay');
    await page.fill('#ov-search', 'health');
    await page.waitForTimeout(500);
    const ovs = await page.evaluate(() => { const e = document.getElementById('ov-results-status'); return { role: e.getAttribute('role'), t: e.textContent }; });
    ok(ovs.role === 'status' && /^\d+( or more)? indicators? match(es)?$/.test(ovs.t), 'polish: the indicator search announces its count', JSON.stringify(ovs));
    await page.fill('#ov-search', '');
    ok(await page.evaluate(() => document.getElementById('q-resultbar').getAttribute('role') === 'status' && document.getElementById('dir-count').getAttribute('role') === 'status'),
      'polish: the Query and Directory counts are status messages');
    // P34: the Layers headings take focus and say whether they are open.
    await page.click('#sbtab-layers');
    await page.waitForTimeout(300);
    const lh = await page.evaluate(() => [...document.querySelectorAll('#tab-layers .sb-lbl[role="button"]')].map(l => l.textContent.trim() + ':' + l.tabIndex + ':' + l.getAttribute('aria-expanded')));
    await page.focus('#tab-layers .sb-lbl[role="button"]');
    await page.keyboard.press('Enter');
    const lh2 = await page.evaluate(() => { const l = document.querySelector('#tab-layers .sb-lbl[role="button"]'); return l.getAttribute('aria-expanded') + ':' + l.parentElement.classList.contains('collapsed'); });
    await page.keyboard.press('Enter');
    ok(lh.includes('Places and services:0:true') && lh.includes('Boundaries:0:true') && lh2 === 'false:true',
      'polish: the Layers headings open and close from the keyboard and say which they are', JSON.stringify([lh, lh2]));
    await checkAxe(page, 'Layers headings as buttons');
    // P31: a second indicator is announced.
    await page.evaluate(() => window._bivSet('census_bad_health_pct'));
    await page.waitForTimeout(400);
    const live = await page.evaluate(() => document.getElementById('sr-live').textContent);
    ok(/compared with Bad or very bad health\.$/.test(live), 'polish: adding a second indicator is announced', live);
    await page.evaluate(() => window._bivSet('none'));
    // P32: the Compare and Query chips say whether they are on.
    await page.click('.mode-btn[data-mode="compare"]');
    await page.waitForTimeout(800);
    const cmpP = await page.evaluate(() => [...document.querySelectorAll('#cmp-metrics [data-metric]')].every(c => c.getAttribute('aria-pressed') === String(c.classList.contains('on'))));
    await page.click('.mode-btn[data-mode="query"]');
    await page.waitForTimeout(800);
    const svcP = await page.evaluate(() => [...document.querySelectorAll('#q-services [data-svc]')].length > 0 &&
      [...document.querySelectorAll('#q-services [data-svc]')].every(c => c.getAttribute('aria-pressed') === String(c.classList.contains('on'))));
    ok(cmpP && svcP, 'polish: the Compare indicator chips and the Query service chips carry aria-pressed', JSON.stringify([cmpP, svcP]));
    await page.click('.mode-btn[data-mode="explore"]');
    await page.waitForTimeout(500);
    // P33: the ward report is a landmark, its charts have text, and better
    // and worse differ in lightness.
    const [rep] = await Promise.all([page.waitForEvent('popup', { timeout: 20000 }).catch(() => null),
      page.evaluate(() => { showWard('Lansbury'); setTimeout(() => document.getElementById('ws-pdf').click(), 600); })]);
    if (rep) {
      await rep.waitForFunction(() => !!document.querySelector('.report'), null, { timeout: 30000 }).catch(() => {});
      await rep.waitForTimeout(800);
      const r = await rep.evaluate(() => ({ main: !!document.querySelector('main.report'),
        bare: [...document.querySelectorAll('svg.ch-stack, svg.ch-compare, svg.ch-hbar')].filter(s => !s.getAttribute('aria-label')).length,
        n: document.querySelectorAll('svg.ch-stack, svg.ch-compare').length }));
      ok(r.main && r.n >= 3 && r.bare === 0, 'polish: the ward report has a main landmark and its charts have text alternatives', JSON.stringify(r));
      await checkAxe(rep, 'the ward report');
      await rep.close();
    } else ok(false, 'polish: the ward report opens');
    const luma = await page.evaluate(() => [REPORT_CMP.better, REPORT_CMP.worse, REPORT_CMP.neutral].map(h => {
      const n = parseInt(h.slice(1), 16); return Math.round(0.299 * (n >> 16) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)); }));
    ok(Math.abs(luma[0] - luma[1]) >= 80 && Math.abs(luma[2] - luma[0]) >= 40 && Math.abs(luma[2] - luma[1]) >= 40,
      'polish: the report\'s better, worse and neutral colours part in greyscale', luma.join(','));
    ok(!errors.length, 'no page errors (polish, desktop)', errors.join(' | '));
    await page.close();
  }
  {
    // Phones: the tour, the Viewing strip, Custom area and the tap targets.
    const { page, errors } = await open(browser, 'index.html?tour=1', PHONE);
    const tour = await page.evaluate(async () => {
      const o = [];
      for (let i = 0; i < PH_TOUR.length; i++) {
        await new Promise(r => setTimeout(r, 450));
        const n = document.getElementById('tour-narr'), b = document.querySelector('.basemap-ctl');
        o.push({ fs: getComputedStyle(n).fontSize, fits: n.scrollHeight <= n.clientHeight + 2,
          h: document.getElementById('tour-strip').getBoundingClientRect().height / innerHeight, sat: !!(b && b.offsetParent) });
        if (i < PH_TOUR.length - 1) document.getElementById('tour-next').click();
      }
      PH_TOUR.end();
      return o;
    });
    ok(tour.every(c => c.fs === '15px' && c.fits && c.h <= 0.61 && !c.sat),
      'polish: on a phone the tour reads at 15px, every chapter fits without scrolling, and the Satellite button waits', JSON.stringify(tour));
    const attrs = await page.evaluate(() => { const i = document.getElementById('hdr-search-input');
      return ['enterkeyhint', 'autocorrect', 'autocapitalize', 'spellcheck'].map(a => i.getAttribute(a)).join(); });
    ok(attrs === 'search,off,off,false', 'polish: the search box asks for a Search key and no autocorrect', attrs);
    await setOv(page, 'imd_decile');
    await page.waitForTimeout(2500);
    const vs = await page.evaluate(() => { const v = document.getElementById('viewing-strip'), x = document.getElementById('vs-ov-x');
      const chip = v.querySelector('.chip.overlay');
      return { h: Math.round(v.getBoundingClientRect().height), t: document.getElementById('vs-digest').textContent,
        x: !x.hidden && x.getBoundingClientRect().width > 0 && x.getAttribute('aria-label'), chip: !!(chip && chip.getClientRects().length) }; });
    ok(vs.h <= 40 && vs.t === 'All of London, coloured by Deprivation decile (IMD 2025)' && vs.x === 'Remove this indicator' && !vs.chip,
      'polish: on a phone the Viewing strip is one line with its own remove button, and the amber chip goes', JSON.stringify(vs));
    await page.tap('#vs-ov-x');
    await page.waitForTimeout(800);
    ok(await page.evaluate(() => curOv === 'none'), 'polish: the strip\'s remove button clears the indicator');
    const hits = await hitSizes(page, '.mode-btn');
    ok(hits.length >= 5 && hits.every(h => h.h >= 44), 'polish: the tool tabs are 44px tall on a phone', JSON.stringify(hits));
    // P27: Custom area keeps count on the map.
    await page.evaluate(() => PH_MODES.activate('report'));
    await page.waitForTimeout(1500);
    await page.evaluate(async () => {
      for (let i = 0; i < 60 && !(typeof LSOA_IMD !== 'undefined' && LSOA_IMD && LSOA_IMD.features); i++) await new Promise(r => setTimeout(r, 250));
      const codes = LSOA_IMD.features.filter(f => f.properties.ward === 'Lansbury').map(f => f.properties.code).slice(0, 2);
      if (window.PH_DRAWER && PH_DRAWER.isOpen()) PH_DRAWER.close();
      codes.forEach(c => PH_REPORT.toggle(c));
    });
    await page.waitForTimeout(600);
    const rb = await page.evaluate(() => { const p = document.getElementById('rb-peek');
      return { on: !!p && !p.hidden && p.getBoundingClientRect().height > 0, t: p ? p.innerText.replace(/\s+/g, ' ').trim() : '', vs: document.getElementById('vs-digest').textContent }; });
    ok(rb.on && /^Your area has 2 LSOAs so far\. Open it to see the combined figures\. Open your area$/.test(rb.t) && rb.vs === 'Custom area: 2 LSOAs',
      'polish: on a phone Custom area counts its LSOAs on the map, with a button back to the figures', JSON.stringify(rb));
    await checkAxe(page, 'phone Custom area with its count');
    await page.tap('#rb-peek .rb-peek-open');
    await page.waitForTimeout(600);
    ok(await page.evaluate(() => PH_DRAWER.isOpen()), 'polish: Open your area opens the drawer');
    ok(!errors.length, 'no page errors (polish, phone)', errors.join(' | '));
    await page.close();
  }
  {
    // P19: the England screen and the 404 page offer a choice of region.
    const { page } = await open(browser, 'index.html?r=england', PHONE);
    const eng = await page.evaluate(() => ({ t: document.title, body: document.body.innerText,
      links: [...document.querySelectorAll('a')].map(a => a.textContent + '=' + a.getAttribute('href')) }));
    ok(eng.t === 'PopHealth Map: England' && /^PopHealth Map/.test(eng.body.trim()) && /Or open one region/.test(eng.body) &&
       /no charity listings or Ask panel/.test(eng.body) && eng.links.some(l => /^North East=.*\?r=north-east$/.test(l)) && eng.links.length >= 9,
      'polish: the England screen names the site and links to every region', JSON.stringify(eng).slice(0, 400));
    await page.close();
    const p4 = await browser.newPage({ viewport: DESK });
    await routeSite(p4);
    await p4.goto(BASE + '/404.html');
    await p4.waitForTimeout(800);
    const nf = await p4.evaluate(() => ({ btn: document.querySelector('main a.btn').textContent,
      regs: [...document.querySelectorAll('main a')].map(a => a.getAttribute('href')).filter(h => /\?r=/.test(h)).length }));
    ok(nf.btn === 'Open the map' && nf.regs === 9, 'polish: the 404 page opens the map or any region', JSON.stringify(nf));
    await p4.close();
  }

  // Polish round (P62 to P76): search, speed, safety, licences, Ask, regions.
  {
    // P68: names from the data are escaped in the LSOA card, its tooltip and
    // the ward report; a CSV cell cannot open with a formula; an= is capped.
    const { page, errors } = await open(browser, 'index.html?t=1', DESK);
    const sec = await page.evaluate(async () => {
      await ensureLsoaImd();
      const f = LSOA_IMD.features.find(x => x.properties.ward === 'Lansbury') || LSOA_IMD.features[0];
      const saved = { name: f.properties.name, ward: f.properties.ward };
      f.properties.name = 'LSOA <b>bold</b>'; f.properties.ward = 'Ward <b>bold</b>';
      showLSOA(f);
      await new Promise(r => setTimeout(r, 500));
      const panel = document.querySelectorAll('#lsoa-panel b, .lsoa-panel b').length;
      const panelHtml = document.body.innerHTML.indexOf('<b>bold</b>') !== -1;
      Object.assign(f.properties, saved);
      const w = Object.values(WARD_DATA).find(x => x.name === 'Lansbury');
      const wSaved = w.name; let html = '';
      w.name = 'Lansbury<b>bold</b>';
      try { _generateWardReport({ sink: h => { html = h; } }, 'Lansbury', {}); } catch (e) { html = 'error ' + e.message; }
      w.name = wSaved;
      const d = new DOMParser().parseFromString(html, 'text/html');
      const repBold = [...d.querySelectorAll('.minimap-cap b, b')].some(b => b.textContent === 'bold');
      return { panel, panelHtml, repBold, safe: [_safeAreaName('=HYPERLINK("x")'), _safeAreaName('x'.repeat(200)).length, _safeAreaName('-1.5 area')] };
    });
    ok(!sec.panelHtml && !sec.repBold && sec.safe[0] === 'HYPERLINK("x")' && sec.safe[1] === 80 && sec.safe[2] === '1.5 area',
      'polish: a name with markup stays text in the LSOA card and the ward report, and an= is capped and loses a leading =', JSON.stringify(sec));
    // P62: London keeps its own address; P64: one font file per family and subset.
    const head = await page.evaluate(() => ({ canon: document.querySelector('link[rel="canonical"]').href, title: document.title }));
    ok(head.canon === 'https://pophealth.uk/' && /^PopHealth Map: health and deprivation by ward in England/.test(head.title), 'polish: London keeps its canonical and title', JSON.stringify(head));
    const fontsCss = fs.readFileSync(path.join(ROOT, 'vendor/fonts.css'), 'utf8');
    ok((fontsCss.match(/@font-face/g) || []).length === 6 && /font-weight: 400 700;/.test(fontsCss) &&
       fs.readdirSync(path.join(ROOT, 'vendor/fonts')).length === 6,
      'polish: one font file per family and subset, each face a weight range');
    // P72: each figure Ask's tools return carries its label, unit and period.
    const tool = await page.evaluate(() => {
      const ga = PH_ASSISTANT.runTool('get_area', { name: 'Lansbury', level: 'ward', indicators: ['census_population', 'ft_obesity_adults'] });
      const all = PH_ASSISTANT.runTool('get_area', { name: 'Notting Dale', level: 'ward' });
      const rk = PH_ASSISTANT.runTool('rank_areas', { indicator: 'census_over65_pct', direction: 'highest', n: 3, level: 'ward' });
      return { keys: Object.keys(ga.indicators || {}), pop: ga.indicators && ga.indicators.census_population,
        size: JSON.stringify(all).length, period: rk.period };
    });
    ok(tool.keys.join() === 'census_population,ft_obesity_adults' && tool.pop && typeof tool.pop.v === 'number' &&
       tool.pop.label && tool.pop.unit === 'people' && tool.pop.period === '2021' && tool.size < 15000 && tool.period === '2021',
      'polish: Ask figures carry a label, unit and period, limited to the indicators asked for', JSON.stringify(tool));
    // P73: no example asks for something the tools cannot count.
    await page.click('#ai-toggle').catch(() => {});
    await page.waitForTimeout(500);
    const chips = await page.evaluate(() => [...document.querySelectorAll('.ai-examples button, .ai-examples .ai-ex')].map(b => b.textContent.trim()));
    ok(!chips.some(c => /GP practices/.test(c)) && chips.some(c => /lowest life expectancy for men/.test(c)),
      'polish: the Ask examples are all answerable', chips.join(' | '));
    // P76: ONS's back to front LA names are shown the everyday way.
    const la = await page.evaluate(() => [phLaName('Kingston upon Hull, City of'), phLaName('Bristol, City of'),
      phLaName('Herefordshire, County of'), phLaName('City of London'), ladPhrase(phLaName('Bristol, City of'))]);
    ok(la.join('|') === 'Hull|Bristol|Herefordshire|City of London|Bristol', 'polish: Hull, Bristol and Herefordshire by their everyday names', la.join('|'));
    ok(!errors.length, 'no page errors (polish round)', errors.join(' | '));
    await page.close();

    // P66: on a phone the output area level loads only the LAs in view.
    if (HAS_OA) {
      const { page: ph, errors: phErr } = await open(browser, 'index.html?t=1', PHONE);
      const k = await ph.evaluate(() => OA_INDEX && OA_INDEX.keys.find(k => document.querySelector('#ov option[value="' + k + '"]')));
      if (k) {
        await setOv(ph, k);
        await ph.evaluate(() => map.setView([51.494, -0.094], 14));
        await ph.waitForTimeout(800);
        await ph.evaluate(() => _setDataLevel('oa'));
        await ph.waitForTimeout(4000);
        const oa = await ph.evaluate(() => ({ loaded: PH_OA.loadedCount(), all: Object.keys(OA_INDEX.lads).length, level: _activeLevel() }));
        ok(oa.level === 'oa' && oa.loaded > 0 && oa.loaded < oa.all, 'polish: on a phone the output area level loads only the LAs in view', JSON.stringify(oa));
      }
      ok(!phErr.length, 'no page errors (phone output areas)', phErr.join(' | '));
      await ph.close();
    }

    // P70 and P71: the terms, and Ask named as London only.
    const pv = await open(browser, 'privacy.html', DESK);
    const pt = await pv.page.evaluate(() => document.body.innerText);
    ok(/not medical advice/.test(pt) && /provided free and as it is/.test(pt) && /available on the London\s+map for now/.test(pt) &&
       /On other regions and the England\s+map it is not shown, and nothing is sent\./.test(pt) &&
       /server passes it to Anthropic's Claude model/.test(pt.replace(/\s+/g, ' ')) && /10 questions a day/.test(pt) && /narrow\s+a question down does not use one up/.test(pt),
      'polish: the privacy page has the terms and says Ask is on the London map for now');
    await pv.page.close();
    const ac = await open(browser, 'accessibility.html', DESK);
    ok(/on the London map for now/.test(await ac.page.evaluate(() => document.body.innerText)), 'polish: the accessibility statement says Ask is London only');
    await ac.page.close();

    // P62 and P75: a region page names its own address, and asks for no file
    // its manifest does not list.
    if (HAS_NE) {
      const packDir = path.join(ROOT, 'data/regions/north-east');
      const list = [];
      (function walk(d, rel) {
        for (const n of fs.readdirSync(d)) {
          const f = path.join(d, n), r = rel ? rel + '/' + n : n;
          if (fs.statSync(f).isDirectory()) walk(f, r); else list.push(r);
        }
      })(packDir, '');
      const { isMobile, hasTouch, ...viewport } = DESK;
      const p = await browser.newPage({ viewport });
      const missing = [], perr = [];
      p.on('pageerror', e => perr.push(e.message));
      p.on('response', r => { if (r.status() === 404 && /data\/regions\//.test(r.url())) missing.push(r.url().replace(BASE, '')); });
      await p.route(/data\/regions\/north-east\/meta\/manifest\.json/, async r => {
        const res = await r.fetch();
        const m = await res.json();
        m.files = list;
        await r.fulfill({ response: res, json: m });
      });
      await p.goto(BASE + '/index.html?r=north-east&t=1');
      await p.waitForTimeout(SETTLE);
      const ne = await p.evaluate(() => ({ canon: document.querySelector('link[rel="canonical"]').href,
        og: (document.querySelector('meta[property="og:url"]') || {}).content || '', title: document.title,
        wards: Object.keys(WARD_DATA).length }));
      ok(ne.canon === 'https://pophealth.uk/?r=north-east' && !ne.og &&
         /^PopHealth Map: health and deprivation by ward in the North East/.test(ne.title) && ne.wards > 300,
        'polish: a region page has its own canonical, og:url and title', JSON.stringify(ne));
      ok(!missing.length, 'polish: a region page asks for no file its manifest does not list', missing.join(' | '));
      ok(!perr.length, 'no page errors (region with a file list)', perr.join(' | '));
      await p.close();
    }
  }

  await browser.close();
  console.log(failed ? '\n' + failed + ' check(s) failed' : '\nAll checks passed');
  process.exit(failed ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
