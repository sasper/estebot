import subprocess, sys


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_a_server_sync_pulls_runs_the_feed_steps_and_pushes(tmp_path, monkeypatch):
    # On a server the sync owns the data repo's remote: it pulls first, commits the tidy
    # tables and pushes, so the Mac only ever pulls. After an upload it runs only the steps
    # the feed affects, and never the iCloud notes step. Phone uploads are ~30 MB a day, so
    # they stay out of git and old ones are deleted once the tidy tables hold them.
    from estebot import config, daily
    remote, data = tmp_path / "remote.git", tmp_path / "data"
    _git("init", "-q", "--bare", "-b", "main", str(remote), cwd=tmp_path)
    _git("clone", "-q", str(remote), str(data), cwd=tmp_path)
    for k, v in (("user.email", "t@example.com"), ("user.name", "t")):
        _git("config", k, v, cwd=data)
    (data / "README").write_text("data\n")
    _git("add", "README", cwd=data); _git("commit", "-qm", "start", cwd=data); _git("push", "-q", "origin", "HEAD", cwd=data)

    cfg = tmp_path / "estebot.toml"
    cfg.write_text(f'data_dir = "{data}"\n[sync]\npull = true\npush = true\nkeep_feed_days = 3\n'
                   f'[sources.health_auto_export]\nmetrics_glob = "{data}/raw/hae/metrics/*.json"\n')
    monkeypatch.setenv("ESTEBOT_CONFIG", str(cfg))
    monkeypatch.setattr(config, "_SEARCH", [str(cfg)])
    ran = []
    def step(name):                      # each fake step writes a tidy file and records itself
        return [ "-c", f"open(r'{data}/tidy/{name}.csv','w').write('x'); print('{name}')"]
    monkeypatch.setattr(daily, "STEPS", [(n, step(n.replace(" ", "_")), True) for n in
                                         ("hevy", "notes", "apple health feed", "lifting tables", "web cache")])
    from datetime import date, timedelta
    feed = data / "raw" / "hae" / "metrics"
    feed.mkdir(parents=True)
    old, new = (feed / f"HealthAutoExport-{(date.today() - timedelta(days=d)).isoformat()}.json"
                for d in (5, 1))
    old.write_text("{}"); new.write_text("{}")

    (data / daily.PENDING).touch()                                # as an upload does
    assert daily.run(feed_only=True) == 0
    tidy = sorted(p.name for p in (data / "tidy").iterdir())
    # Hevy too: the morning's first upload brings in yesterday afternoon's workout.
    assert tidy == ["apple_health_feed.csv", "hevy.csv", "lifting_tables.csv", "web_cache.csv"]
    pushed = subprocess.run(["git", "--git-dir", str(remote), "show", "--stat", "--format=%s", "main"],
                            capture_output=True, text=True).stdout
    assert pushed.startswith("Feed upload") and "tidy/web_cache.csv" in pushed
    assert "HealthAutoExport" not in pushed                       # never committed
    assert new.exists() and not old.exists()                      # older than 3 days: pruned

    # A full sync runs hevy as well, still not notes (no source), and commits the Cronometer pull
    # the nutrition table was rebuilt from, so a clone rebuilds the same table.
    (data / "raw" / "cronometer").mkdir(parents=True)
    (data / "raw" / "cronometer" / "dailysummary-2026-03-10T120000.csv").write_text("x")
    assert daily.run() == 0
    assert (data / "tidy" / "hevy.csv").exists() and not (data / "tidy" / "notes.csv").exists()
    pushed = subprocess.run(["git", "--git-dir", str(remote), "show", "--stat", "--format=%s", "main"],
                            capture_output=True, text=True).stdout
    assert "raw/cronometer/dailysummary-2026-03-10T120000.csv" in pushed


def test_a_burst_of_uploads_is_read_once_per_run_not_once_per_upload(tmp_path, monkeypatch, capsys):
    # Each upload starts a feed run and they queue on the sync lock. The run that gets the
    # lock reads every file there; the runs queued behind it while it worked find nothing
    # new and stop, so three uploads during one run cost one more run, not three.
    from estebot import config, daily
    data = tmp_path / "data"; data.mkdir()
    cfg = tmp_path / "estebot.toml"
    cfg.write_text(f'data_dir = "{data}"\n')
    monkeypatch.setenv("ESTEBOT_CONFIG", str(cfg))
    monkeypatch.setattr(config, "_SEARCH", [str(cfg)])
    runs = []
    monkeypatch.setattr(daily, "_run", lambda cfg, feed_only: runs.append(feed_only) or 0)
    (data / daily.PENDING).touch()
    assert [daily.run(feed_only=True) for _ in range(3)] == [0, 0, 0]
    assert runs == [True] and not (data / daily.PENDING).exists()
    assert "already read by the run before" in capsys.readouterr().out
    (data / daily.PENDING).touch()                                # an upload, then the noon sync
    daily.run()
    assert runs == [True, False] and not (data / daily.PENDING).exists()


def test_a_feed_run_that_fails_leaves_the_upload_for_the_next_run(tmp_path, monkeypatch):
    # The run that took the marker failed (a pull on a network blip); the runs queued behind
    # it skipped as "already read", and the upload waited for the noon sync.
    from estebot import config, daily
    data = tmp_path / "data"; data.mkdir()
    cfg = tmp_path / "estebot.toml"
    cfg.write_text(f'data_dir = "{data}"\n')
    monkeypatch.setenv("ESTEBOT_CONFIG", str(cfg))
    monkeypatch.setattr(config, "_SEARCH", [str(cfg)])
    results = [1, 0]
    runs = []
    monkeypatch.setattr(daily, "_run", lambda cfg, feed_only: runs.append(feed_only) or results.pop(0))
    (data / daily.PENDING).touch()
    assert daily.run(feed_only=True) == 1 and (data / daily.PENDING).exists()
    assert daily.run(feed_only=True) == 0 and not (data / daily.PENDING).exists()
    assert runs == [True, True]

    def boom(cfg, feed_only):
        raise RuntimeError("step crashed")
    monkeypatch.setattr(daily, "_run", boom)
    (data / daily.PENDING).touch()
    try:
        daily.run(feed_only=True)
    except RuntimeError:
        pass
    assert (data / daily.PENDING).exists()


def test_a_hevy_outage_doesnt_hold_up_an_uploads_sleep(tmp_path, monkeypatch):
    # The feed run pulls Hevy first, so the morning call has yesterday's workout. If Hevy is
    # down, the night's sleep still goes through; the full sync still stops on it.
    from estebot import config, daily
    data = tmp_path / "data"; data.mkdir()
    cfg = tmp_path / "estebot.toml"
    cfg.write_text(f'data_dir = "{data}"\n')
    monkeypatch.setenv("ESTEBOT_CONFIG", str(cfg))
    monkeypatch.setattr(config, "_SEARCH", [str(cfg)])
    ok = lambda n: ["-c", f"open(r'{data}/{n}','w')"]
    monkeypatch.setattr(daily, "STEPS", [("hevy", ["-c", "raise SystemExit(1)"], True),
                                         ("apple health feed", ok("feed"), True),
                                         ("nutrition", ok("nutrition"), True)])
    subprocess.run(["git", "init", "-q", str(data)], check=True)
    (data / daily.PENDING).touch()
    assert daily.run(feed_only=True) == 0
    assert (data / "feed").exists() and not (data / "nutrition").exists()
    (data / "feed").unlink()
    assert daily.run() == 1 and not (data / "feed").exists()
