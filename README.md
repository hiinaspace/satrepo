# satrepo

`satrepo` is a prototype for authoring an AT Protocol repository locally,
publishing the signed repo as static files, and serving it back to the current
ATProto network through a small read-only PDS-shaped shim.

The split is close to Git's dumb HTTP mode:

- `satrepo` owns local mutation, signing, repo blocks, CARs, events, and static
  publication output.
- A static HTTP host serves the generated `site/` directory.
- `satrepo-shim` exposes the read/sync XRPCs that relays and appviews expect.

This is a prototype, not a production PDS. There is no OAuth, password login,
hosted write API, account recovery, blob upload API, or multi-user service.

## Current Shape

Each checkout contains human-editable records plus generated repo state:

```text
my-repo/
  worktree/
    app.bsky.feed.post/
      <tid>.json
    site.standard.publication/
      <tid>.json
    site.standard.document/
      <tid>.json

  .satrepo/
    config.json
    refs/
    events/
    commits/
    blocks/
    snapshot.car
    manifest.json

  site/
    .well-known/atproto-did
    .well-known/site.standard.publication
    index.html
    repo/
      manifest.json
      snapshot.car
      refs/
      events/
      commits/
      blocks/
```

Private signing and rotation keys live outside the checkout under
`$XDG_CONFIG_HOME/satrepo/<did>/` or `~/.config/satrepo/<did>/`.

## Development

```sh
uv sync
uv run pre-commit install
uv run pytest
uv run pre-commit run --all-files
```

Useful commands:

```sh
uv run satrepo --help
uv run satrepo-shim --help
```

## Local Smoke Test

Create a repo:

```sh
uv run satrepo init alice.example \
  --pds-url https://satrepo.example \
  --root ./alice-repo
```

Create and commit a Bluesky post:

```sh
uv run satrepo bsky post "hello from satrepo" --root ./alice-repo
uv run satrepo status --root ./alice-repo
uv run satrepo commit --root ./alice-repo
uv run satrepo log --root ./alice-repo
uv run satrepo verify --root ./alice-repo
```

Serve the generated static site locally:

```sh
python -m http.server 8081 --directory ./alice-repo/site
```

Run the shim against that static origin:

```sh
uv run satrepo-shim \
  --origin http://127.0.0.1:8081 \
  --host 127.0.0.1 \
  --port 8781
```

Query the shim:

```sh
DID=$(jq -r .did ./alice-repo/.satrepo/config.json)
curl "http://127.0.0.1:8781/xrpc/com.atproto.sync.getLatestCommit?did=$DID"
curl "http://127.0.0.1:8781/xrpc/com.atproto.repo.describeRepo?repo=$DID"
```

## Standard.site

`satrepo` has convenience commands for Standard.site records:

```sh
uv run satrepo standard publication "Alice Notes" \
  --url https://satrepo.example \
  --description "Small notes from Alice" \
  --root ./alice-repo

uv run satrepo standard document "First Note" "# First Note

This was published from satrepo." \
  --path /first-note \
  --tag satrepo \
  --root ./alice-repo

uv run satrepo commit --root ./alice-repo
```

On commit, `satrepo` renders committed `site.standard.*` records into static
HTML under `site/`. Generated document pages include the required
`<link rel="site.standard.document" href="at://...">` tag, and stale generated
pages are removed when documents are deleted.

The renderer intentionally uses committed repo state, not dirty worktree files.

## PLC Flow

`init` creates local did:plc genesis material, but it does not publish anything
to `plc.directory`.

Inspect local PLC state:

```sh
uv run satrepo plc show --root ./alice-repo
```

Update the DID document's PDS service URL before publishing:

```sh
uv run satrepo plc update \
  --pds-url https://satrepo.example \
  --root ./alice-repo
```

Submit the local genesis operation to a PLC directory:

```sh
uv run satrepo plc submit --root ./alice-repo
```

Only submit test identities you are comfortable registering publicly.

## Public Deployment Sketch

A minimal public setup has one repo per shim process:

```text
https://satrepo.example/
  /.well-known/atproto-did              -> static file from site/
  /.well-known/site.standard.publication -> static file from site/
  /repo/...                             -> static files from site/repo/
  /xrpc/...                             -> satrepo-shim on localhost
  /...                                  -> generated Standard.site HTML
```

Example nginx shape:

```nginx
location = /.well-known/atproto-did {
  alias /path/to/repo/site/.well-known/atproto-did;
  default_type text/plain;
  add_header Access-Control-Allow-Origin "*" always;
}

location = /.well-known/site.standard.publication {
  alias /path/to/repo/site/.well-known/site.standard.publication;
  default_type text/plain;
  add_header Access-Control-Allow-Origin "*" always;
}

location /repo/ {
  alias /path/to/repo/site/repo/;
  default_type application/octet-stream;
  add_header Access-Control-Allow-Origin "*" always;
}

location /xrpc/ {
  proxy_pass http://127.0.0.1:8781;
  proxy_http_version 1.1;
  proxy_set_header Host $host;
  proxy_set_header X-Forwarded-Proto https;
  proxy_read_timeout 10m;
}

location / {
  root /path/to/repo/site;
  try_files $uri $uri/index.html =404;
}
```

Run the shim:

```sh
uv run satrepo-shim \
  --origin https://satrepo.example \
  --host 127.0.0.1 \
  --port 8781 \
  --service-did did:web:satrepo.example
```

## Current Limitations

- The shim is single-origin/single-repo per process.
- A future multi-repo hosted shim would need its own registration layer and a
  stable PDS-wide firehose sequence across repos.
- Blob authoring is not a complete workflow yet.
- Record validation is intentionally limited.
- Static repo artifacts are readable and inspectable, but the format is still a
  prototype and may change.
