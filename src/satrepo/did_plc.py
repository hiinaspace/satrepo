"""did:plc operation helpers.

The initial implementation can build and persist a signed genesis operation
without posting it. A later command can publish the same operation to a PLC
directory when a real shim URL is available.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from urllib.parse import urlparse

from arroba import did as arroba_did
from arroba import util
import dag_cbor
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.hashes import Hash, SHA256


@dataclass(frozen=True)
class PlcGenesis:
    did: str
    operation: dict
    did_doc: dict


def normalize_pds_url(pds_url: str) -> str:
    """Return a canonical absolute PDS service URL."""

    pds_url = pds_url.strip().rstrip("/")
    parsed = urlparse(pds_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("PDS URL must be an absolute http(s) URL, for example https://shim.example")
    return pds_url


def build_genesis_operation(
    *,
    handle: str,
    pds_url: str,
    signing_key: ec.EllipticCurvePrivateKey,
    rotation_key: ec.EllipticCurvePrivateKey,
) -> PlcGenesis:
    """Build a signed PLC genesis operation and derived DID document."""

    if not arroba_did.HANDLE_RE.fullmatch(handle):
        raise ValueError(f"{handle} is not a valid ATProto handle")
    pds_url = normalize_pds_url(pds_url)

    op = {
        "type": "plc_operation",
        "rotationKeys": [arroba_did.encode_did_key(rotation_key.public_key())],
        "verificationMethods": {
            "atproto": arroba_did.encode_did_key(signing_key.public_key()),
        },
        "alsoKnownAs": [f"at://{handle}"],
        "services": {
            "atproto_pds": {
                "type": "AtprotoPersonalDataServer",
                "endpoint": pds_url,
            }
        },
        "prev": None,
    }

    signed_op = util.sign(op, rotation_key)
    signed_op["sig"] = base64.urlsafe_b64encode(signed_op["sig"]).decode().rstrip("=")

    sha256 = Hash(SHA256())
    sha256.update(dag_cbor.encode(signed_op))
    did = "did:plc:" + base64.b32encode(sha256.finalize())[:24].lower().decode()

    operation = {**signed_op, "did": did}
    did_doc = arroba_did.plc_operation_to_did_doc(operation)
    return PlcGenesis(did=did, operation=operation, did_doc=did_doc)
