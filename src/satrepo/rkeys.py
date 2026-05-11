"""ATProto record key validation."""

from __future__ import annotations

import re

from .errors import SatRepoError

RECORD_KEY_RE = re.compile(r"^[a-zA-Z0-9_~.:-]{1,512}$")
RECORD_KEY_INVALID_VALUES = {".", ".."}
TID_RE = re.compile(r"^[234567abcdefghij][234567abcdefghijklmnopqrstuvwxyz]{12}$")


# Key constraints from the app.bsky lexicons. Unknown collections still use the
# baseline ATProto record-key syntax so custom schemas remain usable.
COLLECTION_KEY_CONSTRAINTS = {
    "app.bsky.actor.profile": "literal:self",
    "app.bsky.actor.status": "literal:self",
    "app.bsky.feed.like": "tid",
    "app.bsky.feed.post": "tid",
    "app.bsky.feed.postgate": "tid",
    "app.bsky.feed.repost": "tid",
    "app.bsky.feed.threadgate": "tid",
    "app.bsky.graph.block": "tid",
    "app.bsky.graph.follow": "tid",
    "app.bsky.graph.list": "tid",
    "app.bsky.graph.listblock": "tid",
    "app.bsky.graph.listitem": "tid",
    "app.bsky.graph.starterpack": "tid",
    "app.bsky.graph.verification": "tid",
    "app.bsky.labeler.service": "literal:self",
    "app.bsky.notification.declaration": "literal:self",
}


def validate_rkey(collection: str, rkey: str) -> None:
    if not is_valid_record_key(rkey):
        raise SatRepoError(f"{collection}/{rkey} is not a valid ATProto record key")

    constraint = COLLECTION_KEY_CONSTRAINTS.get(collection, "any")
    if constraint == "tid" and not is_valid_tid(rkey):
        raise SatRepoError(f"{collection}/{rkey} is invalid: {collection} requires a TID rkey")

    if constraint.startswith("literal:"):
        expected = constraint.removeprefix("literal:")
        if rkey != expected:
            raise SatRepoError(
                f"{collection}/{rkey} is invalid: {collection} requires rkey {expected!r}"
            )


def suggested_rkey(collection: str) -> str | None:
    constraint = COLLECTION_KEY_CONSTRAINTS.get(collection, "any")
    if constraint == "tid":
        from arroba.util import next_tid

        return next_tid()
    if constraint.startswith("literal:"):
        return constraint.removeprefix("literal:")
    return None


def is_valid_record_key(rkey: str) -> bool:
    return (
        1 <= len(rkey) <= 512
        and rkey not in RECORD_KEY_INVALID_VALUES
        and bool(RECORD_KEY_RE.fullmatch(rkey))
    )


def is_valid_tid(rkey: str) -> bool:
    return len(rkey) == 13 and bool(TID_RE.fullmatch(rkey))
