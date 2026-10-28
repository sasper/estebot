# EsteBot

I built this to answer questions about my own training and body weight from the data I
already collect: Apple Health, a lifting log (Jefit, then Hevy), and a food log
(Cronometer). It turns several GB of exports into a small set of tidy tables, then runs
plain arithmetic over them to produce a daily page: a readiness verdict, the weight trend,
strength numbers, a training plan for the next session and a nutrition check.

It is an n-of-1 tool. "Your deep sleep is 10%, below the typical 13-23%" is noise. "Your
deep sleep has been 10% every year for five years" is something you can use. Thresholds
come from the person's own history wherever that is possible.

**Live demo: [estebot.app](https://estebot.app)**, running on a made-up person whose data is
regenerated up to today every time the server starts. It's read-only, and the first load
after a quiet spell takes a few seconds while the machine wakes up.

The daily verdict counts seven signals, and each one shows its reasons and thresholds:

<img src="docs/screenshots/verdict.png" alt="Readiness verdict with seven signals" width="720">

The next session in the rotation, with a target for each exercise worked out from the last
time it was done:

<img src="docs/screenshots/session.png" alt="Today's session with per-exercise targets" width="720">

<details>
<summary>More: the training calendar, weight trend, strength standards, and the page on a phone</summary>

<img src="docs/screenshots/calendar.png" alt="Training calendar" width="720">
<img src="docs/screenshots/weight.png" alt="Body weight trend" width="720">
<img src="docs/screenshots/strength.png" alt="Strength standards" width="720">
<img src="docs/screenshots/phone.png" alt="The page on a phone" width="300">

</details>

## Three rules

1. **No personal data in this repository.** The data lives in a separate private repo and
   the code reaches it through a config file. `.gitignore` blocks the personal filenames,
   and CI fails the build if one is ever tracked.
2. **Every number is computed in plain arithmetic.** A readiness verdict or a weight trend
   gets acted on. If a language model produced it, I would have no way to tell a real
   signal from a confident guess. A model may explain a computed output; it never
   generates one.
3. **No metric gets two implementations.** If the page and the text report could disagree
   about a number, nothing either of them says can be trusted. Shared math lives in one
   place, `core.py`.

One more habit sits on top of those. Claims about what will happen are written down before
the data arrives, with a resolution date, a named estimator and a minimum amount of data.
They are allowed to resolve as inconclusive. Early analyses of this dataset produced two
confident headlines that turned out to be wrong, and a written record is the only thing I
found that stops anyone, me or a model, from quietly deciding afterwards that they were
right all along.

## Try it on demo data

You don't need my data or any exports. The demo module writes a synthetic data directory
for a made-up person:

```bash
git clone https://github.com/sasper/estebot && cd estebot
python -m venv .venv && ./.venv/bin/pip install -e ".[dev,web]"

./.venv/bin/python -m estebot.demo --out /tmp/estebot-demo
```

That writes six months of a made-up person's weigh-ins, lifting, sleep, food and notes, plus
an `estebot.toml` pointing at them. Load it into the app's database and start the page:

```bash
export ESTEBOT_CONFIG=/tmp/estebot-demo/estebot.toml
./.venv/bin/python -m estebot.web.ingest
./.venv/bin/uvicorn estebot.web.main:app --port 8000
```

The same seed always gives the same data. `--seed` picks another person, and `--end today`
moves the six months up to today.

## Running it on your own exports

```bash
cp estebot.example.toml estebot.toml      # then edit data_dir, the export globs, your profile

./.venv/bin/python -m estebot.parsers.apple_health   # streams the multi-GB export, ~8 min
./.venv/bin/python -m estebot.parsers.jefit          # lifting tables (merges Hevy too)
./.venv/bin/python -m estebot.parsers.cronometer
./.venv/bin/python -m estebot.web.ingest             # tidy CSVs -> the app's SQLite cache
./.venv/bin/python -m estebot.analysis.report        # a text summary
```

Getting the exports: Apple Health, profile icon, Export All Health Data. Jefit, Account,
export training logs. Cronometer, Profile, gear, Export Data, Daily Nutrition. Each
source's filename pattern is a glob in the config, so a new export is picked up without
edits. Hevy and Cronometer can also be pulled automatically (see `daily.py`); both read
their credentials from the macOS Keychain, never from a file.

## How it fits together

The short version is below. [ARCHITECTURE.md](ARCHITECTURE.md) has the data flow, the rules in
more depth, how I verify refactors, and the known problems.

```
 exports / APIs                 parsers/                 private data repo         app
 --------------                 --------                 -----------------         ---
 Apple Health export.xml  --->  apple_health.py  ---+
 Health Auto Export JSON  --->  health_auto_export  |
 Jefit CSV, Hevy API      --->  jefit.py, hevy.py   +-->  tidy/*.csv  --> ingest --> app.sqlite
 Cronometer CSV           --->  cronometer.py    ---+     (derived,                 |
                                                           rebuildable)              v
 notes from the phone     --->  notes.py  ---------------> health-context.txt   analysis/engine.py
                                                                                     |
 authored: ledger.md, annotations.csv, corrections.csv, renames.csv                  v
                                                                                web/ (FastAPI,
                                                                                inline SVG charts)
```

| Module | What it does |
|---|---|
| `config.py` | Reads the TOML config: where the data lives and every source's glob. Uses `tomllib`, so no dependency. |
| `core.py` | Pure functions, no I/O. Most of them exist because the naive version gave a wrong answer once. |
| `parsers/` | One per source. Stdlib only. The Apple Health parser streams a ~2 GB XML file at constant memory. |
| `analysis/engine.py` | Coverage gates, load ratio, the readiness signals, strength standards, the next-session plan. |
| `analysis/nutrition.py` | Energy balance, protein, and micronutrient gaps against the US reference intakes. |
| `analysis/predictions.py` | Reads the prediction ledger and shows interim progress on open claims. |
| `web/` | FastAPI and Jinja templates. It formats numbers the engine computed; it does not compute its own. |
| `daily.py` | The unattended daily sync, scheduled with launchd on macOS. |

Readiness is seven signals (load, effort, sleep, recovery, how I feel, nutrition, weight
loss pace), each green, amber, red or no data, with the reason written next to it. The
verdict counts them, so no single input decides the day on its own.

Derived data is disposable and gets rebuilt from the exports. Authored data (annotations,
the ledger, dated correction rules) lives apart from it, because only a person can
produce it.

## Things the exports get wrong

Most of the parser code exists because the raw data is full of traps. A few of them:

- Apple emits overlapping sleep records per device and per stage, so summing them
  double-counts. Intervals are unioned first.
- A night spans midnight, so filing sleep by calendar date splits it in two. Segments are
  shifted back 12 hours, which also sends an afternoon nap forward to the coming night.
- Watch and iPhone both count steps over the same intervals. The full export keeps the
  largest single-device total for the day rather than the sum.
- The lifting app changed its dumbbell entry from per-hand to combined weight partway
  through the history. Dated correction rules fix it at parse time; the source data is
  never edited.
- An exercise substitution is not a strength change. A trap bar carry is not a dumbbell
  carry. Renames are dated and keep the series apart, so a swap can't manufacture a gain.
- Pull-ups are logged at 0 lb. Their load is body weight, taken from the 14-day weight
  trend, so on a cut the same reps correctly show up as less strength.
- Epley's 1RM estimate drifts badly above about 10 reps, so those sets return `None`. Not
  `0.0`: a zero is a number and quietly drags down any median taken over the column.

## Tests

```bash
pytest -q
```

Tests in `tests/test_core.py` each correspond to a bug that actually happened. CI runs the
suite on Python 3.11, 3.12 and 3.13, and a separate job fails if any personal-data path is
tracked.

## Status

I use it every day, for one person. The personal values (height, goals, file locations)
are in config, but some assumptions are still mine: imperial units on the page, a
push/pull/legs rotation in the training plan, and reference intakes for one demographic.
The daily sync and the page service assume macOS and launchd.

## License

MIT
