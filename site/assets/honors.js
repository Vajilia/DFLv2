/* Progressive public Honors, independent of simulation state and the ordinary feed.
   Read only files explicitly listed by the released honors index. Text is never HTML. */
(function () {
  'use strict';
  var FORMAT = 'dfl-broadcast-honors', VERSION = 1;
  var TITLES = {rings: 'Tour Rings', tiara: 'Diamond Tiaras', halls: 'Halls', fan_entrant: 'Fan entrant'};
  function el(tag, text, cls) {
    var n = document.createElement(tag);
    if (text != null) n.textContent = String(text);
    if (cls) n.className = cls;
    return n;
  }
  function person(p) {
    var name = p.name || (p.id != null ? 'Player ' + p.id : 'Name not recorded');
    return [name, p.pos || p.role, p.club].filter(Boolean).join(' · ');
  }
  function scope(s) {
    return [s.level === 'league' ? 'League hall' : s.level === 'club' ? 'Club hall' : s.level === 'conference' ? 'Conference hall' : 'Division hall', s.conference, s.division, s.club].filter(Boolean).join(' · ');
  }
  function unavailable(items) {
    if (!items || !items.length) return null;
    var box = el('section', null, 'honors-unavailable'); box.appendChild(el('h3', 'Unavailable'));
    var list = el('ul');
    items.forEach(function (u) { list.appendChild(el('li', (u.award || u.field || 'Unavailable') + ': ' + u.reason)); });
    box.appendChild(list); return box;
  }
  function create(opts) {
    var base = opts.base.replace(/\/?$/, '/'), root = el('section', null, 'honors');
    var notice = el('p', 'Loading released honors…', 'notice'), content = el('div', null, 'stack');
    notice.setAttribute('role', 'status');
    root.append(el('h1', 'Honors'), el('p', 'Recorded honors, revealed as the season unfolds.', 'sub'), notice, content);
    var dead = false, busy = false, cache = {}, opened = {}, last = '', ac = new AbortController();
    function get(path) {
      return fetch(base + path, {cache: 'no-store', signal: ac.signal}).then(function (r) {
        if (!r.ok) { var e = new Error('HTTP ' + r.status); e.status = r.status; throw e; }
        return r.json();
      });
    }
    function check(doc, kind) {
      if (!doc || doc.format !== FORMAT || doc.format_version !== VERSION || doc.kind !== kind) throw new Error('Unsupported Honors format; this page reads version 1.');
    }
    function detail(key, summary, body) {
      var d = el('details', null, 'card'); d.open = !!opened[key]; d.dataset.honorsKey = key;
      d.append(el('summary', summary), body);
      d.addEventListener('toggle', function () { if (d.isConnected) opened[key] = d.open; });
      return d;
    }
    function people(rows) {
      var list = el('ul'); rows.forEach(function (p) { list.appendChild(el('li', person(p))); }); return list;
    }
    function part(doc) {
      var section = el('section', null, 'card honors-part'), key = doc.season + '|' + doc.kind;
      section.dataset.part = doc.kind; section.dataset.season = doc.season;
      section.appendChild(el('h2', 'Season ' + doc.season + ' · ' + TITLES[doc.kind]));
      if (doc.note) section.appendChild(el('p', doc.note));
      if (doc.side || doc.club) section.appendChild(el('p', doc.side || doc.club, 'sub'));
      if (doc.rule) section.appendChild(el('p', doc.rule));
      if (doc.counts) section.appendChild(el('p', 'Players: ' + doc.counts.players + ' · Staff: ' + doc.counts.staff, 'muted'));
      if (doc.recipients && doc.recipients.length) section.appendChild(detail(key + '|people', 'Recipients (' + doc.recipients.length + ')', people(doc.recipients)));
      if (doc.eligible_players != null) section.appendChild(el('p', 'Eligible players: ' + doc.eligible_players, 'muted'));
      (doc.halls || []).forEach(function (hall, i) {
        var body = el('div');
        if (hall.ever_members != null) body.appendChild(el('p', 'Historical members: ' + hall.ever_members));
        Object.keys(hall.registers).sort().forEach(function (pos) { body.append(el('h3', pos), people(hall.registers[pos])); });
        section.appendChild(detail(key + '|hall:' + i, scope(hall.scope), body));
      });
      if (doc.entered_this_year && doc.entered_this_year.length) {
        var entries = el('ul');
        doc.entered_this_year.forEach(function (p) { entries.appendChild(el('li', [person(p), scope(p.scope), p.kind].filter(Boolean).join(' · '))); });
        section.append(el('h3', 'Entries this year'), entries);
      }
      if (doc.entrant) section.appendChild(el('p', person(doc.entrant)));
      var missing = unavailable(doc.unavailable); if (missing) section.appendChild(missing);
      return section;
    }
    async function refresh() {
      if (dead || busy) return;
      busy = true;
      try {
        var index = await get('index.json'); check(index, 'index');
        if (!Array.isArray(index.parts) || !Array.isArray(index.unavailable)) throw new Error('Invalid Honors index.');
        // Validate every descriptor before any part fetch, including relative-path boundaries.
        index.parts.forEach(function (p) {
          if (!Object.prototype.hasOwnProperty.call(TITLES, p.part) || !Number.isInteger(p.season) || p.season < 1 ||
              p.file !== 's' + String(p.season).padStart(3, '0') + '/' + p.part + '.json') throw new Error('Invalid Honors part reference.');
        });
        var failed = false;
        for (var i = 0; i < index.parts.length; i++) {
          var entry = index.parts[i];
          if (!cache[entry.file]) {
            try {
              var doc = await get(entry.file); check(doc, entry.part);
              if (doc.season !== entry.season || !['recorded', 'not_recorded', 'none'].includes(doc.status)) throw new Error('Invalid Honors part identity.');
              cache[entry.file] = doc;
            } catch (e) { if (e.name === 'AbortError') throw e; failed = true; }
          }
        }
        if (dead) return;
        var signature = JSON.stringify(index) + '|' + Object.keys(cache).sort().join(',');
        if (signature !== last) {
          content.querySelectorAll('details[data-honors-key]').forEach(function (d) { opened[d.dataset.honorsKey] = d.open; });
          content.textContent = '';
          index.parts.forEach(function (p) {
            if (cache[p.file]) content.appendChild(part(cache[p.file]));
            else content.appendChild(el('p', 'Season ' + p.season + ' · ' + TITLES[p.part] + ': released details are temporarily unavailable.', 'notice'));
          });
          var u = unavailable(index.unavailable); if (u) content.appendChild(u);
          last = signature;
        }
        notice.textContent = failed ? 'Some released details could not be loaded. Retrying automatically.' : index.parts.length ? 'Showing released honors only. Further parts appear when announced.' : 'Honors have not been revealed yet.';
      } catch (e) {
        if (!dead && e.name !== 'AbortError') notice.textContent = e.status === 404 ? 'Honors have not been revealed yet.' : 'Honors could not be updated: ' + e.message + ' Retrying automatically.';
      } finally { busy = false; }
    }
    var timer = setInterval(refresh, opts.refreshMs || 15000); refresh();
    return {node: root, refresh: refresh, destroy: function () { dead = true; ac.abort(); clearInterval(timer); }};
  }
  window.DFLHonors = {create: create};
})();
