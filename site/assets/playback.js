/* The DFLv2 game replay viewer. Loaded on demand by the site (assets/app.js) when someone opens #/game/<id>.
   It reads published playback files only (format dfl-public-playback, version 1: docs/PUBLIC_PLAYBACK_FORMAT.md) and draws them on a canvas.
   It never runs, imports or asks for the simulation, it cannot change the league, and it holds no secrets. All text from a file goes in as plain text
   nodes; colours are checked before use. What it draws is what the archive recorded: between two recorded samples a player moves in a straight line at
   constant speed (stated on the page); a play with no recorded movement is shown as an event card, never as invented motion. */
(function () {
  'use strict';
  var FORMAT = 'dfl-public-playback', VERSION = 1;
  var LEN = 100, EZ = 10, WID = 160 / 3;               // field: 100 yards between goal lines, 10-yard end zones, 53.33 yards across
  var SPEEDS = [0.25, 0.5, 1, 2, 4];
  var HOLD_S = 1.2, CARD_S = 2.6;                      // pause after a traced play ends, and how long an event card without movement stays up
  var EVENT_LABEL = { handoff: 'Handoff', throw: 'Pass thrown', catch: 'Catch', tackle: 'Tackle', fumble: 'Fumble', intercept: 'Interception', batted: 'Pass batted',
    drop: 'Dropped pass', breakup: 'Pass breakup', scramble: 'Scramble', miss: 'Missed tackle', 'end:td': 'Touchdown', 'end:tackle': 'Tackled', 'end:sack': 'Sack',
    'end:incomplete': 'Incomplete', 'end:fumble': 'Fumble', 'end:int': 'Interception', 'end:out': 'Out of bounds' };

  function h(tag, attrs) {
    var el = document.createElement(tag);
    if (attrs) for (var k in attrs) {
      var v = attrs[k];
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
      else el.setAttribute(k, v === true ? '' : v);
    }
    for (var i = 2; i < arguments.length; i++) add(el, arguments[i]);
    return el;
  }
  function add(el, c) {
    if (c === null || c === undefined || c === false) return;
    if (Array.isArray(c)) { c.forEach(function (x) { add(el, x); }); return; }
    el.appendChild(typeof c === 'object' ? c : document.createTextNode(String(c)));
  }
  function hex(x, d) { return /^#[0-9a-fA-F]{6}$/.test(x || '') ? x : (d || '#28443A'); }
  function lum(c) { return 0.299 * parseInt(c.substr(1, 2), 16) + 0.587 * parseInt(c.substr(3, 2), 16) + 0.114 * parseInt(c.substr(5, 2), 16); }
  function ink(c) { return lum(c) > 150 ? '#10170F' : '#FFFFFF'; }
  function mmss(s) { s = Math.max(0, Math.round(s)); return Math.floor(s / 60) + ':' + ('0' + (s % 60)).slice(-2); }
  function ordinal(n) { return n + (n === 1 ? 'st' : n === 2 ? 'nd' : n === 3 ? 'rd' : 'th'); }
  function periodName(p, total) { return p <= total ? 'Q' + p : 'OT'; }

  // ---------------------------------------------------------------- reading the files
  function checkFormat(o, what) {
    if (!o || o.format !== FORMAT) throw new Error(what + ' is not a ' + FORMAT + ' file');
    if (o.format_version !== VERSION) {
      var e = new Error(what + ' is replay format version ' + o.format_version + '; this page reads version ' + VERSION + '. The site needs updating before this replay can be shown.');
      e.incompatible = true;
      throw e;
    }
  }
  function toHex(buf) { return Array.prototype.map.call(new Uint8Array(buf), function (b) { return ('0' + b.toString(16)).slice(-2); }).join(''); }
  function fetchGame(base, entry, signal) {
    return fetch(base + entry.file, { signal: signal }).then(function (r) {
      if (!r.ok) throw new Error('the replay file could not be read (HTTP ' + r.status + ')');
      return r.arrayBuffer();
    }).then(function (buf) {
      var check = window.crypto && window.crypto.subtle ? window.crypto.subtle.digest('SHA-256', buf).then(function (d) {
        if (entry.sha256 && toHex(d) !== entry.sha256) throw new Error('the replay file does not match its published checksum; it was not shown');
      }) : Promise.resolve();
      return check.then(function () { return JSON.parse(new TextDecoder('utf-8').decode(buf)); });
    });
  }
  // A trace's base64 column-delta int16 arrays -> Float32Array (steps x columns), in yards.
  function decodeCols(b64, n, ncols) {
    var bin = atob(b64), bytes = new Uint8Array(bin.length), i;
    for (i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    if (bytes.length !== n * ncols * 2) throw new Error('a trace is the wrong length');
    var dv = new DataView(bytes.buffer), out = new Float32Array(n * ncols);
    for (var c = 0; c < ncols; c++) {
      var v = 0;
      for (var s = 0; s < n; s++) { v += dv.getInt16((c * n + s) * 2, true); out[s * ncols + c] = v / 100; }
    }
    return out;
  }
  function prepare(play) {                     // decode lazily, once; a damaged trace affects only its own play
    var t = play.trace;
    if (!t) return null;
    if (t._ok === undefined) {
      try { t._pos = decodeCols(t.pos, t.n, 44); t._ball = decodeCols(t.ball, t.n, 3); t._ok = true; } catch (e) { t._ok = false; t._err = e.message; }
    }
    return t._ok ? t : null;
  }
  function sampleAt(t, time, out) {            // linear interpolation between recorded samples
    var f = Math.min(time, t.t_end) / t.dt, i = Math.min(Math.floor(f), t.n - 1), j = Math.min(i + 1, t.n - 1);
    var ti = Math.min(i * t.dt, t.t_end), tj = Math.min(j * t.dt, t.t_end), a = tj > ti ? Math.min(1, Math.max(0, (time - ti) / (tj - ti))) : 0;
    var P = t._pos, B = t._ball, k;
    for (k = 0; k < 44; k++) out.p[k] = P[i * 44 + k] + (P[j * 44 + k] - P[i * 44 + k]) * a;
    for (k = 0; k < 3; k++) out.b[k] = B[i * 3 + k] + (B[j * 3 + k] - B[i * 3 + k]) * a;
    return out;
  }

  // ---------------------------------------------------------------- the viewer
  function create(opts) {
    // opts: { base: 'feed/playback/', entry: <index entry>, load: optional function -> promise of the game object }
    var base = opts.base, entry = opts.entry;
    var root = h('section', { class: 'pb', 'aria-label': 'Game replay' });
    var ac = window.AbortController ? new AbortController() : null;
    var st = { game: null, idx: 0, t: 0, playing: false, speed: 1, hold: 0, dead: false, raf: 0, last: 0, view: 'h', size: [0, 0], scale: 1 };
    var el = {}, ctx, buf = { p: new Float32Array(44), b: new Float32Array(3) }, fieldCache = null;

    function fail(msg, cls) { root.textContent = ''; root.appendChild(h('div', { class: 'err ' + (cls || ''), role: 'alert', text: msg })); }
    root.appendChild(h('p', { class: 'muted', text: 'Loading the replay…' }));

    // opts.load (optional): a function returning a promise of the game object, for a page that carries its game inside itself (the offline demonstration page)
    (opts.load ? Promise.resolve().then(opts.load) : fetchGame(base, entry, ac && ac.signal)).then(function (g) {
      if (st.dead) return;
      checkFormat(g, 'This replay');
      if (g.gid !== entry.gid) throw new Error('the file holds a different game than the one requested');
      st.game = g;
      build();
      goto(0, 0, false);
    }).catch(function (e) {
      if (st.dead || (e && e.name === 'AbortError')) return;
      fail('This replay could not be shown: ' + e.message);
    });

    function teamColors(g) {
      var a = hex(g.teams.away.colors[0], '#B22222'), hm = hex(g.teams.home.colors[0], '#1F4E9C');
      var a2 = hex(g.teams.away.colors[1] || '#FFFFFF', '#FFFFFF'), h2 = hex(g.teams.home.colors[1] || '#FFFFFF', '#FFFFFF');
      var close = Math.abs(lum(a) - lum(hm)) < 40;                  // two similar primaries: the away side wears its second colour
      return { away: close ? a2 : a, home: hm, awayRing: close ? a : a2, homeRing: h2 };
    }

    function build() {
      var g = st.game, C = (st.colors = teamColors(g));
      root.textContent = '';
      var warn = [];
      var last = g.plays[g.plays.length - 1];
      if (last && (last.score.away !== g.final.away || last.score.home !== g.final.home)) warn.push('The running score at the last play does not match the final score in this file; treat the replay with care.');
      el.board = h('div', { class: 'pb-board', role: 'group', 'aria-label': 'Scoreboard' },
        h('div', { class: 'pb-team away' }, h('span', { class: 'pb-chip', 'data-side': 'away', text: g.teams.away.abbr }), h('b', { class: 'pb-score', 'data-k': 'away', text: '0' })),
        h('div', { class: 'pb-mid' }, h('div', { class: 'pb-clock', 'data-k': 'clock', text: '' }), h('div', { class: 'pb-dd', 'data-k': 'dd', text: '' })),
        h('div', { class: 'pb-team home' }, h('b', { class: 'pb-score', 'data-k': 'home', text: '0' }), h('span', { class: 'pb-chip', 'data-side': 'home', text: g.teams.home.abbr })));
      [].forEach.call(el.board.querySelectorAll('.pb-chip'), function (c) { var col = hex(g.teams[c.getAttribute('data-side')].colors[0]); c.style.setProperty('--t', col); c.style.setProperty('--tx', ink(col)); });
      el.info = h('div', { class: 'pb-info' }, h('span', { 'data-k': 'poss', text: '' }), h('span', { 'data-k': 'spot', text: '' }));
      el.canvas = h('canvas', { class: 'pb-canvas', role: 'img', 'aria-label': 'Football field with the 22 players and the ball' });
      el.stage = h('div', { class: 'pb-stage' }, el.canvas, h('div', { class: 'pb-chipline', 'data-k': 'event', 'aria-hidden': 'true', text: '' }),
        h('div', { class: 'pb-card', 'data-k': 'card', hidden: true }));
      el.text = h('p', { class: 'pb-text', role: 'status', 'aria-live': 'polite', text: '' });
      el.who = h('p', { class: 'pb-who', 'data-k': 'who', text: 'Point at or tap a player to see who it is.' });
      el.play = h('button', { type: 'button', class: 'pb-btn', 'aria-label': 'Play', text: '▶' });
      el.prev = h('button', { type: 'button', class: 'pb-btn', 'aria-label': 'Previous play', text: '⏮' });
      el.next = h('button', { type: 'button', class: 'pb-btn', 'aria-label': 'Next play', text: '⏭' });
      el.speed = h('select', { class: 'pb-sel', 'aria-label': 'Speed' }, SPEEDS.map(function (s) { return h('option', { value: String(s), text: s + '×' }); }));
      el.speed.value = '1';
      el.scrub = h('input', { type: 'range', class: 'pb-range', min: '0', max: '1000', value: '0', 'aria-label': 'Position within the play' });
      el.pick = h('input', { type: 'range', class: 'pb-range', min: '0', max: String(g.plays.length - 1), value: '0', 'aria-label': 'Play number' });
      el.pickout = h('output', { class: 'pb-out', text: '' });
      el.list = h('ol', { class: 'pb-list', 'aria-label': 'Plays' });
      g.plays.forEach(function (p, i) {
        var li = h('li', null, h('button', { type: 'button', class: 'pb-row' + (p.trace ? '' : ' nomove'), 'data-i': String(i) },
          h('span', { class: 'pb-rt', text: periodName(p.period, g.periods) + ' ' + mmss(p.clock) }), h('span', { class: 'pb-rx', text: p.text }),
          h('span', { class: 'pb-rs', text: p.score.away + '–' + p.score.home }), p.trace ? null : h('span', { class: 'pb-tag', text: 'no movement recorded' })));
        el.list.appendChild(li);
      });
      root.appendChild(h('div', { class: 'pb-head' }, h('h1', { class: 'pb-title', text: g.teams.away.name + ' at ' + g.teams.home.name }),
        h('div', { class: 'sub', text: (g.date || '') + (g.neutral ? ' · neutral site' : '') + ' · final ' + g.final.away + '–' + g.final.home +
          (g.winner ? ' (' + g.teams[g.winner].abbr + ' win)' : ' (tie)') })));
      if (warn.length) root.appendChild(h('div', { class: 'err', role: 'alert', text: warn.join(' ') }));
      root.appendChild(h('div', { class: 'pb-main' }, el.board, el.info, el.stage, el.who, el.text,
        h('div', { class: 'pb-controls' }, el.prev, el.play, el.next, h('label', { class: 'pb-lab' }, 'Speed ', el.speed),
          h('label', { class: 'pb-lab grow' }, 'In this play ', el.scrub), h('label', { class: 'pb-lab grow' }, 'Play ', el.pick, el.pickout)),
        h('p', { class: 'pb-note muted', text: 'Replayed from what the league recorded: ' + g.traced_plays + ' of ' + g.plays.length + ' plays have recorded player movement' +
          ' (kickoffs, kicks, penalties, timeouts and scoring events have none and are shown as events). Positions are sampled every ' + (g.sampling.every * g.sampling.step_seconds).toFixed(2) +
          ' s; between samples a player moves in a straight line at constant speed. Names come from the public rosters when this file was made. Keys: space plays or pauses, ← → change play.' })));
      root.appendChild(h('div', { class: 'pb-side' }, h('h2', { text: 'Plays' }), el.list));
      ctx = el.canvas.getContext('2d');
      wire();
      resize();
      st.raf = requestAnimationFrame(frame);
    }

    function wire() {
      function whoAt(e) {
        var tr = cur().trace && prepare(cur());
        if (!tr || !st.hits) { q('who').textContent = 'No player movement is recorded for this play.'; return; }
        var rc = el.canvas.getBoundingClientRect(), x = (e.clientX - rc.left) * el.canvas.width / rc.width, y = (e.clientY - rc.top) * el.canvas.height / rc.height, best = null, bd = 1e9;
        st.hits.forEach(function (hh) { var d = Math.pow(hh[0] - x, 2) + Math.pow(hh[1] - y, 2); if (d < bd) { bd = d; best = hh; } });
        var lim = Math.max(14, st.scale * 3.2);
        if (!best || bd > lim * lim) return;
        var pid = String(tr.ids[best[2]]), info = st.game.roster[pid] || {}, ab = st.game.teams[best[3]].abbr, role = (tr.roles[best[2]] || '').replace(/_/g, ' ').toUpperCase();
        q('who').textContent = (info.number != null ? '#' + info.number + ' ' : '') + (info.name || ('player ' + pid + ' (no public name)')) + ' · ' + (info.pos || role) + ' · ' + ab + ' · ' + (best[2] < 11 ? 'offense' : 'defense') + ' (' + role + ' on this play)';
      }
      el.canvas.addEventListener('pointermove', whoAt);
      el.canvas.addEventListener('click', whoAt);
      el.play.addEventListener('click', toggle);
      el.prev.addEventListener('click', function () { goto(st.idx - 1, 0, st.playing); });
      el.next.addEventListener('click', function () { goto(st.idx + 1, 0, st.playing); });
      el.speed.addEventListener('change', function () { st.speed = parseFloat(el.speed.value) || 1; });
      el.pick.addEventListener('input', function () { goto(+el.pick.value, 0, false); });
      el.scrub.addEventListener('input', function () { var tr = cur().trace; if (tr && tr._ok) { st.playing = false; st.hold = 0; st.t = (+el.scrub.value / 1000) * tr.t_end; sync(); } });
      el.list.addEventListener('click', function (e) { var b = e.target.closest ? e.target.closest('.pb-row') : null; if (b) goto(+b.getAttribute('data-i'), 0, false); });
      st.onkey = function (e) {
        if (!root.isConnected || /^(INPUT|SELECT|TEXTAREA)$/.test((e.target || {}).tagName || '') && e.key !== ' ') return;
        if (e.key === ' ' && !/^(BUTTON|SELECT)$/.test((e.target || {}).tagName || '')) { e.preventDefault(); toggle(); }
        else if (e.key === 'ArrowRight' && !/^(INPUT|SELECT)$/.test((e.target || {}).tagName || '')) goto(st.idx + 1, 0, st.playing);
        else if (e.key === 'ArrowLeft' && !/^(INPUT|SELECT)$/.test((e.target || {}).tagName || '')) goto(st.idx - 1, 0, st.playing);
      };
      document.addEventListener('keydown', st.onkey);
      st.onresize = resize;
      window.addEventListener('resize', st.onresize);
    }

    function cur() { return st.game.plays[st.idx]; }
    function toggle() {
      if (st.idx >= st.game.plays.length - 1 && st.hold >= holdFor(cur())) { goto(0, 0, true); return; }          // at the end: play again from the start
      st.playing = !st.playing; st.last = 0; sync();
    }
    function holdFor(p) { return p.trace && prepare(p) ? HOLD_S : CARD_S; }
    function goto(i, t, playing) {
      var n = st.game.plays.length;
      st.idx = Math.max(0, Math.min(n - 1, i)); st.t = t || 0; st.hold = 0; st.playing = !!playing && true; st.last = 0;
      prepare(cur());
      sync(true);
    }
    // advance by `dt` real seconds (already multiplied by speed); the animation loop and the tests both call this
    function advance(dt) {
      var p = cur(), tr = p.trace && prepare(p);
      if (tr) {
        if (st.t < tr.t_end) { st.t = Math.min(tr.t_end, st.t + dt); if (st.t < tr.t_end) return; dt = 0; }
      }
      st.hold += dt;
      if (st.hold >= holdFor(p)) {
        if (st.idx >= st.game.plays.length - 1) { st.playing = false; sync(); return; }
        goto(st.idx + 1, 0, true);
      }
    }
    function frame(ts) {
      if (st.dead) return;
      if (st.playing) {
        var dt = st.last ? Math.min(0.25, (ts - st.last) / 1000) : 0;
        st.last = ts;
        advance(dt * st.speed);
      }
      draw();
      st.raf = requestAnimationFrame(frame);
    }

    // ---- text and controls follow the state
    function q(k) { return root.querySelector('[data-k="' + k + '"]'); }
    function sync(force) {
      var g = st.game, p = cur(), prev = st.idx > 0 ? g.plays[st.idx - 1] : null, sc = prev ? prev.score : { away: 0, home: 0 };
      var shown = p.trace ? sc : p.score;              // a snap shows the score as it stands when the ball is snapped; a scoring event shows its result
      if (p.kind === 'touchdown' || p.kind === 'field goal' || p.kind === 'extra point' || p.kind === 'safety' || p.kind === 'two-point try') shown = p.score;
      q('away').textContent = String(shown.away); q('home').textContent = String(shown.home);
      q('clock').textContent = periodName(p.period, g.periods) + ' ' + mmss(p.clock);
      var dd = p.down ? ordinal(p.down) + ' & ' + p.dist : '';
      q('dd').textContent = dd;
      var off = p.off ? g.teams[p.off].abbr : '', def = p.off ? g.teams[p.off === 'home' ? 'away' : 'home'].abbr : '';
      q('poss').textContent = p.off ? 'Ball: ' + off : '';
      q('spot').textContent = p.yl != null ? 'On ' + (p.yl === 50 ? 'the 50' : p.yl < 50 ? off + ' ' + Math.round(p.yl) : def + ' ' + Math.round(100 - p.yl)) : '';
      el.text.textContent = p.text;
      el.play.textContent = st.playing ? '⏸' : '▶';
      el.play.setAttribute('aria-label', st.playing ? 'Pause' : (st.idx >= g.plays.length - 1 && st.hold >= holdFor(p) ? 'Play again' : 'Play'));
      el.pick.value = String(st.idx); el.pickout.textContent = (st.idx + 1) + ' of ' + g.plays.length;
      var tr = p.trace && prepare(p);
      el.scrub.disabled = !tr; el.scrub.value = tr ? String(Math.round(st.t / tr.t_end * 1000)) : '0';
      var card = q('card'), nomove = !tr;
      card.hidden = !nomove;
      if (nomove) { card.textContent = ''; card.appendChild(h('b', { text: p.kind.charAt(0).toUpperCase() + p.kind.slice(1) })); card.appendChild(h('span', { text: p.trace ? ('The movement for this play is damaged in the file and is not shown (' + (p.trace._err || 'unreadable') + ').') : 'No player movement was recorded for this play.' })); }
      var rows = el.list.querySelectorAll('.pb-row'), k;
      for (k = 0; k < rows.length; k++) { if (+rows[k].getAttribute('data-i') === st.idx) { rows[k].setAttribute('aria-current', 'true'); if (force && rows[k].scrollIntoView && el.list.scrollHeight > el.list.clientHeight) el.list.scrollTop = Math.max(0, rows[k].offsetTop - el.list.clientHeight / 2); } else rows[k].removeAttribute('aria-current'); }
      draw();
    }

    // ---- drawing
    function resize() {
      var w = el.stage.clientWidth || 800, vh = window.innerHeight || 700, portrait = w < 560 && vh > w;
      var dpr = Math.min(3, window.devicePixelRatio || 1);
      st.view = portrait ? 'v' : 'h';
      var cssW = w, cssH = portrait ? Math.min(Math.round(w * (LEN + 2 * EZ) / WID), Math.round(vh * 0.62)) : Math.round(w * WID / (LEN + 2 * EZ));
      if (portrait) cssW = Math.round(cssH * WID / (LEN + 2 * EZ));
      el.canvas.style.width = cssW + 'px'; el.canvas.style.height = cssH + 'px';
      el.canvas.width = Math.round(cssW * dpr); el.canvas.height = Math.round(cssH * dpr);
      st.size = [el.canvas.width, el.canvas.height]; st.scale = (portrait ? el.canvas.height / (LEN + 2 * EZ) : el.canvas.width / (LEN + 2 * EZ));
      fieldCache = null;
      draw();
    }
    function X(x, y) {                 // field position (x along the field with the end zones at -10..0 and 100..110, y up the screen from the bottom sideline) -> canvas px
      var s = st.scale;
      return st.view === 'h' ? [(x + EZ) * s, (WID - y) * s] : [y * s, (x + EZ) * s];
    }
    function abs(p, x, y) {             // a point in the offence's frame -> the fixed field (the home team attacks right in periods 1 and 3)
      return p.dir > 0 ? [x, y] : [LEN - x, WID - y];
    }
    function drawField(c) {
      var g = st.game, s = st.scale, w = st.size[0], hgt = st.size[1], col = st.colors;
      c.fillStyle = '#2C6B3F'; c.fillRect(0, 0, w, hgt);
      function rect(x0, y0, x1, y1) { var a = X(x0, y0), b = X(x1, y1); return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.abs(b[0] - a[0]), Math.abs(b[1] - a[1])]; }
      var r = rect(-EZ, 0, 0, WID); c.fillStyle = col.home; c.globalAlpha = 0.85; c.fillRect(r[0], r[1], r[2], r[3]);
      r = rect(LEN, 0, LEN + EZ, WID); c.fillStyle = col.away; c.fillRect(r[0], r[1], r[2], r[3]); c.globalAlpha = 1;
      for (var k = 0; k < 20; k++) { var x0 = k * 5, a = X(x0, 0), b = X(x0 + 5, WID); if (k % 2 === 0) { c.fillStyle = 'rgba(255,255,255,0.04)'; c.fillRect(Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.abs(b[0] - a[0]), Math.abs(b[1] - a[1])); } }
      c.strokeStyle = 'rgba(255,255,255,0.85)'; c.lineWidth = Math.max(1, s * 0.12);
      for (var y = 0; y <= 100; y += 5) { var p0 = X(y, 0), p1 = X(y, WID); c.beginPath(); c.moveTo(p0[0], p0[1]); c.lineTo(p1[0], p1[1]); c.lineWidth = y % 10 === 0 ? Math.max(1, s * 0.16) : Math.max(1, s * 0.08); c.stroke(); }
      c.lineWidth = Math.max(1, s * 0.08);
      for (var yy = 1; yy < 100; yy++) { if (yy % 5 === 0) continue; [WID / 2 - 3, WID / 2 + 3].forEach(function (hy) { var a1 = X(yy, hy - 0.5), a2 = X(yy, hy + 0.5); c.beginPath(); c.moveTo(a1[0], a1[1]); c.lineTo(a2[0], a2[1]); c.stroke(); }); }
      c.strokeRect(rect(-EZ, 0, LEN + EZ, WID)[0], rect(-EZ, 0, LEN + EZ, WID)[1], rect(-EZ, 0, LEN + EZ, WID)[2], rect(-EZ, 0, LEN + EZ, WID)[3]);
      c.fillStyle = 'rgba(255,255,255,0.75)'; c.textAlign = 'center'; c.textBaseline = 'middle'; c.font = 'bold ' + Math.max(8, s * 2.6) + 'px sans-serif';
      for (var n = 10; n <= 90; n += 10) {
        var num = String(n <= 50 ? n : 100 - n);
        [WID * 0.2, WID * 0.8].forEach(function (yy2) { var q = X(n, yy2); c.save(); c.translate(q[0], q[1]); if (st.view === 'v') c.rotate(Math.PI / 2); c.fillText(num, 0, 0); c.restore(); });
      }
      c.font = 'bold ' + Math.max(9, s * 3.2) + 'px sans-serif'; c.fillStyle = ink(col.home);
      var hq = X(-EZ / 2, WID / 2); c.save(); c.translate(hq[0], hq[1]); c.rotate(st.view === 'h' ? -Math.PI / 2 : 0); c.fillText(g.teams.home.abbr, 0, 0); c.restore();
      c.fillStyle = ink(col.away); var aq = X(LEN + EZ / 2, WID / 2); c.save(); c.translate(aq[0], aq[1]); c.rotate(st.view === 'h' ? Math.PI / 2 : Math.PI); c.fillText(g.teams.away.abbr, 0, 0); c.restore();
    }
    function draw() {
      if (!ctx || !st.game) return;
      var g = st.game, p = cur(), s = st.scale, c = ctx, col = st.colors;
      if (!fieldCache) { fieldCache = document.createElement('canvas'); fieldCache.width = st.size[0]; fieldCache.height = st.size[1]; drawField(fieldCache.getContext('2d')); }
      c.drawImage(fieldCache, 0, 0);
      var tr = p.trace && prepare(p);
      if (!tr) { var e0 = q0('event'); if (e0 && e0.textContent) e0.textContent = ''; return; }   // no recorded movement: nothing is drawn but the field
      // line of scrimmage and the first-down line, from the recorded spot and distance
      var los = abs(p, tr.los, 0)[0];
      function vline(x, color, w) { var a = X(x, 0), b = X(x, WID); c.strokeStyle = color; c.lineWidth = w; c.beginPath(); c.moveTo(a[0], a[1]); c.lineTo(b[0], b[1]); c.stroke(); }
      vline(los, '#4DA3FF', Math.max(2, s * 0.25));
      if (p.down && p.dist) { var fd = abs(p, Math.min(100, tr.los + p.dist), 0)[0]; vline(fd, '#F2D43D', Math.max(2, s * 0.25)); }
      sampleAt(tr, st.t, buf);
      var offSide = p.off, defSide = p.off === 'home' ? 'away' : 'home', r = Math.max(5, s * 1.6);
      st.hits = [];
      for (var k = 0; k < 22; k++) {
        var side = k < 11 ? offSide : defSide, pt = abs(p, buf.p[k * 2], buf.p[k * 2 + 1]), q = X(pt[0], pt[1]);
        var fill = col[side], ring = side === 'home' ? col.homeRing : col.awayRing;
        st.hits.push([q[0], q[1], k, side]);
        c.beginPath(); c.arc(q[0], q[1], r, 0, Math.PI * 2); c.fillStyle = fill; c.fill(); c.lineWidth = Math.max(1, s * 0.22); c.strokeStyle = k < 11 ? '#FFFFFF' : ring; c.stroke();
        var pid = String(tr.ids[k]), info = g.roster[pid], label = info && info.number != null ? String(info.number) : (tr.roles[k] || '').replace(/_.*/, '').toUpperCase().slice(0, 2);
        if (r >= 6) { c.fillStyle = ink(fill); c.font = 'bold ' + Math.round(r * 0.95) + 'px sans-serif'; c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillText(label, q[0], q[1] + 0.5); }
      }
      var showBall = st.t >= (tr.ball_from || 0) * tr.dt - 1e-9;           // before the first recorded ball sample the ball is not drawn
      var bp = abs(p, buf.b[0], buf.b[1]), bq = X(bp[0], bp[1]), lift = buf.b[2] * s * 0.5;
      if (showBall) {
      c.fillStyle = 'rgba(0,0,0,0.3)'; c.beginPath(); c.ellipse(bq[0], bq[1], r * 0.55, r * 0.35, 0, 0, Math.PI * 2); c.fill();
      c.fillStyle = '#7A4A21'; c.strokeStyle = '#FFFFFF'; c.lineWidth = Math.max(1, s * 0.1); c.beginPath();
      c.ellipse(bq[0], bq[1] - (st.view === 'h' ? lift : 0), r * 0.62, r * 0.4, st.view === 'h' ? 0 : Math.PI / 2, 0, Math.PI * 2); c.fill(); c.stroke();
      }
      // the latest recorded event up to now
      var ev = '';
      tr.events.forEach(function (e) { if (e[0] <= st.t + 1e-6 && EVENT_LABEL[e[1]]) ev = EVENT_LABEL[e[1]]; });
      var evEl = q0('event'); if (evEl && evEl.textContent !== ev) evEl.textContent = ev;
    }
    function q0(k) { return el.stage.querySelector('[data-k="' + k + '"]'); }

    // ---- the handle the page uses (and the tests)
    return {
      node: root,
      destroy: function () {
        st.dead = true; st.playing = false;
        if (ac) ac.abort();
        if (st.raf) cancelAnimationFrame(st.raf);
        if (st.onkey) document.removeEventListener('keydown', st.onkey);
        if (st.onresize) window.removeEventListener('resize', st.onresize);
      },
      state: function () { var p = st.game && cur(); return { loaded: !!st.game, index: st.idx, plays: st.game ? st.game.plays.length : 0, t: st.t, playing: st.playing, speed: st.speed, view: st.view,
        traced: !!(p && p.trace && prepare(p)), kind: p ? p.kind : null, gid: st.game ? st.game.gid : null }; },
      refresh: function () { if (st.game) resize(); },
      playerAt: function (k) { var h0 = (st.hits || [])[k]; if (!h0) return null; var rc = el.canvas.getBoundingClientRect(); return [rc.left + h0[0] * rc.width / el.canvas.width, rc.top + h0[1] * rc.height / el.canvas.height]; },
      goto: function (i, t) { if (st.game) { goto(i, t || 0, false); if (t) { st.t = t; sync(); } } },
      advance: function (s) { if (st.game) { advance(s); sync(); } },
      play: function () { if (st.game && !st.playing) toggle(); },
      pause: function () { st.playing = false; if (st.game) sync(); }
    };
  }

  window.DFLPlayback = { create: create, FORMAT: FORMAT, VERSION: VERSION };
})();
