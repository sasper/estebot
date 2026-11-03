# How the call panel's numbers are made

Phase 1 findings for rewriting "How today's call was made" in plain language. Line numbers
are on the `call-explained` branch (off main after #79), which adds `core.call_rule`. PR #80
(open) changes the check-in and safety-hold rules; where that matters it's marked **#80**.
Every example number here is the demo's made-up lifter.

## 1. What "pts" is

**What gets scored per session.** `engine.session_outcome` (`analysis/engine.py:902`) gives
every exercise done that day a goal, set by its progression rule from the last time it was
done (`_target`, `engine.py:884`). It then checks whether one working set reached it
(`core.goal_met`, `core.py:607`). The session's **hit** is the share of its exercises that met
their goal: 3 of 4 is 0.75. Sessions under 5 sets aren't graded (`graded_sessions`,
`engine.py:936`).

**Adjusted for the stretch it was in.** Each hit has the mean hit of the other sessions within
45 days either side subtracted (`PHASE_DAYS`, `engine.py:934`; the subtraction is at
`engine.py:968-970`). A good or bad month as a whole then doesn't look like an area's effect.

**pts = 100 × (mean adjusted hit on flagged days − mean on clear days)**
(`core.flag_effect`, `core.py:336`, called at `engine.py:973`). "Flagged" means the area was
watch or strain that morning, and "clear" means it was good. Days with no reading for that
area count on neither side.

So sleep at **−30 pts** means that on flagged-sleep days you met about **30 percentage points
fewer** of your targets than usual for that stretch. If you usually meet 75%, that's about
45%. It is **not** "30% fewer" in relative terms.

**The range in parentheses** is a 95% confidence interval from the normal approximation:
diff ± 1.96 × √(var_flagged/n_flagged + var_clear/n_clear) (`core.py:349-351`). It isn't a
bootstrap.

## 2. What gives an area a vote

`core.flag_effect` (`core.py:336-354`):

- Each side needs at least **15 sessions** (`EVIDENCE_MIN_N`, `core.py:333`). Below that, the
  status is "not enough data" and the area can't vote.
- An area votes when the **whole range is below zero** (`votes: hi < 0`, `core.py:354`).
- A range wholly **above** zero means the flag goes with *better* sessions. That never gives a
  vote, but it's what a reason to go hard needs (`supports: lo > 0`).

Votes are worked out over every graded session before the day in question (`earned`,
`engine.py:554`, which calls `evidence` up to the day before).

**Why HRV shows "−21 to −0":** the top of its range is a hair below zero (for example −0.004).
It votes because the test is strictly `hi < 0`, and the display's `%+.0f` rounds that to "−0".
It's a real vote, but only just, and the rewrite should print 0.

## 3. The "check-in" reason to go hard

`engine.py:493-495`. All of these must hold:

1. There is a check-in **dated today**. Yesterday's never counts for this reason, even on main.
2. **Energy is 2 or 3** ("ok" or "great"). A sick tick with no energy answer reads 0
   (`core.checkin_energy`, `core.py:218`).
3. **How you feel is good** (`_feel_signal`, `engine.py:380`):
   - the back is "ok" or "tired". "Tired" counts as good; "sore" is watch, "spasm" is strain.
   - recovery between sets isn't "slow" or "very slow" today.
   - recovery between sets wasn't slow on 3 or more of the last 14 days.

It counts only once the reason has **earned** it. The check is the same `flag_effect` turned
around: sessions on days with the reason against check-in days without it, and the whole range
must be above zero (`engine.py:990-995`). On the demo that's +29 pts (+13 to +45).

Plain wording: **"You checked in feeling at least OK, with no back or recovery problems."**
"Feeling great" would overclaim, since "ok" is enough.

## 4. Grading on take-it-easy days: original or lowered targets?

**Neither. Deload days aren't graded at all.** `session_outcome` returns None for any day in
`deload_days` (`engine.py:865-872`, `912`). Those are:
- days with a recorded **take it easy** call, from `DELOAD_SINCE = 2026-11-02` (`engine.py:862`)
- days a check-in flagged the back (tired, sore or spasm), from the same date.

The reason, from the docstring: grading a deliberately light session against normal targets
would make the area that called it look predictive and call more deloads.

From 2 Nov 2026 the plan really does lower targets on those days: take it easy is 80% of sets
and weight, and a flagged back is 60% (`core.py:524-525`, `engine.py:1056`). Before that date,
light days kept their normal loads, and they *are* graded, against the normal targets.

What this means for the copy:
- The "Under today's rules" column replays history. Its take-it-easy row is sessions whose
  replayed call is light, graded against **normal** targets, nearly all before 2 Nov 2026.
- The "Recorded calls" column (`calls.graded`, `analysis/calls.py:118`) also uses
  `session_outcome`. **From 2 Nov 2026 every recorded take-it-easy day drops out**, so that row
  stops growing. This is a logic gap and Phase 2 should not paper over it. See the flags below.

