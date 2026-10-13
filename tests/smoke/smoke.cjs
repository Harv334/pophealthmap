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
  return r.violations.map(v => v.id + ' (' + v.nodes.length + ')');
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
    // Fuel poverty is published by LSOA, so it opens there, with wards on offer.
    await setOv(page, 'fuel_poverty_pct');
    await page.waitForTimeout(2000);
    ok(await page.evaluate(() => _activeLevel() === 'lsoa' && ovHasLevel('fuel_poverty_pct', 'ward')), 'fuel poverty opens at LSOA level',
       await page.evaluate(() => _activeLevel()));
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
    ok(await page.evaluate(() => document.getElementById('borough-focus-label').textContent) === 'Focus by local authority', 'region wording');
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
    ok(/contact@pophealth\.uk/.test(ref.ftr) && /not an NHS service/.test(ref.ftr), 'pages: footer gives the contact address',
       ref.ftr);
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
    const nf = texts['404.html'];
    ok(/We could not find a page at this address/.test(nf.main) && nf.home === 'https://pophealth.uk/',
      '404: new copy, and the way back to the map is absolute');
  }

  // The methodology page is built around the reader (workplan 2.13): the
  // first screen says what the map covers and how to cite it, every heading
  // can be linked to, no ONS product code comes before the appendix, the
  // source table is live, and on a phone it is a stack of cards.
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
