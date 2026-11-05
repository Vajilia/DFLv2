/* Read-only scheduled broadcasts. No simulation calls and no access to full live replays.
   Public protocol: docs/BROADCAST_FRONTEND_CONTRACT.md. */
(function () {
  'use strict';
  function node(tag, text, cls) {
    var e = document.createElement(tag);
    if (text != null) e.textContent = text;
    if (cls) e.className = cls;
    return e;
  }
  function slug(gid) {
    if (!/^[A-Za-z0-9][A-Za-z0-9_:/-]{0,160}$/.test(gid || '')) throw new Error('Invalid broadcast game ID');
    return gid.replace(/[/:]/g, '-');
  }
  function duration(s) {
    s = Math.max(0, Math.ceil(s));
    return Math.floor(s / 3600) + ':' + ('0' + Math.floor(s / 60) % 60).slice(-2) + ':' + ('0' + s % 60).slice(-2);
  }
  function clockLimit(c) {
    return Math.min(c.lease_until, c.ready_until == null ? Infinity : c.anchor_wall + c.ready_until - c.anchor_pos);
  }
  function clockPosition(c, now) {
    return c.anchor_pos + (c.state === 'running' ? Math.max(0, Math.min(now, clockLimit(c)) - c.anchor_wall) : 0);
  }
  function create(opts) {
    var base = opts.base.replace(/\/?$/, '/'), root = node('section', null, 'broadcast');
    var title = node('h1', 'Watch now'), health = node('p', 'Connecting to the broadcast…', 'notice');
    health.setAttribute('role', 'status');
    var nowList = node('div', null, 'bc-games'), upcoming = node('div', null, 'bc-games'), doneList = node('div', null, 'bc-games');
    var stage = node('div', null, 'bc-stage'), message = node('p', 'Choose a game to watch.', 'notice');
    var jump = node('button', 'Jump to live', 'pb-btn'), rewind = node('input');
    rewind.type = 'range'; rewind.min = '0'; rewind.max = '0'; rewind.step = '0.1'; rewind.value = '0';
    rewind.setAttribute('aria-label', 'Broadcast position within this game');
    var controls = node('div', null, 'bc-controls'); controls.append(rewind, jump);
    var behind = node('span', '', 'muted'); controls.appendChild(behind);
    root.append(title, health, node('h2', 'On now'), nowList, message, stage, controls, node('h2', 'Coming up'), upcoming, node('h2', 'Completed broadcasts'), doneList);
    var clock = null, sampledServer = 0, sampledMono = 0, schedule = null, library = [], selected = opts.gid || null;
    var dead = false, busy = false, games = {}, viewer = null, viewerGid = null, viewerMode = null;
    var replayTime = null, lastLists = '', error = '', ticks = 0, ac = new AbortController();
    function request(path) {
      return fetch(base + path, {cache: 'no-store', signal: ac.signal}).then(function (r) {
        if (!r.ok) { var e = new Error('Broadcast data unavailable (HTTP ' + r.status + ')'); e.status = r.status; throw e; }
        return r.json().then(function (data) { return {data: data, date: r.headers.get('Date')}; });
      });
    }
    function serverNow() { return sampledServer + (performance.now() - sampledMono) / 1000; }
    function position() { return clock ? clockPosition(clock, serverNow()) : null; }
    function windowFor(gid) {
      var found = null;
      (schedule ? schedule.windows : []).forEach(function (w) { w.games.forEach(function (g) { if (g.gid === gid) found = w; }); });
      return found;
    }
    function choose(gid) { selected = gid; replayTime = null; lastLists = ''; render(); pollGame(); }
    function button(g, completed) {
      var text = g.away + ' at ' + g.home;
      if (completed) text += ' · ' + completed.final.away + '–' + completed.final.home + ' · Replay';
      var b = node('button', text, 'bc-game'); b.type = 'button'; b.dataset.gid = g.gid;
      b.setAttribute('aria-pressed', String(selected === g.gid)); b.onclick = function () { choose(g.gid); };
      return b;
    }
    function lists(pos) {
      var key = selected + '|' + library.map(function (g) { return g.gid; }).join(',') + '|' + schedule.windows.map(function (w) {
        return w.start_s + ':' + w.slot_s + ':' + (pos >= w.start_s ? 1 : 0) + ':' + w.games.map(function (g) { return g.gid; }).join(',');
      }).join('|');
      if (key === lastLists) return;
      lastLists = key; nowList.textContent = ''; upcoming.textContent = ''; doneList.textContent = '';
      var done = {}; library.forEach(function (g) { done[g.gid] = g; });
      schedule.windows.forEach(function (w) {
        var list = pos < w.start_s ? upcoming : nowList;
        var available = w.games.filter(function (g) { return !done[g.gid]; });
        if (!available.length && w.games.length) return;
        var group = node('div', null, 'card'), label = node('h3', w.label); group.appendChild(label);
        if (pos < w.start_s) { var count = node('p', '', 'muted'); count.dataset.start = w.start_s; group.appendChild(count); }
        if (!w.games.length) group.appendChild(node('p', 'Participants to be announced.', 'muted'));
        available.forEach(function (g) { group.appendChild(button(g)); }); list.appendChild(group);
      });
      library.slice().reverse().forEach(function (e) {
        var w = windowFor(e.gid), g = w && w.games.find(function (x) { return x.gid === e.gid; });
        if (g) doneList.appendChild(button(g, e));
      });
      if (!nowList.children.length) nowList.appendChild(node('p', 'No games are broadcasting right now.', 'muted'));
      if (!upcoming.children.length) upcoming.appendChild(node('p', 'No further kickoffs announced.', 'muted'));
      if (!doneList.children.length) doneList.appendChild(node('p', 'Completed games will appear here after their broadcast.', 'muted'));
    }
    function destroyViewer() { if (viewer) viewer.destroy(); viewer = null; viewerGid = viewerMode = null; stage.textContent = ''; }
    function loadClock() {
      var start = performance.now();
      return request('clock.json').then(function (r) {
        var c = r.data, date = Date.parse(r.date);
        if (c.format !== 'dfl-broadcast-clock' || !['running', 'paused'].includes(c.state) ||
            !['anchor_pos', 'anchor_wall', 'lease_until', 'epoch'].every(function (k) { return Number.isFinite(c[k]); }) || !Number.isFinite(date) || (c.ready_until != null && !Number.isFinite(c.ready_until))) throw new Error('Broadcast clock is unavailable or incompatible');
        if (clock && c.epoch < clock.epoch) return;
        sampledMono = performance.now(); sampledServer = date / 1000 + .5 + (sampledMono - start) / 2000;
        clock = c;
      });
    }
    function loadSchedule() {
      return request('schedule.json').then(function (r) {
        if (r.data.format !== 'dfl-broadcast-schedule' || r.data.format_version !== 1 || !Array.isArray(r.data.windows)) throw new Error('Unsupported broadcast schedule');
        r.data.windows.forEach(function (w) {
          if (!Number.isFinite(w.start_s) || !Number.isFinite(w.slot_s) || !Array.isArray(w.games)) throw new Error('Invalid broadcast window');
          w.games.forEach(function (g) { slug(g.gid); });
        });
        schedule = r.data;
      });
    }
    function loadLibrary() {
      return request('replays/index.json').then(function (r) {
        if (r.data.format !== 'dfl-broadcast-replays' || r.data.format_version !== 1) throw new Error('Unsupported broadcast replay index');
        library = r.data.games || []; lastLists = '';
      }).catch(function (e) { if (e.status !== 404) throw e; });
    }
    function pollGame() {
      var gid = selected, w = windowFor(gid), pos = position();
      if (dead || !w || pos === null || pos < w.start_s) return Promise.resolve();
      var g = games[gid] || (games[gid] = {plays: [], loaded: 0}), path = 'live/' + slug(gid) + '/';
      if (g.loading) return Promise.resolve();
      g.loading = true;
      return request(path + 'manifest.json').then(function (r) {
        var m = r.data;
        if (m.gid !== gid || !Number.isInteger(m.segments) || m.segments < 0 || m.segments > 20000 || m.speed !== 1 || !Number.isFinite(m.delay_s)) throw new Error('Unsupported live manifest');
        g.man = m;
        return g.head ? null : request(path + 'head.json').then(function (r) {
          if (r.data.gid !== gid || r.data.kind !== 'live-game' || r.data.format_version !== 1) throw new Error('Unsupported live header');
          g.head = r.data;
        });
      }).then(async function () {
        // Bounded parallel reads, contiguous commits. Missing segments never cause a jump ahead.
        while (!dead && g.loaded < g.man.segments) {
          var count = Math.min(8, g.man.segments - g.loaded), jobs = [];
          for (var i = 0; i < count; i++) jobs.push(request(path + 's' + String(g.loaded + i).padStart(4, '0') + '.json'));
          var chunks = await Promise.all(jobs);
          chunks.forEach(function (r) {
            if (r.data.gid !== gid || r.data.n !== g.loaded || !Array.isArray(r.data.plays)) throw new Error('Invalid broadcast segment');
            g.plays = g.plays.concat(r.data.plays); g.loaded++;
          });
        }
        if (g.man.state === 'final' && !g.end) g.end = (await request(path + 'end.json')).data;
        render();
      }).catch(function (e) { if (!dead && e.name !== 'AbortError') { error = e.status === 404 ? 'Waiting for the next released broadcast data.' : e.message; render(); } }).finally(function () { g.loading = false; });
    }
    function render() {
      if (dead) return;
      var pos = position();
      if (pos === null || !schedule) { health.textContent = error || 'Connecting to the broadcast…'; return; }
      var frozen = clock.state === 'paused' || serverNow() >= clockLimit(clock);
      health.textContent = (frozen ? 'Broadcast paused' : 'Broadcast running') + ' · season clock ' + duration(pos) + (error ? ' · ' + error : '');
      lists(pos);
      root.querySelectorAll('[data-start]').forEach(function (e) { e.textContent = 'Kickoff in ' + duration(+e.dataset.start - pos); });
      if (!selected) {
        var active = schedule.windows.find(function (w) { return w.start_s <= pos && w.games.some(function (g) { return !library.some(function (e) { return e.gid === g.gid; }); }); });
        if (active) { selected = active.games[0].gid; lastLists = ''; }
      }
      var w = windowFor(selected), g = games[selected];
      controls.hidden = true;
      if (!w) { message.textContent = 'Choose a game to watch.'; return; }
      if (pos < w.start_s) { if (viewer) destroyViewer(); message.textContent = 'Kickoff in ' + duration(w.start_s - pos); return; }
      if (!g || !g.man || !g.head) { message.textContent = 'Waiting for released broadcast data…'; return; }
      var liveTau = Math.max(0, pos - w.start_s - g.man.delay_s), tau = replayTime === null ? liveTau : Math.min(replayTime, liveTau);
      var idx = -1;
      g.plays.forEach(function (p, i) { if (p.t0 <= tau) idx = i; });
      if (idx < 0) { message.textContent = 'Kickoff coverage is starting…'; return; }
      var play = g.plays[idx], elapsed = tau - play.t0;
      var ended = g.end && idx + 1 === g.end.plays && tau >= play.t0 + play.dur;
      if (ended && replayTime === null) {
        message.textContent = 'Final · broadcast complete. Watch the replay at your own pace.';
        if (viewerGid !== selected || viewerMode !== 'replay') {
          destroyViewer(); viewerGid = selected; viewerMode = 'replay';
          if (!/^[A-Za-z0-9_-]+\.[a-f0-9]{10}\.json$/.test(g.end.replay)) { message.textContent = 'Invalid replay filename'; return; }
          viewer = opts.playback.create({entry: {gid: selected}, load: function () { return request('replays/' + g.end.replay).then(function (r) { return r.data; }); }});
          stage.appendChild(viewer.node);
        }
        return;
      }
      message.textContent = frozen ? 'Broadcast paused · playback will resume automatically.' : replayTime === null ? 'Live · synchronized with the broadcast' : 'Watching earlier coverage';
      controls.hidden = false; rewind.max = liveTau; rewind.value = tau;
      behind.textContent = replayTime === null ? 'At live' : duration(liveTau - tau) + ' behind live';
      if (viewerGid !== selected || viewerMode !== 'live') {
        destroyViewer(); viewerGid = selected; viewerMode = 'live';
        var waiting = {i: -1, kind: 'coverage', text: 'Coverage starting', score: {away: 0, home: 0}, period: 1, clock: 900, trace: null};
        var head = Object.assign({}, g.head, {kind: 'game', plays: [waiting], periods: 4, traced_plays: 0});
        viewer = opts.playback.create({entry: {gid: selected}, live: true, load: function () { return head; }});
        stage.appendChild(viewer.node);
      }
      var shown = play;
      if (play.trace && elapsed < play.trace.t_end) shown = Object.assign({}, play, {text: 'Play in progress.'});
      viewer.present(shown, play.trace ? Math.min(elapsed, play.trace.t_end) : 0, idx > 0 ? g.plays[idx - 1].score : {away: 0, home: 0});
      root.dataset.gid = selected; root.dataset.play = play.i; root.dataset.position = pos.toFixed(2);
    }
    jump.onclick = function () { replayTime = null; render(); };
    rewind.oninput = function () { replayTime = +rewind.value; render(); };
    async function poll() {
      if (dead || busy) return;
      busy = true;
      try {
        if (!clock || ticks % 2 === 0) await loadClock();
        if (!schedule || ticks % 15 === 0) { await loadSchedule(); await loadLibrary(); }
        error = ''; render(); await pollGame();
      } catch (e) { if (!dead && e.name !== 'AbortError') { error = e.message; render(); } }
      finally { ticks++; busy = false; }
    }
    var timer = setInterval(poll, 1000), paint = setInterval(render, 100);
    poll();
    return {node: root, destroy: function () { dead = true; ac.abort(); clearInterval(timer); clearInterval(paint); destroyViewer(); },
      state: function () { return {position: position(), selected: selected, play: root.dataset.play, paused: !!clock && (clock.state === 'paused' || serverNow() >= clockLimit(clock))}; }};
  }
  window.DFLBroadcast = {create: create, clockPosition: clockPosition};
})();
