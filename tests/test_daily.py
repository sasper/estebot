import subprocess, sys


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_a_server_sync_pulls_runs_the_feed_steps_and_pushes(tmp_path, monkeypatch):
    # On a server the sync owns the data repo's remote: it pulls first, commits the tidy
    # tables and the phone's uploads, and pushes, so the Mac only ever pulls. After an
    # upload it runs only the steps the feed affects, and never the iCloud notes step.
    from estebot import config, daily
    remote, data = tmp_path / "remote.git", tmp_path / "data"
    _git("init", "-q", "--bare", "-b", "main", str(remote), cwd=tmp_path)
    _git("clone", "-q", str(remote), str(data), cwd=tmp_path)
    for k, v in (("user.email", "t@example.com"), ("user.name", "t")):
        _git("config", k, v, cwd=data)
    (data / "README").write_text("data\n")
    _git("add", "README", cwd=data); _git("commit", "-qm", "start", cwd=data); _git("push", "-q", "origin", "HEAD", cwd=data)

    cfg = tmp_path / "estebot.toml"
    cfg.write_text(f'data_dir = "{data}"\n[sync]\npull = true\npush = true\n'
                   f'[sources.health_auto_export]\nmetrics_glob = "{data}/raw/hae/metrics/*.json"\n')
    monkeypatch.setenv("ESTEBOT_CONFIG", str(cfg))
    monkeypatch.setattr(config, "_SEARCH", [str(cfg)])
    ran = []
    def step(name):                      # each fake step writes a tidy file and records itself
        return [ "-c", f"open(r'{data}/tidy/{name}.csv','w').write('x'); print('{name}')"]
    monkeypatch.setattr(daily, "STEPS", [(n, step(n.replace(" ", "_")), True) for n in
                                         ("hevy", "notes", "apple health feed", "lifting tables", "web cache")])
    (data / "raw" / "hae" / "metrics").mkdir(parents=True)
    (data / "raw" / "hae" / "metrics" / "HealthAutoExport-2026-03-10.json").write_text("{}")

    assert daily.run(feed_only=True) == 0
    tidy = sorted(p.name for p in (data / "tidy").iterdir())
    assert tidy == ["apple_health_feed.csv", "lifting_tables.csv", "web_cache.csv"]
    pushed = subprocess.run(["git", "--git-dir", str(remote), "show", "--stat", "--format=%s", "main"],
                            capture_output=True, text=True).stdout
    assert pushed.startswith("Feed upload") and "HealthAutoExport-2026-03-10.json" in pushed

    assert daily.run() == 0            # full sync: hevy runs now, notes still doesn't (no source)
    assert (data / "tidy" / "hevy.csv").exists() and not (data / "tidy" / "notes.csv").exists()
