"""Configuration. No personal values live in this repository.

Resolution order, first hit wins:
    1. $ESTEBOT_CONFIG
    2. ./estebot.toml
    3. ~/.config/estebot/config.toml

TOML rather than YAML so the package keeps a zero-dependency core
(`tomllib` is stdlib from Python 3.11).
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from glob import glob
from pathlib import Path

_SEARCH = [
    os.environ.get("ESTEBOT_CONFIG"),
    "estebot.toml",
    "~/.config/estebot/config.toml",
]


class ConfigError(RuntimeError):
    pass


def _find() -> Path:
    for cand in _SEARCH:
        if not cand:
            continue
        p = Path(cand).expanduser()
        if p.is_file():
            return p
    raise ConfigError(
        "No config found. Copy estebot.example.toml to estebot.toml and edit it, "
        "or point $ESTEBOT_CONFIG at one. Searched: "
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

    # ---- derived paths. Nothing is ever written inside the code repo. ----
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
    p = Path(path).expanduser() if path else _find()
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
    )
    cfg.tidy.mkdir(parents=True, exist_ok=True)
    return cfg
