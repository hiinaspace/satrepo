# satrepo — Initial Implementation Plan

## Context

`satrepo` is a learning-first prototype that explores whether an
ATProto identity's repo can be authored locally and published as static files
over ordinary HTTP, with a small dynamic shim adapting that static origin back
into the sync XRPC shape current relays expect. The framing is Git's "dumb HTTP"
mode: local CLI owns mutation/signing/serialization, the static host serves
bytes, the shim re-presents bytes as a live PDS-shaped service.

The goal is ATProto understanding first, plausible product second. So the design
should prefer clarity, inspectability, and feeling the protocol over hiding
behind a library — but we can lean on arroba for the gnarliest pieces (MST,
signing, CAR, sequencer) without obscuring the parts we want to learn.

Decisions confirmed with user:
- **Language**: Python. arroba is a clean source for Storage/Repo/MST/CAR/sign
  and is already Sync 1.1-aware (`prevData` is wired and tested in
  `firehose.py:375-420`).
- **Env management**: `uv` (not raw `pip`). Use `uv init` / `uv add` / `uv sync`
  / `uv run` throughout. `pyproject.toml` is the source of truth for deps;
  `uv.lock` is committed.
- **DID method**: `did:plc`. Keep the rotation+signing keys locally; register
  the doc once with `plc.directory`.
- **CLI name**: `satrepo` (static AT-proto repo). Shim binary: `satrepo-shim`.

## Prior Art (additional to charter)

Found in this round of review — none are doing what we're doing, but each is
useful as a reference:

- **GitHub Discussion bluesky-social/atproto#2300** — exactly this idea was
  floated. Bluesky maintainer @bnewbold acknowledged it and said relay-side
  support would be "some large-ish changes" with no timeline. @snarfed (Ryan
  Barrett, Bridgy Fed / arroba) proposed essentially our architecture: "a PDS
  where users provide their own static CAR file storage." Validates the
  approach; no one has shipped it.
- **GitHub Discussion bluesky-social/atproto#2644** — bnewbold's "implementing
  repos is like implementing Git, deceptively complex." Confirms reuse of
  arroba's MST/CAR is the right call.
- **bluesky-social/atproto** — cloned locally at `~/lib/atproto`. This is now
  the authoritative source reference for the production PDS sync endpoints and
  sequencer. The older `~/lib/pds` checkout is the deployment repo, not the
  TypeScript implementation.
- **bluesky-social/proposals/0006-sync-iteration** ("Sync 1.1") — material:
  non-initial `#commit` firehose events must include `prevData`, hosts must emit
  `#sync` events for recovery, commit events are capped at 2 MB / 200 ops, and
  there is no `tooBig`. Relays drop invalid commits and can mark accounts
  `desynchronized` / `throttled`. The proposal itself notes that a static origin
  behind a dynamic shim is a coherent way to participate.
- **DavidBuchanan314/picopds** — closest existing "minimum viable PDS" in
  Python; useful as a reference for what an XRPC surface for the shim looks
  like when stripped down. Author has moved on to `millipds` (more
  production-shaped). Both are still real PDSes (server-side signing keys,
  write APIs) — they don't separate authoring from serving.
- **ascorbic/cirrus** — single-user PDS on Cloudflare Workers (Durable Object +
  SQLite for repo, R2 for blobs). Same "small single-user" target audience as
  us, but again, server-side keys + write API.
- **DavidBuchanan314/millipds**, **mary-ext/danaus**, **samuelgoto/micropod**,
  **alteran-social/alteran** — additional minimal PDSes worth a glance for
  reference patterns. All trad-PDS shaped.
- **SootyOwl/obsidian-standard-site** — interesting *source-format* prior art:
  publishes Obsidian notes to an existing PDS via app-passwords, using a custom
  lexicon (`site.standard.publication` / `site.standard.document` /
  `at.markpub.markdown`). Not the same architecture but the file-to-record
  ergonomics are inspiration if we ever want a friendlier source format than
  raw ATProto JSON.

Conclusion: the architectural niche is genuinely empty. Nothing else combines
"client owns the keys" + "host serves static bytes" + "shim emits a relay-ready
firehose."

## Architecture

Two cooperating Python packages in one repo:

