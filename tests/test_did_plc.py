from arroba import did as arroba_did

from satrepo.did_plc import build_genesis_operation
from satrepo.keys import generate_key


def test_build_genesis_operation_derives_matching_did_doc():
    signing_key = generate_key()
    rotation_key = generate_key()

    genesis = build_genesis_operation(
        handle="alice.example",
        pds_url="https://shim.example/",
        signing_key=signing_key,
        rotation_key=rotation_key,
    )

    assert genesis.did.startswith("did:plc:")
    assert genesis.operation["did"] == genesis.did
    assert genesis.operation["services"]["atproto_pds"]["endpoint"] == "https://shim.example"
    assert genesis.did_doc["id"] == genesis.did
    assert arroba_did.get_signing_key(genesis.did_doc)
