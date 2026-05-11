"""Local consistency checks for generated satrepo artifacts."""

from __future__ import annotations

# ruff: noqa: I001
# Arroba 2.0 requires storage to be imported before repo.
from dataclasses import dataclass, field
from pathlib import Path

from arroba import did as arroba_did
from arroba import util
from arroba.storage import Block, MemoryStorage
from arroba.repo import Repo
from carbox.car import read_car

from .config import read_config
from .errors import SatRepoError
from .jsonio import read_json
from .keys import read_private_key
from .manifest import read_manifest
from .paths import discover_root
from .rkeys import validate_rkey
from .storage_static import StaticStorage


@dataclass
class VerificationResult:
    did: str
    head: str | None = None
    rev: str | None = None
    event_count: int = 0
    record_count: int = 0
    snapshot_block_count: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def verify_repo(root: Path | str | None = None) -> VerificationResult:
    paths = discover_root(root)
    config = read_config(paths.config)
    result = VerificationResult(did=config.did)

    if not config.pds_url.startswith("https://"):
        result.warnings.append("pdsUrl is not an absolute https URL")

    did_doc = read_json(paths.state / "did.json")
    if did_doc.get("id") != config.did:
        result.errors.append(".satrepo/did.json id does not match config DID")

    signing_key = arroba_did.get_signing_key(did_doc)
    if not signing_key:
        result.errors.append("DID document has no atproto signing key")

    manifest = _load_manifest_pair(paths, result)
    storage = StaticStorage(
        paths=paths,
        config=config,
        signing_key=read_private_key(config.key_dir / "signing.key"),
        rotation_key=read_private_key(config.key_dir / "rotation.key"),
    )
    repo = storage.load_repo(config.did)
    if not repo or not repo.head:
        result.errors.append("repo cannot be loaded from .satrepo blocks")
        return result

    head_block = repo.head
    result.head = str(head_block.cid)
    result.rev = head_block.decoded["rev"]
    result.record_count = sum(len(by_rkey) for by_rkey in repo.get_contents().values())

    _verify_head(result, repo, manifest, signing_key)
    _verify_snapshot(result, paths, repo)
    _verify_events(result, paths, manifest, signing_key, storage)
    return result


def _load_manifest_pair(paths, result: VerificationResult) -> dict:
    local_manifest = read_manifest(paths.local_manifest)
    site_manifest = read_manifest(paths.site_manifest)
    if local_manifest != site_manifest:
        result.errors.append("local and site manifests differ")
    result.event_count = len(site_manifest.get("events", []))
    return site_manifest


def _verify_head(result: VerificationResult, repo: Repo, manifest: dict, signing_key) -> None:
    head_block = repo.head
    if not head_block:
        result.errors.append("loaded repo has no head")
        return

    head = manifest.get("head")
    if not head:
        result.errors.append("manifest has no head")
        return

    if head.get("cid") != str(head_block.cid):
        result.errors.append("manifest head CID does not match loaded repo head")
    if head.get("rev") != head_block.decoded["rev"]:
        result.errors.append("manifest head rev does not match loaded repo rev")
    if signing_key and not util.verify_sig(head_block.decoded, signing_key):
        result.errors.append("repo head signature does not verify against DID document")


def _verify_snapshot(result: VerificationResult, paths, repo: Repo) -> None:
    head_block = repo.head
    if not head_block:
        result.errors.append("loaded repo has no head")
        return

    snapshot_path = paths.site / "repo" / "snapshot.car"
    if not snapshot_path.exists():
        result.errors.append("site snapshot.car is missing")
        return

    roots, blocks = read_car(snapshot_path.read_bytes())
    result.snapshot_block_count = len(blocks)
    if not roots:
        result.errors.append("snapshot.car has no root")
        return
    if str(roots[0]) != str(head_block.cid):
        result.errors.append("snapshot.car root does not match repo head")

    memory = MemoryStorage()
    memory.write_blocks(
        Block(cid=block.cid, encoded=block.data, repo=repo.did, seq=0) for block in blocks
    )
    snapshot_repo = Repo.load(memory, cid=roots[0], signing_key=repo.signing_key)
    if snapshot_repo.get_contents() != repo.get_contents():
        result.errors.append("snapshot.car contents do not match local repo contents")


