# How this was built

I built EsteBot in a private repo first, because my own health data was all through the
early history. That repo had 52 pull requests over about three weeks. This is the short
version of what happened in them, in order, and what each stage taught me. From here on the
work happens in this repo, one PR at a time.

## 8-12 Oct: a repo with no data in it

The first commit set the rule everything else follows: the code repo holds no personal
data at all. Config points at a separate private data repo, `.gitignore` blocks the data
file names, and CI fails the build if one is ever tracked.

The parsers came first: Apple Health (a 2 GB XML export, streamed rather than loaded),
Jefit for lifting history, Cronometer for food. Each one writes tidy CSVs, and given the same
inputs it writes byte-identical files. That turned out to be the most useful property in the
project, because it gives every refactor a hard test.

What broke: Jefit exports were picked up in file-modified order, so an old re-download could
replace newer history. Exports are now ordered by the date in their name.

## 12-15 Oct: no more manual exports

Exporting files by hand every few days meant the data was always stale, so I automated
every input:

- workouts from the Hevy API (I moved from Jefit to Hevy, and imported the whole Jefit
  history into Hevy through its API, which has no delete and a daily post limit)
- Apple Health from Health Auto Export's iCloud feed
- the Cronometer daily summary through its web export
- my phone notes, copied into the data repo on every sync

A launchd job runs all of it at noon and commits the data repo.

What broke:
- iCloud leaves placeholder files until something asks for the real file, so the parser
  read empty files. It now forces the download first.
- Hevy logs carries as distance with no reps, and the parser dropped them as empty sets.
- The daily job ran from my working checkout, so whatever branch I had open went into the
  noon sync. It now runs from its own checkout of `main`.

Two data problems mattered more than any code bug. Pull-ups are logged at 0 lb, so their
estimated max said nothing on a cut; they now use the 14-day body-weight trend. And the
same exercise was spelled differently across app eras, or swapped for a near relative, so
renames are dated and keep the series apart. Fusing a Pendlay row into a bent-over row
invents strength changes that never happened.

## 15-19 Oct: the page

The daily page went in as five PRs: the training question and the charts behind it, a
weight goal with a forecast date, a panel of pre-registered predictions with interim
progress, training load (hard sets and tonnage, not just minutes), and nutrition, with the
calorie deficit computed two ways.

The second deficit is the interesting one. Apple's burn estimate minus logged intake says
one thing; the weight trend says another. The page shows both and labels where each comes
from.

Then the parts that make it usable every day:
- the page runs as a launchd service, and restarts within 15 minutes of a merge (a
  hand-started server once served stale code for three days)
- a training calendar
- weight pace against the plan, week on week and month on month
- strength standards per lift, now and at the goal weight

The biggest change in this stretch was the readiness verdict. The first version let single
rules decide the day: six training days in a row meant rest, whatever else was true. The
second counted seven areas (load, effort, sleep, HRV and resting heart rate, how I feel,
nutrition, weight-loss pace), each green, amber or red with its reasons written out, and
took the verdict from the count. That lasted until 29 Oct (below).

## 28 Oct: a plan for the session, and going public

- A "Today's session" card works out the next day in the push/pull/legs rotation and gives
  each exercise a target from its last session: add load, add a rep, or hold. Testing it
  against real sessions caught two bugs in the first version, including one that read a
  1-rep set instead of the 3-rep set at the same weight.
- Walks show as light movement and don't count as training days.
- A second person wanted to use it, so the training split and rep ranges moved into
  config, and the page got a tap check-in so nobody has to write notes.
- A presentation mode leaves private details out of the HTML entirely, because CSS-hidden
  text is still in view-source.
- A generator writes six months of data for a made-up person from one seed (a year since
  29 Oct), and the same seed always gives the same files. Running the app on someone else's data exposed four
  bugs at once, all fixed before launch. The demo runs on Fly.io at
  [estebot.app](https://estebot.app), read-only, regenerated every time it starts.
- Before publishing I scrubbed comments, tests and docs of my own history. Then an audit of
  the exported tree, checked against my private notes, found more. This repo started from
  that cleaned tree with no history.

## 29-30 Oct: a server, and a call that has to earn its votes

- The site moved to one Fly.io machine. Visitors still get the demo; signing in with Google
  shows a person's own page, and the noon sync runs on the server and pushes the data repo.
- The counted verdict said "go all out" most days, so I tested it: over 689 sessions, days
  after its top call met 78% of their targets and days after "take it easy" met 77%. The
  call didn't tell days apart. Now every session is graded on its targets, an area only
  votes once its flagged days have gone with worse sessions, and going hard needs a reason
  that has itself gone with better ones. On my data no area has earned a vote yet, so the
  call mostly says "go as planned", which is the honest answer.
- Each morning's first call is written down and never rewritten, so the page can grade the
  calls as they were made.
- A check-in can be said in a sentence. Claude picks the tap card's own words from it as a
  draft, and nothing counts until I save it.
- I tried measuring training load in Hevy hard sets instead of Apple's exercise minutes and
  went back: in daily use the minutes version read closer to how training actually felt.
- The demo's chat went live, inside a $3 daily budget shared by every visitor and five
  questions an hour each.
- Every rate and average on the page now says which days it covers, after three weight
  rates on one page looked like the page disagreeing with itself.
- Backfilling the demo's recorded calls at startup took the site down for about twenty
  minutes. It regraded every past session once for each of the 90 days, 24 seconds of CPU,
  and Fly throttles a small shared CPU hard after a short burst. Now each session is graded
  once and the backfill takes under two seconds, before the server starts.

## What I'd tell someone starting the same thing

- Make the parsers deterministic and diff their output. It caught more mistakes than the
  tests did.
- Distrust any correlation in personal data until you've checked whether it's just "later".
  Training frequency fell over the years, so almost everything correlated with it.
- Write the rule before the data comes in. The predictions panel exists so I can't quietly
  move the goalposts after seeing a result.
- Run the code on somebody else's data early. The demo found bugs my own data never would.
