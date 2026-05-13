# Remote Signing PDS Factoring

This is a feasibility note for splitting atproto repo signing out of the PDS
process. It is not a new protocol proposal yet; it documents the constraints and
the small prototype in this repo.

## Current atproto Constraints

Atproto clients still discover the account's PDS from the DID document. The PDS
is the OAuth resource server, and either the PDS itself or a separate entryway is
the OAuth authorization server. So a normal app should still see the DID's PDS as
authoritative during OAuth discovery.

Repository writes such as `com.atproto.repo.createRecord` and
`com.atproto.repo.applyWrites` are authenticated PDS requests. The PDS validates
the client token, applies the write to the user's MST, signs a repo commit with
the DID document's `#atproto` key, stores the new repo state, and emits sync
events. The signing operation is the part that can be delegated.

That delegation does not have to be visible to apps. A factored PDS can keep the
same OAuth and repo APIs, but ask a signer for signatures when a write requires
one. The signer might be a small self-hosted service, a browser tab, a browser
extension, a mobile app, a passkey/WebAuthn flow, or hardware.

The awkward part is broader than repo commits: service-auth JWTs and PLC
operations may also need signing. A practical design needs a policy layer that
distinguishes signing purposes, presents enough human-readable context, and
prevents the signer from becoming a blind arbitrary-signature oracle.

## Existing Work

- The [atproto OAuth spec][atproto-oauth] already allows the authorization server to be separate
  from the PDS resource server, but clients must verify the DID still points to
  the authoritative PDS/authorization server.
- The atproto ["what does a PDS entail"][pds-entail] discussion lists PDS responsibilities,
  including account auth, key management, PLC operations, repo hosting, and
  sync.
- An early [atproto key-management issue][atproto-key-management] explicitly mentioned in-browser device
  key storage, hosted HSM-style custody, and sovereign hardware/mobile-wallet
  key management.
- [Vow][vow] is the closest current implementation I found: a BYOK PDS where repo
  commits are signed by a user passkey instead of a private signing key stored
  on the server. Its [technical notes][vow-specs] document a passkey-derived
  repo signing key and compatibility gaps around service-auth signing.
- The Nostr ecosystem has mature analogues: [NIP-07][nip-07] browser extension signing and
  [NIP-46][nip-46] remote signer/bunker flows.

## Prototype in satrepo

This repo now has an experimental signing provider API:

```sh
uv run satrepo signer serve --root ./alice-repo --port 8790
```

And `commit` can delegate repo commit signing to that provider:

```sh
uv run satrepo commit \
  --root ./alice-repo \
  --signer-url http://127.0.0.1:8790
```

With a bearer token:

```sh
export SATREPO_SIGNER_TOKEN=dev-secret
uv run satrepo signer serve --root ./alice-repo --port 8790
uv run satrepo commit --root ./alice-repo --signer-url http://127.0.0.1:8790
```

This deliberately proves only the repo-signing seam. The repo writer still
builds the commit object, stores blocks, writes static artifacts, and emits
firehose events. The signer receives DAG-CBOR commit bytes, verifies the payload
looks like an atproto repo commit for the configured DID, signs those bytes, and
returns an ECDSA DER signature. The writer checks that the signer's public key
matches the DID document's `#atproto` key before using it.

## Implications for a Real PDS

A fuller prototype would put this same signer call behind authenticated
`com.atproto.repo.*` write endpoints. The PDS would own OAuth/session state,
write validation, repo storage, blob storage, indexing, and firehose publishing.
The signer would own key custody and purpose-specific approval.

For browser-extension shape, the PDS cannot directly call `window.*` APIs, so it
needs a rendezvous channel: likely a WebSocket from the extension/browser tab to
the PDS, or a deep-link/QR style flow similar to remote signers in Nostr. OAuth
approval could create a pending signing session at the PDS, then the signer
approves the session and later handles write-signing requests.

Open questions:

- Should signer requests carry the final commit bytes, a normalized JSON summary,
  or both?
- How much semantic validation belongs in the signer versus the PDS?
- Should repo commit signing, service-auth JWT signing, and PLC operation signing
  be separate capabilities?
- Can service-auth signing use a separate DID verification method in practice, or
  does current network compatibility require the repo signing key?
- What is the recovery story if the signer key is lost?

[atproto-oauth]: https://atproto.com/specs/oauth
[pds-entail]: https://github.com/bluesky-social/atproto/discussions/2350
[atproto-key-management]: https://github.com/bluesky-social/atproto/issues/87
[vow]: https://tangled.org/julien.rbrt.fr/vow
[vow-specs]: https://tangled.org/julien.rbrt.fr/vow/blob/main/specs.md
[nip-07]: https://nips.nostr.com/7
[nip-46]: https://nostr-nips.com/nip-46
