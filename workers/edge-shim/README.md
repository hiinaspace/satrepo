# satrepo edge Worker shim

This is a TypeScript version of the read-only satrepo shim for fetch-based edge
and serverless runtimes. It reads a generated satrepo `site/` directory from any
static HTTP origin and exposes the read/sync XRPCs expected by ATProto clients.

The source uses web platform APIs like `fetch`, `Request`, `Response`, and
`WebSocket`. The checked-in `wrangler.toml` is Cloudflare-specific deployment
config for the live smoke test:

```text
SATREPO_ORIGIN = "https://satrepo-static-site.pages.dev"
```

## Commands

```sh
npm install
npm run check
npm run dev
npm run deploy
```

## Static origin

Deploy a generated satrepo site separately, for example:

```sh
wrangler pages project create satrepo-static-site --production-branch main
wrangler pages deploy /path/to/satrepo/site \
  --project-name satrepo-static-site \
  --branch main
```

Then set `SATREPO_ORIGIN` in `wrangler.toml` to that static host and deploy the
shim.

The shim is intentionally single-origin/single-repo, matching the Python shim
prototype. A public multi-repo hosted shim would need a registration layer and
shared firehose sequence state.
