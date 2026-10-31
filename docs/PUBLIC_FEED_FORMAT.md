# The public feed (format `dfl-public-feed`, version 1)

The league and the website are separate programs joined by one thing: a folder of plain JSON files. The league side writes it
(`tools/export_public_feed.py`); the website only reads it (`site/`). Nothing the website does can reach the simulation.

Code: `src/dflsite/feed.py` (what is exported, the allowlist schemas), `src/dflsite/publish.py` (atomic publication, the reader).
Tests: `tests/test_site_feed.py`, `tests/test_site_publish.py`, `tests/test_site_static.py`. Frozen example: `tests/fixtures/feed_v1/`.

## Layout

```
feed/latest.json                         the pointer (never cached)
feed/releases/<release>/manifest.json    league identity, season, simulation date, file list with size and SHA-256
feed/releases/<release>/<file>.json      season, calendar, notices, teams, schedule, standings, playoffs, history, venues, rules
feed/releases/<release>/rosters/<ABBR>.json   one public roster per club (48)
```

A release is named `e<events fired>-<first 10 hex of its content hash>`. A release directory is written completely, then given its name,
then never changed. `latest.json` is replaced last, in one rename. A page therefore sees the old release or the new one, never a mixture.

## What every file carries

`manifest.json`: `format`, `format_version` (1), `format_minor`, `league` (`id`, `name`, `kind: "development"`, `engine`, `label`), `release`,
`generated_at` (the only wall-clock value), `content_hash`, `season`, `year`, `simulation_date`, `last_event`, `events_done`, `events_total`,
`files` (path, SHA-256, bytes). The development label and the "last update" indicator on the site come from here.

## What is exported, and what is never

Exported (each record type has an exact allowlist in `feed.SCHEMAS`; an unlisted field fails the build):

- the calendar and which events are done; the schedule and every result already committed; standings; the playoff bracket as it stands;
  the Ambassador Tour; the Bowl rosters and result after the Bowl; the Coronation after it is played; history of finished seasons;
- teams (names, colours, grounds, conference, division, status); public rosters: name, position, number, age, credited seasons, size, slot,
  injury status (out or not, weeks out), cap hit, years left; club cap used; named club leaders (role, name, age);
- a curated summary of the approved rules, tied to a spec edition and hash (`docs/public/RULES_SUMMARY.md`); notices.

Never exported, and checked on every build by `feed.check_public` (allowlist schema, a forbidden-key net, forbidden words, and a scan for the
league seed): hidden ratings and attributes, the true overall or any estimate of it, growth, traits, memories, relationships, private valuations,
the league seed or any random draw (the Week 4 designation's seeded-draw value is dropped), the Bowl ballots and counts, the draft order and
lottery, awards, rings, tiaras and halls, results of events not yet committed (a whole week's results appear when the week's event commits;
Tour and Bowl venues appear only once played).

The tests also scramble every hidden value in a league (ratings, attributes, growth, traits, memories, ballots, honours, the draft order) and
require the feed to come out byte for byte the same, and they check that building a feed leaves the league's own state digest unchanged.

## Versioning

- `format_version` is the major version. A reader must refuse a feed whose major version it does not know (the site does, and says so).
- Minor 1 added the optional `score_source` to each history season. A minor version may add files and fields. Readers ignore what they do not know. A field may be added only as optional (`Opt`) in the schema.
- Removing or renaming a field, or making one required, needs a new major version and a new frozen fixture. The compatibility tests
  (`test_site_static.Compatibility`) fail if the current schemas stop accepting `tests/fixtures/feed_v1`. Do not regenerate the fixture to make them pass.
- To add something to the feed: edit `feed.py` (the builder and `SCHEMAS`), add a test for what it reveals and when, update this file, and
  say so in the changelog. Adding information is always a deliberate edit.

## Known limits (honest list)

- Results appear per committed event, not per kickoff; the real-time clock is not built.
- Diamond Coronation scores (Edition 8.58, format minor 1): every season closed from Edition 8.58 on carries its Coronation score in its season
  result (`result_revision` 2), and history shows it with `score_source: "recorded"`. A season closed earlier has no score in the league's own state; the
  exporter then shows the score only if the league's game archive holds that game (`S<season>/coronation`, checksummed) and it matches the season report in
  season, sides, nominal home side and winner (`score_source: "archive"`, and the page says it was recovered). Otherwise `score` is null with
  `score_source: "unavailable"` and the page says the score was not recorded. A score is never estimated. Carrying a pre-8.58 league forward needs
  the normal upgrade (`league_store.py upgrade OLD NEW --run-originating-code`); on the real preview league (event 73, one closed season) it reported
  `state_changed: false` and the original file untouched.
- Results appear per committed event (a whole week at a time in this milestone): the header shows the **simulation date** and the **last published update**
  (the time the data last changed; a quiet league publishes nothing new, so this does not move).
- No statistics, box scores, play-by-play, draft pages, transactions, awards or halls yet.
- No player or team photos or logos; clubs show a coloured abbreviation badge.
- The official map is part of the site, not the feed: `site/assets/map/dfl-map.png`, named by `site/assets/map/map.json` (alignment of 30 October 2026). A new map is a new site release; the feed does not change.