## 5. Where watch and strain are set

All in `analysis/engine.py`. An area takes the worst of its parts (`core.worst`).

| Area | Function | Watch (amber) | Strain (red) |
|---|---|---|---|
| Training load | `_load_signal` :304 | exercise-minute ratio, last 7 days vs last 28 (`acwr` :150), above 1.3 **or below 0.8**; or monotony above 2.0 | ratio above 1.5; or monotony above 2.5 |
| Effort (RPE) | `_effort_signal` :330 | mean session RPE over the last 7 days at least 0.75 above the 4 weeks before, or at or above 9.0 | 1.5 or more above. Needs 2 recent and 4 baseline sessions |
| Sleep | `_sleep_signal` :343 | last night under 6 h; or the last 3 nights average more than 0.75 h below the 30 before | last night under 5 h; or more than 1.5 h below |
| HRV & resting HR | `_recovery_signal` :361 | 7-day HRV z at or below −1.0 (`core.weekly_shift_z` :319, against the 30 days before the week); or resting HR more than 3 bpm above | z at or below −1.5; or resting HR more than 5 above |
| How you feel | `_feel_signal` :380 | energy 1; back sore; recovery between sets slow today, or on 3+ of 14 days | energy 0; back spasm |
| Nutrition | `_nutrition_signal` :406 | intake over 1,000 kcal/day below maintenance, or protein under 80% of target (complete days of the last 4) | over 1,500 below, or protein under 60% |
| Weight-loss pace | `_pace_signal` :429 | losing faster than the max (default 1%/week, 4 weeks vs 4 before) | faster than 1.5× the max |

Load and effort read through yesterday; sleep, recovery and feel through today.

## 6. Is the planned workout known?

**Yes for lifting: the session type and its full exercise list. Nothing for anything else.**
`training_plan` (`engine.py:1010` on) works out:
- **Session type:** the next day in the rotation after the last lifting session (push, pull,
  legs by default; `ROTATION` :756, set per person in `[training]`). It also picks the A or B
  variant by its main lift (`VARIANTS` :759).
- **Exercise list:** the exercises from the last session of that same variant, ordered mains
  first, then by spinal load. Each gets a target. The Hevy routine is filled from this.
- **Day type of past sessions** comes from `lift_session.muscle_groups` (`day_type` :670,
  `DAY_TYPES` :665: push = chest/shoulders/triceps, pull = back/biceps/carry/olympic,
  legs = quads/hamstrings/calves).
- **Spinal load per exercise** already exists: `AXIAL` (`engine.py:647`) scores each exercise
  0-3 by name. 3 covers deadlifts, barbell and bent-over rows, cleans, carries and back or front
  squats. 2 covers other squats, lunges, RDLs, overhead and military press, and pull-ups.

**Not known:**
- whether today is a training day at all. The plan always names the next session.
- non-lifting sessions such as rucks or runs. Apple Health workouts arrive after the fact and
  are never planned.

"Unknown" happens when there is no lifting history of the next type.

For Phase 3: "back is used" can be decided per exercise from `AXIAL` (score 2 or more), or by
session type, both from data already there.

## 7. Every place the back spasm is stored, read or used

**Stored:** `checkin.back` (`web/db.py:66`, values ok | tired | sore | spasm) plus
`back_reported` (`:78`). It's written by:
- the tap card save, `main.checkin` (`web/main.py:564`), with `BACK_LEVELS` at `:101`
- the typed-sentence draft (`web/checkin_text.py:21,35`), which only becomes a check-in on Save
- notes, by regex at each sync: `ingest.BACK_SPASM` (`web/ingest.py:186`) matches **any
  "spasm"**, negations included, and writes a derived check-in (`:228-248`). It never
  overwrites a tapped one.
- the demo generator (`demo.py:229-231`, notes at `:533-534`)

**Read and used:**
- `_feel_signal` (`engine.py:394-396`): spasm makes how you feel strain.
- `readiness` (`engine.py:468-470`): spasm means **forced rest**, passed to `core.call_rule` and
  `evidence_call` (`core.py:244`, `:286`). On main it uses the latest check-in of today or
  yesterday. **#80** makes it today only.
- `engine.INJURED` (`engine.py:249`): "spasm" in a **note's** text marks the day injured, which
  holds the call at take it easy. It also draws the calendar's injured ring. On main the hold
  covers today or yesterday. **#80** lets a clean check-in lift yesterday's.
- `recommend_pattern` (`engine.py:702-705`): spasm means "rest" as the suggested day type.
- `training_plan` (`engine.py:1052-1056`): any flagged back (tired, sore, spasm) loads at 60%.
- `deload_days` (`engine.py:863-872`): any flagged back from 2 Nov 2026 means the session isn't
  graded.
- `main.presentable` (`web/main.py:297`) blanks the back in presentation mode. The new tile's
  copy does the same (`today.html:370,380`).
