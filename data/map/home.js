/* PopHealth Map home page.

   The bare address shows this instead of a region's map (the gate is in the
   head of index.html). It draws England's wards coloured by deprivation, from
   data/map/home_map.js (scripts/build_home_map.py), with the nine regions
   over them. Pointing to a region brings it to the front with a card, and
   selecting it opens that region's map; on a touch screen the first tap shows
   the card and a button to open it. The list of regions beside the map does
   the same from the keyboard and for screen readers, and works without the
   map file. None of the map's own data loads here.

   Plain script, no build step, as data/map/assistant.js is. */
(function () {
  'use strict';

  var HOME = window.PH_HOME || { live: [], regions: [] };
  var M = window.PH_HOME_MAP || null;

  // The map's ten decile colours, darkest for the most deprived (index.html,
  // IMD_DECILE_COLORS). 0 is a ward with no decile.
  var IMD = ['#E8EDEE',
    '#081d58', '#0c2c84', '#225ea8', '#1d91c0', '#41b6c4',
    '#7fcdbb', '#a8ddb5', '#c7e9b4', '#e0f3b2', '#ffffd9'];

  var ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';
  var IDX = {};
  for (var a = 0; a < ALPHABET.length; a++) IDX[ALPHABET.charCodeAt(a)] = a;

  // Zigzag varints, five bits a character, bit 32 continues: the encoding of
  // scripts/build_loader_map.py, which the loading screen decodes the same way.
  function decode(s) {
    var out = [], i = 0, n = s.length;
    while (i < n) {
      var shift = 0, val = 0, c;
      do {
        c = IDX[s.charCodeAt(i++)];
        val |= (c & 31) << shift;
        shift += 5;
      } while (c & 32);
      out.push((val >>> 1) ^ -(val & 1));
    }
    return out;
  }
  // Rings from a decoded run: a count, a first point, then each step.
  function rings(s, count) {
    var v = decode(s), out = [], i = 0;
    while (i < v.length && (count === undefined || out.length < count)) {
      var n = v[i++], x = v[i++], y = v[i++], pts = [[x, y]];
      for (var k = 1; k < n; k++) { x += v[i++]; y += v[i++]; pts.push([x, y]); }
      out.push(pts);
    }
    return out;
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }
  function hrefOf(id) { return location.pathname + '?r=' + encodeURIComponent(id); }
  // "the North East", "Yorkshire and The Humber", "London".
  function placeOf(name) {
    return (/^(London|Yorkshire)/.test(name) ? '' : 'the ') + name;
  }
  function pct(v) { return v == null ? '' : Math.round(v) + '%'; }

  // The regions to offer: the live ones, with the home map's figures where it
  // has them.
  var byId = {};
  (M && M.regions || []).forEach(function (r) { byId[r.id] = r; });
  var REGIONS = HOME.regions.filter(function (r) { return HOME.live.indexOf(r.id) !== -1; })
    .map(function (r) { return Object.assign({}, r, byId[r.id] || {}); });
  // North to south, as the home map lists them; a region it lacks goes last.
  var ORDER = (M && M.regions || []).map(function (r) { return r.id; });
  REGIONS.sort(function (x, y) {
    var a = ORDER.indexOf(x.id), b = ORDER.indexOf(y.id);
    return (a === -1 ? 99 : a) - (b === -1 ? 99 : b);
  });

  var LAST = '';
  try { LAST = localStorage.getItem('ph-last-region') || ''; } catch (e) { /* storage blocked */ }
  var lastRegion = REGIONS.filter(function (r) { return r.id === LAST; })[0] ||
    (LAST === 'england' && HOME.live.indexOf('england') !== -1 ? { id: 'england', name: 'England' } : null);

  var TOUCH = window.matchMedia && matchMedia('(hover: none)').matches;

  function regionCard(r) {
    var lines = [];
    if (r.d20 != null) lines.push(pct(r.d20) + ' of its LSOAs are in the most deprived 20% in England.');
    if (r.las) lines.push(r.las + ' LAs and ' + r.wards.toLocaleString('en-GB') + ' wards.');
    return lines;
  }

  function markup() {
    var brand = '<svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true" focusable="false">' +
      '<rect width="9" height="9" rx="2" fill="#003087"/><rect x="11" width="9" height="9" rx="2" fill="#8BBDEA"/>' +
      '<rect y="11" width="9" height="9" rx="2" fill="#005EB8"/><rect x="11" y="11" width="9" height="9" rx="2" fill="#3D8FDA"/></svg>';
    var list = REGIONS.map(function (r) {
      return '<li><a class="hr-item" href="' + esc(hrefOf(r.id)) + '" data-id="' + esc(r.id) + '">' +
        '<span class="hr-nm">' + esc(r.name) + '</span>' +
        (r.d20 != null ? '<span class="hr-pct">' + pct(r.d20) + '<small>in the most deprived 20%</small></span>' : '') +
        (r.las ? '<span class="hr-sub">' + r.las + ' LAs, ' + r.wards.toLocaleString('en-GB') + ' wards</span>' : '') +
        '</a></li>';
    }).join('');
    var ramp = IMD.slice(1).map(function (c) { return '<span style="background:' + c + '"></span>'; }).join('');
    var ar = M ? (M.w / M.h).toFixed(4) : '.88';
    var england = HOME.live.indexOf('england') !== -1
      ? '<p class="home-eng">Or <a href="' + esc(hrefOf('england')) + '">open all of England on one map</a>. ' +
        'It is a large download of about 40 MB (megabytes), and it can be slow on a phone.</p>' : '';
    return '<a class="skip" href="#main">Skip to main content</a>' +
      '<header class="site-hdr"><div class="site-hdr-in">' +
      '<a class="brand" href="' + esc(location.pathname) + '" aria-current="page">' + brand + '<span>PopHealth Map</span></a>' +
      '<nav class="site-nav" aria-label="Site"><ul>' +
      '<li><a href="methodology.html">Methodology</a></li>' +
      '<li><a href="accessibility.html">Accessibility</a></li>' +
      '<li><a href="privacy.html">About and privacy</a></li>' +
      '</ul></nav></div></header>' +
      '<main id="main" class="home" tabindex="-1">' +
      '<div class="home-intro">' +
      '<h1>Health and deprivation in every neighbourhood in England</h1>' +
      '<p class="home-lede">PopHealth Map shows deprivation, health, people and local services for every ward and ' +
      'LSOA in England. An LSOA is a small area of about 1,500 people. Choose a region to open its map.</p>' +
      (lastRegion ? '<p class="home-continue"><a href="' + esc(hrefOf(lastRegion.id)) + '">Continue to the ' +
        esc(lastRegion.id === 'england' ? 'England' : lastRegion.name) + ' map</a></p>' : '') +
      '</div>' +
      '<div class="home-grid">' +
      (M ? '<figure class="home-map">' +
        // The drawing is hidden from screen readers, which have the list of
        // regions below with the same choices; the card a tap opens is not.
        '<div class="hm-box" style="--ar:' + ar + '">' +
        '<div class="hm-art" aria-hidden="true"><canvas></canvas>' +
        '<svg class="hm-svg" viewBox="0 0 ' + M.w + ' ' + M.h + '" focusable="false"></svg></div>' +
        '<div class="hm-tip" hidden></div></div>' +
        '<p class="hm-hint">' + (TOUCH ? 'Tap a region to see it, then tap the button to open its map.'
          : 'Point to a region to see it, and select it to open its map.') + '</p>' +
        '<figcaption class="hm-key"><div class="hm-ramp" aria-hidden="true">' + ramp + '</div>' +
        '<div class="hm-ends"><span>Most deprived</span><span>Least deprived</span></div>' +
        '<p class="hm-cap">Each ward is shaded by the average deprivation decile of its LSOAs, from the Index of ' +
        'Multiple Deprivation (IMD) 2025. Decile 1 is the most deprived 10% of areas in England, and decile 10 is ' +
        'the least deprived 10%.</p></figcaption></figure>' : '') +
      '<section class="home-regions" aria-labelledby="hr-h">' +
      '<h2 id="hr-h">Choose a region</h2>' +
      (M ? '<p class="hr-note">The figure for each region is the share of its LSOAs in the most deprived 20% in ' +
        'England. For England as a whole, it is 20%. LA means local authority, the council area.</p>' : '') +
      '<ul class="hr-list">' + list + '</ul>' + england +
      '</section></div></main>' +
      '<footer class="site-ftr"><div class="site-ftr-in">' +
      '<p>PopHealth Map is an independent project, and it is not part of the NHS. You can get in touch at ' +
      '<a href="mailto:contact@pophealth.uk">contact@pophealth.uk</a>.</p>' +
      '<p><a href="privacy.html#terms">Terms of use</a></p></div></footer>';
  }

  function build() {
    // The map's own document, swallowed unread by the gate, is dropped.
    Array.prototype.forEach.call(document.querySelectorAll('plaintext'), function (n) { n.remove(); });
    document.body.className = 'home-body';
    document.body.insertAdjacentHTML('afterbegin', markup());
    if (M) drawMap();
    wireList();
  }

  var setOn = function () {};

  function drawMap() {
    var box = document.querySelector('.hm-box');
    var canvas = box.querySelector('canvas');
    var svg = box.querySelector('svg');
    var tip = box.querySelector('.hm-tip');
    var NS = 'http://www.w3.org/2000/svg';

    // Each ward once, as a Path2D in map units, and its colour.
    var wardRings = rings(M.wards.p, M.wards.n);
    var wardPaths = wardRings.map(function (pts) {
      var p = new Path2D();
      p.moveTo(pts[0][0], pts[0][1]);
      for (var k = 1; k < pts.length; k++) p.lineTo(pts[k][0], pts[k][1]);
      p.closePath();
      return p;
    });
    var wardFill = [];
    for (var i = 0; i < wardPaths.length; i++) wardFill.push(IMD[IDX[M.wards.d.charCodeAt(i)]] || IMD[0]);

    function paint() {
      var w = box.clientWidth, h = box.clientHeight;
      if (!w || !h) return;
      var dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      var ctx = canvas.getContext('2d');
      var s = Math.min(canvas.width / M.w, canvas.height / M.h);
      ctx.setTransform(s, 0, 0, s, (canvas.width - M.w * s) / 2, (canvas.height - M.h * s) / 2);
      ctx.clearRect(-1e4, -1e4, 2e4, 2e4);
      // A hairline in the ward's own colour closes the seams between wards.
      ctx.lineWidth = 0.8 / s * dpr;
      ctx.lineJoin = 'round';
      for (var j = 0; j < wardPaths.length; j++) {
        ctx.fillStyle = ctx.strokeStyle = wardFill[j];
        ctx.fill(wardPaths[j]);
        ctx.stroke(wardPaths[j]);
      }
    }

    // The regions over the wards, and their names.
    var regPaths = {}, labels = {};
    REGIONS.forEach(function (r) {
      if (!r.p) return;
      var d = rings(r.p, r.n).map(function (pts) {
        return 'M' + pts.map(function (q) { return q[0] + ' ' + q[1]; }).join('L') + 'Z';
      }).join('');
      var path = document.createElementNS(NS, 'path');
      path.setAttribute('d', d);
      path.setAttribute('class', 'hm-reg');
      path.setAttribute('data-id', r.id);
      svg.appendChild(path);
      regPaths[r.id] = path;
    });
    REGIONS.forEach(function (r) {
      if (!r.p) return;
      var t = document.createElementNS(NS, 'text');
      t.setAttribute('x', r.lx);
      t.setAttribute('y', r.ly);
      t.setAttribute('text-anchor', 'middle');
      t.setAttribute('class', 'hm-lbl');
      t.textContent = r.name;
      svg.appendChild(t);
      labels[r.id] = t;
    });

    var on = null, pinned = false;
    function tipHtml(r) {
      return '<span class="tn">' + esc(r.name) + '</span>' +
        regionCard(r).map(function (l) { return '<span class="tl">' + esc(l) + '</span>'; }).join('') +
        '<span class="th">Select it to open its map.</span>' +
        '<a class="go" href="' + esc(hrefOf(r.id)) + '">Open the map of ' + esc(placeOf(r.name)) + '</a>';
    }
    function place(x, y) {
      var bw = box.clientWidth, bh = box.clientHeight, tw = tip.offsetWidth, th = tip.offsetHeight;
      var left = x + 16, top = y + 16;
      if (left + tw > bw - 8) left = Math.max(8, x - tw - 16);
      if (top + th > bh - 8) top = Math.max(8, y - th - 16);
      tip.style.left = left + 'px';
      tip.style.top = top + 'px';
    }
    // A point in the box for a region's label, for a card opened from the list.
    function labelPoint(r) {
      var s = Math.min(box.clientWidth / M.w, box.clientHeight / M.h);
      return [(box.clientWidth - M.w * s) / 2 + r.lx * s, (box.clientHeight - M.h * s) / 2 + r.ly * s];
    }
    setOn = function (id, x, y, pin) {
      var r = REGIONS.filter(function (q) { return q.id === id; })[0] || null;
      if (on && regPaths[on]) { regPaths[on].classList.remove('on'); labels[on].classList.remove('on'); }
      on = r && regPaths[r.id] ? r.id : null;
      svg.classList.toggle('has-on', !!on);
      document.querySelectorAll('.hr-item').forEach(function (a) { a.classList.toggle('on', a.getAttribute('data-id') === id); });
      if (!on) { tip.hidden = true; tip.classList.remove('pinned'); pinned = false; return; }
      regPaths[on].classList.add('on');
      labels[on].classList.add('on');
      // In front of the others, so its whole outline shows.
      svg.insertBefore(regPaths[on], labels[REGIONS[0].id] || null);
      pinned = !!pin;
      tip.classList.toggle('pinned', pinned);
      tip.innerHTML = tipHtml(r);
      tip.hidden = false;
      if (!pinned) {
        var pt = (x == null) ? labelPoint(r) : [x, y];
        place(pt[0], pt[1]);
      }
    };

    function boxPoint(e) {
      var b = box.getBoundingClientRect();
      return [e.clientX - b.left, e.clientY - b.top];
    }
    svg.addEventListener('pointermove', function (e) {
      if (e.pointerType === 'touch' || pinned) return;
      var t = e.target.closest ? e.target.closest('.hm-reg') : null;
      var p = boxPoint(e);
      if (!t) { if (on) setOn(null); return; }
      if (t.getAttribute('data-id') !== on) setOn(t.getAttribute('data-id'), p[0], p[1]);
      else place(p[0], p[1]);
    });
    svg.addEventListener('pointerleave', function (e) { if (e.pointerType !== 'touch' && !pinned) setOn(null); });
    svg.addEventListener('click', function (e) {
      var t = e.target.closest ? e.target.closest('.hm-reg') : null;
      if (!t) { setOn(null); return; }
      var id = t.getAttribute('data-id');
      // A mouse opens the map at once; a tap shows the card and its button
      // first, since a finger has no way to point without choosing.
      if (!TOUCH && !lastPointerTouch) { location.href = hrefOf(id); return; }
      setOn(id, null, null, true);
    });
    var lastPointerTouch = false;
    svg.addEventListener('pointerdown', function (e) { lastPointerTouch = e.pointerType === 'touch'; });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && on) setOn(null); });

    paint();
    var t0 = null;
    window.addEventListener('resize', function () { clearTimeout(t0); t0 = setTimeout(paint, 80); });
    window.PH_HOME_PAINT = paint;
  }

  // The list: hovering or focusing a region brings it to the front on the map.
  function wireList() {
    document.querySelectorAll('.hr-item').forEach(function (a) {
      var id = a.getAttribute('data-id');
      a.addEventListener('mouseenter', function () { setOn(id); });
      a.addEventListener('mouseleave', function () { setOn(null); });
      a.addEventListener('focus', function () { setOn(id); });
      a.addEventListener('blur', function () { setOn(null); });
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', build);
  else build();
})();
