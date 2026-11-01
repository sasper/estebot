"""Configuration. No personal values live in this repository.

Resolution order, first hit wins:
    1. $ESTECOACH_CONFIG
    2. ./estecoach.toml
    3. ~/.config/estecoach/config.toml

TOML rather than YAML so the package keeps a zero-dependency core
(`tomllib` is stdlib from Python 3.11).
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from glob import glob
from pathlib import Path

def env(name: str, default=None):
    """$ESTECOACH_<name>, else $ESTEBOT_<name>: the project was EsteBot until 1 Nov 2026, and
    a secret set under the old name keeps working until it has been set again."""
    for prefix in ("ESTECOACH_", "ESTEBOT_"):
        v = os.environ.get(prefix + name)
        if v is not None:
            return v
    return default


def _either(p: Path) -> Path:
    """A path written before the rename can name estebot.toml after the file became
    estecoach.toml (or the other way round, before it has moved)."""
    if p.is_file():
        return p
    for old, new in (("estebot", "estecoach"), ("estecoach", "estebot")):
        if old in p.name and (q := p.with_name(p.name.replace(old, new))).is_file():
            return q
    return p


_SEARCH = [
    env("CONFIG"),
    "estecoach.toml",
    "estebot.toml",
    "~/.config/estecoach/config.toml",
]


class ConfigError(RuntimeError):
    pass


def _find() -> Path:
    for cand in _SEARCH:
        if not cand:
            continue
        p = _either(Path(cand).expanduser())
        if p.is_file():
            return p
    raise ConfigError(
        "No config found. Copy estecoach.example.toml to estecoach.toml and edit it, "
        "or point $ESTECOACH_CONFIG at one. Searched: "
        + ", ".join(c for c in _SEARCH if c)
    )


@dataclass(frozen=True)
class Config:
    path: Path
    data_dir: Path
    sources: dict = field(default_factory=dict)
    profile: dict = field(default_factory=dict)
    goals: dict = field(default_factory=dict)
    analysis: dict = field(default_factory=dict)
    training: dict = field(default_factory=dict)
    sync: dict = field(default_factory=dict)

    # ---- derived paths. Nothing is ever written inside the code repo. ----
    @property
    def is_demo(self) -> bool:
        """Written by estecoach.demo (.estebot-demo before the rename)."""
        return any((self.data_dir / m).exists() for m in (".estecoach-demo", ".estebot-demo"))

    @property
    def tidy(self) -> Path:
        return self.data_dir / "tidy"

    def data_file(self, name: str) -> Path:
        return self.data_dir / name

    def tidy_file(self, name: str) -> Path:
        return self.tidy / name

    def newest_export(self, source: str) -> Path:
        """Newest file matching a source's glob, by mtime."""
        pattern = self._glob(source)
        hits = sorted(glob(pattern), key=os.path.getmtime)
        if not hits:
            raise ConfigError(f"No {source} export matched {pattern}")
        return Path(hits[-1])

    def all_exports(self, source: str) -> list[Path]:
        """Every file matching a source's glob, oldest first.

        Merging all of them means a narrow re-export (a single month, say)
        cannot silently truncate history.
        """
        pattern = self._glob(source)
        return [Path(p) for p in sorted(glob(pattern), key=os.path.getmtime)]

    def _glob(self, source: str) -> str:
        try:
            return os.path.expanduser(self.sources[source]["export_glob"])
        except KeyError as e:
            raise ConfigError(f"No [sources.{source}].export_glob in {self.path}") from e


def load(path: str | os.PathLike | None = None) -> Config:
    p = _either(Path(path).expanduser()) if path else _find()
    with open(p, "rb") as fh:
        raw = tomllib.load(fh)
    if "data_dir" not in raw:
        raise ConfigError(f"{p} is missing the required `data_dir` key")
    data_dir = Path(raw["data_dir"]).expanduser()
    if not data_dir.is_dir():
        raise ConfigError(f"data_dir does not exist: {data_dir}")
    cfg = Config(
        path=p,
        data_dir=data_dir,
        sources=raw.get("sources", {}),
        profile=raw.get("profile", {}),
        goals=raw.get("goals", {}),
        analysis=raw.get("analysis", {}),
        training=raw.get("training", {}),
        sync=raw.get("sync", {}),
    )
    cfg.tidy.mkdir(parents=True, exist_ok=True)
    return cfg
