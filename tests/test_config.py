import pytest

from estebot import config


def write(tmp_path, body, data_dir):
    cfg = tmp_path / "estebot.toml"
    cfg.write_text(f'data_dir = "{data_dir}"\n' + body)
    return cfg


def test_loads_and_resolves_paths(tmp_path):
    data = tmp_path / "data"; data.mkdir()
    cfg = config.load(write(tmp_path, "", data))
    assert cfg.data_dir == data
    assert cfg.tidy == data / "tidy"
    assert cfg.tidy.is_dir()                      # created on load
    assert cfg.tidy_file("sets.csv").name == "sets.csv"


def test_missing_data_dir_is_an_error(tmp_path):
    cfg = write(tmp_path, "", tmp_path / "nope")
    with pytest.raises(config.ConfigError, match="data_dir does not exist"):
        config.load(cfg)


def test_missing_data_dir_key_is_an_error(tmp_path):
    cfg = tmp_path / "estebot.toml"
    cfg.write_text("[profile]\nheight_in = 70\n")
    with pytest.raises(config.ConfigError, match="missing the required"):
        config.load(cfg)


def test_newest_export_wins(tmp_path):
    import os, time
    data = tmp_path / "data"; data.mkdir()
    exports = tmp_path / "dl"; exports.mkdir()
    old, new = exports / "a_1.csv", exports / "a_2.csv"
    old.write_text("x"); new.write_text("y")
    os.utime(old, (time.time() - 100,) * 2)
    cfg = config.load(write(
        tmp_path, f'[sources.jefit]\nexport_glob = "{exports}/a_*.csv"\n', data))
    assert cfg.newest_export("jefit") == new
    assert [p.name for p in cfg.all_exports("jefit")] == ["a_1.csv", "a_2.csv"]


def test_unknown_source_names_the_problem(tmp_path):
    data = tmp_path / "data"; data.mkdir()
    cfg = config.load(write(tmp_path, "", data))
    with pytest.raises(config.ConfigError, match=r"sources.jefit"):
        cfg.newest_export("jefit")
