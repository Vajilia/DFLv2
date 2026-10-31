/* The DFLv2 viewer: a static page that reads the published public feed (format dfl-public-feed, version 1) and draws it.
   It only ever reads files under feed/; it has no control over the league and holds no secrets. All text from the feed goes in as plain text
   nodes (never as markup), and colours are checked before use. */
(function () {
  'use strict';
  var FORMAT = 'dfl-public-feed', VERSION = 1, REFRESH_MS = 60000;
  var BASE = 'feed/';
  var S = { ptr: null, manifest: null, cache: {}, route: null, fresh: 0, error: null };

  // ---------------------------------------------------------------- tiny DOM helpers
  function h(tag, attrs) {
    var el = document.createElement(tag);
    if (attrs) for (var k in attrs) {
      var v = attrs[k];
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
      else if (k === 'style') el.setAttribute('style', v);
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
  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); }
  function safeHex(x, d) { return /^#[0-9a-fA-F]{6}$/.test(x || '') ? x : (d || '#28443A'); }
  function ink(hex) {
    var r = parseInt(hex.substr(1, 2), 16), g = parseInt(hex.substr(3, 2), 16), b = parseInt(hex.substr(5, 2), 16);
    return (0.299 * r + 0.587 * g + 0.114 * b) > 150 ? '#10170F' : '#FFFFFF';
  }

  // ---------------------------------------------------------------- formatting
  function parseDate(iso) {
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || '');
    return m ? new Date(+m[1], +m[2] - 1, +m[3]) : null;
  }
  function fmtDate(iso, long) {
    var d = parseDate(iso);
    if (!d) return iso || '';
    return d.toLocaleDateString(undefined, long ? { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' } : { weekday: 'short', month: 'short', day: 'numeric' });
  }
  function fmtMoney(n) { return '$' + (n >= 100 ? Math.round(n).toString() : (Math.round(n * 10) / 10).toString()); }
  function ago(iso) {
    var t = Date.parse(iso);
    if (isNaN(t)) return '';
    var s = Math.max(0, Math.round((Date.now() - t) / 1000));
    if (s < 90) return 'just now';
    if (s < 5400) return Math.round(s / 60) + ' min ago';
    if (s < 129600) return Math.round(s / 3600) + ' h ago';
    return Math.round(s / 86400) + ' days ago';
  }
  function fmtStamp(iso) {
    var t = Date.parse(iso);
    if (isNaN(t)) return iso || '';
    return new Date(t).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  }
  function rec(c) { return c.w + '-' + c.l + (c.t ? '-' + c.t : ''); }
  function pct(c) { var d = c.w + c.l; return d ? (c.w / d).toFixed(3).replace(/^0/, '') : '.000'; }

  // ---------------------------------------------------------------- feed access
  function embedded(url) { return window.DFL_EMBED && Object.prototype.hasOwnProperty.call(window.DFL_EMBED, url) ? window.DFL_EMBED[url] : undefined; }
  function getJSON(url, fresh) {
    var e = embedded(url);
    if (e !== undefined) return Promise.resolve(e);
    return fetch(url, fresh ? { cache: 'no-store' } : {}).then(function (r) {
      if (!r.ok) { var err = new Error(url + ' → HTTP ' + r.status); err.status = r.status; throw err; }
      return r.json();
    });
  }
  function checkFormat(o, what) {
    if (!o || o.format !== FORMAT) throw new Error(what + ' is not a ' + FORMAT + ' feed');
    if (o.format_version !== VERSION) throw new Error(what + ' is feed version ' + o.format_version + '; this page reads version ' + VERSION + '. The site needs updating.');
  }
  function loadPointer() {
    return getJSON(BASE + 'latest.json', true).then(function (p) { checkFormat(p, 'latest.json'); return p; });
  }
  function loadRelease(ptr) {
    return getJSON(BASE + 'releases/' + ptr.release + '/manifest.json').then(function (m) {
      checkFormat(m, 'manifest.json');
      S.ptr = ptr; S.manifest = m; S.cache = {};
    });
  }
  function file(name) {
    if (S.cache[name]) return S.cache[name];
    var entry = S.manifest.files[name];
    if (!entry) return Promise.reject(new Error('This release has no ' + name));
    var rel = S.ptr.release;
    return (S.cache[name] = getJSON(BASE + 'releases/' + rel + '/' + name).catch(function (e) {
      delete S.cache[name];
      if (e.status === 404 || e.status === 410) e.gone = rel;          // the release this page is reading has been removed from the server
      throw e;
    }));
  }
  // A release can be pruned while a slow viewer is still reading it. Recovery: forget everything read from it, ask for the live release, and
  // carry on with that one (never mixing files of two releases on one page). If the live release is the very one that failed, the files are
  // really missing and the error is shown.
  function recover(goneRelease) {
    return loadPointer().then(function (p) {
      if (p.release === goneRelease) throw new Error('release ' + goneRelease + ' is missing from the server');
      return loadRelease(p).then(function () { S.fresh = Date.now(); S.recovered = Date.now(); chrome(); });
    });
  }
  function files() { return Promise.all(Array.prototype.map.call(arguments, file)); }

  // ---------------------------------------------------------------- shared pieces
  function badge(team, size) {
    var c = team && team.colors && team.colors[0] ? safeHex(team.colors[0].hex) : '#28443A';
    return h('span', { class: 'badge ' + (size || ''), style: '--t:' + c + ';--tx:' + ink(c), 'aria-hidden': 'true', text: team ? team.abbr : '?' });
  }
  function teamIndex(teams) { var m = {}; teams.teams.forEach(function (t) { m[t.abbr] = t; }); return m; }
  function clubLink(T, abbr, full) {
    var t = T[abbr];
    return h('a', { class: 'club', href: '#/team/' + encodeURIComponent(abbr) }, badge(t, 'sm'), h('span', { class: 'nm', text: t ? (full ? t.full_name : t.city) : abbr }));
  }
  function note(n) { return h('p', { class: 'notice ' + (n.level === 'important' ? 'important' : '') , text: n.text }); }
  function empty(text) { return h('p', { class: 'empty', text: text }); }
  function section(title, body, extra) { return h('section', { class: 'stack', style: 'padding-top:0;gap:12px' }, h('h2', { text: title }), extra, body); }
  function tableWrap(t) { return h('div', { class: 'tablewrap' }, t); }

  function gameRow(g, T, opt) {
    opt = opt || {};
    var done = g.status === 'final';
    var aw = done && g.away_score > g.home_score, hw = done && g.home_score > g.away_score;
    function side(abbr, score, won) {
      var named = T[abbr] ? clubLink(T, abbr, true) : h('span', { class: 'club', text: abbr });
      return h('div', null, named, h('span', { class: 'sc ' + (done ? (won ? 'won' : 'lost') : 'lost'), text: done ? String(score) : '–' }));
    }
    var when = [h('span', { text: opt.noDate ? '' : fmtDate(g.date) }), g.kickoff ? h('span', { text: g.kickoff, title: 'league time' }) : null];
    var tags = [];
    if (g.kind === 'tour') tags.push(h('span', { class: 'tag', text: 'Tour' }));
    if (g.status !== 'final') tags.push(h('span', { class: 'tag', text: 'Scheduled' }));
    var tail = [];
    if (g.venue) tail.push(h('div', { class: 'muted', style: 'font-size:14px', text: g.venue }));
    if (g.away_seed || g.home_seed) tail.push(h('div', { class: 'muted', style: 'font-size:14px', text: '#' + g.away_seed + ' at #' + g.home_seed }));
    if (g.note && g.kind !== 'regular' && g.kind !== 'playoff') tail.push(h('div', { class: 'muted', style: 'font-size:13px', text: g.note }));
    return h('div', { class: 'game' }, h('div', { class: 't' }, when, tags), h('div', { class: 'teams' }, side(g.away, g.away_score, aw), side(g.home, g.home_score, hw), tail));
  }
  function groupByDate(games) {
    var m = {}, order = [];
    games.forEach(function (g) { var k = g.date || ''; if (!m[k]) { m[k] = []; order.push(k); } m[k].push(g); });
    return order.map(function (k) { return [k, m[k]]; });
  }
  function gameList(games, T, withDay) {
    var box = h('div', { class: 'tablewrap' });
    if (!games.length) { box.appendChild(empty('No games to show.')); return box; }
    groupByDate(games).forEach(function (g) {
      if (withDay !== false) box.appendChild(h('div', { class: 'day', text: fmtDate(g[0], true) }));
      g[1].forEach(function (x) { box.appendChild(gameRow(x, T, { noDate: withDay !== false })); });
    });
    return box;
  }

  // ---------------------------------------------------------------- pages
  function pageHome() {
    return files('season.json', 'notices.json', 'teams.json', 'schedule.json', 'playoffs.json').then(function (a) {
      var s = a[0], n = a[1], T = teamIndex(a[2]), sch = a[3], po = a[4];
      var pctDone = s.events_total ? Math.round(100 * s.events_done / s.events_total) : 0;
      var out = [];
      out.push(h('div', { class: 'stack', style: 'gap:8px;padding-top:0' },
        h('div', { class: 'eyebrow', text: 'SEASON ' + s.season + ' · LEAGUE YEAR ' + s.year }),
        h('h1', { text: s.status_text }),
        h('div', { class: 'progress', role: 'progressbar', 'aria-valuemin': '0', 'aria-valuemax': String(s.events_total), 'aria-valuenow': String(s.events_done), 'aria-label': 'League year progress' }, h('i', { style: 'width:' + pctDone + '%' })),
        h('div', { class: 'sub', text: s.events_done + ' of ' + s.events_total + ' calendar events done (' + pctDone + '%).' })));
      if (s.champion && T[s.champion]) out.push(h('div', { class: 'champ' }, badge(T[s.champion], 'lg'), h('div', null, h('div', { class: 'eyebrow', text: 'DIAMOND CORONATION CHAMPION · ' + s.year }), h('div', { class: 'big', text: T[s.champion].full_name }))));
      out.push(h('div', { class: 'kpis' },
        kpi(s.phase, 'phase'),
        kpi(s.regular_weeks.total ? s.regular_weeks.played + ' / ' + s.regular_weeks.total : '–', 'regular-season weeks played'),
        kpi(s.simulation_date ? fmtDate(s.simulation_date) : '–', 'simulation date'),
        kpi(s.last_event ? s.last_event.label : 'none yet', 'last event'),
        kpi(s.next_event ? s.next_event.label : 'none', s.next_event ? 'next · ' + fmtDate(s.next_event.date) : 'next event')));
      var played = sch.games.filter(function (g) { return g.status === 'final'; });
      var upcoming = sch.games.filter(function (g) { return g.status !== 'final'; });
      if (played.length) {
        var lastDate = played.reduce(function (m, g) { return g.date > m ? g.date : m; }, '');
        var last = played.filter(function (g) { return g.date >= shiftDate(lastDate, -3); });
        out.push(section('Latest results', gameList(last, T)));
      }
      if (upcoming.length) {
        var first = upcoming.reduce(function (m, g) { return g.date < m ? g.date : m; }, upcoming[0].date);
        var next = upcoming.filter(function (g) { return g.date <= shiftDate(first, 3); });
        out.push(section('Coming up', gameList(next, T)));
      } else if (!sch.games.length) {
        out.push(section('Schedule', empty(s.schedule_published ? 'No games yet.' : 'The schedule is published when training camp opens.')));
      }
      if (po.status !== 'not started') out.push(h('p', null, h('a', { href: '#/playoffs', text: 'The playoff bracket is under way →' })));
      out.push(section('About this preview', h('div', { class: 'stack', style: 'gap:8px;padding-top:0' }, n.notices.map(note))));
      return out;
    });
  }
  function kpi(v, l) { return h('div', { class: 'card kpi' }, h('b', { text: v }), h('span', { text: l })); }
  function shiftDate(iso, days) { var d = parseDate(iso); if (!d) return iso; d.setDate(d.getDate() + days); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }

  function pageSchedule(arg) {
    return files('schedule.json', 'teams.json', 'season.json').then(function (a) {
      var sch = a[0], T = teamIndex(a[1]), s = a[2];
      if (!sch.games.length) return [h('h1', { text: 'Schedule' }), empty(s.schedule_published ? 'No games yet.' : 'The schedule is published when training camp opens.')];
      var weeks = [], seen = {};
      sch.games.forEach(function (g) { if (g.kind === 'regular' && !seen[g.week]) { seen[g.week] = 1; weeks.push(g.week); } });
      weeks.sort(function (x, y) { return x - y; });
      var current = weeks.filter(function (w) { return sch.games.some(function (g) { return g.week === w && g.status !== 'final'; }); })[0] || weeks[weeks.length - 1];
      var sel = arg === 'tour' ? 'tour' : (arg && !isNaN(+arg) ? +arg : current);
      var chips = h('div', { class: 'chips weeks' });
      weeks.forEach(function (w) { chips.appendChild(h('a', { class: 'chip', href: '#/schedule/' + w, 'aria-current': sel === w ? 'true' : null, text: String(w) })); });
      chips.appendChild(h('a', { class: 'chip', href: '#/schedule/tour', 'aria-current': sel === 'tour' ? 'true' : null, text: 'Ambassador Tour' }));
      var games = sel === 'tour' ? sch.games.filter(function (g) { return g.kind === 'tour'; }) : sch.games.filter(function (g) { return g.kind === 'regular' && g.week === sel; });
      return [h('h1', { text: 'Schedule' }), h('div', { class: 'sub', text: 'League year ' + sch.year + ' · times are league time.' }), chips,
        h('h2', { text: sel === 'tour' ? 'Ambassador Tour' : 'Week ' + sel }), gameList(games, T)];
    });
  }

  function standingsTable(clubs, T, ambassador) {
    var thead = h('tr', null, h('th', { class: 'l', text: 'Club' }), ['W-L', 'Pct', 'PF', 'PA', 'Diff'].map(function (x) { return h('th', { class: 'num', text: x }); }),
      ambassador ? null : [h('th', { class: 'num hide-sm', text: 'Div' }), h('th', { class: 'num hide-sm', text: 'Conf' })]);
    var tb = h('tbody');
    clubs.forEach(function (c) {
      tb.appendChild(h('tr', { class: c.exiled ? 'exile' : null },
        h('td', { class: 'l' }, clubLink(T, c.abbr, true), c.exiled ? h('span', { class: 'pill', text: 'exile' }) : null),
        h('td', { class: 'num', text: rec(c) }), h('td', { class: 'num', text: pct(c) }), h('td', { class: 'num', text: String(c.pf) }), h('td', { class: 'num', text: String(c.pa) }),
        h('td', { class: 'num', text: (c.pd > 0 ? '+' : '') + c.pd }),
        ambassador ? null : [h('td', { class: 'num hide-sm', text: c.div }), h('td', { class: 'num hide-sm', text: c.conf })]));
    });
    return tableWrap(h('table', null, h('thead', null, thead), tb));
  }
  function pageStandings() {
    return files('standings.json', 'teams.json', 'season.json').then(function (a) {
      var st = a[0], T = teamIndex(a[1]), out = [h('h1', { text: 'Standings' }), h('div', { class: 'sub', text: 'League year ' + st.year + (st.through_week ? ' · through Week ' + st.through_week : ' · no games played yet') })];
      if (!st.divisions.length) { out.push(empty('Standings appear once the season has started.')); return out; }
      var confs = {};
      st.divisions.forEach(function (d) { (confs[d.conference] = confs[d.conference] || []).push(d); });
      var grid = h('div', { class: 'confs' });
      Object.keys(confs).sort().forEach(function (conf) {
        var col = h('div', { class: 'stack', style: 'padding-top:0' }, h('h2', { text: conf + ' Conference' }));
        confs[conf].forEach(function (d) { col.appendChild(h('div', { class: 'stack', style: 'padding-top:0;gap:6px' }, h('h3', { text: d.division }), standingsTable(d.clubs, T, false))); });
        var amb = st.ambassadors.filter(function (x) { return x.conference === conf; })[0];
        if (amb) col.appendChild(h('div', { class: 'stack', style: 'padding-top:0;gap:6px' }, h('h3', { text: 'Ambassadors · Tour' }), standingsTable(amb.clubs, T, true)));
        grid.appendChild(col);
      });
      out.push(grid);
      if (st.designation) {
        var d = st.designation, parts = Object.keys(d.conferences).sort().map(function (k) { var c = d.conferences[k]; return k + ' ' + c.w + '-' + c.l + (c.t ? '-' + c.t : '') + ' (' + (c.pd > 0 ? '+' : '') + c.pd + ')'; });
        out.push(h('p', { class: 'notice', text: 'Week 4 comparison of the conferences: ' + parts.join(' · ') + '. The ' + d.home_conference + ' Conference finalist is the nominal home club of the Diamond Coronation (decided by ' + d.decided_by.replace(/_/g, ' ') + ').' }));
      }
      out.push(h('p', { class: 'notice', text: 'Tiebreaks: wins, win %, division wins, division win %, conference wins, conference win %, point differential, strength of schedule, strength of victory, seeded draw. See Rules.' }));
      return out;
    });
  }

  function pagePlayoffs() {
    return files('playoffs.json', 'teams.json').then(function (a) {
      var p = a[0], T = teamIndex(a[1]), out = [h('h1', { text: 'Playoffs' }), h('div', { class: 'sub', text: 'League year ' + p.year })];
      if (p.status === 'not started') { out.push(empty('The playoff field is set when the regular season ends.')); }
      Object.keys(p.seeds).sort().forEach(function (conf) {
        out.push(section(conf + ' Conference seeds', h('div', { class: 'chips' }, p.seeds[conf].map(function (s) {
          return h('span', { class: 'chip' }, '#' + s.playoff_seed + ' ', clubLink(T, s.abbr, false)); }))));
      });
      p.rounds.forEach(function (r) {
        var br = h('div', { class: 'bracket' });
        Object.keys(r.conferences).sort().forEach(function (conf) {
          var col = h('div', null, h('h3', { text: conf }));
          r.conferences[conf].forEach(function (g) { col.appendChild(h('div', { class: 'slot' }, gameRow(g, T))); });
          br.appendChild(col);
        });
        out.push(section(r.label, br));
      });
      if (p.coronation) {
        var g = p.coronation;
        var box = h('div', { class: 'stack', style: 'padding-top:0;gap:8px' }, h('div', { class: 'slot' }, gameRow(g, T)));
        if (p.coronation_home_conference) box.appendChild(h('p', { class: 'sub', text: 'Nominal home: the ' + p.coronation_home_conference + ' Conference finalist. Always played at ' + g.venue + ', on neutral terms.' }));
        out.push(section('Diamond Coronation', box));
      }
      if (p.champion && T[p.champion]) out.push(h('div', { class: 'champ' }, badge(T[p.champion], 'lg'), h('div', null, h('div', { class: 'eyebrow', text: 'CHAMPION' }), h('div', { class: 'big', text: T[p.champion].full_name }))));
      if (p.bowl) {
        var b = p.bowl, box2 = h('div', { class: 'stack', style: 'padding-top:0;gap:10px' }, h('div', { class: 'slot' }, gameRow(b.game, T)));
        if (b.winner) box2.appendChild(h('p', { class: 'sub', text: 'Winner: ' + b.winner + '.' }));
        Object.keys(b.rosters).sort().forEach(function (conf) {
          var d = h('details', { class: 'card' }, h('summary', { text: conf + ' All-Stars · ' + b.rosters[conf].length + ' players · coached by ' + (T[b.coach_clubs[conf]] ? T[b.coach_clubs[conf]].full_name : b.coach_clubs[conf]) }));
          var tb = h('tbody');
          b.rosters[conf].forEach(function (pl) { tb.appendChild(h('tr', null, h('td', { class: 'l', text: pl.name }), h('td', { class: 'l', text: pl.pos }), h('td', { class: 'l' }, pl.club && T[pl.club] ? clubLink(T, pl.club, false) : ''))); });
          d.appendChild(tableWrap(h('table', null, h('thead', null, h('tr', null, h('th', { class: 'l', text: 'Player' }), h('th', { class: 'l', text: 'Pos' }), h('th', { class: 'l', text: 'Club' }))), tb)));
          box2.appendChild(d);
        });
        out.push(section('Ambassador Bowl', box2));
      }
      return out;
    });
  }

  function pageTeams() {
    return files('teams.json', 'standings.json').then(function (a) {
      var tm = a[0], st = a[1], R = {};
      st.divisions.forEach(function (d) { d.clubs.forEach(function (c) { R[c.abbr] = c; }); });
      st.ambassadors.forEach(function (d) { d.clubs.forEach(function (c) { R['T:' + c.abbr] = c; }); });
      var by = {};
      tm.teams.forEach(function (t) { var k = t.conference + '|' + t.division; (by[k] = by[k] || []).push(t); });
      var out = [h('h1', { text: 'Teams' }), h('div', { class: 'sub', text: tm.teams.length + ' clubs · league year ' + tm.year })];
      Object.keys(by).sort().forEach(function (k) {
        var parts = k.split('|');
        out.push(section(parts[0] + ' · ' + parts[1], h('div', { class: 'grid tight' }, by[k].map(function (t) {
          var r = R[t.abbr] || R['T:' + t.abbr];
          return h('a', { class: 'card', href: '#/team/' + encodeURIComponent(t.abbr), style: 'display:flex;gap:12px;align-items:center' }, badge(t, 'lg'),
            h('div', null, h('b', { text: t.full_name }), h('div', { class: 'sub', text: (t.status === 'ambassador' ? 'Ambassador' : 'Active') + (r ? ' · ' + rec(r) : '') })));
        }))));
      });
      return out;
    });
  }

  function pageTeam(abbr) {
    return files('teams.json', 'schedule.json', 'standings.json').then(function (a) {
      var tm = a[0], sch = a[1], st = a[2], T = teamIndex(tm), t = T[abbr];
      if (!t) return [h('h1', { text: 'Team not found' }), h('a', { href: '#/teams', text: '← All teams' })];
      return file('rosters/' + abbr + '.json').then(function (ro) {
        var line = null;
        st.divisions.concat(st.ambassadors).forEach(function (d) { d.clubs.forEach(function (c) { if (c.abbr === abbr) line = c; }); });
        var out = [h('div', { class: 'crumbs' }, h('a', { href: '#/teams', text: 'Teams' }), ' / ' + t.full_name)];
        out.push(h('div', { class: 'teamhead' }, badge(t, 'lg'), h('div', null, h('h1', { text: t.full_name }),
          h('div', { class: 'sub', text: t.conference + ' · ' + t.division + ' · ' + (t.status === 'ambassador' ? 'Ambassador this season' : 'Active club') })),
          h('div', { class: 'stripe3', 'aria-hidden': 'true' }, t.colors.map(function (c) { return h('i', { style: 'background:' + safeHex(c.hex) }); }))));
        var kv = h('dl', { class: 'kv' });
        function row(k, v) { kv.appendChild(h('dt', { text: k })); kv.appendChild(h('dd', null, v)); }
        row('Home ground', t.venue + ', ' + t.location);
        row('Record', line ? rec(line) + ' (' + pct(line) + ') · PF ' + line.pf + ' · PA ' + line.pa : 'no games yet');
        row('Colours', t.colors.map(function (c) { return h('span', { style: 'margin-right:12px;white-space:nowrap' }, h('span', { class: 'sw', style: 'background:' + safeHex(c.hex) }), c.name); }));
        row('Salary cap', ro.cap ? fmtMoney(ro.cap.used) + ' of ' + fmtMoney(ro.cap.limit) + ' used' : '–');
        out.push(h('div', { class: 'card' }, kv));
        if (ro.leaders.length) out.push(section('Leadership', h('div', { class: 'card' }, h('div', { class: 'people' }, ro.leaders.map(function (l) { return h('div', null, h('b', { text: l.name }), h('small', { text: roleName(l.role) + ' · age ' + l.age })); })))));
        var mine = sch.games.filter(function (g) { return g.away === abbr || g.home === abbr; });
        if (mine.length) out.push(section('Schedule and results', gameList(mine, T)));
        ['roster', 'practice squad', 'injured reserve'].forEach(function (slot) {
          var ps = ro.players.filter(function (p) { return p.slot === slot; });
          if (!ps.length) return;
          var tb = h('tbody');
          ps.forEach(function (p) {
            tb.appendChild(h('tr', null, h('td', { class: 'num', text: p.number === null ? '' : String(p.number) }), h('td', { class: 'l', text: p.name }), h('td', { class: 'l', text: p.pos }),
              h('td', { class: 'num', text: String(p.age) }), h('td', { class: 'num hide-sm', text: String(p.seasons) }), h('td', { class: 'num hide-sm', text: p.height_cm + ' cm / ' + p.weight_kg + ' kg' }),
              h('td', { class: 'num', text: fmtMoney(p.contract.cap_hit) }), h('td', { class: 'num', text: String(p.contract.years_left) }),
              h('td', { class: 'l', text: p.injury.out ? 'Out ' + p.injury.weeks_out + ' wk' : '' })));
          });
          out.push(section(cap1(slot) + ' (' + ps.length + ')', tableWrap(h('table', null,
            h('thead', null, h('tr', null, h('th', { class: 'num', text: '#' }), h('th', { class: 'l', text: 'Player' }), h('th', { class: 'l', text: 'Pos' }), h('th', { class: 'num', text: 'Age' }),
              h('th', { class: 'num hide-sm', text: 'Seasons' }), h('th', { class: 'num hide-sm', text: 'Size' }), h('th', { class: 'num', text: 'Cap hit' }), h('th', { class: 'num', text: 'Yrs left' }), h('th', { class: 'l', text: 'Injury' }))), tb))));
        });
        out.push(h('p', { class: 'notice', text: 'Rosters show public facts only. Ratings, statistics and deliberations are not published.' }));
        return out;
      });
    });
  }
  function roleName(r) { return ({ ceo: 'CEO', gm: 'General manager', hc: 'Head coach', oc: 'Offensive coordinator', dc: 'Defensive coordinator', stc: 'Special-teams coordinator' })[r] || r; }
  function cap1(s) { return s.charAt(0).toUpperCase() + s.slice(1); }

  function pageCalendar() {
    return files('calendar.json', 'season.json').then(function (a) {
      var cal = a[0], s = a[1], nextIdx = s.next_event ? s.next_event.index : -1;
      var tb = h('tbody');
      cal.events.forEach(function (e) {
        tb.appendChild(h('tr', { class: e.index === nextIdx ? 'next' : null },
          h('td', { class: 'l', text: fmtDate(e.date) }), h('td', { class: 'l', text: e.label }), h('td', { class: 'l hide-sm', text: e.phase }),
          h('td', { class: 'l' }, h('span', { class: 'pill ' + (e.index === nextIdx ? 'live' : ''), text: e.status === 'done' ? 'done' : e.index === nextIdx ? 'next' : 'upcoming' }))));
      });
      return [h('h1', { text: 'Calendar' }), h('div', { class: 'sub', text: 'League year ' + cal.year + ' · ' + s.events_done + ' of ' + s.events_total + ' events done.' }),
        tableWrap(h('table', null, h('thead', null, h('tr', null, h('th', { class: 'l', text: 'Date' }), h('th', { class: 'l', text: 'Event' }), h('th', { class: 'l hide-sm', text: 'Phase' }), h('th', { class: 'l', text: 'Status' }))), tb))];
    });
  }

  function pageHistory() {
    return files('history.json', 'teams.json').then(function (a) {
      var hist = a[0], T = teamIndex(a[1]), out = [h('h1', { text: 'History' }), h('div', { class: 'sub', text: 'Seasons played in this development league.' })];
      if (!hist.seasons.length) { out.push(empty('No season has finished yet.')); return out; }
      hist.seasons.slice().reverse().forEach(function (s, idx) {
        var d = h('details', { class: 'card', open: idx === 0 },  h('summary', null, h('b', { text: 'Season ' + s.season + ' · ' + s.year + ' — ' }), (T[s.champion] ? T[s.champion].full_name : s.champion) + ' champions'));
        var kv = h('dl', { class: 'kv' });
        function row(k, v) { kv.appendChild(h('dt', { text: k })); kv.appendChild(h('dd', null, v)); }
        row('Diamond Coronation', (T[s.champion] ? T[s.champion].full_name : s.champion) + ' over ' + (T[s.runner_up] ? T[s.runner_up].full_name : s.runner_up) + (s.score ? ', ' + s.score + (s.score_source === 'archive' ? ' (recovered from the game archive)' : '') : ', score not recorded') + ' · ' + s.venue);
        if (s.bowl_score) row('Ambassador Bowl', s.bowl_score + (s.bowl_winner ? ' · ' + s.bowl_winner + ' won' : ''));
        row('Exiled', s.exiled.map(function (x) { return T[x] ? T[x].full_name : x; }).join(', '));
        row('Division winners', s.division_winners.map(function (w) { return w.division + ': ' + (T[w.abbr] ? T[w.abbr].city : w.abbr); }).join(' · '));
        Object.keys(s.seeds).sort().forEach(function (c) { row(c + ' seeds', s.seeds[c].map(function (x, i) { return (i + 1) + ' ' + (T[x] ? T[x].city : x); }).join(' · ')); });
        d.appendChild(kv);
        var games = s.playoff_games;
        if (games.length) d.appendChild(h('div', { style: 'margin-top:12px' }, gameList(games.map(function (g) { return Object.assign({}, g, { date: g.date }); }), T)));
        out.push(d);
      });
      return out;
    });
  }

  function pageRules() {
    return file('rules.json').then(function (r) {
      var out = [h('h1', { text: 'Rules' }), h('p', { class: 'notice', text: 'A plain-language summary of the approved rules, checked against design document edition ' + r.spec_edition + ' (hash ' + r.spec_hash.slice(0, 12) + '…, ' + r.updated + '). The design document is the authority.' })];
      r.sections.forEach(function (s) { out.push(h('section', { class: 'rules' }, h('h2', { text: s.title }), s.paragraphs.map(function (p) { return h('p', { text: p }); }))); });
      return out;
    });
  }

  // The official map is an ordinary file the league owner supplies: site/assets/map/map.json names it ({"file": "dfl-map.png", "alt": "...", "caption": "..."}).
  // The shipped map.json says {"file": null}: the page then says no map has been added; nothing is drawn in its place.
  function pageMap() {
    var box = h('div', { class: 'mapslot' }, h('p', { class: 'muted', text: 'Loading the map…' }));
    fetch('assets/map/map.json', { cache: 'no-store' }).then(function (r) {
      if (!r.ok) throw new Error('none');
      return r.json();
    }).then(function (m) {
      if (!m || typeof m.file !== 'string' || !/^[A-Za-z0-9._-]+$/.test(m.file) || m.file === 'map.json') throw new Error('none');
      var src = 'assets/map/' + m.file;
      clear(box);
      add(box, h('a', { href: src, target: '_blank', rel: 'noopener', title: 'Open the map at full size' },
        h('img', { class: 'dflmap', src: src, alt: typeof m.alt === 'string' && m.alt ? m.alt : 'The official Diamond Football League map' })));
      if (typeof m.caption === 'string' && m.caption) add(box, h('p', { class: 'muted', text: m.caption }));
    }).catch(function () {
      clear(box);
      add(box, h('p', { text: 'The official DFL map has not been added to this site yet.' }));
    });
    return [h('h1', { text: 'Map' }), box];
  }

  // ---------------------------------------------------------------- shell
  var ROUTES = { '': pageHome, schedule: pageSchedule, standings: pageStandings, playoffs: pagePlayoffs, teams: pageTeams, team: pageTeam, calendar: pageCalendar, history: pageHistory, rules: pageRules, map: pageMap };
  function parseRoute() {
    var p = (location.hash || '#/').replace(/^#\/?/, '').split('/');
    return { name: p[0] || '', arg: p[1] ? decodeURIComponent(p[1]) : '' };
  }
  function showErr(app, e) {
    clear(app); app.appendChild(h('div', { class: 'err', role: 'alert', text: 'This page could not be shown: ' + e.message }));
  }
  function render(again) {
    var app = document.getElementById('app'), r = parseRoute();
    S.route = r;
    var page = ROUTES[r.name] || pageHome;
    Array.prototype.forEach.call(document.querySelectorAll('#tabs a'), function (a) {
      var on = a.getAttribute('data-route') === (r.name === 'team' ? 'teams' : r.name);
      if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    });
    var token = (render.token = (render.token || 0) + 1);
    return Promise.resolve(page(r.arg)).then(function (content) {
      if (token !== render.token) return;
      clear(app); add(app, content);
    }).catch(function (e) {
      if (token !== render.token) return;
      if (e.gone && !again) {
        return recover(e.gone).then(function () { return render(true); }, function (e2) { showErr(app, e2); });
      }
      showErr(app, e);
    });
  }
  function chrome() {
    var m = S.manifest, st = document.getElementById('status'), ft = document.getElementById('foot');
    document.getElementById('devtext').textContent = m.league.label.replace(/^DEVELOPMENT \/ TEST LEAGUE\.\s*/, '');
    clear(st);
    st.appendChild(h('span', null, h('b', { text: m.league.name })));
    if (m.year) st.appendChild(h('span', { text: 'Season ' + m.season + ' · ' + m.year }));
    if (m.simulation_date) st.appendChild(h('span', { class: 'simdate', title: 'The date inside the simulation: the day of its last completed event' }, 'Simulation date ', h('b', { text: fmtDate(m.simulation_date, true) })));
    st.appendChild(h('span', { class: 'updated' + (S.fresh > Date.now() - 20000 ? ' fresh' : ''), title: 'Published ' + m.generated_at + ' (this is when the data last changed; a quiet league publishes nothing new)' },
      'Last published update ', h('b', { text: fmtStamp(m.generated_at) }), ' (' + ago(m.generated_at) + ')'));
    if (S.recovered && S.recovered > Date.now() - 30000) st.appendChild(h('span', { class: 'updated fresh', text: 'Switched to the newest release' }));
    clear(ft);
    ft.appendChild(h('p', { class: 'muted', style: 'font-size:14px;max-width:none' },
      'Release ' + m.release + ' · feed format ' + m.format_version + '.' + m.format_minor + ' · league ' + m.league.id.slice(0, 8) + ' · content ' + m.content_hash.slice(0, 10) + '. ',
      'This page only reads published data; it cannot change the league. It checks for a newer release every minute.'));
  }
  function check() {
    return loadPointer().then(function (p) {
      if (S.ptr && p.release === S.ptr.release) { chrome(); return; }
      var first = !S.ptr;
      return loadRelease(p).then(function () { if (!first) S.fresh = Date.now(); chrome(); return render(); });
    }).catch(function (e) {
      if (!S.manifest) { var app = document.getElementById('app'); clear(app); app.appendChild(h('div', { class: 'err', role: 'alert', text: 'The league feed could not be read: ' + e.message })); }
      else { var st = document.getElementById('status'); st.appendChild(h('span', { class: 'updated', text: 'Could not check for updates' })); }
    });
  }
  window.addEventListener('hashchange', function () { if (S.manifest) { render(); window.scrollTo(0, 0); } });
  check();
  if (!window.DFL_EMBED) setInterval(check, REFRESH_MS);
  window.DFL_VIEWER = { render: render, state: S };
})();
