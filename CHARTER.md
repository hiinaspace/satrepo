# satrepo Charter

## Purpose

This project explores whether an AT Protocol repository can be authored locally,
published as static files over ordinary HTTP, and adapted back into the current
ATProto sync shape by a small dynamic shim.

The guiding analogy is Git's "dumb HTTP" mode: a local tool owns mutation,
object creation, signing, and publication, while a static web server only serves
bytes. For ATProto, that means the hosted side would not offer OAuth, password
sessions, or XRPC write methods. It would only make signed repo state, commit
data, and blobs available for readers.

## Background

We started from a working self-hosted Bluesky PDS on `testpds.hiina.space`.
That clarified the normal production shape:

- A DID identifies the account.
- The DID document points to a PDS service endpoint.
- The PDS stores the repo, holds the signing key for ordinary writes, exposes
  sync XRPCs, and serves a resumable `subscribeRepos` firehose.
- Bluesky clients write records to the PDS; relays and AppViews ingest them via
  the sync path.

We then compared this with ActivityPub and RSS/IndieWeb-style publication. The
interesting gap is that ATProto records and repo commits are already signed and
content-addressed, but the current network expects a live PDS-shaped HTTP/XRPC
service for discovery, repo fetches, and firehose streaming.

## Prior Art Inspected

We cloned and inspected:

- `~/lib/atproto`: official Bluesky ATProto source repo, including PDS sync and
  sequencer implementation details.
- `~/lib/pds`: official Bluesky PDS deployment repo.
- `~/lib/bridgy-fed`: Bridgy Fed, especially its Web-to-ATProto bridge.
- `~/lib/arroba`: Ryan Barrett's Python ATProto repo/PDS implementation used by
  Bridgy Fed.

Important observations:

- Bridgy Fed's Web mode polls RSS/Atom and microformats, converts entries to
  AS1, then routes them through its protocol pipeline.
- For Web-to-ATProto, Bridgy Fed creates and controls a `did:plc`, handle,
  signing key, rotation key, and Arroba repo for the bridged identity.
- Bridgy Fed commits converted `app.bsky.*` records into Arroba repos and
  exposes a PDS-like sync surface at `atproto.brid.gy`.
- Arroba already has storage-agnostic repo, MST, CAR, commit, and
  `com.atproto.sync.*` machinery.
- Current relays expect a PDS-like endpoint, especially `subscribeRepos`; a
  purely static origin is not enough for today's network unless a relay learns a
  new polling protocol.

## Prototype Shape

The project should start with two cooperating parts.

### Static Publisher

A local command-line tool should:

- Maintain a worktree-like editable form for records.
- Keep repo machinery in hidden local state, analogous to `.git`, so the
  editable files stay simple while commits, blocks, CARs, event logs, refs, and
  publication metadata are managed by the tool.
- Validate records against ATProto lexicons where practical.
- Map files to ATProto collection/rkey paths.
- Maintain the repo MST.
- Create signed commit objects using a local private key.
- Write static artifacts such as event files, CAR files, block files, blobs,
  refs, and a manifest.

The private signing key should live locally, in a user-controlled location
similar in spirit to `~/.ssh` or `~/.gnupg`. The static host should not need the
signing key.

The intended local authoring shape is Git-like:

- The working tree contains human-editable ATProto records, probably as
  `worktree/<collection>/<rkey>.json` at first.
- Hidden local state under `.satrepo/` contains the repo's machine artifacts:
  refs, blocks, commit CARs, event-log entries, snapshots, blob indexes, and
  publication metadata.
- A push-like command should eventually export a "bare" static view from
  `.satrepo/` to an ordinary HTTP directory. The static site should not need
  the editable working tree or any private key material.

### Dynamic Shim

A small service should:

- Poll a configured static base URL, preferably with ETag / If-None-Match.
- Fetch new manifest, event, commit, block, and blob data.
- Verify event sequence continuity, commit signatures against the DID document's
  signing key, commit chain continuity, repo head, MST root, block integrity,
  and blob references.
- Expose the normal read/sync XRPC surface expected by current relays:
  `com.atproto.sync.getRepo`, `getLatestCommit`, `listRepos`, `getRecord`,
  `getBlob`, and `subscribeRepos`.
- Synthesize a resumable firehose from the published static event log.

For compatibility with the existing Bluesky relay network, the DID service
endpoint would probably point to the dynamic shim. The shim can then treat the
static base URL as its upstream source of truth. A future relay extension could
allow DID documents to point directly at a static sync endpoint.

## Local And Published Shape

One possible local authoring layout:

```text
my-atproto-repo/
  worktree/
    app.bsky.actor.profile/
      self.json
    app.bsky.feed.post/
      2026-05-11-test.json
    blobs/
      sha256-...

  .satrepo/
    config.json
    refs/
      did
      handle
      head
      last_seq
    events/
      0000000000000001.json
    commits/
      <commit-cid>.car
      <commit-cid>.json
    snapshot.car
    blocks/
      <cid>
    blobs/
      <blob-ref>
```

The publish output should look more like a bare repo served over dumb HTTP:

```text
site/
  .well-known/
    atproto-did
  repo/
    refs/
      head
      rev
      last_seq
    events/
      0000000000000001.json
    commits/
      <commit-cid>.car
      <commit-cid>.json
    snapshot.car
    blocks/
      <cid>
    blobs/
      <blob-ref>
    manifest.json
```

The exact format is intentionally not fixed yet. The durable principle is that
local editing happens in a normal filesystem tree, local repo state lives under
`.satrepo/`, and the static site receives only the bare artifacts needed by
readers and the shim. Early implementation should optimize for clarity,
inspectability, and easy verification over compactness.

## Non-Goals For The First Prototype

- No OAuth server.
- No password login.
- No hosted write API.
- No account recovery UX.
- No full replacement for the official PDS.
- No production backup story.
- No attempt to make existing relays poll static files until the shim works.

## Open Questions

- How long should the static event log remain append-only before compaction or
  snapshotting becomes necessary?
- Should records be authored directly as ATProto JSON, or should there be a
  friendlier source format that compiles to ATProto records?
- How much of Arroba can be reused unchanged for repo mutation and sync serving?
- Is the dynamic shim a cache of the static origin, a PDS facade, or both?
- What is the smallest DID/handle setup that works against the real Bluesky
  network without handing signing authority to a server?
- Can the static commit log be shaped like a transparency log or Git pack/index
  format later, without over-designing the first version?

## Initial Success Criteria

1. Create a local DID/repo and profile record.
2. Add a post by editing a file and running a CLI command.
3. Publish static repo artifacts to an ordinary HTTP directory.
4. Run a shim that ingests those artifacts and exposes ATProto sync XRPCs.
5. Point a test DID service endpoint at the shim.
6. Have an external ATProto client or relay fetch the repo and see the test post.
