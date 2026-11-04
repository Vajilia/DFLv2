"use strict";
// A protocol demonstration, not the site: it shows what any viewer can compute from the public files alone.
var offset = 0, clock = null, sched = null, cur = null, games = {}, tick = 0;
function $(i) { return document.getElementById(i); }
function pos() {
  if (!clock) return null;
  if (clock.state !== "running") return clock.anchor_pos;
  var now = Date.now() / 1000 + offset;
  return clock.anchor_pos + Math.max(0, Math.min(now, clock.lease_until) - clock.anchor_wall);
}
function getJSON(url) {
  return fetch(url, {cache: "no-store"}).then(function (r) {
    if (!r.ok) { var e = new Error(url + " " + r.status); e.status = r.status; throw e; }
    return r.json().then(function (j) { return {j: j, date: r.headers.get("Date")}; });
  });
}
function syncClock() {
  var t0 = Date.now();
  return getJSON("/broadcast/clock.json").then(function (r) {
    clock = r.j;
    if (r.date) { var rtt = (Date.now() - t0) / 2000; offset = new Date(r.date).getTime() / 1000 + 0.5 + rtt - Date.now() / 1000; }
  }).catch(function () {});
}
function G(gid) { return games[gid] || (games[gid] = {gid: gid, head: null, segs: {}, man: null, end: null, plays: []}); }
function load(g) {
  var base = "/broadcast/live/" + g.gid.replace(/[\/:]/g, "-") + "/";
  return getJSON(base + "manifest.json").then(function (m) {
    g.man = m.j;
    var jobs = [];
    if (!g.head) jobs.push(getJSON(base + "head.json").then(function (r) { g.head = r.j; }));
    for (var i = 0; i < g.man.segments; i++) (function (i) {
      if (!g.segs[i]) { g.segs[i] = "wait"; jobs.push(getJSON(base + "s" + ("000" + i).slice(-4) + ".json").then(function (r) { g.segs[i] = r.j; rebuild(g); }).catch(function () { delete g.segs[i]; })); }
    })(i);
    if (g.man.state === "final" && !g.end) jobs.push(getJSON(base + "end.json").then(function (r) { g.end = r.j; }));
    return Promise.all(jobs);
  }).catch(function (e) { if (e.status !== 404) throw e; g.man = null; });
}
function rebuild(g) {
  var p = []; Object.keys(g.segs).sort().forEach(function (k) { if (g.segs[k] && g.segs[k] !== "wait") p = p.concat(g.segs[k].plays); });
  g.plays = p;
}
function render() {
  var p = pos(); if (p === null) return;
  $("clock").textContent = p.toFixed(1) + " s" + (clock.state === "running" ? "" : " (paused)");
  document.body.setAttribute("data-pos", p.toFixed(2));
  if (!cur || !sched) return;
  var g = G(cur), w = null;
  sched.windows.forEach(function (x) { x.games.forEach(function (q) { if (q.gid === cur) w = x; }); });
  var rel = p - w.start_s;
  if (rel < 0) { $("status").textContent = "kickoff in " + Math.ceil(-rel) + " s"; $("board").textContent = "-"; $("play").textContent = "upcoming"; document.body.setAttribute("data-play", "-1"); return; }
  if (!g.man || !g.head) { $("status").textContent = "loading"; return; }
  var tau = rel - g.man.delay_s, idx = -1, shown = null;
  for (var i = 0; i < g.plays.length; i++) if (g.plays[i].t0 <= tau) idx = i;
  var teams = g.head.teams;
  if (idx >= 0) shown = g.plays[idx];
  var ended = shown && g.end && g.end.plays === idx + 1 && tau >= shown.t0 + shown.dur;
  var sc = shown ? shown.score : {away: 0, home: 0};
  $("board").textContent = teams.away.abbr + " " + sc.away + " - " + sc.home + " " + teams.home.abbr;
  $("play").textContent = shown ? shown.text : "kickoff";
  $("status").textContent = ended ? "final" : "live";
  document.body.setAttribute("data-play", String(idx));
  document.body.setAttribute("data-state", ended ? "final" : "live");
  $("meta").textContent = "game time " + Math.max(0, tau).toFixed(1) + " s; segments released " + g.man.segments + "; speed " + g.man.speed;
}
function pick(gid) {
  cur = gid;
  Array.prototype.forEach.call($("games").children, function (b) { b.setAttribute("aria-current", b.getAttribute("data-gid") === gid ? "true" : "false"); });
}
function start() {
  syncClock().then(function () { return getJSON("/broadcast/schedule.json"); }).then(function (r) {
    sched = r.j;
    sched.windows.forEach(function (w) { w.games.forEach(function (g) {
      var b = document.createElement("button"); b.textContent = g.away + " @ " + g.home; b.setAttribute("data-gid", g.gid);
      b.onclick = function () { pick(g.gid); }; $("games").appendChild(b);
    }); });
    if (sched.windows.length) pick(sched.windows[0].games[0].gid);
    setInterval(syncClock, 2000);   // a viewer must re-read the clock well inside the lease (90 s in production: every 15 s is plenty)
    setInterval(function () { if (cur) load(G(cur)); }, 1000);
    setInterval(render, 200);
  });
}
start();
