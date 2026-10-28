# Architecture

How EsteBot is put together, the rules it holds itself to, and the mistakes that shaped it.
The README covers what it is and how to run it.

## Three rules

1. **No personal data in the code repo.** Not in code, tests, comments or commit messages.
   My data lives in a separate private repo and is reached only through `estebot.toml`.
   `.gitignore` blocks the data filenames, and CI fails the build if one is tracked.
2. **Every number is plain arithmetic.** No model produces a figure that gets acted on. If a
   language model is ever added, it explains computed outputs and never generates them. If
   I'm about to have a model estimate a value, the design is wrong.
3. **No metric gets two implementations.** If the text report and the web page can disagree
   about a number, the whole project's claim is false. Shared math lives in `core.py`.

## How data moves

```
exports and APIs           parsers (stdlib)          tidy CSVs           web
--------------------       -------------------       -------------       ------------------
Apple Health export   ->   apple_health.py      ->
Health Auto Export    ->   health_auto_export   ->   daily, sleep,
Hevy API / Jefit      ->   hevy.py, jefit.py    ->   workouts, sets,  -> ingest -> SQLite
Cronometer            ->   cronometer.py        ->   lift_sessions,      cache -> engine
iCloud notes          ->   notes.py             ->   nutrition, ...           -> FastAPI page
```

The tidy CSVs are the contract between parsing and analysis. The parsers are deterministic:
the same inputs always give byte-identical CSVs, which is what makes refactoring safe (below).
`web/ingest.py` loads the CSVs, plus notes and authored state, into a SQLite cache. The
analysis engine reads from SQLite. The web layer only formats what the engine returns.

## Layout

```
src/estebot/
  config.py        TOML config: the data directory, export globs, goals, [training]
  core.py          pure functions, no I/O. Most of them exist because of a bug that happened
  daily.py         the unattended daily sync: pull, parse, ingest, commit the data repo
  demo.py          writes a complete data directory for a made-up person, from one seed
  parsers/         one module per source, plus shared table layouts (health_tables,
                   lift_tables) so two sources can't drift apart
  analysis/
    engine.py      readiness (seven green/amber/red signals, counted, no single input
                   decides), training load, e1RM windows, spinal-load scoring, the
                   training plan (next day in the rotation, a target per exercise)
    nutrition.py   energy balance, protein, micronutrient gaps and upper limits vs the US DRI
    predictions.py pre-registered predictions and their interim progress
    report.py      text report
  web/             FastAPI page, inline SVG charts, the SQLite cache and its loader
tests/             pytest; most tests pin a failure that really happened
```

## Readiness, briefly

The daily verdict comes from seven areas: training load, effort (RPE), sleep, HRV and
resting heart rate, how I feel, nutrition and weight-loss pace. Each one is green, amber or
red with its reasons written out. `core.combine_signals` counts them: rest when two or more
areas show strain, take it easy with one strain or two to watch, go all out only when
everything is good. An earlier version let single inputs decide the day: six training days
in a row forced rest, whatever everything else said. The one deliberate exception left is a
back spasm, which forces rest on its own.

## Verifying a refactor

The parsers are deterministic over fixed inputs, so a refactor has a hard test:

```bash
git -C <data_dir> status --porcelain tidy/     # must be clean before you start
python -m estebot.parsers.jefit
python -m estebot.parsers.cronometer
git -C <data_dir> diff --quiet tidy/ && echo IDENTICAL || echo "CHANGED - investigate"
```

Byte-identical output is the bar for anything labelled a refactor. The same trick works on
the text report by capturing stdout before and after, and on the demo generator with
`diff -r` over two generated directories. If `tidy/` isn't clean because a sync hasn't been
committed, snapshot it and diff against the snapshot instead of discarding anything.

## Conventions

- Stdlib only in the core and the parsers. FastAPI, uvicorn and Jinja are `[web]` extras.
  TOML over YAML so the core stays dependency-free (`tomllib` ships with Python 3.11).
- Python 3.11+. CI runs 3.11, 3.12 and 3.13.
- A branch per change, a PR, squash merge.
- Commit messages explain why. The diff already shows what.
- A test in `tests/test_core.py` should correspond to a real failure, not hypothetical input.

## Known problems, in the order worth fixing

**Two data access paths.** The engine reads SQLite, while `report.py` reads the tidy CSVs
directly. Unifying them is a real decision (SQLite as the single read path, or CSVs as the
contract with SQLite only for authored state), and it needs making before code. The visible
symptom: "best e1RM in a date window" is computed three times, by `report.py`, `blocks.py`
and the engine. Only one read path lets them collapse into one function.

**Smaller:** `report.py` orders tied rows in its strength table by set iteration, so its
output changes between runs. Until that's fixed, compare report output with
`PYTHONHASHSEED=0` on both sides.

**Smaller:** `report.py` writes `daily_combined.csv` as a side effect. A module called
"report" shouldn't change the data directory; that belongs in the parser stage.

## Domain gotchas that caused wrong conclusions

Each of these produced a confident, wrong analysis before I understood it.

- **Dumbbell loads switched between combined and per-hand** partway through the history,
  when the logging app changed its UI. Dated rules in the data repo's `corrections.csv` fix
  it at parse time. The source data is never edited.
- **An exercise substitution is not a strength change.** A trap bar carry isn't a dumbbell
  carry, and a Pendlay row isn't a bent-over row. Fusing the series invents gains or losses
  that never happened. Renames are dated and keep the series separate (`renames.csv`).
- **Pull-ups are logged at 0 lb, so their load is body weight.** Their e1RM uses the 14-day
  weight trend plus any logged belt weight. On a cut, flat reps mean falling strength. For
  years the old logging app also stored +10 lb on pull-up sets that were never weighted; a
  ranged correction removes it.
- **Machine loads don't compare across gyms.** Stacks differ, so any cross-gym comparison of
  machine work is meaningless.
- **Correlation here is full of confounds.** Training frequency fell over the years, so
  anything else that changed over the years correlates with it. One early headline
  ("compound lifts hurt strength") was purely a time trend and would have produced harmful
  advice. Check the era, check regression to the mean after layoffs, and check whether the
  thing being measured is just "later".
