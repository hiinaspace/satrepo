"""Local did:plc management commands."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, replace
from pathlib import Path

from .config import read_config, write_config
from .did_plc import build_genesis_operation, normalize_pds_url
from .errors import SatRepoError
from .jsonio import read_json, write_json_atomic
from .keys import private_key_pem, read_private_key, write_private_key
from .manifest import initial_manifest, write_manifest
from .paths import RepoPaths, discover_root, key_dir_for_did
from .publish import publish, publish_static


@dataclass(frozen=True)
class PlcUpdateResult:
    old_did: str
    new_did: str
    pds_url: str
    key_dir: Path
    published: bool

    @property
    def did_changed(self) -> bool:
        return self.old_did != self.new_did


def plc_summary(root: Path | str | None = None) -> dict:
    paths = discover_root(root)
    config = read_config(paths.config)
    did_doc = read_json(paths.state / "did.json")

    endpoint = None
    for service in did_doc.get("service", []):
        if service.get("id") == "#atproto_pds":
            endpoint = service.get("serviceEndpoint")
            break

    return {
        "did": config.did,
        "handle": config.handle,
        "pdsUrl": config.pds_url,
        "serviceEndpoint": endpoint,
        "keyDir": config.key_dir,
        "plcRegistered": config.plc_registered,
    }


def update_pds_url(
    root: Path | str | None,
    *,
    pds_url: str,
    publish_after: bool = True,
) -> PlcUpdateResult:
    """Update the local PLC service endpoint.

    Registered DID updates need PLC directory interaction. For now this command
    handles the unregistered local case by rebuilding the genesis operation,
    which changes the DID and requires republishing repo artifacts.
    """

    paths = discover_root(root)
    config = read_config(paths.config)
    pds_url = normalize_pds_url(pds_url)

    if config.plc_registered:
        raise SatRepoError("registered PLC updates are not implemented yet")

    signing_key = read_private_key(config.key_dir / "signing.key")
    rotation_key = read_private_key(config.key_dir / "rotation.key")
    old_did = config.did
    operation = read_json(paths.state / "plc_operation.json")

    if _operation_pds_url(operation) == pds_url:
        key_dir = key_dir_for_did(config.did)
        _ensure_private_key(key_dir / "signing.key", signing_key)
        _ensure_private_key(key_dir / "rotation.key", rotation_key)
        if config.pds_url != pds_url or config.key_dir != key_dir:
            write_config(paths.config, replace(config, pds_url=pds_url, key_dir=key_dir))
        if publish_after:
            publish(paths.root)
        return PlcUpdateResult(
            old_did=old_did,
            new_did=config.did,
            pds_url=pds_url,
            key_dir=key_dir,
            published=publish_after,
        )

    genesis = build_genesis_operation(
        handle=config.handle,
        pds_url=pds_url,
        signing_key=signing_key,
        rotation_key=rotation_key,
    )
    key_dir = key_dir_for_did(genesis.did)
    _ensure_private_key(key_dir / "signing.key", signing_key)
    _ensure_private_key(key_dir / "rotation.key", rotation_key)

    new_config = replace(
        config,
        did=genesis.did,
        pds_url=pds_url,
        key_dir=key_dir,
        plc_registered=False,
    )

    _reset_generated_repo(paths)
    write_config(paths.config, new_config)
    write_json_atomic(paths.state / "plc_operation.json", genesis.operation)
    write_json_atomic(paths.state / "did.json", genesis.did_doc)
    _write_ref(paths.state / "refs" / "did", genesis.did)
    _write_ref(paths.state / "refs" / "handle", config.handle)
    _write_ref(paths.state / "refs" / "last_seq", "0")

    manifest = initial_manifest(new_config)
    write_manifest(paths.local_manifest, manifest)
    publish_static(paths, new_config, manifest)

    if publish_after:
        publish(paths.root)

    return PlcUpdateResult(
        old_did=old_did,
        new_did=genesis.did,
        pds_url=pds_url,
        key_dir=key_dir,
        published=publish_after,
    )


def _reset_generated_repo(paths: RepoPaths) -> None:
    for directory in (
        paths.state / "events",
        paths.state / "commits",
        paths.state / "blocks",
        paths.state / "blobs",
        paths.site / "repo" / "events",
        paths.site / "repo" / "commits",
        paths.site / "repo" / "blocks",
        paths.site / "repo" / "blobs",
    ):
        _clear_dir(directory)

    for file_path in (
        paths.state / "block_index.json",
        paths.state / "snapshot.car",
        paths.state / "refs" / "head",
        paths.state / "refs" / "rev",
        paths.site / "repo" / "snapshot.car",
        paths.site / "repo" / "refs" / "head",
        paths.site / "repo" / "refs" / "rev",
    ):
        file_path.unlink(missing_ok=True)


def _clear_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _ensure_private_key(path: Path, key) -> None:
    if not path.exists():
        write_private_key(path, key)
        return

    existing = read_private_key(path)
    if private_key_pem(existing) != private_key_pem(key):
        raise SatRepoError(f"refusing to overwrite different key at {path}")
    path.chmod(0o600)


def _operation_pds_url(operation: dict) -> str | None:
    try:
        return operation["services"]["atproto_pds"]["endpoint"]
    except KeyError:
        return None


def _write_ref(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{value}\n", encoding="utf-8")
