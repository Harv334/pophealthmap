/* Build the site's icons and its link preview image from the repo's own files.

   The site mark is four rounded squares in four shades of the site blue, the
   same drawing as the inline mark in the app header and the pages' header.
   From it this script renders:

     favicon-32.png          the coloured mark, transparent background
     apple-touch-icon.png    180px, white mark on #005EB8, full bleed
     icon-192.png, icon-512.png
                             white mark on a rounded #005EB8 tile
     icon-maskable-512.png   white mark on #005EB8, full bleed, the mark kept
                             inside the central safe zone so a launcher can
                             crop it to any shape

   and og.png (1200x630), the image a shared link previews with: the London
   ward map coloured by deprivation score (imd_score_ward, the same field,
   seven classes and 5th to 95th percentile range the app uses), with the
   name, the tagline and "Free, no sign-in".

   Run from the repo root against a local server, so the self-hosted fonts load:

     python3 -m http.server 8800 &
     node scripts/build_images.cjs http://localhost:8800

   Needs the playwright npm package. Rerun after a ward boundary or IMD change
   if the preview should follow it. */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const BASE = (process.argv[2] || 'http://localhost:8800').replace(/\/$/, '');

// Top left, top right, bottom left, bottom right. Darkest to lightest is
// TL, BL, BR, TR, so the white version keeps the same order in opacity.
const SHADES = ['#003087', '#8BBDEA', '#005EB8', '#3D8FDA'];
const WHITE = [1, 0.5, 0.86, 0.68];
const CELLS = [[0, 0], [11, 0], [0, 11], [11, 11]];

function mark(px, white) {
  const r = CELLS.map(([x, y], i) => `<rect x="${x}" y="${y}" width="9" height="9" rx="2" ` +
    (white ? `fill="#fff" fill-opacity="${WHITE[i]}"` : `fill="${SHADES[i]}"`) + '/>').join('');
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${px}" height="${px}" viewBox="0 0 20 20">${r}</svg>`;
}

// size: canvas; markFrac: the mark's share of the canvas; tile: corner radius
// of the blue tile as a share of the canvas (0 for full bleed, null for none).
const ICONS = [
  { file: 'favicon-32.png', size: 32, markFrac: 1, tile: null },
  { file: 'apple-touch-icon.png', size: 180, markFrac: 0.56, tile: 0 },
  { file: 'icon-192.png', size: 192, markFrac: 0.54, tile: 0.22 },
  { file: 'icon-512.png', size: 512, markFrac: 0.54, tile: 0.22 },
  // The safe zone is a circle 80% of the canvas across. A square mark fits
  // in it at up to 0.8 / sqrt(2) = 57%; 50% leaves a margin.
  { file: 'icon-maskable-512.png', size: 512, markFrac: 0.5, tile: 0 },
];

function iconHtml({ size, markFrac, tile }) {
  const m = Math.round(size * markFrac);
  const bg = tile == null ? 'transparent' : '#005EB8';
  const radius = tile ? Math.round(size * tile) + 'px' : '0';
  return `<!doctype html><html><head><style>
    html,body{margin:0;background:transparent}
    .c{width:${size}px;height:${size}px;display:flex;align-items:center;justify-content:center;
       background:${bg};border-radius:${radius}}
    svg{display:block}
  </style></head><body><div class="c">${mark(m, tile != null)}</div></body></html>`;
}

/* ── The London ward map ── */
function loadWards() {
  const src = fs.readFileSync(path.join(ROOT, 'data/map/wards.js'), 'utf8');
  const gj = JSON.parse(src.slice(src.indexOf('{'), src.lastIndexOf('}') + 1));
  const wd = JSON.parse(fs.readFileSync(path.join(ROOT, 'ward_data.json'), 'utf8')).wards;
  return { gj, wd };
}
const RAMP = ['#ffffd9', '#c7e9b4', '#7fcdbb', '#41b6c4', '#1d91c0', '#225ea8', '#0c2c84'];
const NO_FIGURE = '#cccccc';

function mapSvg(width, height) {
  const { gj, wd } = loadWards();
  // imd_score_ward reads each ward's imd_score (OV_FIELD_ALIAS in index.html).
  const val = code => { const w = wd[code]; const v = w && w.indicators && w.indicators.imd_score;
    return v == null || v === '' || isNaN(+v) ? null : +v; };
  const vals = Object.keys(wd).map(val).filter(v => v != null).sort((a, b) => a - b);
  const lo = vals[Math.floor(vals.length * 0.05)], hi = vals[Math.ceil(vals.length * 0.95 - 1)];
  const colour = v => v == null ? NO_FIGURE
    : RAMP[Math.min(RAMP.length - 1, Math.floor(Math.min(Math.max((v - lo) / (hi - lo), 0), 1) * RAMP.length))];

  const k = Math.cos(51.5 * Math.PI / 180);
  const rings = f => f.geometry.type === 'Polygon' ? f.geometry.coordinates : f.geometry.coordinates.flat();
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  gj.features.forEach(f => rings(f).forEach(r => r.forEach(([lon, lat]) => {
    const x = lon * k, y = -lat;
    if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
  })));
  const s = Math.min(width / (x1 - x0), height / (y1 - y0));
  const ox = (width - (x1 - x0) * s) / 2, oy = (height - (y1 - y0) * s) / 2;
  const pt = ([lon, lat]) => ((lon * k - x0) * s + ox).toFixed(1) + ' ' + ((-lat - y0) * s + oy).toFixed(1);
  const paths = gj.features.map(f => {
    const d = rings(f).map(r => 'M' + r.map(pt).join('L') + 'Z').join('');
    return `<path d="${d}" fill="${colour(val(f.properties.WD24CD))}"/>`;
  }).join('');
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">` +
    `<g stroke="#fff" stroke-width="0.6" stroke-linejoin="round">${paths}</g></svg>`;
}

