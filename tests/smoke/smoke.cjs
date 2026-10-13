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
          const right = k === 'census_population' ? /add up the residents of the output areas/.test(m)
            : /average their percentages, weighting each by its number of residents/.test(m);
          if (!right || /add up the LSOAs/.test(m)) out.census.push(k);
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
    // It sits behind More in the header (workplan 2.11).
    await page.click('#tb-more');
    await page.waitForTimeout(200);
    await page.click('#tb-png');
    await page.waitForTimeout(400);
    await page.click('text=This view');
    await page.waitForTimeout(800);
    const pres = await page.evaluate(() => ({ on: document.body.classList.contains('presenting'),
      hidden: ['.hdr', '#sidebar'].every(s => getComputedStyle(document.querySelector(s)).display === 'none'),
      h: map.getSize().y }));
    await page.keyboard.press('Escape');
    await page.waitForTimeout(500);
    const after = await page.evaluate(() => ({ on: document.body.classList.contains('presenting'),
      shown: getComputedStyle(document.getElementById('sidebar')).display !== 'none', banner: !!document.getElementById('screenshot-done') }));
    ok(pres.on && pres.hidden && pres.h > 700 && !after.on && after.shown && !after.banner,
      'Presentation view hides the panels and Esc leaves it', JSON.stringify([pres, after]));
    const wpt = await page.evaluate(() => { let c = null; wLyr.eachLayer(l => { if (!c && /Bethnal|Hoxton|Stratford|Peckham/.test(l.feature.properties.WD24NM)) c = l.getBounds().getCenter(); });
      if (!c) { wLyr.eachLayer(l => { if (!c) c = l.getBounds().getCenter(); }); } const p = map.latLngToContainerPoint(c); const r = document.getElementById('map').getBoundingClientRect(); return [r.left + p.x, r.top + p.y]; });
    await page.mouse.click(wpt[0], wpt[1]);
    await page.waitForTimeout(1500);
    ok(await page.evaluate(() => !!selW), 'clicking a ward selects it');
    await checkAxe(page, 'ward selected');
    await page.click('#sbtab-layers');
    const pcn = await page.evaluate(() => document.querySelectorAll('#pcn-focus option').length);
    ok(pcn > 50, 'PCN list loaded', pcn + ' options');
    // A blocked report window gets a note beside the button, with a link that
    // opens the report in this tab when the reader chooses to.
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
    for (const q of ['smoking', 'pollution', 'arson']) {
      await page.fill('#ov-search', q);
      await page.waitForTimeout(400);
      ind[q] = await page.evaluate(() => document.getElementById('ov-results').innerText.replace(/\s+/g, ' ').trim());
    }
    await page.fill('#ov-search', '');
    ok(!/No indicator matches/.test(ind.smoking) && /Adults who smoke is only published for whole LAs/.test(ind.smoking) && /full profile/.test(ind.smoking),
      'a council-only indicator says where its figures are', ind.smoking);
    ok(/Nitrogen dioxide/.test(ind.pollution), 'indicator search knows everyday words', ind.pollution.slice(0, 120));
    ok(/^1 match\b/.test(ind.arson), 'one result reads "1 match"', ind.arson.slice(0, 60));
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
      const els = [...document.querySelectorAll('.hdr button, .hdr input, .hdr h1, .hdr .dev-badge')].filter(e => e.offsetParent);
      const over = [];
      for (let i = 0; i < els.length; i++) for (let j = i + 1; j < els.length; j++) {
        if (els[i].contains(els[j]) || els[j].contains(els[i])) continue;
        const a = els[i].getBoundingClientRect(), b = els[j].getBoundingClientRect();
        if (a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1) over.push((els[i].id || els[i].className) + ' x ' + (els[j].id || els[j].className));
      }
      return { over, wide: document.documentElement.scrollWidth > window.innerWidth, tall: document.querySelector('.hdr').offsetHeight > 60 };
    });
    ok(!r.over.length && !r.wide && !r.tall, 'nothing in the header overlaps at ' + w + 'px', JSON.stringify(r));
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
    // The drawer: its button says it will hide the panel, and Reset, Copy
    // link and More share one row under the search box.
    const drawer = await page.evaluate(() => {
      const m = document.getElementById('hdr-menu');
      const tops = ['tb-reset', 'tb-link', 'tb-more'].map(id => Math.round(document.getElementById(id).getBoundingClientRect().top));
      return { name: m.getAttribute('aria-label'), exp: m.getAttribute('aria-expanded'), row: tops.every(t => t === tops[0]) };
    });
    ok(drawer.name === 'Hide panel' && drawer.exp === 'true' && drawer.row, 'phone: the drawer button and the tools row', JSON.stringify(drawer));
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