```
satrepo/
  pyproject.toml
  README.md
  CHARTER.md
  satrepo/
    __init__.py
    cli.py              # `satrepo` entrypoint (Click or argparse)
    config.py           # paths, key locations, site root resolution
    keys.py             # secp256k1 key gen + load/save (delegates to arroba.util)
    did_plc.py          # local did:plc genesis: build op, sign, POST to plc.directory
    worktree.py         # collection/rkey file scanning + JSON parsing + validation
    storage_static.py   # arroba Storage subclass that writes static artifacts
    publish.py          # high-level: read worktree -> Repo.commit -> emit static files
    manifest.py         # manifest.json schema + read/write helpers
    blobs.py            # blob ingest (hash, store, return blob ref dict)
  satrepo_shim/
    __init__.py
    server.py           # aiohttp app exposing com.atproto.sync.* + identity/server describe
    origin.py           # HTTP client polling the static base URL (ETag/If-None-Match)
    storage_remote.py   # read-only arroba-shaped Storage backed by `origin`
    firehose.py         # subscribeRepos: WebSocket, CBOR framing, seq + cursor
    verify.py           # commit signature verification against the published DID doc
  tests/
    test_publish.py
    test_storage_roundtrip.py
    test_shim_xrpc.py
    test_shim_firehose.py
```

### Local checkout layout

The user-facing repo should feel like a Git working tree, not like a static web
root. Editable records live in normal files; machine state lives in `.satrepo/`;
`site/` is generated publish output.

```
my-atproto-repo/
  worktree/
    app.bsky.actor.profile/
      self.json
    app.bsky.feed.post/
      2026-05-11-hello.json
    blobs/
      avatar.jpg

  .satrepo/
    config.json            # DID, handle, origin/publish settings; no private keys
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
      <cid>
    manifest.json          # local copy of the generated static manifest

  site/                    # generated bare static publication tree
```

Private signing and rotation keys still live under
`~/.config/satrepo/<did>/`, outside both the working tree and
publication output.

### Static layout (event-log-first first cut)

`site/` is the bare repo view served over ordinary HTTP. It should be generated
from `.satrepo/` and should not include the editable working tree or private key
material.

```
site/
  .well-known/
    atproto-did             # plaintext file containing the DID (for handle resolution)
  did.json                  # for did:web, not used in did:plc; reserve the slot
  repo/
    refs/
      head                  # plaintext: latest commit CID
      rev                   # plaintext: latest rev TID
      last_seq              # plaintext: latest event seq
    events/
      0000000000000001.json # decoded #identity/#account/#sync/#commit event metadata
    commits/
      <commit-cid>.car      # precomputed firehose CAR for this #commit event
      <commit-cid>.json     # decoded commit object for human inspection
    snapshot.car            # full repo CAR (regenerated on every publish; used for getRepo)
    blocks/                 # optional: individual blocks addressable by CID
      <cid>
    blobs/
      <cid>                 # raw blob bytes, or sha256 path mapped in manifest
    manifest.json           # see below
```

`manifest.json`:
```json
{
  "did": "did:plc:...",
  "handle": "testhandle.example",
  "head": {"cid": "bafy...", "rev": "3kj..."},
  "lastSeq": 5,
  "events": [
    {"seq": 1, "type": "#identity", "path": "repo/events/0000000000000001.json"},
    {"seq": 2, "type": "#account", "path": "repo/events/0000000000000002.json"},
    {"seq": 3, "type": "#sync", "path": "repo/events/0000000000000003.json"},
    {"seq": 4, "type": "#commit", "rev": "3kj...", "commit": "bafy...", "path": "repo/events/0000000000000004.json"}
  ],
  "blobs": {
    "bafkrei...": {"path": "repo/blobs/bafkrei...", "sha256": "...", "mimeType": "image/jpeg", "size": 12345}
  },
  "version": 1
}
```

Manifest is the index. The authoritative static stream is the append-only
`events[]` list ordered by `seq`, not just a list of commits. A full-repo
`snapshot.car` lets the shim serve `getRepo` cheaply without reconstructing from
blocks.

Commit event files are the replay contract for the shim:

```json
{
  "seq": 4,
  "type": "#commit",
  "repo": "did:plc:...",
  "time": "...",
  "rev": "3kj...",
  "commit": "bafy...",
  "since": "bafy...",
  "prevData": "bafy...",
  "blocks": "repo/commits/bafy....car",
  "ops": [
    {"action": "create", "path": "app.bsky.feed.post/3kj...", "cid": "bafy..."}
  ],
  "blobs": ["bafkrei..."]
}
```

