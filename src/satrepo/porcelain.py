"""Git-like inspection helpers for local satrepo checkouts."""

from __future__ import annotations

# ruff: noqa: I001
# Arroba 2.0 requires storage to be imported before repo.
from dataclasses import dataclass
from pathlib import Path

from arroba.storage import Action
from arroba.repo import Repo, Write

from .config import RepoConfig, read_config
from .jsonio import read_json
from .keys import read_private_key
from .paths import RepoPaths, discover_root
from .storage_static import StaticStorage
from .worktree import WorktreeRecord, scan_records


@dataclass(frozen=True)
class WorktreeChange:
    action: str
    path: str


@dataclass(frozen=True)
class WorktreeStatus:
    root: Path
    did: str
    handle: str
    pds_url: str
    head: str | None
    rev: str | None
    records: int
    changes: tuple[WorktreeChange, ...]

    @property
    def clean(self) -> bool:
        return not self.changes


@dataclass(frozen=True)
class CommitLogEntry:
    seq: int
    commit: str
    rev: str
    time: str | None
    since: str | None
    ops: tuple[WorktreeChange, ...]


def load_storage(paths: RepoPaths, config: RepoConfig) -> StaticStorage:
    signing_key = read_private_key(config.key_dir / "signing.key")
    rotation_key = read_private_key(config.key_dir / "rotation.key")
    return StaticStorage(
        paths=paths,
        config=config,
        signing_key=signing_key,
        rotation_key=rotation_key,
    )


def worktree_status(root: Path | str | None = None) -> WorktreeStatus:
    paths = discover_root(root)
    config = read_config(paths.config)
    storage = load_storage(paths, config)
    repo = storage.load_repo(config.did)
    records = scan_records(paths.root)
    writes = diff_writes(repo, records)

    head = None
    rev = None
    if repo and repo.head:
        head = str(repo.head.cid)
        rev = repo.head.decoded["rev"]

    return WorktreeStatus(
        root=paths.root,
        did=config.did,
        handle=config.handle,
        pds_url=config.pds_url,
        head=head,
        rev=rev,
        records=len(records),
        changes=tuple(_write_to_change(write) for write in writes),
    )


def commit_log(root: Path | str | None = None, *, limit: int | None = None) -> list[CommitLogEntry]:
    paths = discover_root(root)
    config = read_config(paths.config)
    manifest = read_json(paths.local_manifest)
    entries = []

    for item in reversed(manifest.get("events", [])):
        if item.get("type") != "#commit":
            continue

        event_path = _local_event_path(paths, item["path"])
        event = read_json(event_path)
        entries.append(
            CommitLogEntry(
                seq=event["seq"],
                commit=event["commit"],
                rev=event["rev"],
                time=event.get("time"),
                since=event.get("since"),
                ops=tuple(
                    WorktreeChange(action=op["action"], path=op["path"])
                    for op in event.get("ops", [])
                ),
            )
        )
        if limit is not None and len(entries) >= limit:
            break

    if manifest.get("did") != config.did:
        return []
    return entries


def diff_writes(repo: Repo | None, records: list[WorktreeRecord]) -> list[Write]:
    current = {}
    if repo:
        current = {
            f"{collection}/{rkey}": record
            for collection, by_rkey in repo.get_contents().items()
            for rkey, record in by_rkey.items()
        }
    desired = {record.repo_path: record for record in records}

    writes = []
    for repo_path, record in sorted(desired.items()):
        collection, rkey = repo_path.split("/", 1)
        if repo_path not in current:
            action = Action.CREATE
        elif current[repo_path] != record.record:
            action = Action.UPDATE
        else:
            continue

        writes.append(
            Write(
                action=action,
                collection=collection,
                rkey=rkey,
                record=record.record,
            )
        )

    for repo_path in sorted(set(current) - set(desired)):
        collection, rkey = repo_path.split("/", 1)
        writes.append(
            Write(
                action=Action.DELETE,
                collection=collection,
                rkey=rkey,
                record=None,
            )
        )

    return writes


def _write_to_change(write: Write) -> WorktreeChange:
    return WorktreeChange(action=write.action.name.lower(), path=f"{write.collection}/{write.rkey}")


def _local_event_path(paths: RepoPaths, manifest_path: str) -> Path:
    path = Path(manifest_path)
    return paths.state / "events" / path.name