// The year in the OS credit is the year of the data on the site.
function dataYear() {
  try {
    const m = JSON.parse(fs.readFileSync(path.join(ROOT, 'data/meta/manifest.json'), 'utf8'));
    if (m.last_run) return String(m.last_run).slice(0, 4);
  } catch (e) { /* fall through */ }
  return String(new Date().getFullYear());
}

function ogHtml(tagline) {
  const swatches = RAMP.map(c => `<i style="background:${c}"></i>`).join('');
  return `<!doctype html><html><head>  <link rel="stylesheet" href="vendor/fonts.css">
  <style>
    html,body{margin:0}
    body{width:1200px;height:630px;overflow:hidden;background:#F4F7F9;position:relative;
      font-family:Inter,system-ui,sans-serif;color:#1F2D3D}
    .bar{position:absolute;left:0;top:0;right:0;height:8px;background:#005EB8}
    .txt{position:absolute;left:68px;top:0;bottom:0;width:490px;display:flex;flex-direction:column;justify-content:center}
    .name{display:flex;align-items:center;gap:16px;font-family:'Inter Tight',Inter,sans-serif;font-weight:700;
      font-size:56px;white-space:nowrap;letter-spacing:-.02em;color:#003087;line-height:1}
    .name svg{flex:none}
    .tag{margin:30px 0 0;font-size:29px;line-height:1.36;color:#1F2D3D}
    .free{align-self:flex-start;margin-top:30px;padding:10px 20px;border-radius:999px;background:#005EB8;color:#fff;
      font-size:24px;font-weight:600}
    .url{position:absolute;left:68px;bottom:74px;font-size:22px;font-weight:600;color:#005EB8}
    .map{position:absolute;left:580px;top:34px}
    .key{position:absolute;right:48px;bottom:66px;display:flex;align-items:center;gap:10px;font-size:15px;color:#4A5B6C}
    .key span.k{display:flex}
    .key i{display:block;width:22px;height:12px}
    /* The image shows the map, so it carries the map's credits, and says
       whose site it is despite the NHS blue. */
    .credit{position:absolute;left:68px;right:48px;bottom:16px;font-size:13.5px;line-height:1.45;color:#4A5B6C}
  </style></head><body>
  <div class="bar"></div>
  <div class="txt">
    <div class="name">${mark(48)}<span>PopHealth Map</span></div>
    <p class="tag">${tagline}</p>
    <div class="free">Free, no sign-in</div>
  </div>
  <div class="url">pophealth.uk</div>
  <div class="map">${mapSvg(590, 490)}</div>
  <div class="key">Deprivation score by London ward, IMD 2025 <span>Lower</span><span class="k">${swatches}</span><span>Higher</span></div>
  <div class="credit">An independent project, not affiliated with the NHS<br>
    Contains OS data &copy; Crown copyright and database right ${dataYear()}. Source: ONS boundaries; MHCLG English Indices of Deprivation 2025.</div>
  </body></html>`;
}

(async () => {
  const html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');
  const desc = (html.match(/<meta name="description" content="([^"]+)"/) || [])[1];
  if (!desc) throw new Error('index.html has no meta description');
  // The description's first sentence, less its "free and with no sign-in",
  // which the image says on its own line.
  const tagline = desc.split('. ')[0].replace(/,?\s*free and with no sign-in\.?$/, '') + '.';

  const browser = await chromium.launch();
  for (const ic of ICONS) {
    const page = await browser.newPage({ viewport: { width: ic.size, height: ic.size } });
    await page.setContent(iconHtml(ic));
    await page.screenshot({ path: path.join(ROOT, ic.file), omitBackground: true,
      clip: { x: 0, y: 0, width: ic.size, height: ic.size } });
    await page.close();
    console.log('wrote ' + ic.file);
  }
  // Served from the site's own origin, so the fonts load without CORS headers.
  const page = await browser.newPage({ viewport: { width: 1200, height: 630 } });
  const ogUrl = BASE + '/__og_preview.html';
  await page.route(ogUrl, r => r.fulfill({ contentType: 'text/html', body: ogHtml(tagline) }));
  await page.goto(ogUrl, { waitUntil: 'networkidle' });
  await page.evaluate(() => document.fonts.ready);
  const fonts = await page.evaluate(() => [...document.fonts].filter(f => f.status === 'loaded').map(f => f.family + ' ' + f.weight));
  if (!fonts.some(f => /Inter Tight/.test(f)) || !fonts.some(f => /^"?Inter"? /.test(f))) throw new Error('fonts did not load: ' + fonts.join(', '));
  await page.screenshot({ path: path.join(ROOT, 'og.png'), clip: { x: 0, y: 0, width: 1200, height: 630 } });
  console.log('wrote og.png');
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
