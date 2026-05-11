"""Local .satrepo config."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .jsonio import read_json, write_json_atomic
from .did_plc import normalize_pds_url


CONFIG_VERSION = 1


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class RepoConfig:
    handle: str
    did: str
    pds_url: str
    key_dir: Path
    created_at: str
    version: int = CONFIG_VERSION
    plc_registered: bool = False

    @classmethod
    def create(
        cls,
        *,
        handle: str,
        did: str,
        pds_url: str,
        key_dir: Path,
        plc_registered: bool = False,
    ) -> "RepoConfig":
        return cls(
            handle=handle,
            did=did,
            pds_url=normalize_pds_url(pds_url),
            key_dir=key_dir,
            created_at=utc_now_iso(),
            plc_registered=plc_registered,
        )

    @classmethod
    def from_dict(cls, data: dict) -> "RepoConfig":
        return cls(
            version=data["version"],
            handle=data["handle"],
            did=data["did"],
            pds_url=data["pdsUrl"],
            key_dir=Path(data["keyDir"]).expanduser(),
            created_at=data["createdAt"],
            plc_registered=data.get("plcRegistered", False),
        )

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "handle": self.handle,
            "did": self.did,
            "pdsUrl": self.pds_url,
            "keyDir": str(self.key_dir),
            "createdAt": self.created_at,
            "plcRegistered": self.plc_registered,
        }


def read_config(path: Path) -> RepoConfig:
    return RepoConfig.from_dict(read_json(path))


def write_config(path: Path, config: RepoConfig) -> None:
    write_json_atomic(path, config.to_dict())
