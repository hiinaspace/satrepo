# satrepo Status And Next Steps

This file tracks the prototype state after the initial build. See `CHARTER.md`
for the original motivation and design constraints.

## Implemented

- Local repo initialization with did:plc genesis material and local signing /
  rotation keys.
- Git-like porcelain:
  - `satrepo status`
  - `satrepo commit`
  - `satrepo log`
  - `satrepo verify`
- Bluesky post helper:
  - `satrepo bsky post`
- Standard.site helpers:
  - `satrepo standard publication`
  - `satrepo standard document`
- Static repo publication under `site/repo/`:
  - manifest
  - refs
  - event log
  - commit CARs
  - blocks
  - snapshot CAR
- Read-only shim:
  - `com.atproto.sync.getLatestCommit`
  - `com.atproto.sync.getRepo`
  - `com.atproto.sync.getRepoStatus`
  - `com.atproto.sync.listRepos`
  - `com.atproto.sync.getRecord`
  - `com.atproto.sync.getBlocks`
  - `com.atproto.sync.listBlobs`
  - `com.atproto.sync.subscribeRepos`
  - `com.atproto.repo.describeRepo`
  - `com.atproto.repo.getRecord`
  - `com.atproto.repo.listRecords`
  - `com.atproto.identity.resolveHandle`
  - `com.atproto.server.describeServer`
- PLC management:
  - `satrepo plc show`
  - `satrepo plc update`
  - `satrepo plc submit`
- Standard.site static HTML generation from committed repo state.
- Public smoke test against a real did:plc, nginx vhost, shim, Bluesky appview,
  Taproot, Leaflet, and Standard.site validator.

## Current Architecture

The prototype has one static repo per checkout and one repo per shim process.

```text
worktree/  -> editable records
.satrepo/  -> local signed repo state and event artifacts
site/      -> generated static publication tree
shim       -> read-only XRPC facade over one static origin
```

The shim currently treats the static origin as its source of truth. It does not
hold private keys and does not expose write methods.

## Known Limitations

- The shim is single-repo. A hosted multi-repo shim would need registration,
  tenancy boundaries, origin validation, and a stable global `subscribeRepos`
  sequence across all served repos.
- Blob authoring and serving are skeletal.
- Lexicon validation is limited and pragmatic.
- The static artifact format is inspectable but not yet versioned as a stable
  external contract beyond `repo/manifest.json` version 1.
- The prototype has no backup, recovery, key-rotation UX, or production
  operational story.

## Useful Next Steps

1. Add a proper license and package metadata before publishing.
2. Add a short tutorial with a fresh test identity and public nginx deployment.
3. Decide whether `push` should copy `site/` to a target directory or remote.
4. Add blob helper commands for images/assets.
5. Add stricter record validation where it can be done without hiding the repo
   model.
6. Decide whether a future multi-repo hosted shim belongs in this repo or as a
   separate service.
