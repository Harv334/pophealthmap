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
const SETTLE = +(process.env.SMOKE_SETTLE_MS || 9000);

let failed = 0;
const ok = (cond, name, detail) => {
  console.log((cond ? 'PASS ' : 'FAIL ') + name + (detail && !cond ? '  -- ' + detail : ''));
  if (!cond) failed++;
};
const DESK = { width: 1400, height: 900 };
const PHONE = { width: 390, height: 844, isMobile: true, hasTouch: true };

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
    ok(await page.evaluate(() => /Published at/.test(document.getElementById('ov-info-panel').innerText)), 'indicator card opens');
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
          const m = dd('Method');
          const right = k === 'census_population' ? /sum the residents of the output areas/.test(m)
            : /average their percentages, weighting each by its number of residents/.test(m);
          if (!right || /(add up|sum) the LSOAs/.test(m)) out.census.push(k);
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
        card: /^Deaths from all causes\s*Official title: Mortality from all causes/.test(card),
        controls: [document.getElementById('ws-profile').textContent, document.getElementById('ws-pdf').textContent,
          document.getElementById('sbtab-vcse').textContent, document.querySelector('.mode-btn[data-mode="report"] .ml-short').textContent,
          document.querySelector('#icb-row .lname').firstChild.textContent.trim()].join(' | '),
      };
    });
    ok(names.bad.length === 0, 'every indicator is named from the one label table', names.bad.slice(0, 5).join(', '));
    ok(names.old.length === 0, 'no old names (Soc rent, Boards, metric, VCSE, Borough/council)', names.old.join(', '));
    ok(names.heads.length === 0, 'no heading outside the Area profile uses "&"', names.heads.slice(0, 3).join(' / '));
    ok(names.card, 'indicator card leads with the display label, then the official title');
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
      return { out, tiles, cols: getComputedStyle(document.querySelector('#ws-body .ws-strip')).gridTemplateColumns.split(' ').length };
    });
    const P = plain.out;
    ok(Object.values(P).every(x => x.sheetFirst && x.profFirst && x.sheet === x.prof),
      '"Summary" comes first in the ward sheet and the profile, and they match', JSON.stringify(P).slice(0, 300));
    ok(/^Stonebridge is more deprived than 99% of London's wards, and \d+ of its \d+ LSOAs are among the most deprived fifth of England\./.test(P.Stonebridge.sheet),
      'Stonebridge leads with its ranking and its Core20 count', P.Stonebridge.sheet);
    ok(/^Holloway is (more|less) deprived than \d+% of London's wards\./.test(P.Holloway.sheet) && !/deprived fifth/.test(P.Holloway.sheet),
      'Holloway, with no Core20 LSOAs, has no Core20 clause', P.Holloway.sheet);
    ok(/^Barnes is less deprived than \d+% of London's wards/.test(P.Barnes.sheet), 'a Richmond ward says "less deprived than"', P.Barnes.sheet);
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
      ok(r.h === 'Summary' && r.beforeKpis && r.text === P.Bishopsgate.sheet,
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
    ok(prof.oldPills.length === 0 && ['estimated from MSOAs', 'estimated from LSOAs', 'whole-LA figure'].every(t => prof.pills.indexOf(t) !== -1),
      'profile marks estimates with worded pills, not level codes', prof.pills.join(', ') + ' / ' + prof.oldPills.join(', '));
    ok(prof.captions >= 6 && /output areas/.test(prof.key) && !/~/.test(prof.name), 'each section has a caption, the key covers output areas, no "~"', prof.name);
    ok(prof.shownBefore === 8 && prof.shownAfter === prof.total && prof.total > 10, 'a long section shows eight rows and then all of them',
      prof.shownBefore + ' then ' + prof.shownAfter + ' of ' + prof.total);
    ok(prof.imd.indexOf('Deprivation score (IMD 2025)') !== -1 && prof.work.indexOf('Claimant rate, working age') !== -1 &&
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
      ok(r2.careFill.toUpperCase() === '#9AA6B2', 'the report draws unpaid care as descriptive, in the neutral colour', r2.careFill);
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
    ok(/^Higher than 99% of /.test(fig.ctx[0]) && /^Lower than 99% of /.test(fig.ctx[1]) && !/100%/.test(fig.ctx.join(' ')),
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
    for (const q of ['smoking', 'pollution', 'arson', 'flu', 'no2', 'zzqx']) {
      await page.fill('#ov-search', q);
      await page.waitForTimeout(400);
      ind[q] = await page.evaluate(() => document.getElementById('ov-results').innerText.replace(/\s+/g, ' ').trim());
    }
    await page.fill('#ov-search', '');
    ok(!/No indicator matches/.test(ind.smoking) && /Adults who smoke is only published for whole LAs/.test(ind.smoking) && /full profile/.test(ind.smoking),
      'a council-only indicator says where its figures are', ind.smoking);
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
    // Answering steps 1 and 2 folds them to a line each; Change opens them again.
    await page.click('#cb-level button[data-lvl="ward"]');
    await page.selectOption('#cb-scope', 'Tower Hamlets');
    await page.waitForTimeout(300);
    const fold = await page.evaluate(() => [1, 2].map(n => {
      const st = document.getElementById('cb-step-' + n);
      return st.classList.contains('folded') && !st.querySelector('.cb-chg').hidden &&
        getComputedStyle(st.querySelector('.cb-step-body')).display === 'none' ? st.querySelector('h3').textContent : '';
    }));
    ok(/Wards/.test(fold[0]) && /Tower Hamlets/.test(fold[1]), 'export: steps 1 and 2 fold once answered', JSON.stringify(fold));
    await page.click('#cb-step-1 .cb-chg');
    ok(await page.evaluate(() => !document.getElementById('cb-step-1').classList.contains('folded') &&
      document.activeElement === document.querySelector('#cb-level button.on')), 'export: Change opens the step again');
    const ex = await page.evaluate(() => {
      const before = PH_CSV.columns().length;
      PH_CSV.pick('ft_93283');
      const one = before === 0 ? PH_CSV.notes() : '';
      PH_CSV.pick('ft_smoking_prevalence_adults'); PH_CSV.pick('crime_total');
      const o = PH_CSV.build();
      const col = i => o.table.slice(1).map(r => r[i]).filter(v => v !== '');
      const le = o.header.indexOf('Life expectancy at birth, women (years, 2019 to 2023)');
      const groupOf = k => { const b = document.querySelector('#cb-cols input[data-col="' + k + '"]');
        return b ? b.closest('.cb-grp-body').previousElementSibling.getAttribute('data-grp') : ''; };
      const sorted = [...document.querySelectorAll('#cb-cols .cb-grp-body')].every(g => {
        const l = [...g.querySelectorAll('.l')].map(e => e.textContent);
        return l.every((t, i) => !i || l[i - 1].localeCompare(t, 'en-GB', { numeric: true, sensitivity: 'base' }) <= 0);
      });
      return {
        header: o.header, le, leVals: le === -1 ? [] : col(le),
        smoke: o.header.find(h => /^Adults who smoke/.test(h)) || '',
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
    ok(/^Near E1 6AN, nearest first/.test(dnear.head) && dnear.d.length === 30 && dnear.d.every((t, i) => !i || metres(t) >= metres(dnear.d[i - 1])),
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
    await page.check('#vcse-tab-toggle');
    await page.waitForFunction(() => document.querySelectorAll('.vcse-ring').length > 3, null, { timeout: 30000 }).catch(() => {});
    const ch = await page.evaluate(() => {
      const t = document.getElementById('vcse-about').textContent;
      const num = re => { const m = t.match(re); return m ? +m[1].replace(/,/g, '') : NaN; };
      const ring = document.querySelector('.leaflet-marker-icon.vcse-cluster .vcse-ring');
      return { t, pinned: num(/Showing ([\d,]+) registered charities/), wide: num(/The Directory also lists ([\d,]+)/),
        tab: +document.getElementById('vcse-tab-count').textContent.replace(/,/g, ''),
        key: !document.getElementById('vcse-key').hidden && document.querySelectorAll('#vcse-key li').length,
        ring: ring ? getComputedStyle(ring).backgroundImage : '' };
    });
    ok(ch.pinned === ch.tab && ch.pinned + ch.wide === az.n && !/[–—]/.test(ch.t),
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
    ok(!!near && near.h === 'Near E1 6AN' && near.cols.join() === 'GP practices,Pharmacies,Charities' &&
       near.d.every(l => l.length === 3 && l.every((t, i) => !i || metres(t) >= metres(l[i - 1]))),
      'a postcode search lists the nearest GP practices, pharmacies and charities by distance', JSON.stringify(near));
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
    ok(errors.length === 0, 'no page errors (two indicators)', errors.join(' | '));
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
  for (const [vpName, vp] of [['desktop', DESK], ['phone', PHONE]]) {
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
        out.push({ t: document.getElementById('tour-title').textContent, c: document.getElementById('tour-ctr').textContent,
          n: document.getElementById('tour-narr').innerText, spot: document.querySelectorAll('.tour-spot').length });
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
    ok(/postcode/.test(ch[2].n), vpName + ': the place chapter mentions postcode search');
    ok(['Query', 'Compare', 'Custom area', 'Data export', 'Directory', 'Ask about this data'].every(w => ch[3].n.includes(w)) &&
       ASK_SAME.every(re => re.test(ch[3].n)) && /personal/.test(ch[3].n) && ch[3].spot >= 2,
      vpName + ': the tools chapter names every tool and describes Ask as the panel does', ch[3].n);
    const dirSentence = (ch[3].n.match(/Directory[^.]*\./) || [''])[0];
    ok(dirSentence && !/\d/.test(dirSentence), vpName + ': the tour gives no charity or Directory count of its own', dirSentence);
    ok(/Key indicators|Start here/.test(ch[5].n) && (vpName === 'phone') === /menu button first/.test(ch[5].n),
      vpName + ': the last chapter names a group on the Indicators tab, and on a phone says to open the menu first', ch[5].n);
    // Do the last chapter as a reader would.
    const before = await page.evaluate(() => ({ next: document.getElementById('tour-next').textContent, ov: document.getElementById('ov').value }));
    ok(before.next === 'Skip and finish' && before.ov === 'none', vpName + ': the last chapter waits, with no indicator chosen', JSON.stringify(before));
    if (vpName === 'phone') {
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
      const attr = await rectOf(page, '.leaflet-control-attribution');
      ok(!!attr && attr.b <= sh.t && !overlaps(attr, await rectOf(page, '#map-legend-strip')) && !overlaps(attr, await rectOf(page, '#ai-toggle')),
        'phone: the credits sit on the sheet\'s edge, one line, covered by nothing', JSON.stringify({ attr, sh }));
      // And at peek, where the sheet is as tall as its head, which wraps
      // with a long indicator name.
      await page.evaluate(() => { showWard('Stonebridge'); PH_SHEET.setState(1); });
      await page.waitForTimeout(900);
      const shP = await rectOf(page, '#ward-sheet'), attrP = await rectOf(page, '.leaflet-control-attribution');
      ok(!!attrP && !!shP && attrP.b <= shP.t + 1, 'phone: the credits clear the sheet at peek too', JSON.stringify({ attrP, shP }));
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
    ok(!!tr && tr.r >= mp.r - 10 && tr.h >= mp.h - 20 && tr.w <= 380, 'landscape: the tour stands down the right of the map', JSON.stringify({ tr, mp }));
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

  // A region
  if (HAS_NE) {
    const { page, errors } = await open(browser, 'index.html?r=north-east&t=1', DESK);
    ok(await page.evaluate(() => { let n = 0; wLyr.eachLayer(() => n++); return n; }) > 300, 'North East wards drawn');
    ok(await page.evaluate(() => document.getElementById('borough-focus-label').textContent) === 'Focus by LA', 'region wording: the level is LA');
    ok(await page.evaluate(() => { const h = document.getElementById('dl-help').title;
      return (h.match(/local authority \(LA\)/g) || []).length === 1 && /(four|five) sizes of area/.test(h) && !/\bBorough\b/.test(h); }),
      'region Area level help introduces local authority (LA) once');
    ok(errors.length === 0, 'no page errors (North East)', errors.join(' | '));
    await page.close();
  } else {
    console.log('SKIP North East (no data/regions/north-east)');
  }

  // The England gate shows before the heavy load
  {
    const { page } = await open(browser, 'index.html?r=england', DESK);
    ok(await page.evaluate(() => /England map is large/.test(document.body.innerText)), 'England warning shows');
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
    // are combined (averaged by population, not added up).
    const mt = await mp.evaluate(() => document.querySelector('main').innerHTML);
    ok(/About this indicator/.test(mt) && !/<strong>i<\/strong>/.test(mt) && !/<strong>About<\/strong> button/.test(mt),
      'methodology: names the About this indicator button, not controls that are gone');
    ok(!/Census[^.<]{0,80}added up|added up[^.<]{0,40}Census/.test(mt), 'methodology: Census figures are not said to be added up');
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
        laIntro: /local authority \(LA\)/.test((document.querySelector('[aria-label="Words for areas"]') || {}).textContent || ''),
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
    ok(m.laIntro, 'methodology: glossary introduces "local authority (LA)"');
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

  await browser.close();
  console.log(failed ? '\n' + failed + ' check(s) failed' : '\nAll checks passed');
  process.exit(failed ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
