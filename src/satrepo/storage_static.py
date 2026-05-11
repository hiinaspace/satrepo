"""Arroba storage backed by .satrepo files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from arroba.storage import (
    Action,
    Block,
    CommitData,
    Sequences,
    Storage,
    SUBSCRIBE_REPOS_NSID,
)
from arroba.mst import MST
from carbox import car
import dag_json
from multiformats import CID

from .config import RepoConfig
from .jsonio import read_json, write_bytes_atomic, write_json_atomic
from .paths import RepoPaths


@dataclass(frozen=True)
class BlockMeta:
    seq: int
    repo: str

    @classmethod
    def from_dict(cls, data: dict) -> "BlockMeta":
        return cls(seq=data["seq"], repo=data["repo"])

    def to_dict(self) -> dict:
        return {"seq": self.seq, "repo": self.repo}


class FileSequences(Sequences):
    """Sequence allocator persisted in .satrepo/refs/last_seq."""

    def __init__(self, paths: RepoPaths):
        self.paths = paths

    def allocate(self, nsid: str) -> int:
        if nsid != SUBSCRIBE_REPOS_NSID:
            raise ValueError(f"unsupported sequence namespace {nsid}")
        seq = self.last(nsid) + 1
        _write_ref(self.paths.state / "refs" / "last_seq", str(seq))
        return seq

    def last(self, nsid: str) -> int:
        if nsid != SUBSCRIBE_REPOS_NSID:
            raise ValueError(f"unsupported sequence namespace {nsid}")
        path = self.paths.state / "refs" / "last_seq"
        if not path.exists():
            return 0
        return int(path.read_text(encoding="utf-8").strip() or "0")


class StaticStorage(Storage):
    """Local filesystem storage for repo blocks and publish artifacts."""

    def __init__(self, *, paths: RepoPaths, config: RepoConfig, signing_key, rotation_key):
        super().__init__(sequences=FileSequences(paths))
        self.paths = paths
        self.config = config
        self.signing_key = signing_key
        self.rotation_key = rotation_key
        self._index = self._load_index()

    @property
    def head(self):
        path = self.paths.state / "refs" / "head"
        if path.exists():
            return CID.decode(path.read_text(encoding="utf-8").strip())
        return None

    def create_repo(self, repo):
        self.store_repo(repo)

    def load_repo(self, did_or_handle):
        if did_or_handle not in (self.config.did, self.config.handle):
            return None
        if not self.head:
            return None

        from arroba.repo import Repo

        return Repo.load(
            self,
            cid=self.head,
            handle=self.config.handle,
            signing_key=self.signing_key,
            rotation_key=self.rotation_key,
        )

    def store_repo(self, repo):
        _write_ref(self.paths.state / "refs" / "did", self.config.did)
        _write_ref(self.paths.state / "refs" / "handle", self.config.handle)
        if repo.head:
            _write_ref(self.paths.state / "refs" / "head", str(repo.head.cid))
            _write_ref(self.paths.state / "refs" / "rev", repo.head.decoded["rev"])

    def load_repos(self, after=None, limit=500, minimal=False):
        repo = self.load_repo(self.config.did)
        if not repo:
            return []
        if after and repo.did <= after:
            return []
        return [repo][:limit]

    def _set_repo_status(self, repo, status):
        repo.status = status

    def read(self, cid):
        cid = _decode_cid(cid)
        path = self._block_path(cid)
        if not path.exists():
            return None

        meta = self._index.get(str(cid), {})
        return Block(
            cid=cid,
            encoded=path.read_bytes(),
            seq=meta.get("seq"),
            repo=meta.get("repo"),
        )

    def read_many(self, cids):
        return {cid: self.read(cid) for cid in cids}

    def read_many_raw(self, cids):
        raw = {}
        for cid in cids:
            block = self.read(cid)
            raw[cid] = (block.encoded, block.seq) if block else None
        return raw

    def read_blocks_by_seq(self, start=0, repo=None):
        blocks = []
        for cid_text, meta in self._index.items():
            block_meta = BlockMeta.from_dict(meta)
            if block_meta.seq >= start and (not repo or block_meta.repo == repo):
                block = self.read(CID.decode(cid_text))
                if block:
                    blocks.append(block)
        return sorted(blocks, key=lambda block: block.seq)

    def has(self, cid):
        return self._block_path(_decode_cid(cid)).exists()

    def write(self, repo_did, obj, seq=None):
        if seq is None:
            seq = self.sequences.allocate(SUBSCRIBE_REPOS_NSID)

        block = Block(decoded=obj, seq=seq, repo=repo_did)
        self._store_block(block)
        return block

    def write_blocks(self, blocks):
        for block in blocks:
            self._store_block(block)

    def write_event(self, repo, type, **kwargs):
        block = super().write_event(repo, type, **kwargs)
        self._emit_non_commit_event(block)
        return block

    def commit(self, repo, writes, repo_did=None):
        commit_data = super().commit(repo, writes, repo_did=repo_did)
        self._emit_commit_event(commit_data)
        return commit_data

    def write_snapshot(self) -> None:
        if not self.head:
            return

        blocks = []
        for block in self._repo_blocks():
            blocks.append(car.Block(cid=block.cid, data=block.encoded, decoded=block.decoded))

        snapshot = car.write_car([self.head], blocks)
        write_bytes_atomic(self.paths.state / "snapshot.car", snapshot)

    def _repo_blocks(self) -> Iterable[Block]:
        for cid_text in self._index:
            block = self.read(CID.decode(cid_text))
            if not block:
                continue
            block_type = block.decoded.get("$type", "")
            if block_type.startswith("com.atproto.sync.subscribeRepos#"):
                continue
            yield block

    def _emit_non_commit_event(self, block: Block) -> None:
        decoded = dict(block.decoded)
        event_type = decoded.pop("$type").split("#", 1)[1]
        blocks = decoded.pop("blocks", None)
        event = {
            "seq": decoded.pop("seq"),
            "type": f"#{event_type}",
            "repo": decoded.pop("did"),
            **_json_safe(decoded),
        }

        if isinstance(blocks, bytes):
            car_name = f"{event['seq']:016d}.car"
            write_bytes_atomic(self.paths.state / "events" / car_name, blocks)
            event["blocks"] = f"repo/events/{car_name}"

        write_json_atomic(self._event_path(event["seq"]), event)

    def _emit_commit_event(self, commit_data: CommitData) -> None:
        commit = commit_data.commit
        decoded = commit.decoded

        tree = MST(storage=self, pointer=decoded["data"])
        tree.add_covering_proofs(commit_data, blocks=commit_data.blocks)

        car_blocks = [
            car.Block(cid=block.cid, data=block.encoded, decoded=block.decoded)
            for block in commit_data.blocks.values()
        ]
        car_bytes = car.write_car([commit.cid], car_blocks)
        car_name = f"{commit.cid}.car"
        write_bytes_atomic(self.paths.state / "commits" / car_name, car_bytes)
        write_bytes_atomic(
            self.paths.state / "commits" / f"{commit.cid}.json",
            dag_json.encode(decoded) + b"\n",
        )

        prev_data = None
        if prev := decoded.get("prev"):
            prev_block = self.read(prev)
            if prev_block:
                prev_data = prev_block.decoded.get("data")

        event = {
            "seq": commit.seq,
            "type": "#commit",
            "repo": decoded["did"],
            "time": commit.time.isoformat(),
            "rev": decoded["rev"],
            "commit": str(commit.cid),
            "since": str(commit_data.prev) if commit_data.prev else None,
            "blocks": f"repo/commits/{car_name}",
            "ops": [_commit_op_to_json(op) for op in commit.ops or []],
            "blobs": [],
        }
        if prev_data is not None:
            event["prevData"] = str(prev_data)

        write_json_atomic(self._event_path(commit.seq), event)

    def _event_path(self, seq: int) -> Path:
        return self.paths.state / "events" / f"{seq:016d}.json"

    def _store_block(self, block: Block) -> None:
        cid_text = str(block.cid)
        if cid_text not in self._index:
            write_bytes_atomic(self._block_path(block.cid), block.encoded)
            self._index[cid_text] = BlockMeta(seq=block.seq, repo=block.repo).to_dict()
            self._write_index()

    def _block_path(self, cid: CID) -> Path:
        return self.paths.state / "blocks" / str(cid)

    def _index_path(self) -> Path:
        return self.paths.state / "block_index.json"

    def _load_index(self) -> dict:
        path = self._index_path()
        return read_json(path) if path.exists() else {}

    def _write_index(self) -> None:
        write_json_atomic(self._index_path(), self._index)


def _commit_op_to_json(op) -> dict:
    data = {
        "action": op.action.name.lower(),
        "path": op.path,
        "cid": None if op.action == Action.DELETE else str(op.cid),
    }
    if op.action != Action.CREATE:
        data["prev"] = str(op.prev_cid)
    return data


def _decode_cid(cid) -> CID:
    return cid if isinstance(cid, CID) else CID.decode(str(cid))


def _json_safe(value):
    if isinstance(value, dict):
        return {key: _json_safe(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, bytes):
        return value
    if isinstance(value, CID):
        return str(value)
    return value


def _write_ref(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{value}\n", encoding="utf-8")
