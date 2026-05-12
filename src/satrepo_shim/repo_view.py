"""In-memory view of one static satrepo origin."""

from __future__ import annotations

# ruff: noqa: I001
# Arroba 2.0 requires storage to be imported before repo.
import json
from dataclasses import dataclass
from typing import Any

import dag_json
from arroba import util
from arroba.storage import Action, Block, CommitData, CommitOp, MemoryStorage
from arroba.repo import Repo
from carbox import car
from carbox.car import read_car
from multiformats import CID

from .origin import StaticOrigin

CAR_MIME_TYPE = "application/vnd.ipld.car"


class RepoViewError(ValueError):
    def __init__(self, message: str, *, name: str = "InvalidRequest", status: int = 400):
        super().__init__(message)
        self.name = name
        self.status = status


@dataclass(frozen=True)
class LoadedRepo:
    repo: Repo
    storage: MemoryStorage


@dataclass(frozen=True)
class StaticRepoView:
    origin: StaticOrigin
    service_did: str

    @classmethod
    def from_origin(cls, origin: str, *, service_did: str = "did:web:localhost") -> StaticRepoView:
        return cls(origin=StaticOrigin.from_location(origin), service_did=service_did)

    def health(self) -> dict[str, Any]:
        manifest = self.manifest()
        return {
            "ok": True,
            "did": manifest["did"],
            "head": (manifest.get("head") or {}).get("cid"),
            "lastSeq": manifest.get("lastSeq", 0),
        }

    def manifest(self) -> dict[str, Any]:
        return self.origin.read_json("repo/manifest.json")

    def current_rev(self) -> str | None:
        return (self.manifest().get("head") or {}).get("rev")

    def last_seq(self) -> int:
        return int(self.manifest().get("lastSeq", 0))

    def events_after(self, cursor: int) -> list[dict[str, Any]]:
        manifest = self.manifest()
        events = []
        for entry in manifest.get("events", []):
            seq = int(entry.get("seq", 0))
            if seq > cursor:
                events.append(self.origin.read_json(entry["path"]))
        return sorted(events, key=lambda event: int(event["seq"]))

    def did_doc(self) -> dict[str, Any]:
        return self.origin.read_json("did.json")

    def latest_commit(self, did: str) -> dict[str, str]:
        manifest = self._manifest_for_did(did)
        head = manifest.get("head")
        if not head:
            raise RepoViewError(f"Could not find root for DID: {did}", name="RepoNotFound")
        return {"cid": head["cid"], "rev": head["rev"]}

    def repo_status(self, did: str) -> dict[str, Any]:
        latest = self.latest_commit(did)
        return {"did": did, "active": True, "rev": latest["rev"]}

    def list_repos(self, *, limit: int = 500, cursor: str | None = None) -> dict[str, Any]:
        manifest = self.manifest()
        did = manifest["did"]
        if cursor and did <= cursor:
            return {"repos": []}

        head = manifest.get("head") or {}
        repos = [
            {
                "did": did,
                "head": head.get("cid"),
                "rev": head.get("rev", ""),
                "active": True,
            }
        ][:limit]
        return {"repos": repos}

    def resolve_handle(self, handle: str) -> dict[str, str]:
        manifest = self.manifest()
        if handle != manifest["handle"]:
            raise RepoViewError("Unable to resolve handle", name="InvalidRequest")
        return {"did": manifest["did"]}

    def describe_repo(self, repo: str) -> dict[str, Any]:
        manifest = self._manifest_for_repo(repo)

        return {
            "handle": manifest["handle"],
            "did": manifest["did"],
            "didDoc": self.did_doc(),
            "collections": sorted(self.load_repo().repo.get_contents()),
            "handleIsCorrect": True,
        }

    def record(self, repo: str, collection: str, rkey: str, cid: str | None = None) -> dict:
        manifest = self._manifest_for_repo(repo)
        loaded = self.load_repo()
        record_cid = self._record_cid(loaded, collection, rkey)
        if cid and CID.decode(cid) != record_cid:
            raise RepoViewError(f"Record not found: {collection}/{rkey}", name="RecordNotFound")

        record_block = loaded.storage.read(record_cid)
        if not record_block:
            raise RepoViewError(f"Record block not found: {record_cid}", name="BlockNotFound")
        return _record_json(manifest["did"], collection, rkey, record_block)

    def list_records(
        self,
        repo: str,
        collection: str,
        *,
        limit: int = 50,
        cursor: str | None = None,
        reverse: bool = False,
    ) -> dict[str, Any]:
        manifest = self._manifest_for_repo(repo)
        loaded = self.load_repo()
        if not loaded.repo.mst:
            raise RepoViewError("loaded repo has no MST", name="RepoNotFound")

        prefix = f"{collection}/"
        entries = loaded.repo.mst.list_with_prefix(prefix)
        if cursor:
            cursor_key = f"{collection}/{cursor}"
            if reverse:
                entries = [entry for entry in entries if entry.key < cursor_key]
            else:
                entries = [entry for entry in entries if entry.key > cursor_key]
        if reverse:
            entries = list(reversed(entries))

        page = entries[: limit + 1]
        has_more = len(page) > limit
        page = page[:limit]
        blocks = loaded.storage.read_many([entry.value for entry in page])
        records = []
        for entry in page:
            record_block = blocks[entry.value]
            if not record_block:
                raise RepoViewError(f"Record block not found: {entry.value}", name="BlockNotFound")
            _, rkey = entry.key.split("/", 1)
            records.append(_record_json(manifest["did"], collection, rkey, record_block))

        response: dict[str, Any] = {"records": records}
        if has_more and page:
            response["cursor"] = page[-1].key.split("/", 1)[1]
        return response

    def describe_server(self) -> dict[str, Any]:
        handle = self.manifest()["handle"]
        domain = handle[handle.find(".") :] if "." in handle else f".{handle}"
        return {
            "did": self.service_did,
            "availableUserDomains": [domain],
            "inviteCodeRequired": True,
        }

    def snapshot_car(self, did: str) -> bytes:
        self._manifest_for_did(did)
        return self.origin.read_bytes("repo/snapshot.car")

    def get_record_car(self, did: str, collection: str, rkey: str) -> bytes:
        self._manifest_for_did(did)
        loaded = self.load_repo()
        if not loaded.repo.mst:
            raise RepoViewError("loaded repo has no MST", name="RepoNotFound")

        record_cid = self._record_cid(loaded, collection, rkey)
        record_block = loaded.storage.read(record_cid)
        if not record_block:
            raise RepoViewError(f"Record block not found: {record_cid}", name="BlockNotFound")

        path = f"{collection}/{rkey}"
        synthetic_commit = Block(
            decoded=record_block.decoded,
            ops=[CommitOp(Action.CREATE, path, record_block.cid)],
        )
        proof_blocks = loaded.repo.mst.add_covering_proofs(
            CommitData(commit=synthetic_commit, blocks={})
        )
        car_blocks = [
            car.Block(
                cid=record_block.cid,
                data=record_block.encoded,
                decoded=record_block.decoded,
            ),
            *(
                car.Block(cid=block.cid, data=block.encoded, decoded=block.decoded)
                for block in proof_blocks.values()
                if block
            ),
        ]
        return car.write_car([record_block.cid], car_blocks)

    def get_blocks_car(self, did: str, cids: list[str]) -> bytes:
        latest = self.latest_commit(did)
        blocks = []
        for cid in cids:
            decoded_cid = CID.decode(cid)
            try:
                encoded = self._read_block_bytes(decoded_cid)
            except Exception as exc:
                raise RepoViewError(f"No block found for CID {cid}", name="BlockNotFound") from exc
            blocks.append(car.Block(cid=decoded_cid, data=encoded))
        return car.write_car([CID.decode(latest["cid"])], blocks)

    def list_blobs(self, did: str) -> dict[str, Any]:
        self._manifest_for_did(did)
        return {"cids": sorted(self.manifest().get("blobs", {}))}

    def load_repo(self) -> LoadedRepo:
        snapshot = self.snapshot_car(self.manifest()["did"])
        roots, blocks = read_car(snapshot)
        if not roots:
            raise RepoViewError("snapshot.car has no root")

        manifest_head = self.latest_commit(self.manifest()["did"])["cid"]
        if str(roots[0]) != manifest_head:
            raise RepoViewError("snapshot.car root does not match manifest head")

        storage = MemoryStorage()
        storage.write_blocks(
            Block(cid=block.cid, encoded=block.data, repo=self.manifest()["did"], seq=0)
            for block in blocks
        )
        repo = Repo.load(storage, cid=roots[0], signing_key=util.new_key())
        return LoadedRepo(repo=repo, storage=storage)

    def _manifest_for_did(self, did: str) -> dict[str, Any]:
        manifest = self.manifest()
        if manifest.get("did") != did:
            raise RepoViewError(f"Could not find repo for DID: {did}", name="RepoNotFound")
        return manifest

    def _manifest_for_repo(self, repo: str) -> dict[str, Any]:
        manifest = self.manifest()
        if repo not in {manifest["did"], manifest["handle"]}:
            raise RepoViewError(f"Could not find repo: {repo}", name="RepoNotFound")
        return manifest

    def _record_cid(self, loaded: LoadedRepo, collection: str, rkey: str) -> CID:
        if not loaded.repo.mst:
            raise RepoViewError("loaded repo has no MST", name="RepoNotFound")

        path = f"{collection}/{rkey}"
        record_cid = loaded.repo.mst.get(path)
        if not record_cid:
            raise RepoViewError(f"Record not found: {path}", name="RecordNotFound")
        return record_cid

    def _read_block_bytes(self, cid: CID) -> bytes:
        candidates = (
            cid.encode("base32"),
            cid.encode("base58btc"),
            str(cid),
        )
        for candidate in dict.fromkeys(candidates):
            try:
                return self.origin.read_bytes(f"repo/blocks/{candidate}")
            except Exception:
                pass
        raise RepoViewError(f"No block found for CID {cid}", name="BlockNotFound")


def _record_json(did: str, collection: str, rkey: str, record_block: Block) -> dict:
    return json.loads(
        dag_json.encode(
            {
                "uri": util.at_uri(did, collection, rkey),
                "cid": record_block.cid.encode("base32"),
                "value": record_block.decoded,
            },
            dialect="atproto",
        )
    )
