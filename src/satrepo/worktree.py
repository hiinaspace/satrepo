"""Scan human-editable ATProto record files."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Iterable

from .errors import SatRepoError
from .paths import WORKTREE_DIR
from .rkeys import suggested_rkey, validate_rkey


@dataclass(frozen=True)
class WorktreeRecord:
    collection: str
    rkey: str
    path: Path
    record: dict

    @property
    def repo_path(self) -> str:
        return f"{self.collection}/{self.rkey}"


def scan_records(root: Path | str) -> list[WorktreeRecord]:
    """Return all records in worktree/<collection>/<rkey>.json."""

    root = Path(root)
    worktree = root / WORKTREE_DIR
    if not worktree.exists():
        return []

    records: list[WorktreeRecord] = []
    for collection_dir in _collection_dirs(worktree):
        for record_path in sorted(collection_dir.glob("*.json")):
            with record_path.open(encoding="utf-8") as file:
                try:
                    record = json.load(file)
                except json.JSONDecodeError as exc:
                    raise SatRepoError(f"{record_path} is not valid JSON: {exc}") from exc

            if not isinstance(record, dict):
                raise SatRepoError(f"{record_path} must contain a JSON object")

            collection = collection_dir.name
            rkey = record_path.stem
            try:
                validate_rkey(collection, rkey)
            except SatRepoError as exc:
                suggestion = suggested_rkey(collection)
                if suggestion:
                    raise SatRepoError(
                        f"{record_path} uses invalid rkey {rkey!r}: {exc}. "
                        f"Rename it to something like {suggestion}.json"
                    ) from exc
                raise SatRepoError(f"{record_path} uses invalid rkey {rkey!r}: {exc}") from exc

            records.append(
                WorktreeRecord(
                    collection=collection,
                    rkey=rkey,
                    path=record_path,
                    record=record,
                )
            )

    return records


def _collection_dirs(worktree: Path) -> Iterable[Path]:
    for path in sorted(worktree.iterdir()):
        if not path.is_dir() or path.name.startswith(".") or path.name == "blobs":
            continue
        if "." not in path.name:
            raise SatRepoError(f"{path} is not an ATProto collection directory")
        yield path