`prevData` is a Sync 1.1 firehose payload field, not a repo commit field. It is
omitted for the initial commit. The `blocks` CAR is precomputed by the publisher
and includes the commit block, changed record/MST blocks, and any additional MST
covering-proof blocks required for Sync 1.1. The shim should be able to serve
`subscribeRepos` by validating and replaying these event files instead of
reconstructing operation diffs or covering proofs at subscription time.

Static publication should be atomic from the shim's point of view: write
versioned event, commit, block, blob, and snapshot artifacts first, then publish
`manifest.json` last.

### Key/DID model

- secp256k1 signing key + secp256k1 rotation key generated locally with
  `arroba.util.new_key`, stored at `~/.config/satrepo/<did>/`:
  `signing.key`, `rotation.key`.
- `did:plc` genesis op built locally, signed locally with the rotation key,
  POSTed to `plc.directory`. `satrepo init` needs the future shim/PDS URL up
  front, or must support a later PLC update command before live-network testing.
  Reuse `arroba.did.create_plc` if it cleanly separates "build the signed op"
  from "post it"; otherwise reimplement (~100 lines) so we feel that step.
- DID doc's `service` entry points at the shim's host. The PLC doc is hosted
  by plc.directory, not by us — that's the one piece that's *not* static.
  This is acceptable for the prototype; the charter's "open question" of a
  fully-static DID is shelved for v2 (did:web is the natural answer there).

## Implementation phases

### Phase A — CLI publishes a signed repo as static files

Hits charter success criteria 1–3.

1. `satrepo init <handle> --pds-url <shim-url>` — generate signing+rotation
   keys, register a did:plc, scaffold `worktree/`, `.satrepo/`, and generated
   `site/` directories, write keys to `~/.config/...`. For local-only testing,
   allow a placeholder PDS URL and provide `satrepo plc update --pds-url ...`
   before Phase C.
2. `satrepo publish` — scan `worktree/<nsid>/<rkey>.json` for records, do
   minimal lexicon validation (depend on `lexrpc` for schemas, but call
   it as a plain function — no Flask context), diff against the current repo
   contents to build a Write list, call `Repo.create()` (first commit) or
   `Repo.load(); storage.commit(repo, writes)` on subsequent runs.
3. After each commit, build and persist replay-ready event artifacts from the
   returned `CommitData`: operation list, previous record CIDs, `prevData`,
   commit CAR with Sync 1.1 covering proofs, decoded commit JSON, and manifest
   index updates. Non-commit events (`#identity`, `#account`, `#sync`) are event
   log entries too; `manifest.events` is the stream the shim replays.
4. Reuse:
   - `arroba.util.new_key`, `arroba.util.sign` — signing
   - `arroba.repo.Repo`, `arroba.repo.Write` — commit construction
   - `arroba.mst.MST` — tree mutation
   - `arroba.storage.Block`, `arroba.storage.CommitData` — block dataclasses
   - `arroba.firehose.process_event` and/or `MST.add_covering_proofs` — event
     payload and covering-proof shape, if cleanly reusable
   - `carbox` — CAR read/write
5. Implement ourselves (for learning):
   - `satrepo/storage_static.py` — a `Storage` subclass for local filesystem
     block/repo metadata under `.satrepo/`. It may inherit Arroba's base
     `_commit`; the important custom work is durable block writes, repo head
     storage, and event-log artifact emission after commits.
   - `satrepo/manifest.py` — append event entries, recompute `head`/`rev`/
     `lastSeq`, and write the publication manifest atomically after all
     referenced artifacts.
   - `satrepo/did_plc.py` — genesis op construction and PLC POST.
6. Output: a `site/` directory that's a static `rsync`-able artifact generated
   from `.satrepo/`.

**Done when**: `satrepo init` produces a valid did:plc resolvable on
`plc.directory`; `satrepo publish` produces a `site/` whose `snapshot.car`
loads via `arroba.repo.Repo.load` from a `MemoryStorage` populated by the CAR;
`manifest.json` describes the head and latest seq correctly; every `#commit`
event has a CAR and ops list that can be converted directly into a
`subscribeRepos` frame.

### Phase B — Shim serves sync XRPCs from the static origin

Hits charter criterion 4.

