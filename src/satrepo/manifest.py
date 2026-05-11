"""Static publication manifest helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import RepoConfig
from .jsonio import read_json, write_json_atomic

MANIFEST_VERSION = 1


def initial_manifest(config: RepoConfig) -> dict[str, Any]:
    return {
        "version": MANIFEST_VERSION,
        "did": config.did,
        "handle": config.handle,
        "head": None,
        "lastSeq": 0,
        "events": [],
        "blobs": {},
    }


def read_manifest(path: Path) -> dict[str, Any]:
    return read_json(path)


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    write_json_atomic(path, manifest)
