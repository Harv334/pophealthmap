/* A short check of every region on the assembled site, run by the Pages
   deploy after the region packs are unpacked and the England view is built,
   and before anything is uploaded:

     python3 -m http.server 8793 --directory _site &
     node tests/smoke/regions.cjs http://localhost:8793

   Each region under data/regions/ (other than england) must open with its
   wards drawn and no page errors, and ?r=england&go=1 must draw more than
   6,000 wards. The full smoke suite (smoke.cjs) tests London and the North
   East in depth; this is the one place every published region is opened. */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const BASE = (process.argv[2] || 'http://localhost:8793').replace(/\/$/, '');
const SITE = process.argv[3] || path.resolve(__dirname, '..', '..', '_site');
const SETTLE = +(process.env.SMOKE_SETTLE_MS || 12000);

let failed = 0;
const ok = (cond, name, detail) => {
  console.log((cond ? 'PASS ' : 'FAIL ') + name + (detail && !cond ? '  -- ' + detail : ''));
  if (!cond) failed++;
};
const wardsDrawn = page => page.evaluate(() => { let n = 0; if (typeof wLyr === 'object' && wLyr) wLyr.eachLayer(() => n++); return n; });

(async () => {
  const dir = path.join(SITE, 'data', 'regions');
  const regions = fs.existsSync(dir) ? fs.readdirSync(dir).filter(r => r !== 'england' &&
    fs.existsSync(path.join(dir, r, 'map', 'wards.js'))).sort() : [];
  if (!regions.length) { console.log('No region packs on the site; nothing to check.'); process.exit(0); }
  const browser = await chromium.launch();
  for (const r of regions) {
    const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(BASE + '/index.html?r=' + r + '&t=1');
    await page.waitForFunction(() => typeof wLyr === 'object' && wLyr && wLyr.getLayers().length > 0, null, { timeout: 60000 }).catch(() => {});
    await page.waitForTimeout(1500);
    const n = await wardsDrawn(page);
    ok(n > 50 && !errors.length, `region ${r}: wards drawn (${n}) with no page errors`, errors.join(' | '));
    await page.close();
  }
  if (fs.existsSync(path.join(dir, 'england', 'map', 'wards.js'))) {
    // More than 6,000 once all eight regions are published (England has
    // about 6,900 wards). With fewer, nearly all of the wards London and the
    // published regions hold between them.
    const wardCount = f => { try { return Object.keys(JSON.parse(fs.readFileSync(f, 'utf8')).wards || {}).length; } catch (e) { return 0; } };
    const held = wardCount(path.join(SITE, 'ward_data.json')) + regions.reduce((s, r) => s + wardCount(path.join(dir, r, 'ward_data.json')), 0);
    const want = regions.length >= 8 ? 6000 : Math.floor(held * 0.98);
    const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(BASE + '/index.html?r=england&go=1&t=1');
    await page.waitForFunction(w => typeof wLyr === 'object' && wLyr && wLyr.getLayers().length > w, want, { timeout: 120000 }).catch(() => {});
    await page.waitForTimeout(SETTLE / 4);
    const n = await wardsDrawn(page);
    ok(n > want && !errors.length, `England: more than ${want.toLocaleString('en-GB')} wards drawn (${n}, ${regions.length} regions) with no page errors`, errors.join(' | '));
    await page.close();
  } else {
    ok(false, 'England: the view was built', 'no data/regions/england/map/wards.js');
  }
  await browser.close();
  console.log(failed ? '\n' + failed + ' check(s) failed' : '\nAll region checks passed');
  process.exit(failed ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