1. `satrepo_shim/origin.py` — periodic HTTP poller (configurable interval, ETag/
   If-None-Match) for `manifest.json`. On change, fetch any new event files and
   their referenced commit CARs, blocks, snapshots, and blobs.
2. `satrepo_shim/storage_remote.py` — read-only `arroba.storage.Storage` subclass
   backed by an in-memory block dict that's populated from the origin. Local
   disk cache underneath so restarts don't re-fetch everything.
3. `satrepo_shim/verify.py` — for each new commit, fetch the DID document (resolve
   did:plc), extract the signing key, verify the commit signature via
   `arroba.util.verify_sig`. Also verify monotonic event seqs, commit chain
   continuity, `prevData`, repo head, MST root, event CAR roots, block CIDs, and
   blob CID/path mappings. Drop and warn on failure.
4. `satrepo_shim/server.py` — aiohttp app, handlers for:
   - `com.atproto.sync.getRepo` — return `snapshot.car` (or a `since`-filtered
     subset built from the static block store)
   - `com.atproto.sync.getLatestCommit`
   - `com.atproto.sync.listRepos`
   - `com.atproto.sync.getRepoStatus`
   - `com.atproto.sync.getRecord` — MST lookup + covering proofs (lift
     directly from `arroba.xrpc_sync`)
   - `com.atproto.sync.getBlob` — proxy/redirect to the manifest-mapped blob path
   - `com.atproto.sync.listBlobs`
   - `com.atproto.sync.getBlocks`
   - `com.atproto.sync.subscribeRepos` — WebSocket
   - `com.atproto.server.describeServer` — minimal
   - `com.atproto.repo.describeRepo` — minimal
   - `com.atproto.identity.resolveHandle` — return our DID
   - `/xrpc/_health` — minimal operational health endpoint
5. `satrepo_shim/firehose.py` — subscribeRepos as WebSocket with CBOR-framed
   `#commit` / `#sync` / `#identity` / `#account` / `#info` messages. seq is
   the publisher-assigned global monotonic int stored in the event log. Cursor
   support: replay from `manifest.events` by `seq`, using stored event metadata
   and precomputed commit CARs.
6. Reuse selectively:
   - `arroba.util.verify_sig`, `arroba.did.resolve_plc`
   - `arroba.did.get_signing_key` — DID doc signing-key extraction
   - `arroba.mst.MST.add_covering_proofs` (or equivalent) — for getRecord and
     any fallback event verification
   - `arroba.firehose` event-frame helpers if cleanly separable from Flask;
     otherwise reimplement frame encoding (~80 lines) by hand from the
     `subscribeRepos` lexicon — good learning material.

**Done when**: `satrepo_shim/server.py` running locally serves a valid CAR for
`getRepo`, the right block for `getRecord`, and a working WebSocket that
delivers stored event-log frames when new event entries are appended to
`manifest.json`, without recomputing commit ops or firehose CARs at subscription
time.

### Phase C — End-to-end against the live network

Hits charter criteria 5–6.

1. Deploy `site/` to a real HTTPS origin (any static host).
2. Deploy `satrepo_shim/server.py` to a real HTTPS endpoint (cheap VPS or Fly).
3. Update the did:plc doc to point its `#atproto_pds` service at the shim.
4. Request a crawl from a Bluesky relay (or a private one we run) via
   `com.atproto.sync.requestCrawl`.
5. Verify: an external client (e.g., `bsky.app` or a CLI like
   `goat` / `atproto-cli`) can fetch the repo and read the test post.

**Done when**: a test post made via `satrepo publish` is visible through a
third-party ATProto client that resolved our DID and reached the shim.

## Key files to study & reuse

| Reuse | Where | What |
|-------|-------|------|
| arroba.util | ~/lib/arroba/arroba/util.py | `new_key`, `sign`, `verify_sig` |
| arroba.repo | ~/lib/arroba/arroba/repo.py | `Repo`, `Write`, commit pipeline |
| arroba.mst | ~/lib/arroba/arroba/mst.py | MST mutation, covering proofs |
| arroba.storage | ~/lib/arroba/arroba/storage.py | `Storage` abstract base, `Block`, `CommitData` |
| arroba.did | ~/lib/arroba/arroba/did.py | `resolve_plc`, `create_plc` |
| arroba.firehose | ~/lib/arroba/arroba/firehose.py | prevData wiring (line 375–420), event frame shape |
| carbox | (pip dep of arroba) | CAR read/write |
| lexrpc | (pip dep) | lexicon schemas + validation, used standalone |

