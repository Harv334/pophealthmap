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
const setOv = (page, k) => page.evaluate(k => {
  const s = document.getElementById('ov'); s.value = k; s.dispatchEvent(new Event('change'));
}, k);

(async () => {
  const opts = process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {};
  const browser = await chromium.launch(opts);

  // London, desktop
  {
    const { page, errors } = await open(browser, 'index.html?t=1', DESK);
    ok(await page.evaluate(() => { let n = 0; wLyr.eachLayer(() => n++); return n; }) > 600, 'London wards drawn');
    await checkAxe(page, 'London on open');
    await page.click('#sbtab-overlay');
    await page.click('#ov-chips button');
    await page.waitForTimeout(2500);
    ok(await page.evaluate(() => curOv && curOv !== 'none'), 'key indicator chip sets an indicator');
    ok(await page.evaluate(() => /\d/.test(document.getElementById('sc-lo').textContent)), 'legend shows numbers');
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
    await checkAxe(page, 'ward selected');
    await page.click('#sbtab-layers');
    const pcn = await page.evaluate(() => document.querySelectorAll('#pcn-focus option').length);
    ok(pcn > 50, 'PCN list loaded', pcn + ' options');
    ok(errors.length === 0, 'no page errors (London desktop)', errors.join(' | '));
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
    const box = await page.evaluate(() => { const r = document.getElementById('map').getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; });
    await page.touchscreen.tap(box[0], box[1]);
    await page.waitForTimeout(1200);
    ok(await page.evaluate(() => !document.getElementById('area-peek').hidden), 'phone: tapping an area shows its card');
    await checkAxe(page, 'phone with area card');
    await page.click('#hdr-menu'); await page.waitForTimeout(400);
    await checkAxe(page, 'phone drawer');
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
