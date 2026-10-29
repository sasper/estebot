import importlib

from starlette.requests import Request

DAY = "2026-03-10"


def _person(tmp_path, name, weight, training=""):
    data = tmp_path / name
    data.mkdir()
    cfg = tmp_path / f"{name}.toml"
    cfg.write_text(f'data_dir = "{data}"\n[goals]\ntarget_weight_lb = {weight - 20}\n{training}')
    from estebot.web import people
    p = people.load(str(cfg))
    con = p.connect()
    con.executemany("INSERT INTO weight (date, weight_lb) VALUES (?, ?)",
                    [(f"2026-03-{d:02d}", weight) for d in range(1, 11)])
    con.commit()
    return p


def test_each_person_gets_their_own_database_goals_and_split(con, tmp_path, monkeypatch):
    # The page read one config at import and served it to everyone. A second person must
    # see their own weigh-ins, goal and training split, and a write for one must not touch
    # the other.
    from estebot.web import main, people
    main = importlib.reload(main)
    a = _person(tmp_path, "a", 200)
    b = _person(tmp_path, "b", 150, '[training]\nrotation = ["upper", "lower"]\n'
                '[training.variants]\nupper = {A = ["Barbell Bench Press"], B = ["Barbell Military Press"]}\n'
                'lower = {A = ["Barbell Squat"], B = ["Barbell Deadlift"]}\n'
                '[training.day_types]\nupper = ["Chest", "Back", "Shoulders"]\nlower = ["Legs"]\n')
    assert a.db_path != b.db_path
    assert b.training.rotation == ("upper", "lower") and a.training.rotation == ("push", "pull", "legs")

    req = Request({"type": "http", "method": "GET", "path": "/", "query_string": b"", "headers": []})
    def page(p):
        monkeypatch.setattr(people, "for_request", lambda request: p)
        return main.today(req, DAY, None).body.decode()
    goal = lambda html: html.split('name="goal_weight_lb"')[1].split('value="')[1].split('"')[0]
    assert goal(page(a)) == "180" and goal(page(b)) == "130"

    monkeypatch.setattr(people, "for_request", lambda request: b)
    main.set_goal(None, 125.0, "")
    get = lambda p: p.connect().execute("SELECT value FROM setting WHERE key='goal_weight_lb'").fetchone()
    assert get(b)["value"] == "125.0" and get(a) is None
