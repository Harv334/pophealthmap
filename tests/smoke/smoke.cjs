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
async function axeViolations(page) {
  await page.addScriptTag({ content: AXE });
  const r = await page.evaluate(() => axe.run(document, {
    runOnly: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice'], resultTypes: ['violations'] }));
  return r.violations.map(v => v.id + ' (' + v.nodes.length + ')');
}
async function checkAxe(page, name) {
  const v = await axeViolations(page);
  ok(v.length === 0, 'accessibility: ' + name, v.join(', '));
}
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
    let g = await glyphCheck(page);
    ok(!g.length, 'no glyph or emoji icons on the opening screen', g.slice(0, 5).join(' | '));
    g = await smallText(page);
    ok(!g.length, 'nothing on the opening screen is smaller than 11px', g.slice(0, 5).join(' | '));
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
    await page.click('#ov-themes .ov-theme');
    await page.waitForTimeout(300);
    ok(await page.evaluate(() => !document.getElementById('ov-theme-view').hidden), 'theme list opens');
    await checkAxe(page, 'theme list');
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
        cardIntro: (card.match(/local authority \(LA\)/gi) || []).length,
        oldNames: (words.match(/Borough\/council|\bBoroughs?\b|Focus by borough|\bLocal authorit(y|ies)\b/g) || []),
      };
    });
    ok(la.button === 'LA' && la.name === 'LA' && la.focus === 'Focus by LA', 'the local authority level is called LA', JSON.stringify(la));
    ok(la.helpIntro === 1 && la.helpFive && !la.helpDash, 'Area level help introduces local authority (LA) once and counts five sizes', JSON.stringify(la));
    ok(la.cardIntro === 1, 'indicator card introduces local authority (LA) once', JSON.stringify(la));
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
    await page.click('.mode-btn[data-mode="explore"]');
    await page.waitForTimeout(500);
    // A ward clicked opens its sheet.
    await setOv(page, 'crime_rate_per_1000');
    await page.waitForTimeout(2000);
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
    // "In plain English" (workplan 2.5): first in the sheet and the profile,
    // leading with the ward's ranking, with a case for each kind of ward.
    const plain = await page.evaluate(async () => {
      const out = {};
      for (const w of ['Stonebridge', 'Holloway', 'Barnes', 'Bishopsgate']) {
        showWard(w);
        await new Promise(r => setTimeout(r, 400));
        const body = document.getElementById('ws-body'), prof = document.getElementById('wpanel');
        const sheetBlock = body.firstElementChild, profBlock = prof.querySelector('.ws-insight');
        out[w] = {
          sheetFirst: !!sheetBlock && !!sheetBlock.querySelector('.ws-insight-h') && sheetBlock.querySelector('.ws-insight-h').textContent === 'In plain English',
          profFirst: !!profBlock && profBlock.querySelector('.ws-insight-h').textContent === 'In plain English' &&
            !!(profBlock.compareDocumentPosition(prof.querySelector('.keystats')) & Node.DOCUMENT_POSITION_FOLLOWING),
          sheet: sheetBlock.querySelector('p').textContent,
          prof: profBlock ? profBlock.querySelector('p').textContent : '',
        };
      }
      const tiles = [...document.querySelectorAll('#ws-body .ws-kpi .ws-k-lbl')].map(e => e.textContent);
      return { out, tiles, cols: getComputedStyle(document.querySelector('#ws-body .ws-strip')).gridTemplateColumns.split(' ').length };
    });
    const P = plain.out;
    ok(Object.values(P).every(x => x.sheetFirst && x.profFirst && x.sheet === x.prof),
      '"In plain English" comes first in the ward sheet and the profile, and they match', JSON.stringify(P).slice(0, 300));
    ok(/^Stonebridge is more deprived than 99% of London's wards, and \d+ of its \d+ LSOAs are among the most deprived fifth of England\./.test(P.Stonebridge.sheet),
      'Stonebridge leads with its ranking and its Core20 count', P.Stonebridge.sheet);
    ok(/^Holloway is (more|less) deprived than \d+% of London's wards\./.test(P.Holloway.sheet) && !/deprived fifth/.test(P.Holloway.sheet),
      'Holloway, with no Core20 LSOAs, has no Core20 clause', P.Holloway.sheet);
    ok(/^Barnes is less deprived than \d+% of London's wards/.test(P.Barnes.sheet), 'a Richmond ward says "less deprived than"', P.Barnes.sheet);
    ok(/^Bishopsgate is home to only 345 residents/.test(P.Bishopsgate.sheet) && !/median|deprived than/.test(P.Bishopsgate.sheet),
      'Bishopsgate opens with its residents and makes no comparison with the median', P.Bishopsgate.sheet);
    ok(!Object.values(P).some(x => /[–—]/.test(x.sheet)), 'no dashes in the ward summaries');
    ok(plain.tiles.length === 6 && plain.cols === 3 && !plain.tiles.some(t => /Population|Avg|\+|\(/.test(t)),
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
      ok(r.h === 'In plain English' && r.beforeKpis && r.text === P.Bishopsgate.sheet,
        'the printable report opens with the same "In plain English" summary', JSON.stringify(r).slice(0, 300));
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
      };
    });
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
        };
      });
      ok(r2.parks.some(p => /Bartlett Park/.test(p)) && r2.parks.every(p => / ha( in all)?$/.test(p)), 'the Lansbury report lists real parks', r2.parks.join(' | '));
      ok(r2.lang === 'en-GB' && r2.pcodes.length === 0 && r2.counts.length === 1 && r2.dashes === 0,
        'the report declares en-GB, has no "P95", gives one ward count and no dashes', JSON.stringify(r2));
      ok(r2.png, 'every report chart offers SVG and PNG');
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
    await checkAxe(page, 'LSOA profile');
    // The screenshot dialog and the level toast are built on the Data
    // credits dialog's frame and the page's own tokens (workplan 2.9).
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
    ok(errors.length === 0, 'no page errors (London desktop)', errors.join(' | '));
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
      PH_CSV.pick('ft_93283'); PH_CSV.pick('ft_smoking_prevalence_adults'); PH_CSV.pick('crime_total');
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
        notes: PH_CSV.notes(),
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
      return { t, pinned: num(/([\d,]+) charities are shown on the map/), wide: num(/The Directory also lists ([\d,]+)/),
        tab: +document.getElementById('vcse-tab-count').textContent.replace(/,/g, ''),
        key: !document.getElementById('vcse-key').hidden && document.querySelectorAll('#vcse-key li').length,
        ring: ring ? getComputedStyle(ring).backgroundImage : '' };
    });
    ok(ch.pinned === ch.tab && ch.pinned + ch.wide === az.n && !/[–—]/.test(ch.t),
      'charities: the tab and the Directory agree on one count, explained once', ch.t + ' / Directory ' + az.n);
    ok(ch.key === 11 && /conic-gradient/.test(ch.ring), 'charities: a key to the dots, and groups drawn as a ring of causes',
      ch.key + ' / ' + ch.ring.slice(0, 40));
    await checkAxe(page, 'Charities tab with its key');
    const tour = await page.evaluate(async () => {
      const src = await (await fetch('index.html')).text();
      const m = src.match(/t: 'Directory: find one place',[\s\S]*?go: function/);
      return m ? m[0] : '';
    });
    ok(tour && !/\d/.test(tour.replace(/^t: '[^']*',/, '')), 'the tour gives no charity or Directory count of its own');
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
    await page.click('#sbtab-overlay');
    await checkAxe(page, 'two indicators');
    ok(errors.length === 0, 'no page errors (two indicators)', errors.join(' | '));
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
    // nothing floating over them, and "Colour the map" over the map.
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
        'phone: "Colour the map" floats over the map, clear of the Ask button and the key', JSON.stringify(chip));
      await page.touchscreen.tap(chip.l + 20, chip.t + 20);
      await page.waitForTimeout(600);
      ok(await page.evaluate(() => PH_DRAWER.isOpen() && document.getElementById('tab-overlay').classList.contains('active')),
        'phone: "Colour the map" opens the Indicators tab');
      await page.evaluate(() => PH_DRAWER.close());
      await page.waitForTimeout(400);
      const lg = await rectOf(page, '#map-legend-strip');
      const lgName = await page.evaluate(() => [document.getElementById('map-legend-strip').getAttribute('aria-label'),
        document.getElementById('mls-label').firstChild.textContent.trim()]);
      ok(lgName[0] === 'About this indicator: ' + lgName[1], 'phone: the key is a button named after its indicator', lgName.join(' / '));
      await page.touchscreen.tap(lg.l + 30, lg.t + lg.h / 2);
      await page.waitForTimeout(700);
      ok(await page.evaluate(() => PH_DRAWER.isOpen() && document.getElementById('tab-overlay').classList.contains('active') &&
          document.getElementById('ov-info-panel').style.display === 'block' &&
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
    await checkAxe(page, 'phone drawer');
    await checkTargets(page, 'the drawer');
    g = await glyphCheck(page);
    ok(!g.length, 'phone: no glyph or emoji icons in the drawer', g.slice(0, 5).join(' | '));
    // The four tools wait behind More, so the tabs and their content start
    // straight under the search row. 650px for the content needs the drawer
    // to scroll as one column (workplan 1.10); until then it is checked here
    // against what the pinned footer leaves.
    const dr = await page.evaluate(() => ({
      tools: Math.round(document.getElementById('sb-tools').getBoundingClientRect().height),
      pane: Math.round(document.querySelector('#sidebar > .tab-pane.active').getBoundingClientRect().height),
      inMenu: ['tb-reset', 'tb-link', 'tb-cb', 'tb-png'].every(id => document.getElementById(id).parentNode.id === 'sb-more-menu'),
      menuHidden: document.getElementById('sb-more-menu').hidden,
    }));
    ok(dr.tools <= 64 && dr.pane >= 540 && dr.inMenu && dr.menuHidden,
      'phone: the drawer keeps the four tools behind More, so its content starts under one row', JSON.stringify(dr));
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
    ok(ld.name === 'PopHealth Map' && /^Free map of health/.test(ld.line) && !DASH.test(ld.line),
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
    // wards.js blocked: the application cannot start, so the loading screen
    // says so and offers a reload, rather than breathing for ever.
    const page = await browser.newPage({ viewport: DESK });
    await page.route(/data\/map\/wards\.js/, r => r.abort());
    await page.goto(BASE + '/index.html?t=1');
    await page.waitForFunction(() => !document.getElementById('ldr-msg').hidden, null, { timeout: 20000 }).catch(() => {});
    const m = await page.evaluate(() => ({ text: document.getElementById('ldr-msg').hidden ? '' : document.getElementById('ldr-msg-text').textContent,
      btn: document.getElementById('ldr-reload').textContent, focus: document.activeElement && document.activeElement.id }));
    ok(/could not be loaded/.test(m.text) && /reload the page/.test(m.text) && !DASH.test(m.text) && m.btn === 'Reload the page' && m.focus === 'ldr-reload',
      'wards.js blocked: the loading screen says so within 20 seconds and offers a reload', JSON.stringify(m));
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

  // Other pages
  for (const p of ['methodology.html', 'privacy.html', 'accessibility.html']) {
    const { page, errors } = await open(browser, p, { width: 1200, height: 900 });
    await checkAxe(page, p);
    ok(errors.length === 0, 'no page errors (' + p + ')', errors.join(' | '));
    await page.close();
  }

  await browser.close();
  console.log(failed ? '\n' + failed + ' check(s) failed' : '\nAll checks passed');
  process.exit(failed ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