- `calls.py:64` and `chat.py:130` pass the check-in on to `training_plan`.
- Shown in: the check-in card, the call card's feel row, the session card ("Back is
  spasming…"), the calendar ring, and the landing page's static check-in picture
  (`landing.html:82`).

## Flags on the Phase 2 copy

1. **"You hit 30% fewer of your targets" is wrong.** It's percentage points, not a relative
   drop. Proposed default line: **"On short-sleep days, you hit about 3 fewer of every 10
   targets."** In the numbers view: −30 pts (likely −38 to −22).
2. **"Too few days to judge" below 10:** the engine won't judge below 15 on either side. Use
   the engine's 15, or the panel would show numbers the engine ignores.
3. **The track-record explainer is wrong.** Graded take-it-easy sessions were graded against
   *normal* targets, and actually-lowered days aren't graded. Proposed: **"On days the app
   says to take it easy, you've hit fewer of your usual targets, so it is picking the right
   days to back off."** Separately, recorded take-it-easy days stop being graded from 2 Nov
   2026 (section 4). That needs a logic decision, for example grading deload days against
   their lowered targets.
4. **Ladder, Rest:** "Pain or injury that stops you training" overclaims while the rule only
   reads a back spasm. A painful knee doesn't trigger it until Phase 3. Interim: **"A back
   spasm in today's check-in, or two counted signals show Concern."**
5. **Ladder, Go hard:** the code requires *no counted signal at Watch or Concern*, not just no
   Concern. Proposed: **"No counted signal shows Watch or Concern, and something that predicts
   good days for you is present."**
6. **The safety rules and "Energy … yesterday" copy depend on #80.** After #80, how you feel
   reads today only, and the hold is "injured today, or injured yesterday without a clean
   check-in this morning". "No check-in" alone isn't the rule. A check-in that still reports
   a sore back keeps the hold.
7. **HRV & resting HR:** the area uses both, so keep the label and show resting HR on its own
   line when it's there.
8. **Sleep's line** should name the rule's two parts: last night's hours, and the last 3 nights
   against the 30 before. "Lately vs before" blurs it.
9. **Status for no reading:** "No concern / Watch / Concern" needs a fourth word for no data.
   "No reading yet" fits.

## Phase 3: decided, and built on 2 Nov 2026 (merges after the LinkedIn post)

Pain-or-injury check-in in place of the back level. Recorded here so the decisions don't
live only in a chat.

- **No rucking clause.** Non-lifting sessions aren't planned (section 6), so the rules only
  look at the planned lifting session.
- **Back is used** when any planned exercise has `AXIAL` 2 or more (`engine.py:647`). Other
  areas map from `lift_session.muscle_groups` through one config file the owner can edit.
- **Migration of existing back levels:** ok → none, tired → 1, sore → 2, spasm → 3. A
  spasm-to-severity-3 day stays a rest day, so historical calls don't change.
- **Back severity by level (confirmed 1 Nov 2026):**
  - **1 (was "tired")**: a note only. No load change, scored normally, and still counts as
    fine for the check-in reason to go hard.
  - **2 or worse, on a day with any planned lift at AXIAL 2 or more**: the 60% load rule
    (`core.DELOAD_BACK`), and the day isn't scored, as now.
  - **2, on a day with no such lifts**: a note. The plan loads normally.
  - **3**: rest.

  Today a "tired" back triggers the 60% load and an unscored day (`BACK_FLAGGED`,
  `engine.py:863`). Phase 3 drops that.

### As built

- The check-in asks "Any pain or injury? (Not normal muscle soreness.)", None or Yes, and on
  Yes a severity of 1-3 per area. The Injured toggle is gone from the card; notes that say
  injured still hold the day as before.
- `core.pain_rule` is the rule; `engine.pain_of` reads a check-in, falling back to the old
  back level when the row has no pain answer, so nothing in the database is rewritten.
- `engine.areas_used`: the back from `AXIAL` 2 or more, every other area from each planned
  exercise's muscle group through `src/estecoach/pain_areas.toml`. No known workout means
  every area counts as used, and so does "other".
- Severity 2 in a used area is a safety hold ("pain that limits your knees, which today's
  workout uses") and the plan says "Skip or swap moves that load your knees." Back pain 2
  on a day with spinal loading keeps the 60% rule instead.
- Severity 2 or more yesterday, with no check-in yet today, is a hold.
- Calls do move where they should: a sore back (now severity 2) on a spinal-loading day
  becomes take it easy, as does the day after with no check-in. The demo year has no
  verdict changes; `tests/fixtures/demo_calls.json` changes only in two days' hold text.

### Scoring take-it-easy days (built with Phase 3)

Easy days from 2 Nov 2026 are scored against the lighter targets their recorded call gave
(`engine.lighter_outcome`), shown as their own row under "Do the calls match how workouts
went?" They stay out of `session_outcome` and so out of the evidence for which signals count.

## Open decisions

None right now.
