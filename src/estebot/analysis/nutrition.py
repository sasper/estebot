"""Nutrition on the page: energy balance, protein, and micronutrient gaps.

Every figure is arithmetic over logged data. Reference intakes are the published US Dietary
Reference Intakes for men 31-50 (RDA, or AI where no RDA exists), keyed by Cronometer's own
column names. The food suggestions are a fixed table, not generated text.

Supplements come from supplements.toml (data repo): a fixed daily stack added to food, so
"is this a gap" and "is this near an upper limit" are both answered on total intake.

Only days marked complete in Cronometer count: a half-logged day reads as a deficit and as
a deficiency at once. Micronutrients additionally need most of the day's calories to come
from foods that report that nutrient (nutrients.csv kcal_coverage_pct); otherwise a
restaurant meal with no micro data would show up as a gap you don't have.
"""
import statistics as st
import tomllib
from datetime import date, timedelta

KCAL_PER_LB = 3500          # the ledger's convention; an approximation, labelled as one
LB_TO_KG = 0.45359237

# US DRI, men 31-50. Units as Cronometer exports them.
REFERENCE = {
    "Vitamin A (µg)": 900, "Vitamin C (mg)": 90, "Vitamin D (IU)": 600, "Vitamin E (mg)": 15,
    "Vitamin K (µg)": 120, "B1 (Thiamine) (mg)": 1.2, "B2 (Riboflavin) (mg)": 1.3,
    "B3 (Niacin) (mg)": 16, "B5 (Pantothenic Acid) (mg)": 5, "B6 (Pyridoxine) (mg)": 1.3,
    "B12 (Cobalamin) (µg)": 2.4, "Folate (µg)": 400, "Calcium (mg)": 1000, "Copper (mg)": 0.9,
    "Iron (mg)": 8, "Magnesium (mg)": 420, "Manganese (mg)": 2.3, "Phosphorus (mg)": 700,
    "Potassium (mg)": 3400, "Selenium (µg)": 55, "Zinc (mg)": 11, "Fiber (g)": 38,
}

# US Tolerable Upper Intake Levels, adults 19-50. Some apply to all intake, some only to
# supplements (magnesium, niacin, vitamin E, folic acid), so each carries its basis.
# Folate is in DFE to match Cronometer and the label: 1,000 µg folic acid = 1,667 µg DFE at
# the label's own conversion (500 µg folic acid = 833 µg DFE). Vitamin A is left out: its
# limit is for preformed retinol only, and neither Cronometer nor the label splits that out.
UPPER = {
    "Vitamin D (IU)": (4000, "total"), "Calcium (mg)": (2500, "total"),
    "Iron (mg)": (45, "total"), "Zinc (mg)": (40, "total"), "Selenium (µg)": (400, "total"),
    "Copper (mg)": (10, "total"), "Manganese (mg)": (11, "total"),
    "B6 (Pyridoxine) (mg)": (100, "total"), "Vitamin C (mg)": (2000, "total"),
    "Phosphorus (mg)": (4000, "total"),
    "Magnesium (mg)": (350, "supplements"), "B3 (Niacin) (mg)": (35, "supplements"),
    "Vitamin E (mg)": (1000, "supplements"), "Folate (µg)": (1667, "supplements"),
}

FOODS = {
    "Vitamin A (µg)": "sweet potato, carrots, spinach, eggs",
    "Vitamin C (mg)": "bell peppers, kiwi, oranges, strawberries, broccoli",
    "Vitamin D (IU)": "salmon or sardines, eggs, fortified milk; food alone rarely closes it, so ask your doctor about a supplement",
    "Vitamin E (mg)": "sunflower seeds, almonds, peanut butter, avocado",
    "Vitamin K (µg)": "kale, spinach, broccoli, Brussels sprouts",
    "B1 (Thiamine) (mg)": "pork, whole grains, beans",
    "B2 (Riboflavin) (mg)": "milk, Greek yogurt, eggs, mushrooms",
    "B3 (Niacin) (mg)": "chicken, tuna, peanuts",
    "B5 (Pantothenic Acid) (mg)": "chicken, mushrooms, avocado, eggs",
    "B6 (Pyridoxine) (mg)": "chicken, potatoes, bananas, chickpeas",
    "B12 (Cobalamin) (µg)": "meat, fish, dairy, eggs",
    "Folate (µg)": "lentils and beans, spinach, asparagus",
    "Calcium (mg)": "Greek yogurt, milk, cheese, tofu made with calcium, canned salmon with bones",
    "Copper (mg)": "cashews, sesame seeds, mushrooms, dark chocolate",
    "Iron (mg)": "red meat, lentils, spinach (with vitamin C to help absorption)",
    "Magnesium (mg)": "pumpkin seeds, almonds, spinach, black beans, dark chocolate",
    "Manganese (mg)": "oats, brown rice, nuts, tea",
    "Phosphorus (mg)": "dairy, meat, fish, beans",
    "Potassium (mg)": "potatoes, beans and lentils, bananas, yogurt, spinach",
    "Selenium (µg)": "a Brazil nut or two, tuna, eggs",
    "Zinc (mg)": "beef, oysters, pumpkin seeds, chickpeas",
    "Fiber (g)": "beans and lentils, oats, berries, chia seeds, vegetables",
}


def _complete_days(con, day, days):
    start = (date.fromisoformat(day) - timedelta(days=days - 1)).isoformat()
    return [r["date"] for r in con.execute(
        "SELECT date FROM nutrition WHERE complete=1 AND date BETWEEN ? AND ? ORDER BY date",
        (start, day))]


