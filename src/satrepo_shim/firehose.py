"""ATProto subscribeRepos event-stream framing for static repo events."""

from __future__ import annotations

from typing import Any

import dag_cbor
from multiformats import CID

from .repo_view import StaticRepoView


def encode_message_frame(event: dict[str, Any], view: StaticRepoView) -> bytes:
    """Encode a static event JSON object as one subscribeRepos message frame."""
    event_type = event["type"]
    header = {"op": 1, "t": event_type}
    return _encode_frame(header, _event_payload(event, view))


def encode_error_frame(error: str, message: str | None = None) -> bytes:
    body = {"error": error}
    if message:
        body["message"] = message
    return _encode_frame({"op": -1}, body)


def _encode_frame(header: dict[str, Any], body: dict[str, Any]) -> bytes:
    return dag_cbor.encode(header) + dag_cbor.encode(body)


def _event_payload(event: dict[str, Any], view: StaticRepoView) -> dict[str, Any]:
    event_type = event["type"]
    if event_type == "#commit":
        return _commit_payload(event, view)
    if event_type == "#sync":
        return _sync_payload(event, view)
    if event_type in {"#identity", "#account"}:
        return _did_event_payload(event)
    return {key: value for key, value in event.items() if key != "type"}


def _commit_payload(event: dict[str, Any], view: StaticRepoView) -> dict[str, Any]:
    payload = {key: value for key, value in event.items() if key != "type"}
    payload.setdefault("rebase", False)
    payload.setdefault("tooBig", False)
    payload.setdefault("blobs", [])

    payload["commit"] = CID.decode(payload["commit"])
    payload["blocks"] = view.origin.read_bytes(payload["blocks"])
    payload["since"] = _normalize_since(payload.get("since"), view)
    payload["ops"] = [_repo_op_payload(op) for op in payload["ops"]]
    payload["blobs"] = [CID.decode(cid) for cid in payload["blobs"]]
    if prev_data := payload.get("prevData"):
        payload["prevData"] = CID.decode(prev_data)
    return payload


def _normalize_since(since: Any, view: StaticRepoView) -> Any:
    if not isinstance(since, str):
        return since

    try:
        prev_cid = CID.decode(since)
    except Exception:
        return since

    try:
        prev_commit = dag_cbor.decode(view.origin.read_bytes(f"repo/blocks/{prev_cid}"))
    except Exception:
        return since

    if isinstance(prev_commit, dict) and isinstance(prev_commit.get("rev"), str):
        return prev_commit["rev"]
    return since


def _repo_op_payload(op: dict[str, Any]) -> dict[str, Any]:
    payload = dict(op)
    if cid := payload.get("cid"):
        payload["cid"] = CID.decode(cid)
    if prev := payload.get("prev"):
        payload["prev"] = CID.decode(prev)
    return payload


def _sync_payload(event: dict[str, Any], view: StaticRepoView) -> dict[str, Any]:
    payload = _did_event_payload(event)
    payload["blocks"] = view.origin.read_bytes(payload["blocks"])
    return payload


def _did_event_payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = {key: value for key, value in event.items() if key != "type"}
    if "repo" in payload and "did" not in payload:
        payload["did"] = payload.pop("repo")
    return payload