## Reference patterns (read but don't import)

| Where | Why |
|-------|-----|
| ~/lib/atproto/packages/pds/src/api/com/atproto/sync/*.ts | Authoritative behavior of each XRPC endpoint |
| ~/lib/atproto/packages/pds/src/sequencer/ | Sequencer / event-emission patterns |
| ~/lib/pds | Official Bluesky PDS deployment repo; useful for ops expectations, not source internals |
| ~/lib/bridgy-fed/atproto.py:737-1024 (`ATProto.send`) | Chokepoint pattern for "add a record then commit + emit" |
| ~/lib/bridgy-fed/hub.py | How arroba's firehose gets wired into a real server |
| ~/lib/picopds/pds.py | Tiny Python PDS/XRPC surface |
| ~/lib/millipds/src/millipds/atproto_sync.py | Newer Python sync endpoint reference |
| ~/lib/cirrus/packages/pds/src/xrpc/sync.ts | Cloudflare single-user PDS sync surface |
| ~/lib/danaus/packages/danaus/src/api/com.atproto/sync.*.ts | TypeScript PDS endpoint shape and tests |
| ~/lib/micropod/src/sequencer.js | Small JS sequencer and relay-facing behavior |
| ~/lib/alteran/src/lib/firehose/frames.ts | Firehose frame encoding/decoding tests and helpers |
| ~/lib/obsidian-standard-site/src/publish.ts | File-to-record authoring ergonomics |

## Open items deferred from charter

These stay open and the plan does not pre-commit answers — we'll learn the
right shape by building Phase A and B:

- Snapshot-first vs append-log-first static format. **Current plan: both, but
  event-log-first for replay** (`manifest.events` + per-event JSON + per-commit
  firehose CARs, plus `snapshot.car` for cheap `getRepo`), reassess after Phase B.
- Friendlier source format than ATProto JSON. **Current plan: defer**;
  raw JSON in `worktree/<nsid>/<rkey>.json`.
- Static-only DID. **Current plan: defer**; use did:plc now, revisit did:web
  in a v2.
- Compaction / transparency-log shape of the commit log. **Defer.**

## Verification

End-to-end smoke test for the prototype:

1. `uv sync` in repo root (installs the project + dev deps; produces `.venv/`).
2. `uv run satrepo init alice.example --pds-url https://shim.example` — creates
   keys, registers did:plc, scaffolds `worktree/`, `.satrepo/`, and generated
   `site/`. Verify with `curl https://plc.directory/<did>`.
3. Write `worktree/app.bsky.actor.profile/self.json` with a profile record.
4. Write `worktree/app.bsky.feed.post/2026-05-11-hello.json` with a post.
5. `uv run satrepo publish` — emits `site/`.
6. `python -m http.server 8080 --directory site` (mock static origin).
7. `uv run satrepo-shim --origin http://localhost:8080 --did did:plc:...`
   (the shim).
8. In another terminal:
   - `curl http://localhost:PORT/xrpc/com.atproto.sync.getLatestCommit?did=...`
   - `curl http://localhost:PORT/xrpc/com.atproto.sync.getRepo?did=...` →
     load resulting CAR via arroba, assert head matches.
   - `websocat ws://localhost:PORT/xrpc/com.atproto.sync.subscribeRepos` →
     observe initial cursor messages, then `uv run satrepo publish` a new
     post and observe a `#commit` event arrive.
9. Repeat 4–8 with an actual https deployment + a relay's `requestCrawl` to
   close Phase C.

Test coverage to add as we go:
- `test_publish.py` — invariants: every published commit's signature
  verifies; manifest's head matches the last commit; manifest event seqs are
  monotonic; each `#commit` event's ops/prevData/CAR match the commit; snapshot.car
  re-hydrates to the same head.
- `test_storage_roundtrip.py` — write blocks via `storage_static`, read them
  back via `storage_remote`, assert equality.
- `test_shim_xrpc.py` — hit each XRPC endpoint with the test fixture site.
- `test_shim_firehose.py` — append a commit event to manifest, assert a `#commit`
  event arrives on the WebSocket within a poll interval and uses the precomputed
  event metadata/CAR rather than recomputing ops.
