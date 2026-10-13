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
