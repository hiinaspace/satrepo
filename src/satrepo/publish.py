"""Publish worktree records into static repo artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil

from arroba.storage import Action
from arroba.repo import Repo, Write

from .config import RepoConfig, read_config
from .jsonio import read_json
from .keys import read_private_key
from .manifest import write_manifest
from .paths import RepoPaths, discover_root
from .storage_static import StaticStorage
from .worktree import WorktreeRecord, scan_records


@dataclass(frozen=True)
class PublishResult:
    did: str
    head: str | None
    rev: str | None
    last_seq: int
    writes: int
    events: int


def publish(root: Path | str | None = None) -> PublishResult:
    paths = discover_root(root)
    config = read_config(paths.config)
    storage = _load_storage(paths, config)

    repo = storage.load_repo(config.did)
    if repo is None:
        repo = Repo.create(
            storage,
            config.did,
            signing_key=storage.signing_key,
            rotation_key=storage.rotation_key,
            handle=config.handle,
        )

    writes = _diff_records(repo, scan_records(paths.root))
    if writes:
        storage.commit(repo, writes)

    storage.write_snapshot()
    manifest = rebuild_manifest(paths, config)
    publish_static(paths, config, manifest)

    head = manifest["head"] or {}
    return PublishResult(
        did=config.did,
        head=head.get("cid"),
        rev=head.get("rev"),
        last_seq=manifest["lastSeq"],
        writes=len(writes),
        events=len(manifest["events"]),
    )


def rebuild_manifest(paths: RepoPaths, config: RepoConfig) -> dict:
    event_entries = []
    for event_path in sorted((paths.state / "events").glob("*.json")):
        event = read_json(event_path)
        entry = {
            "seq": event["seq"],
            "type": event["type"],
            "path": f"repo/events/{event_path.name}",
        }
        if event["type"] == "#commit":
            entry["rev"] = event["rev"]
            entry["commit"] = event["commit"]
        event_entries.append(entry)

    head = _read_ref(paths.state / "refs" / "head")
    rev = _read_ref(paths.state / "refs" / "rev")
    manifest = {
        "version": 1,
        "did": config.did,
        "handle": config.handle,
        "head": {"cid": head, "rev": rev} if head and rev else None,
        "lastSeq": int(_read_ref(paths.state / "refs" / "last_seq") or "0"),
        "events": event_entries,
        "blobs": {},
    }
    write_manifest(paths.local_manifest, manifest)
    return manifest


def publish_static(paths: RepoPaths, config: RepoConfig, manifest: dict) -> None:
    """Copy bare repo artifacts into site/, writing manifest last."""

    for subdir in (
        ".well-known",
        "repo/refs",
        "repo/events",
        "repo/commits",
        "repo/blocks",
        "repo/blobs",
    ):
        (paths.site / subdir).mkdir(parents=True, exist_ok=True)

    _write_text(paths.site / ".well-known" / "atproto-did", config.did)
    _copy_if_exists(paths.state / "did.json", paths.site / "did.json")

    for ref_name in ("head", "rev", "last_seq"):
        _copy_if_exists(paths.state / "refs" / ref_name, paths.site / "repo" / "refs" / ref_name)

    _copy_tree_files(paths.state / "events", paths.site / "repo" / "events")
    _copy_tree_files(paths.state / "commits", paths.site / "repo" / "commits")
    _copy_tree_files(paths.state / "blocks", paths.site / "repo" / "blocks")
    _copy_tree_files(paths.state / "blobs", paths.site / "repo" / "blobs")
    _copy_if_exists(paths.state / "snapshot.car", paths.site / "repo" / "snapshot.car")

    write_manifest(paths.site_manifest, manifest)


def _load_storage(paths: RepoPaths, config: RepoConfig) -> StaticStorage:
    signing_key = read_private_key(config.key_dir / "signing.key")
    rotation_key = read_private_key(config.key_dir / "rotation.key")
    return StaticStorage(
        paths=paths,
        config=config,
        signing_key=signing_key,
        rotation_key=rotation_key,
    )


def _diff_records(repo: Repo, records: list[WorktreeRecord]) -> list[Write]:
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
            )
        )

    return writes


def _copy_tree_files(src: Path, dest: Path) -> None:
    if not src.exists():
        return
    for path in src.iterdir():
        if path.is_file():
            shutil.copy2(path, dest / path.name)


def _copy_if_exists(src: Path, dest: Path) -> None:
    if src.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{value}\n", encoding="utf-8")


def _read_ref(path: Path) -> str | None:
    if not path.exists():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None
