"""Repository initialization."""

from __future__ import annotations

from pathlib import Path

from .config import RepoConfig, write_config
from .did_plc import build_genesis_operation
from .errors import SatRepoError
from .jsonio import write_json_atomic
from .keys import generate_key, write_private_key
from .manifest import initial_manifest, write_manifest
from .paths import RepoPaths, key_dir_for_did, repo_paths


STATE_SUBDIRS = (
    "refs",
    "events",
    "commits",
    "blocks",
    "blobs",
)

SITE_SUBDIRS = (
    ".well-known",
    "repo/refs",
    "repo/events",
    "repo/commits",
    "repo/blocks",
    "repo/blobs",
)

WORKTREE_SUBDIRS = (
    "app.bsky.actor.profile",
    "app.bsky.feed.post",
    "blobs",
)


def init_repo(
    root: Path | str,
    *,
    handle: str,
    pds_url: str,
    force: bool = False,
) -> RepoConfig:
    paths = repo_paths(root)
    if paths.config.exists() and not force:
        raise SatRepoError(f"{paths.root} already has a .satrepo/config.json")

    signing_key = generate_key()
    rotation_key = generate_key()
    genesis = build_genesis_operation(
        handle=handle,
        pds_url=pds_url,
        signing_key=signing_key,
        rotation_key=rotation_key,
    )

    key_dir = key_dir_for_did(genesis.did)
    config = RepoConfig.create(
        handle=handle,
        did=genesis.did,
        pds_url=pds_url,
        key_dir=key_dir,
        plc_registered=False,
    )

    _create_layout(paths)
    write_private_key(key_dir / "signing.key", signing_key, overwrite=force)
    write_private_key(key_dir / "rotation.key", rotation_key, overwrite=force)

    write_config(paths.config, config)
    write_json_atomic(paths.state / "plc_operation.json", genesis.operation)
    write_json_atomic(paths.state / "did.json", genesis.did_doc)
    _write_ref(paths.state / "refs" / "did", genesis.did)
    _write_ref(paths.state / "refs" / "handle", handle)
    _write_ref(paths.state / "refs" / "last_seq", "0")

    manifest = initial_manifest(config)
    write_manifest(paths.local_manifest, manifest)
    _publish_static_baseline(paths, config, genesis.did_doc, manifest)
    return config


def _create_layout(paths: RepoPaths) -> None:
    paths.root.mkdir(parents=True, exist_ok=True)
    for subdir in WORKTREE_SUBDIRS:
        (paths.worktree / subdir).mkdir(parents=True, exist_ok=True)
    for subdir in STATE_SUBDIRS:
        (paths.state / subdir).mkdir(parents=True, exist_ok=True)
    for subdir in SITE_SUBDIRS:
        (paths.site / subdir).mkdir(parents=True, exist_ok=True)


def _publish_static_baseline(
    paths: RepoPaths,
    config: RepoConfig,
    did_doc: dict,
    manifest: dict,
) -> None:
    _write_ref(paths.site / ".well-known" / "atproto-did", config.did)
    write_json_atomic(paths.site / "did.json", did_doc)
    _write_ref(paths.site / "repo" / "refs" / "last_seq", "0")
    write_manifest(paths.site_manifest, manifest)


def _write_ref(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{value}\n", encoding="utf-8")
