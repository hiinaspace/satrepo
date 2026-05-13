"""Experimental HTTP signing provider for repo commits."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import dag_cbor
from aiohttp import web
from arroba import did as arroba_did
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from .errors import SatRepoError
from .jsonio import read_json
from .keys import PrivateKey, read_private_key

SIGNER_PREFIX = "/satrepo-signer/v0"
REPO_COMMIT_PURPOSE = "atproto.repo.commit"
JSON_TYPE = "application/json"
SIGNING_KEY = web.AppKey("signing_key", PrivateKey)
TOKEN = web.AppKey("token", object)
ALLOWED_DID = web.AppKey("allowed_did", object)


class RemoteSignerError(SatRepoError):
    """Raised when a remote signing provider refuses or fails a request."""


@dataclass(frozen=True)
class SignerIdentity:
    did_key: str


class RemoteSigningKey:
    """Small adapter shaped like cryptography's EC private key sign API.

    Arroba's repo commit helper only needs a `.curve` attribute and a `.sign()`
    method. This adapter lets the repo writer keep building commits locally
    while moving the actual private-key operation to an HTTP signer.
    """

    curve = ec.SECP256K1()

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout: float = 10.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def identity(self) -> SignerIdentity:
        data = self._request("GET", f"{SIGNER_PREFIX}/health")
        did_key = data.get("didKey")
        if not isinstance(did_key, str):
            raise RemoteSignerError("signer health response is missing didKey")
        return SignerIdentity(did_key=did_key)

    def sign(self, data: bytes, signature_algorithm: object) -> bytes:
        if not isinstance(signature_algorithm, ec.ECDSA):
            raise RemoteSignerError("remote signer only supports ECDSA signatures")

        response = self._request(
            "POST",
            f"{SIGNER_PREFIX}/sign",
            {
                "purpose": REPO_COMMIT_PURPOSE,
                "payload": _b64encode(data),
            },
        )
        signature = response.get("signatureDer")
        if not isinstance(signature, str):
            raise RemoteSignerError("signer response is missing signatureDer")

        try:
            return base64.b64decode(signature, validate=True)
        except ValueError as exc:
            raise RemoteSignerError("signer returned invalid base64 signatureDer") from exc

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Accept": JSON_TYPE,
            "User-Agent": "satrepo",
        }
        payload = None
        if body is not None:
            headers["Content-Type"] = JSON_TYPE
            payload = json.dumps(body).encode("utf-8")
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        request = Request(
            f"{self.base_url}{path}",
            data=payload,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                data = response.read()
        except HTTPError as exc:
            message = exc.reason
            try:
                error_data = json.loads(exc.read().decode("utf-8"))
                message = error_data.get("message") or error_data.get("error") or message
            except (ValueError, UnicodeDecodeError):
                pass
            raise RemoteSignerError(f"signer returned HTTP {exc.code}: {message}") from exc
        except URLError as exc:
            raise RemoteSignerError(
                f"could not reach signer {self.base_url}: {exc.reason}"
            ) from exc

        try:
            decoded = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise RemoteSignerError("signer returned invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise RemoteSignerError("signer returned a non-object JSON response")
        return decoded


def assert_signer_matches_did_doc(signer: RemoteSigningKey, did_doc_path: Path) -> None:
    expected = did_doc_atproto_key(read_json(did_doc_path))
    actual = signer.identity().did_key
    if actual != expected:
        raise RemoteSignerError(
            f"signer key {actual} does not match DID document atproto key {expected}"
        )


def did_doc_atproto_key(did_doc: dict[str, Any]) -> str:
    for method in did_doc.get("verificationMethod", []):
        if not isinstance(method, dict):
            continue
        if not str(method.get("id", "")).endswith("#atproto"):
            continue
        public_key = method.get("publicKeyMultibase")
        if isinstance(public_key, str):
            return f"did:key:{public_key}"
    raise RemoteSignerError("DID document has no #atproto publicKeyMultibase")


def create_signer_app(
    *,
    signing_key: PrivateKey,
    token: str | None = None,
    allowed_did: str | None = None,
) -> web.Application:
    app = web.Application()
    app[SIGNING_KEY] = signing_key
    app[TOKEN] = token
    app[ALLOWED_DID] = allowed_did
    app.router.add_get(f"{SIGNER_PREFIX}/health", _health)
    app.router.add_post(f"{SIGNER_PREFIX}/sign", _sign)
    return app


def run_signer_server(
    *,
    signing_key_path: Path,
    host: str,
    port: int,
    token: str | None = None,
    allowed_did: str | None = None,
) -> None:
    signing_key = read_private_key(signing_key_path)
    web.run_app(
        create_signer_app(
            signing_key=signing_key,
            token=token,
            allowed_did=allowed_did,
        ),
        host=host,
        port=port,
    )


async def _health(request: web.Request) -> web.Response:
    signing_key = request.app[SIGNING_KEY]
    return web.json_response(
        {
            "ok": True,
            "didKey": arroba_did.encode_did_key(signing_key.public_key()),
            "purposes": [REPO_COMMIT_PURPOSE],
        }
    )


async def _sign(request: web.Request) -> web.Response:
    _require_token(request)
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid JSON") from exc
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="request body must be a JSON object")
    if body.get("purpose") != REPO_COMMIT_PURPOSE:
        raise web.HTTPBadRequest(text=f"unsupported signing purpose: {body.get('purpose')}")

    payload = _decode_payload(body.get("payload"))
    decoded = _decode_commit(payload)
    allowed_did = request.app[ALLOWED_DID]
    if isinstance(allowed_did, str) and decoded.get("did") != allowed_did:
        raise web.HTTPForbidden(text=f"signer is not configured for DID {decoded.get('did')}")

    signing_key = request.app[SIGNING_KEY]
    signature = signing_key.sign(payload, ec.ECDSA(hashes.SHA256()))
    return web.json_response(
        {
            "signatureDer": _b64encode(signature),
            "payload": _payload_summary(decoded),
        }
    )


def _require_token(request: web.Request) -> None:
    token = request.app[TOKEN]
    if not isinstance(token, str):
        return
    if request.headers.get("Authorization") != f"Bearer {token}":
        raise web.HTTPUnauthorized(text="missing or invalid bearer token")


def _decode_payload(value: object) -> bytes:
    if not isinstance(value, str):
        raise web.HTTPBadRequest(text="payload must be base64")
    try:
        return base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise web.HTTPBadRequest(text="payload must be valid base64") from exc


def _decode_commit(payload: bytes) -> dict[str, Any]:
    try:
        decoded = dag_cbor.decode(payload)
    except Exception as exc:
        raise web.HTTPBadRequest(text="payload must be DAG-CBOR") from exc
    if not isinstance(decoded, dict):
        raise web.HTTPBadRequest(text="payload must decode to a commit object")
    required = {"did", "version", "rev", "prev", "data"}
    missing = sorted(required - set(decoded))
    if missing:
        raise web.HTTPBadRequest(text=f"commit payload missing fields: {', '.join(missing)}")
    return decoded


def _payload_summary(decoded: dict[str, Any]) -> dict[str, Any]:
    return {
        "did": decoded.get("did"),
        "version": decoded.get("version"),
        "rev": decoded.get("rev"),
        "prev": _string_or_none(decoded.get("prev")),
        "data": str(decoded.get("data")),
    }


def _string_or_none(value: object) -> str | None:
    return None if value is None else str(value)


def _b64encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")
