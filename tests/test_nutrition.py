from estebot.analysis import nutrition


def _day(con, d, kcal, protein, complete=1):
    con.execute("INSERT INTO nutrition (date, kcal, protein_g, complete) VALUES (?,?,?,?)",
                (d, kcal, protein, complete))


def test_a_meal_without_micronutrient_data_is_not_a_deficiency(con):
    # A day where only 60% of the calories came from foods reporting magnesium, so its
    # 92 mg total understated the day. Such days are left out, not averaged in.
    for i in range(1, 7):
        d = f"2026-10-0{i}"
        _day(con, d, 2500, 160)
        con.execute("INSERT INTO nutrient VALUES (?,?,?,?)", (d, "Magnesium (mg)", 430.0, 100.0))
    _day(con, "2026-10-07", 2500, 160)
    con.execute("INSERT INTO nutrient VALUES (?,?,?,?)", ("2026-10-07", "Magnesium (mg)", 92.0, 60.0))
    gaps, _, _ = nutrition.micronutrients(con, "2026-10-07")
    assert "Magnesium" not in [g["nutrient"] for g in gaps]


def test_an_unfinished_day_counts_toward_nothing(con):
    # A half-logged day reads as a deficit and a deficiency at once.
    _day(con, "2026-10-14", 2600, 170)
    _day(con, "2026-10-15", 900, 40, complete=0)
    con.execute("INSERT INTO daily (date, active_kcal, basal_kcal) VALUES ('2026-10-14', 1200, 2600)")
    con.execute("INSERT INTO daily (date, active_kcal, basal_kcal) VALUES ('2026-10-15', 300, 2600)")
    e = nutrition.energy_balance(con, "2026-10-15", rate_per_week=-1.5)
    assert e["n"] == 1 and e["intake"] == 2600 and e["logged"] == 1200
    assert e["measured"] == 750          # 1.5 lb/week x 3500 / 7
    assert nutrition.protein(con, "2026-10-15", 200.0)["mean"] == 170


def _mg_days(con, food_mg, n=6):
    for i in range(1, n + 1):
        d = f"2026-10-0{i}"
        _day(con, d, 2500, 160)
        con.execute("INSERT INTO nutrient VALUES (?,?,?,?)", (d, "Magnesium (mg)", food_mg, 100.0))
        con.execute("INSERT INTO nutrient VALUES (?,?,?,?)", (d, "Zinc (mg)", 10.0, 100.0))


def test_the_daily_stack_closes_a_food_only_gap(con):
    # Food alone put magnesium far under the reference on the page, while a daily
    # supplement stack that was never logged in Cronometer covered the difference.
    _mg_days(con, 165.0)
    stack = [{"name": "multi", "servings_per_day": 1, "since": "",
              "per_serving": {"Magnesium (mg)": 120, "Zinc (mg)": 12}},
             {"name": "mag-zinc", "servings_per_day": 1, "since": "2026-05-11",
              "per_serving": {"Magnesium (mg)": 60, "Zinc (mg)": 9}}]
    gaps, near, _ = nutrition.micronutrients(
        con, "2026-10-06", nutrition.supplement_amounts(stack, "2026-10-06"))
    mg = next(g for g in gaps if g["nutrient"] == "Magnesium")
    assert (mg["food"], mg["supplements"], mg["mean"]) == (165.0, 180, 345.0)
    # Zinc: 21 mg from pills + 10 from food = 31 of 40 -> flagged near the limit.
    zinc = next(g for g in near if g["nutrient"] == "Zinc")
    assert zinc["amount"] == 31.0 and zinc["basis"] == "total"


def test_a_product_counts_only_inside_its_dates(con):
    stack = [{"servings_per_day": 1, "since": "2026-05-11", "per_serving": {"Zinc (mg)": 9}}]
    assert nutrition.supplement_amounts(stack, "2026-05-10") == {}
    assert nutrition.supplement_amounts(stack, "2026-05-11") == {"Zinc (mg)": 9}


def test_the_magnesium_limit_is_on_supplements_alone(con):
    # The magnesium upper limit (350 mg) covers supplemental magnesium only, not food.
    _mg_days(con, 400.0)
    _, near, _ = nutrition.micronutrients(con, "2026-10-06", {"Magnesium (mg)": 180})
    mg = next(g for g in near if g["nutrient"] == "Magnesium")
    assert mg["amount"] == 180 and mg["basis"] == "supplements"