def load_supplements(path):
    """supplements.toml -> (products, notes). Missing file: no supplements, no notes."""
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except FileNotFoundError:
        return [], {}
    return raw.get("product", []), raw.get("notes", {})


def active(product, day):
    return (product.get("since") or "") <= day <= (product.get("until") or "9999")


def supplement_amounts(products, day):
    """Daily amount per nutrient from every product active on `day`."""
    out = {}
    for p in products:
        if not active(p, day):
            continue
        for k, v in (p.get("per_serving") or {}).items():
            out[k] = out.get(k, 0) + v * p.get("servings_per_day", 1)
    return out


def micronutrients(con, day, supplements=None, notes=None, days=28, min_coverage=80.0,
                   min_days=5, below_pct=90.0, near_limit_pct=50.0):
    """-> (gaps, near_limit, short_on_data).

    Food is the mean over qualifying days (complete, and >= min_coverage % of kcal from
    foods reporting the nutrient); the supplement stack active on `day` is added to it.
    gaps: total under below_pct % of the reference (the margin keeps logging noise around
    the line off the list). near_limit: at or over near_limit_pct % of an upper limit, on
    that limit's own basis (total intake, or supplements only)."""
    supp, notes = supplements or {}, notes or {}
    complete = set(_complete_days(con, day, days))
    gaps, near, unmeasured = [], [], []
    for name, ref in REFERENCE.items():
        vals = [r["amount"] for r in con.execute(
            "SELECT date, amount, kcal_coverage_pct FROM nutrient WHERE nutrient=?", (name,))
            if r["date"] in complete and (r["kcal_coverage_pct"] or 0) >= min_coverage]
        label = name.rsplit(" (", 1)[0]
        if len(vals) < min_days:
            unmeasured.append(label); continue
        food, extra = st.mean(vals), supp.get(name, 0)
        total = food + extra
        row = {"nutrient": label, "food": food, "supplements": extra, "mean": total,
               "unit": name.rsplit("(", 1)[1].rstrip(")"), "reference": ref,
               "pct": total / ref * 100, "days": len(vals), "foods": FOODS[name],
               "note": notes.get(name)}
        if total < ref * below_pct / 100:
            gaps.append(row)
        if name in UPPER:
            limit, basis = UPPER[name]
            amount = total if basis == "total" else extra
            if amount / limit * 100 >= near_limit_pct:
                near.append(dict(row, limit=limit, basis=basis, amount=amount,
                                 limit_pct=amount / limit * 100))
    return (sorted(gaps, key=lambda g: g["pct"]),
            sorted(near, key=lambda g: -g["limit_pct"]), unmeasured)


def energy_balance(con, day, rate_per_week, days=14):
    """Two deficits, labelled, because they disagree and the disagreement is worth seeing.
    logged:   Apple's burn (active + basal) minus Cronometer intake, mean over complete days.
    measured: implied by the weight trend, -rate x 3500 / 7."""
    rows = [r for d_ in _complete_days(con, day, days) for r in con.execute(
        """SELECT n.kcal, d.active_kcal, d.basal_kcal FROM nutrition n JOIN daily d USING (date)
           WHERE n.date=? AND n.kcal IS NOT NULL AND d.active_kcal IS NOT NULL
             AND d.basal_kcal IS NOT NULL""", (d_,))]
    intake = st.mean(r["kcal"] for r in rows) if rows else None
    logged = st.mean(r["active_kcal"] + r["basal_kcal"] - r["kcal"] for r in rows) if rows else None
    measured = -rate_per_week * KCAL_PER_LB / 7 if rate_per_week is not None else None
    return {"intake": intake, "logged": logged, "measured": measured, "n": len(rows)}


def intake(con, day, days):
    """Mean logged kcal over complete days in the `days` ending `day`, NULLs skipped.
    -> (mean or None, n)."""
    kc = [r["kcal"] for d_ in _complete_days(con, day, days) for r in con.execute(
        "SELECT kcal FROM nutrition WHERE date=? AND kcal IS NOT NULL", (d_,))]
    return (st.mean(kc) if kc else None), len(kc)


def protein_target(goals, body_lb):
    """g/day from protein_g_per_lb_target (estebot.toml) at the given body weight, or None.
    The one place the target is computed: the page and readiness both call it."""
    per_lb = (goals or {}).get("protein_g_per_lb_target")
    return per_lb * body_lb if per_lb and body_lb else None


def protein(con, day, body_lb, days=14):
    vals = [r["protein_g"] for d_ in _complete_days(con, day, days) for r in con.execute(
        "SELECT protein_g FROM nutrition WHERE date=? AND protein_g IS NOT NULL", (d_,))]
    if not vals:
        return {"mean": None, "n": 0, "g_per_kg": None}
    m = st.mean(vals)
    return {"mean": m, "n": len(vals),
            "g_per_kg": m / (body_lb * LB_TO_KG) if body_lb else None}


def maintenance(con, day, rate_per_week, days=28):
    """Maintenance kcal/day as the scale sees it: mean logged intake over complete days plus
    the deficit the weight trend implies. Apple's burn has run over 1,000 kcal/day above
    this, so readiness uses this, not Apple, to judge under-eating."""
    mean, n = intake(con, day, days)
    if n < 10 or rate_per_week is None:
        return None
    return mean - rate_per_week * KCAL_PER_LB / 7
