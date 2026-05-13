# satrepo

`satrepo` is a prototype for authoring an AT Protocol repository locally,
publishing the signed repo as static files, and serving it back to the current
ATProto network through a small read-only PDS-shaped shim.

This is roughly analogous to Git's [dumb HTTP protocol][git-dumb-http] mode for
atproto, where an actual PDS is closer to Git's smart protocol.

This is not a production PDS. There is no OAuth, password login,
hosted write API, account recovery, blob upload API, or multi-user service.

## Why

I finally got around to trying atproto, after it seemed clear enough that I can
in fact try things out without giving bsky.app an email/password pair. You can
do this by [running your own PDS][pds], with a [did:plc][did-plc] identity (or
`did:web`, but I'm personally okay enough with did:plc over the tradeoffs of
did:web). And the PDS is quite light as far as other things I've self-hosted:
it is basically a service process around SQLite state, blob files, HTTP XRPC
APIs, and a sync WebSocket.

However, as I learned more about how atproto works, I kept squinting at the PDS
and wondering if it could be pulled apart further into something closer to the
simplest form of putting stuff on the internet I know: uploading a bunch of HTML
files and an RSS feed to a static site host that serves them, like GitHub Pages,
Neocities, or even some ancient cPanel thing. Grugware deploys, in other words.

It also reminded me of Git's dumb HTTP mode, where you can serve a bare Git repo
from a static site host and Git still knows how to find all the files it needs
when cloning. If you replace the semantics of a PDS needing a live WebSocket
firehose with the RSS model of "please poll this URL every so often", and the
signing part with something more like GPG or SSH (keys in your local dir), then
a dumb HTTP mode for atproto felt possible.

I also squinted at the [Bridgy Fed docs][bridgy-fed] a lot and felt that if you
can convert microformatted HTML and RSS into atproto, then you could also
convert static MST stuff on disk into the "real PDS" form.

It turns out it is possible, and there were some
[earlier discussions][static-atproto-discussion] about essentially this shape. I
didn't see any actual implementations though, so I made this. It seems to work.
To interact with the rest of the network (like actual bsky.app or Leaflet),
there's a shim process that takes an address to your static site host and
dynamically serves the PDS XRPC endpoints from it, as well as polling the static
site host and pushing stuff on its firehose endpoint. Which I know is a little
silly; if you're capable of hosting something more dynamic than a static site
host (WebSockets and stuff), then you might as well run the real PDS. However,
in theory the shim could be more like a public service (like fed.brid.gy), that
many people could register their static repo URLs to, it'd poll all of them and
serve the XRPC endpoints and firehose. If I sit on this impl for a while and it
still seems worth doing, I'll probably run one.

## Demo

