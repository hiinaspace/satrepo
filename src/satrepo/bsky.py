"""Bluesky worktree convenience helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from arroba.util import next_tid

from .config import utc_now_iso
from .errors import SatRepoError
from .jsonio import write_json_atomic
from .paths import discover_root

POST_COLLECTION = "app.bsky.feed.post"


@dataclass(frozen=True)
class CreatedBskyPost:
    rkey: str
    path: Path

    @property
    def repo_path(self) -> str:
        return f"{POST_COLLECTION}/{self.rkey}"


def create_bsky_post(
    root: Path | str | None = None,
    *,
    text: str,
    created_at: str | None = None,
) -> CreatedBskyPost:
    """Create a text-only app.bsky.feed.post record in the worktree."""

    if not text:
        raise SatRepoError("post text cannot be empty")

    paths = discover_root(root)
    post_dir = paths.worktree / POST_COLLECTION
    post_dir.mkdir(parents=True, exist_ok=True)

    for _ in range(100):
        rkey = next_tid()
        path = post_dir / f"{rkey}.json"
        if not path.exists():
            break
    else:
        raise SatRepoError("could not allocate an unused post TID")

    record = {
        "$type": POST_COLLECTION,
        "text": text,
        "createdAt": created_at or utc_now_iso(),
    }
    write_json_atomic(path, record)
    return CreatedBskyPost(rkey=rkey, path=path)
