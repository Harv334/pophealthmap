/* PopHealth Map home page.

   The bare address shows this instead of a region's map (the gate is in the
   head of index.html). It is a title and a picture of England that fills the
   rest of the screen: every ward coloured by deprivation, from
   data/map/home_map.js (scripts/build_home_map.py), with the nine regions
   over them. Each region is a link to its map. Pointing to one, or moving to
   it with Tab, makes it pop out of the picture, a little larger and lifted
   on a shadow, with a card that gives its figures; selecting it opens its
   map. On a touch screen the first tap shows the card and a button to open
   it, and a second tap on the same region opens it too. Without the map
   file, the page lists the regions instead. None of the map's own data
   loads here.

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
  function pathD(pts) {
    return 'M' + pts.map(function (q) { return q[0] + ' ' + q[1]; }).join('L') + 'Z';
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
    var drawn = M && REGIONS.some(function (r) { return r.p; });
    // Without the map file, the regions as a list of links.
    var list = '<ul class="hr-list">' + REGIONS.map(function (r) {
      return '<li><a class="hr-item" href="' + esc(hrefOf(r.id)) + '">' + esc(r.name) + '</a></li>';
    }).join('') + '</ul>';
    return '<a class="skip" href="#main">Skip to main content</a>' +
      // The header and the map fill the screen; the footer is below it.
      '<div class="home-screen">' +
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
      '<p class="home-lede">Select an area to get started.</p>' +
      '</div>' +
      (drawn
        ? '<div class="hm-box">' +
          '<canvas aria-hidden="true"></canvas>' +
          // Each region is a link, so the keyboard and screen readers have
          // the same choices; the names and the raised copy are drawing only.
          '<svg class="hm-svg" viewBox="0 0 ' + M.w + ' ' + M.h + '" role="group" ' +
          'aria-label="Map of England. Each region is a link to its map."></svg>' +
          '<div class="hm-tip" hidden></div></div>'
        : list) +
      '</main></div>' +
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
    if (document.querySelector('.hm-box')) drawMap();
  }

  function drawMap() {
    var box = document.querySelector('.hm-box');
    var canvas = box.querySelector('canvas');
    var svg = box.querySelector('svg');
    var tip = box.querySelector('.hm-tip');
    var NS = 'http://www.w3.org/2000/svg';
    function el(name, attrs) {
      var n = document.createElementNS(NS, name);
      for (var k in attrs) n.setAttribute(k, attrs[k]);
      return n;
    }

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

    // The picture is drawn to the middle of the box, as large as fits, the
    // way the SVG over it places its viewBox.
    function fit() {
      var s = Math.min(box.clientWidth / M.w, box.clientHeight / M.h);
      return { s: s, x: (box.clientWidth - M.w * s) / 2, y: (box.clientHeight - M.h * s) / 2 };
    }
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

    // The regions over the wards, each a link, and their names.
    var regPaths = {}, regBox = {}, labels = {};
    var regLayer = svg.appendChild(el('g', {}));
    REGIONS.forEach(function (r) {
      if (!r.p) return;
      var outline = rings(r.p, r.n), b = [Infinity, Infinity, -Infinity, -Infinity];
      outline.forEach(function (pts) {
        pts.forEach(function (q) {
          b[0] = Math.min(b[0], q[0]); b[1] = Math.min(b[1], q[1]);
          b[2] = Math.max(b[2], q[0]); b[3] = Math.max(b[3], q[1]);
        });
      });
      regBox[r.id] = b;
      var link = el('a', { 'class': 'hm-link', href: hrefOf(r.id), 'data-id': r.id,
        'aria-label': r.name + '. ' + regionCard(r).join(' ') });
      regPaths[r.id] = link.appendChild(el('path', { 'class': 'hm-reg', 'data-id': r.id, d: outline.map(pathD).join('') }));
      regLayer.appendChild(link);
    });
    function label(r) {
      var t = el('text', { x: r.lx, y: r.ly, 'text-anchor': 'middle', 'class': 'hm-lbl' });
      t.textContent = r.name;
      return t;
    }
    var lblLayer = svg.appendChild(el('g', { 'aria-hidden': 'true' }));
    REGIONS.forEach(function (r) { if (r.p) labels[r.id] = lblLayer.appendChild(label(r)); });

    // The region that pops out: its own wards drawn again over the picture,
    // with its outline and name, then raised on a shadow and made a little
    // larger. Each region's copy is made the first time it is needed. It
    // takes no pointer: what the pointer selects is always the region
    // outline beneath it.
    var defs = el('defs', {});
    defs.innerHTML = '<filter id="hm-lift" x="-25%" y="-25%" width="150%" height="150%" color-interpolation-filters="sRGB">' +
      '<feDropShadow dx="0" dy="5" stdDeviation="7" flood-color="#212B32" flood-opacity=".38"/></filter>';
    svg.insertBefore(defs, svg.firstChild);
    var popLayer = svg.appendChild(el('g', { 'class': 'hm-pops', 'aria-hidden': 'true' }));
    var regionIndex = {};
    (M.regions || []).forEach(function (r, i) { regionIndex[r.id] = i; });
    var pops = {};
    function popOf(r) {
      if (pops[r.id]) return pops[r.id];
      var code = ALPHABET.charAt(regionIndex[r.id]), html = '';
      for (var j = 0; j < wardRings.length; j++) {
        if (M.wards.g.charAt(j) !== code) continue;
        html += '<path fill="' + wardFill[j] + '" stroke="' + wardFill[j] + '" d="' + pathD(wardRings[j]) + '"/>';
      }
      var d = regPaths[r.id].getAttribute('d');
      var g = el('g', { 'class': 'hm-pop', 'data-id': r.id, filter: 'url(#hm-lift)' });
      // The ring shows when the region was reached with the keyboard.
      g.innerHTML = '<g class="hm-pop-wards">' + html + '</g>' +
        '<path class="hm-pop-ring" d="' + d + '"/><path class="hm-pop-edge" d="' + d + '"/>';
      g.appendChild(label(r));
      g.style.display = 'none';
      popLayer.appendChild(g);
      // How far it grows: about the same distance for every region, so the
      // small ones (London) show as clearly as the large ones, from its
      // middle, and moved back in where that would take it off the picture.
      var bb = regBox[r.id], pad = 6;
      var k = 1 + Math.max(0.06, Math.min(0.6, 44 / Math.max(bb[2] - bb[0], bb[3] - bb[1])));
      var tx = (bb[0] + bb[2]) / 2 * (1 - k), ty = (bb[1] + bb[3]) / 2 * (1 - k);
      var x0 = bb[0] * k + tx, x1 = bb[2] * k + tx, y0 = bb[1] * k + ty, y1 = bb[3] * k + ty;
      if (x0 < pad) tx += pad - x0; else if (x1 > M.w - pad) tx -= x1 - (M.w - pad);
      if (y0 < pad) ty += pad - y0; else if (y1 > M.h - pad) ty -= y1 - (M.h - pad);
      pops[r.id] = { g: g, lift: 'translate(' + tx.toFixed(1) + 'px,' + ty.toFixed(1) + 'px) scale(' + k.toFixed(3) + ')' };
      return pops[r.id];
    }
    function lift(id, ring) {
      Object.keys(pops).forEach(function (k) {
        if (k === id) return;
        pops[k].g.style.display = 'none';
        pops[k].g.style.transform = '';
      });
      if (!id) return;
      var p = popOf(REGIONS.filter(function (q) { return q.id === id; })[0]);
      p.g.classList.toggle('ring', !!ring);
      if (p.g.style.display !== 'none') return;
      // From where it sits in the picture to its raised place.
      p.g.style.transform = 'none';
      p.g.style.display = '';
      void p.g.getBoundingClientRect();
      p.g.style.transform = p.lift;
    }

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
    // A point in the box for a region's label, for a card opened from the
    // keyboard.
    function labelPoint(r) {
      var f = fit();
      return [f.x + r.lx * f.s, f.y + r.ly * f.s];
    }
    // One region in front, or none. x and y place its card (by the pointer;
    // without them, by its name); pin keeps it open with its button, after a
    // tap; ring marks a region reached with the keyboard.
    function setOn(id, x, y, pin, ring) {
      var r = REGIONS.filter(function (q) { return q.id === id; })[0] || null;
      if (on && regPaths[on]) { regPaths[on].classList.remove('on'); labels[on].classList.remove('on'); }
      on = r && regPaths[r.id] ? r.id : null;
      svg.classList.toggle('has-on', !!on);
      lift(on, ring);
      if (!on) { tip.hidden = true; tip.classList.remove('pinned'); pinned = false; return; }
      regPaths[on].classList.add('on');
      labels[on].classList.add('on');
      pinned = !!pin;
      tip.classList.toggle('pinned', pinned);
      // A pinned card sits across the map, at the top for a region in the
      // south and at the foot for one in the north, so it never hides it.
      tip.classList.toggle('top', pinned && r.ly > M.h / 2);
      tip.innerHTML = tipHtml(r);
      tip.hidden = false;
      if (!pinned) {
        var pt = (x == null) ? labelPoint(r) : [x, y];
        place(pt[0], pt[1]);
      }
    }

    function boxPoint(e) {
      var b = box.getBoundingClientRect();
      return [e.clientX - b.left, e.clientY - b.top];
    }
    function linkOf(e) { return e.target.closest ? e.target.closest('.hm-link') : null; }
    svg.addEventListener('pointermove', function (e) {
      if (e.pointerType === 'touch' || pinned) return;
      var a = linkOf(e), p = boxPoint(e);
      if (!a) { if (on) setOn(null); return; }
      if (a.getAttribute('data-id') !== on) setOn(a.getAttribute('data-id'), p[0], p[1]);
      else place(p[0], p[1]);
    });
    svg.addEventListener('pointerleave', function (e) {
      if (e.pointerType !== 'touch' && !pinned && !svg.contains(document.activeElement)) setOn(null);
    });
    var lastPointerTouch = false;
    svg.addEventListener('pointerdown', function (e) { lastPointerTouch = e.pointerType === 'touch'; });
    svg.addEventListener('click', function (e) {
      var a = linkOf(e);
      if (!a) { setOn(null); return; }
      var id = a.getAttribute('data-id');
      // A mouse, the keyboard or a screen reader (a click with no press,
      // detail 0) follows the link. A tap shows the card and its button
      // first, since a finger has no way to point without choosing; a second
      // tap on the same region follows the link.
      if (e.detail === 0 || !(TOUCH || lastPointerTouch)) return;
      if (pinned && on === id) return;
      e.preventDefault();
      setOn(id, null, null, true);
    });
    // Tab moves from region to region, north to south.
    svg.addEventListener('focusin', function (e) {
      var a = linkOf(e);
      if (!a || (pinned && on === a.getAttribute('data-id'))) return;
      var kb = false;
      try { kb = a.matches(':focus-visible'); } catch (err) { kb = !lastPointerTouch; }
      setOn(a.getAttribute('data-id'), null, null, false, kb);
    });
    svg.addEventListener('focusout', function (e) {
      // Not while a tapped card is open: its button is about to be pressed.
      if (!pinned && !(e.relatedTarget && svg.contains(e.relatedTarget))) setOn(null);
    });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && on) setOn(null); });

    paint();
    var t0 = null;
    window.addEventListener('resize', function () { clearTimeout(t0); t0 = setTimeout(paint, 80); });
    window.PH_HOME_PAINT = paint;
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', build);
  else build();
})();