def _verify_events(
    result: VerificationResult, paths, manifest: dict, signing_key, storage: StaticStorage
) -> None:
    expected_seq = 1
    manifest_events = manifest.get("events", [])
    if manifest.get("lastSeq") != len(manifest_events):
        result.errors.append("manifest lastSeq does not match event count")

    for entry in manifest_events:
        seq = entry.get("seq")
        if seq != expected_seq:
            result.errors.append(f"event seq {seq} appears where {expected_seq} was expected")
        expected_seq += 1

        event_path = paths.site / entry["path"]
        if not event_path.exists():
            result.errors.append(f"event file is missing: {entry['path']}")
            continue

        event = read_json(event_path)
        if event.get("seq") != entry.get("seq") or event.get("type") != entry.get("type"):
            result.errors.append(f"event file {entry['path']} does not match manifest entry")

        if event.get("type") == "#commit":
            _verify_commit_event(result, paths, event, signing_key, storage)


def _verify_commit_event(
    result: VerificationResult, paths, event: dict, signing_key, storage: StaticStorage
) -> None:
    blocks_path = paths.site / event["blocks"]
    if not blocks_path.exists():
        result.errors.append(f"commit CAR is missing: {event['blocks']}")
        return

    roots, blocks = read_car(blocks_path.read_bytes())
    block_by_cid = {str(block.cid): block for block in blocks}
    if not roots or str(roots[0]) != event["commit"]:
        result.errors.append(f"commit CAR root does not match event commit for seq {event['seq']}")
        return

    commit_block = block_by_cid.get(event["commit"])
    if not commit_block:
        result.errors.append(
            f"commit CAR does not contain root commit block for seq {event['seq']}"
        )
        return
    commit_decoded = commit_block.decoded
    if not isinstance(commit_decoded, dict):
        result.errors.append(f"commit block is not an object for seq {event['seq']}")
        return

    if signing_key and not util.verify_sig(commit_decoded, signing_key):
        result.errors.append(f"commit signature does not verify for seq {event['seq']}")
    if commit_decoded.get("rev") != event.get("rev"):
        result.errors.append(f"commit rev does not match event rev for seq {event['seq']}")

    if since := event.get("since"):
        prev = storage.read(since)
        if not prev:
            result.errors.append(f"previous commit {since} is missing for seq {event['seq']}")
        elif event.get("prevData") != str(prev.decoded.get("data")):
            result.errors.append(
                f"prevData does not match previous commit data for seq {event['seq']}"
            )
    elif "prevData" in event:
        result.errors.append(f"initial commit event has unexpected prevData for seq {event['seq']}")

    for op in event.get("ops", []):
        path = op.get("path")
        try:
            if not isinstance(path, str):
                raise ValueError("path must be a string")
            collection, rkey = path.split("/", 1)
            validate_rkey(collection, rkey)
        except (SatRepoError, ValueError) as exc:
            result.errors.append(f"invalid op path {path} for seq {event['seq']}: {exc}")

        cid = op.get("cid")
        if cid and cid not in block_by_cid:
            result.errors.append(f"op CID {cid} is missing from commit CAR for seq {event['seq']}")


def format_result(result: VerificationResult) -> str:
    lines = [
        f"did: {result.did}",
        f"head: {result.head or '(none)'}",
        f"rev: {result.rev or '(none)'}",
        f"events: {result.event_count}",
        f"records: {result.record_count}",
        f"snapshot_blocks: {result.snapshot_block_count}",
    ]

    for warning in result.warnings:
        lines.append(f"warning: {warning}")
    for error in result.errors:
        lines.append(f"error: {error}")

    lines.append("ok: yes" if result.ok else "ok: no")
    return "\n".join(lines)
