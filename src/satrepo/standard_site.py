"""Standard.site worktree convenience helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from arroba.util import next_tid

from .config import read_config, utc_now_iso
from .errors import SatRepoError
from .jsonio import write_json_atomic
from .paths import discover_root

PUBLICATION_COLLECTION = "site.standard.publication"
DOCUMENT_COLLECTION = "site.standard.document"
MARKDOWN_TYPE = "at.markpub.markdown"


@dataclass(frozen=True)
class CreatedStandardRecord:
    collection: str
    rkey: str
    path: Path

    @property
    def repo_path(self) -> str:
        return f"{self.collection}/{self.rkey}"


def create_standard_publication(
    root: Path | str | None = None,
    *,
    name: str,
    url: str,
    description: str | None = None,
) -> CreatedStandardRecord:
    """Create a site.standard.publication record in the worktree."""

    if not name:
        raise SatRepoError("publication name cannot be empty")
    if not url:
        raise SatRepoError("publication URL cannot be empty")

    paths = discover_root(root)
    config = read_config(paths.config)
    rkey, path = _allocate_record_path(paths.worktree / PUBLICATION_COLLECTION)

    record: dict[str, Any] = {
        "$type": PUBLICATION_COLLECTION,
        "url": url,
        "name": name,
    }
    if description:
        record["description"] = description

    write_json_atomic(path, record)
    _write_publication_well_known(
        paths.site / ".well-known" / PUBLICATION_COLLECTION,
        f"at://{config.did}/{PUBLICATION_COLLECTION}/{rkey}",
    )
    return CreatedStandardRecord(collection=PUBLICATION_COLLECTION, rkey=rkey, path=path)


def create_standard_document(
    root: Path | str | None = None,
    *,
    title: str,
    markdown: str,
    path: str,
    description: str | None = None,
    tags: list[str] | None = None,
    published_at: str | None = None,
    publication_rkey: str | None = None,
) -> CreatedStandardRecord:
    """Create a site.standard.document record in the worktree."""

    if not title:
        raise SatRepoError("document title cannot be empty")
    if not markdown:
        raise SatRepoError("document markdown cannot be empty")
    if not path.startswith("/"):
        raise SatRepoError("document path must start with /")

    paths = discover_root(root)
    publication_uri = _publication_uri(paths, publication_rkey)
    rkey, record_path = _allocate_record_path(paths.worktree / DOCUMENT_COLLECTION)

    record: dict[str, Any] = {
        "$type": DOCUMENT_COLLECTION,
        "site": publication_uri,
        "title": title,
        "path": path,
        "publishedAt": published_at or utc_now_iso(),
        "textContent": markdown,
        "content": {
            "$type": MARKDOWN_TYPE,
            "text": markdown,
            "flavor": "GFM",
        },
    }
    if description:
        record["description"] = description
    if tags:
        record["tags"] = tags

    write_json_atomic(record_path, record)
    return CreatedStandardRecord(collection=DOCUMENT_COLLECTION, rkey=rkey, path=record_path)


def _allocate_record_path(directory: Path) -> tuple[str, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    for _ in range(100):
        rkey = next_tid()
        path = directory / f"{rkey}.json"
        if not path.exists():
            return rkey, path
    raise SatRepoError("could not allocate an unused Standard.site TID")


def _publication_uri(paths, publication_rkey: str | None) -> str:
    config = read_config(paths.config)
    if publication_rkey:
        return f"at://{config.did}/{PUBLICATION_COLLECTION}/{publication_rkey}"

    publication_dir = paths.worktree / PUBLICATION_COLLECTION
    publications = sorted(publication_dir.glob("*.json")) if publication_dir.exists() else []
    if len(publications) != 1:
        raise SatRepoError(
            "expected exactly one site.standard.publication record; pass --publication-rkey"
        )
    return f"at://{config.did}/{PUBLICATION_COLLECTION}/{publications[0].stem}"


def _write_publication_well_known(path: Path, uri: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{uri}\n", encoding="utf-8")
    path.chmod((path.stat().st_mode & 0o777) | 0o644)