I'm running this live at
[satrepo-dev.hiina.space](https://satrepo-dev.hiina.space/). You can inspect the
repo from [Taproot][demo-taproot], see posts on [bsky.app][demo-bsky], and read
the Standard.site smoke test at
[satrepo-dev.hiina.space/standard-site-smoke-test][demo-standard-site].

There is also a serverless smoke test: the static repo is mirrored to
[Cloudflare Pages][demo-pages], and an [edge Worker shim][demo-worker] serves
the same read-only XRPC shim from that Pages origin.

## Install

Easiest is using `uv`:

```sh
uv tool install git+https://github.com/hiinaspace/satrepo
```

Or clone the repo and run `uv` in it.

## Usage

This essentially mirrors the experience of making, modifying, and publishing a
Git repo in dumb HTTP mode.

Create a repo:

```sh
uv run satrepo init alice.example \
  --pds-url https://satrepo.example \
  --root ./alice-repo
```

This creates a repo like:

```text
alice-repo/
  worktree/       editable ATProto record JSON, grouped by collection
  .satrepo/       local repo state, refs, event log, CARs, manifests
  site/           generated static files to serve over HTTP
```

You can then add arbitrary atproto collections into the `worktree`,
and once ready `commit` them to turn them into a proper signed repo.
In practice it's sort of tricky to construct the JSON though. For
the usual Bsky.app microblogging posts, there's a helper:

```sh
uv run satrepo bsky post "hello from satrepo" --root ./alice-repo
```

Check the status and then commit with:

```sh
uv run satrepo status --root ./alice-repo
uv run satrepo commit --root ./alice-repo
uv run satrepo log --root ./alice-repo
uv run satrepo verify --root ./alice-repo
```

Then you can serve the generated static site locally:

```sh
python -m http.server 8081 --directory ./alice-repo/site
```

And run the shim against that static origin so it appears like
a "real" PDS again:

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

This all runs locally with no connection to the rest of the network. There are
staging/sandbox pieces in the ecosystem, including a
[PLC staging directory][plc-staging] and historical
[federation sandbox][federation-sandbox] work, but I haven't found a currently
documented end-to-end public testnet that exercises PLC, relays, appviews, and
popular apps like production does. So the realistic integration test is usually
a disposable production identity.

First make the site and shim public (e.g. through a reverse proxy). You could
even publish the site to a static site host if you wanted, and point the shim to
whatever its address is.

Once that's working, publish the PLC identity:

```sh
uv run satrepo plc submit --root ./alice-repo
```

Generic inspection tools like [Taproot][taproot] and [pdscheck][pdscheck] should
then be able to look up your repo and read the files, as proxied through the
shim onto your host. Appviews like [bsky.app][bsky-app] or [Leaflet][leaflet]
also need to crawl or index the repo and recognize the collections you're
publishing.

### Standard.site

`satrepo` has convenience commands for the [Standard.site][standard-site]
lexicons, since they seem to be an early common shape for long-form publishing
on atproto. Standard.site records describe a publication and documents in the
ATProto repo, while the documents also have canonical HTTP pages. Since satrepo
already generates a static HTTP site, it is useful to render those pages
alongside the repo artifacts:

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

The generated pages are very barebones. For real sites you'd probably be better
off with whatever static site generator you like, and with the Standard.site
publication URL pointing at that site. But the generated pages are enough to
pass the [Standard.site validator][standard-site-validator] at least.

### PLC Flow

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

### Remote Signing Experiment

There is an experimental repo-signing split documented in
[`docs/remote-signing-pds.md`](docs/remote-signing-pds.md). It lets
`satrepo commit` delegate commit signatures to `satrepo signer serve`, as a
small proof for a PDS shape where OAuth/write handling and key custody are
separate.

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

## Edge Worker Proof

Cloudflare's [Python Workers][cloudflare-python-workers] run on Pyodide, but
package deployment is still limited enough that reusing the Python `aiohttp`
shim directly is not the right shape yet. The repo includes a small TypeScript
Worker-style shim at `workers/edge-shim/` instead.

The shim is written around web platform APIs like `fetch`, `Request`,
`Response`, and `WebSocket`, which are the rough portability target for
[WinterTC][wintertc] runtimes. The checked-in deployment config is still
Cloudflare-specific because that is the live host for this proof.

The current proof uses:

```text
https://satrepo-static-site.pages.dev/       -> static site/repo files
https://satrepo-shim.hiinaops.workers.dev/  -> Worker XRPC shim
```

Deploy the static site to Cloudflare Pages:

```sh
wrangler pages project create satrepo-static-site --production-branch main
wrangler pages deploy /path/to/satrepo-checkout/site \
  --project-name satrepo-static-site \
  --branch main
```

Deploy the Worker:

```sh
cd workers/edge-shim
npm install
npm run check
npm run deploy
```

Set `SATREPO_ORIGIN` in `workers/edge-shim/wrangler.toml` to the static
host you want the Worker to read from. The Worker implements the read-only sync
and repo XRPCs from static files, including `subscribeRepos` over Workers
[WebSockets][cloudflare-workers-websockets].

## Current Limitations

- The shim is single-origin/single-repo per process.
- A future multi-repo hosted shim would need its own registration layer and a
  stable PDS-wide firehose sequence across repos.
- Blob authoring is not a complete workflow yet.
- Record validation is intentionally limited.
- Static repo artifacts are readable and inspectable, but the format is still a
  prototype and may change.

## License

[WTFPL](LICENSE).

## Well I read your entire readme and I still don't get it

The true purpose of all of this is to convince me the, uh, prompter of this
fine slopject that yes I do understand atproto and at least some of the "atproto
theory" (a la Peter Naur's
[Programming as Theory Building][programming-as-theory-building]). And also
hopefully help you the reader also understand atproto better. Personally, I've
vaguely kept up with "decentralized stuff" since BitTorrent, and in particular
I am quite familiar with Git's internals. Atproto always interested me but I
didn't really care that much about microblogging. I read other explainers like
[Atproto for distributed systems engineers][atproto-distsys] and
[A Social Filesystem][atproto-social-filesystem], but I still didn't really grok
it. Until I finally decided to set up the official PDS and kick the tires, and
then do this project. So I think it's succeeded. We'll see if it's actually
useful to anyone else.

[bridgy-fed]: https://fed.brid.gy/docs
[bsky-app]: https://bsky.app/
[cloudflare-python-workers]: https://developers.cloudflare.com/workers/languages/python/
[cloudflare-workers-websockets]: https://developers.cloudflare.com/workers/runtime-apis/websockets/
[demo-bsky]: https://bsky.app/profile/satrepo-dev.hiina.space
[demo-pages]: https://satrepo-static-site.pages.dev/
[demo-standard-site]: https://satrepo-dev.hiina.space/standard-site-smoke-test/
[demo-taproot]: https://atproto.at/uri/at://did:plc:6sgey5rgl4ce5fvssg4gzkph
[demo-worker]: https://satrepo-shim.hiinaops.workers.dev/xrpc/_health
[did-plc]: https://web.plc.directory/spec/v0.1/did-plc
[federation-sandbox]: https://atproto.com/blog/building-on-atproto
[git-dumb-http]: https://git-scm.com/docs/http-protocol
[leaflet]: https://about.leaflet.pub/
[pds]: https://github.com/bluesky-social/pds
[pdscheck]: https://pdscheck.dev/
[plc-staging]: https://web.plc.staging.bsky.dev/
[standard-site]: https://standard.site/
[standard-site-validator]: https://site-validator.fly.dev/
[static-atproto-discussion]: https://github.com/bluesky-social/atproto/discussions/2300
[taproot]: https://atproto.at/
[wintertc]: https://wintertc.org/

[atproto-distsys]: https://atproto.com/articles/atproto-for-distsys-engineers
[atproto-social-filesystem]: https://overreacted.io/a-social-filesystem/
[programming-as-theory-building]: https://pages.cs.wisc.edu/~remzi/Naur.pdf
