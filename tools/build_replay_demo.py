#!/usr/bin/env python3
"""Build ONE self-contained page that replays one or more public game files, with no server and no network.

  build_replay_demo.py --out replay_demo.html GAME.json [GAME.json ...] [--title TEXT] [--source TEXT]

GAME.json are files in the public playback format (docs/PUBLIC_PLAYBACK_FORMAT.md), e.g. from tools/export_playback.py. The page carries the viewer
(site/assets/playback.js and .css, unchanged), the styling tokens (base.css) and the game files themselves; it makes no request of any kind, so it opens
by double-click and works without the engine, the league or any credentials. The games are checked with the public key gate before they are embedded.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dflsite import playback as PB  # noqa: E402

SHELL_CSS = """
body { padding-inline: 16px; }
.demo-top { max-width: 1080px; margin: 0 auto; padding-block: 16px 6px; display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 18px; }
.demo-top h1 { font-size: clamp(22px, 4vw, 34px); }
.demo-wrap { max-width: 1080px; margin: 0 auto; display: flex; flex-direction: column; gap: 14px; padding-bottom: 40px; }
.demo-note { background: var(--surface); border: 1px solid var(--line); border-left: 4px solid var(--los); padding: 8px 14px; color: var(--muted); font-size: 15px; max-width: none; }
.demo-pick { display: flex; flex-wrap: wrap; gap: 8px; }
.demo-pick button { font: inherit; padding: 8px 14px; min-height: 44px; background: var(--soft); color: var(--ink); border: 1px solid var(--line); cursor: pointer; }
.demo-pick button[aria-pressed="true"] { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }
.err { background: #3A1414; border: 1px solid var(--loss); color: #FFD6D6; padding: 14px 16px; }
.muted { color: var(--muted); }
.sub { color: var(--muted); }
"""

SHELL_JS = """
(function () {
  var GAMES = JSON.parse(document.getElementById('games').textContent), box = document.getElementById('viewer'), pick = document.getElementById('pick'), cur = null, buttons = [];
  function show(i) {
    if (cur) cur.destroy();
    box.textContent = '';
    var g = GAMES[i];
    cur = window.DFLPlayback.create({ entry: { gid: g.gid }, load: function () { return Promise.resolve(g); } });
    box.appendChild(cur.node);
    buttons.forEach(function (b, k) { b.setAttribute('aria-pressed', k === i ? 'true' : 'false'); });
    window.DEMO = { viewer: cur, index: i };
  }
  GAMES.forEach(function (g, i) {
    var b = document.createElement('button'); b.type = 'button';
    b.textContent = g.teams.away.abbr + ' at ' + g.teams.home.abbr + ' (' + g.final.away + '-' + g.final.home + ')';
    b.addEventListener('click', function () { show(i); });
    pick.appendChild(b); buttons.push(b);
  });
  if (GAMES.length < 2) pick.hidden = true;
  show(0);
})();
"""


def read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("games", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="Game replay demonstration")
    ap.add_argument("--source", default="")
    a = ap.parse_args(argv)
    games = []
    for path in a.games:
        with open(path, "rb") as f:
            g = json.loads(f.read().decode("utf-8"))
        if g.get("format") != PB.FORMAT or g.get("format_version") != PB.VERSION:
            raise SystemExit(f"{path}: not a {PB.FORMAT} version {PB.VERSION} file")
        PB.check_public(g)
        games.append(g)
    blob = json.dumps(games, separators=(",", ":"), sort_keys=True).replace("</", "<\\/")
    note = ("This page replays recorded games from the Diamond Football League development league (a fictional, deterministic simulation). It holds the published public "
            "game files inside itself and makes no network request; it does not run the simulation. " + a.source).strip()
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{a.title}</title>
<style>{read("site", "assets", "base.css")}
{read("site", "assets", "playback.css")}
{SHELL_CSS}</style></head>
<body>
<div class="demo-top"><h1>{a.title}</h1><span class="sub">Diamond Football League · development league</span></div>
<div class="demo-wrap"><p class="demo-note">{note}</p><div class="demo-pick" id="pick" role="group" aria-label="Game"></div><div id="viewer"></div></div>
<script type="application/json" id="games">{blob}</script>
<script>{read("site", "assets", "playback.js").replace("</script", "<\\/script")}</script>
<script>{SHELL_JS}</script>
</body></html>
"""
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(html)
    print(json.dumps({"written": a.out, "bytes": os.path.getsize(a.out), "games": [g["gid"] for g in games]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
