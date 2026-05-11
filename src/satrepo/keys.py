"""Local private key persistence."""

from __future__ import annotations

import os
from pathlib import Path

from arroba import util
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .errors import SatRepoError

PrivateKey = ec.EllipticCurvePrivateKey


def generate_key() -> PrivateKey:
    return util.new_key()


def private_key_pem(key: PrivateKey) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def write_private_key(path: Path, key: PrivateKey, *, overwrite: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)

    flags = os.O_WRONLY | os.O_CREAT
    flags |= os.O_TRUNC if overwrite else os.O_EXCL

    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise SatRepoError(f"refusing to overwrite existing key {path}") from exc

    with os.fdopen(fd, "wb") as file:
        file.write(private_key_pem(key))

    path.chmod(0o600)


def read_private_key(path: Path) -> PrivateKey:
    with path.open("rb") as file:
        key = serialization.load_pem_private_key(file.read(), password=None)

    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise SatRepoError(f"{path} is not an elliptic curve private key")
    if not isinstance(key.curve, ec.SECP256K1):
        raise SatRepoError(f"{path} is not a secp256k1 private key")

    return key
