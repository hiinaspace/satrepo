"""Filesystem paths for a local satrepo checkout."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from .errors import SatRepoError


STATE_DIR = ".satrepo"
WORKTREE_DIR = "worktree"
SITE_DIR = "site"
CONFIG_FILE = "config.json"


@dataclass(frozen=True)
class RepoPaths:
    """Resolved paths for one local checkout."""

    root: Path

    @property
    def worktree(self) -> Path:
        return self.root / WORKTREE_DIR

    @property
    def state(self) -> Path:
        return self.root / STATE_DIR

    @property
    def site(self) -> Path:
        return self.root / SITE_DIR

    @property
    def config(self) -> Path:
        return self.state / CONFIG_FILE

    @property
    def site_manifest(self) -> Path:
        return self.site / "repo" / "manifest.json"

    @property
    def local_manifest(self) -> Path:
        return self.state / "manifest.json"


def repo_paths(root: Path | str) -> RepoPaths:
    return RepoPaths(root=Path(root).expanduser().resolve())


def config_home() -> Path:
    """Return the satrepo config home, respecting XDG_CONFIG_HOME."""

    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base).expanduser() / "satrepo"
    return Path.home() / ".config" / "satrepo"


def key_dir_for_did(did: str) -> Path:
    return config_home() / did


def discover_root(start: Path | str | None = None) -> RepoPaths:
    """Find the nearest parent with a .satrepo/config.json."""

    current = Path(start or ".").expanduser().resolve()
    if current.is_file():
        current = current.parent

    for candidate in (current, *current.parents):
        paths = repo_paths(candidate)
        if paths.config.exists():
            return paths

    raise SatRepoError(f"no {STATE_DIR}/{CONFIG_FILE} found from {current}")
