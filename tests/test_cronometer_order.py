import importlib, os


def test_newest_export_wins_by_the_time_in_its_name_not_the_file_clock(con, tmp_path, monkeypatch):
    # "Newest file wins per day" went by modification time. A git clone gives every file
    # the clone's time, so on a fresh server the winner was arbitrary: a partial-day manual
    # export beat the complete pull made that evening.
    import sys
    monkeypatch.setattr(sys, "argv", ["cronometer"])   # the module reads its arguments on import
    cfg = tmp_path / "estebot.toml"                     # ...and its config
    cfg.write_text(cfg.read_text() + f'[sources.cronometer]\nexport_glob = "{tmp_path}/none/*.csv"\n')
    from estebot.parsers import cronometer
    cronometer = importlib.reload(cronometer)
    morning = tmp_path / "dailysummary-2026-10-14T090000.csv"
    evening = tmp_path / "dailysummary-2026-10-14T205100.csv"
    manual = tmp_path / "dailysummary(3).csv"           # a fresh download: no time in the name
    for f in (morning, evening, manual):
        f.write_text("x")
    os.utime(evening, (1, 1))                           # clocks that say the opposite
    os.utime(manual, (1_800_000_000, 1_800_000_000))    # 2027
    order = sorted([evening, manual, morning], key=cronometer.export_time)
    assert order == [morning, evening, manual]
