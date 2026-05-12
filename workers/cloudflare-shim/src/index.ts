import { decode, encode } from "@atproto/lex-cbor";
import { BlockMap, MemoryBlockstore, Repo, blocksToCarFile, readCar } from "@atproto/repo";
import { parseCid, type Cid } from "@atproto/lex-data";

const CAR_MIME_TYPE = "application/vnd.ipld.car";
const JSON_TYPE = "application/json; charset=utf-8";

type Env = {
  SATREPO_ORIGIN: string;
  SERVICE_DID?: string;
  POLL_INTERVAL_MS?: string;
};

type Manifest = {
  did: string;
  handle: string;
  head?: {
    cid: string;
    rev: string;
  };
  lastSeq?: number;
  events?: Array<{
    seq: number;
    type: string;
    path: string;
  }>;
  blobs?: Record<string, unknown>;
};

type StaticEvent = {
  type: string;
  seq: number;
  repo?: string;
  did?: string;
  blocks?: string;
  commit?: string;
  since?: string | null;
  prevData?: string | null;
  ops?: Array<Record<string, unknown>>;
  blobs?: string[];
  [key: string]: unknown;
};

type LoadedRepo = {
  manifest: Manifest;
  repo: Repo;
  storage: MemoryBlockstore;
  root: Cid;
};

type RepoRecord = {
  uri: string;
  cid: string;
  value: unknown;
};

class XrpcError extends Error {
  name: string;
  status: number;

  constructor(name: string, message: string, status = 400) {
    super(message);
    this.name = name;
    this.status = status;
  }
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    try {
      return await handleRequest(request, env, ctx);
    } catch (error) {
      if (error instanceof XrpcError) {
        return json({ error: error.name, message: error.message }, error.status);
      }
      const message = error instanceof Error ? error.message : String(error);
      return json({ error: "InternalServerError", message }, 500);
    }
  },
};

async function handleRequest(
  request: Request,
  env: Env,
  ctx: ExecutionContext,
): Promise<Response> {
  if (request.method === "OPTIONS") {
    return withCors(new Response(null, { status: 204 }));
  }

  if (request.method !== "GET") {
    return xrpcError("MethodNotAllowed", "Only GET is supported", 405);
  }

  const url = new URL(request.url);
  if (url.pathname === "/xrpc/_health") {
    const manifest = await readManifest(env);
    return withRepoRev(
      json({
        ok: true,
        did: manifest.did,
        head: manifest.head?.cid ?? null,
        lastSeq: manifest.lastSeq ?? 0,
      }),
      manifest,
    );
  }

  const prefix = "/xrpc/";
  if (!url.pathname.startsWith(prefix)) {
    return xrpcError("NotFound", "Not found", 404);
  }

  const method = url.pathname.slice(prefix.length);
  if (method === "com.atproto.sync.subscribeRepos") {
    return subscribeRepos(request, env, ctx);
  }

  const response = await routeXrpc(method, url.searchParams, env);
  if (response.status < 400) {
    return withRepoRev(response, await readManifest(env));
  }
  return response;
}

async function routeXrpc(method: string, params: URLSearchParams, env: Env): Promise<Response> {
  switch (method) {
    case "com.atproto.sync.getLatestCommit":
      return json(latestCommit(await manifestForDid(env, required(params, "did"))));
    case "com.atproto.sync.getRepo":
      await manifestForDid(env, required(params, "did"));
      return bytes(await originBytes(env, "repo/snapshot.car"), CAR_MIME_TYPE);
    case "com.atproto.sync.getRepoStatus": {
      const did = required(params, "did");
      const manifest = await manifestForDid(env, did);
      return json({ did, active: true, rev: latestCommit(manifest).rev });
    }
    case "com.atproto.sync.listRepos":
      return json(await listRepos(env, params));
    case "com.atproto.sync.getRecord":
      return bytes(
        await getRecordCar(
          env,
          required(params, "did"),
          required(params, "collection"),
          required(params, "rkey"),
        ),
        CAR_MIME_TYPE,
      );
    case "com.atproto.sync.getBlocks":
      return bytes(
        await getBlocksCar(env, required(params, "did"), params.getAll("cids")),
        CAR_MIME_TYPE,
      );
    case "com.atproto.sync.listBlobs":
      await manifestForDid(env, required(params, "did"));
      return json({ cids: Object.keys((await readManifest(env)).blobs ?? {}).sort() });
    case "com.atproto.sync.getBlob":
      return xrpcError("BlobNotFound", "Blob not found", 404);
    case "com.atproto.repo.describeRepo":
      return json(await describeRepo(env, required(params, "repo")));
    case "com.atproto.repo.getRecord":
      return json(
        await getRepoRecord(
          env,
          required(params, "repo"),
          required(params, "collection"),
          required(params, "rkey"),
          params.get("cid"),
        ),
      );
    case "com.atproto.repo.listRecords":
      return json(
        await listRecords(
          env,
          required(params, "repo"),
          required(params, "collection"),
          boundedLimit(params.get("limit"), 50, 100),
          params.get("cursor"),
          queryBool(params.get("reverse")),
        ),
      );
    case "com.atproto.identity.resolveHandle":
      return json(await resolveHandle(env, required(params, "handle")));
    case "com.atproto.server.describeServer":
      return json(await describeServer(env));
    default:
      return xrpcError("NotFound", `Unknown XRPC method: ${method}`, 404);
  }
}

async function subscribeRepos(
  request: Request,
  env: Env,
  ctx: ExecutionContext,
): Promise<Response> {
  if (request.headers.get("Upgrade") !== "websocket") {
    return xrpcError("InvalidRequest", "Expected WebSocket upgrade", 400);
  }

  const pair = new WebSocketPair();
  const [client, server] = Object.values(pair);
  server.accept();

  ctx.waitUntil(pumpSubscribeRepos(server, new URL(request.url), env));
  return new Response(null, { status: 101, webSocket: client });
}

async function pumpSubscribeRepos(ws: WebSocket, url: URL, env: Env): Promise<void> {
  let cursor: number;
  try {
    const manifest = await readManifest(env);
    cursor = subscriptionCursor(url.searchParams.get("cursor"), manifest.lastSeq ?? 0);
  } catch (error) {
    safeSend(ws, encodeErrorFrame("InvalidRequest", errorMessage(error)));
    ws.close();
    return;
  }

  const pollInterval = Number(env.POLL_INTERVAL_MS ?? "2000") || 2000;
  let closed = false;
  ws.addEventListener("close", () => {
    closed = true;
  });
  ws.addEventListener("error", () => {
    closed = true;
  });

  try {
    while (!closed) {
      let hadEvent = false;
      try {
        const events = await eventsAfter(env, cursor);
        const event = events[0];
        if (event) {
          hadEvent = true;
          if (!safeSend(ws, await encodeMessageFrame(env, event))) {
            closed = true;
          } else {
            cursor = Math.max(cursor, Number(event.seq));
          }
        }
      } catch (error) {
        safeSend(ws, encodeErrorFrame("OriginUnavailable", errorMessage(error)));
        ws.close();
        return;
      }
      await sleep(closed ? 0 : 25);
      if (!hadEvent) {
        await sleep(pollInterval);
      }
    }
  } catch (error) {
    if (!errorMessage(error).includes("Network connection lost")) {
      safeSend(ws, encodeErrorFrame("InternalServerError", errorMessage(error)));
    }
  }
}

async function readManifest(env: Env): Promise<Manifest> {
  return originJson<Manifest>(env, "repo/manifest.json");
}

async function didDoc(env: Env): Promise<unknown> {
  return originJson(env, "did.json");
}

async function originJson<T = unknown>(env: Env, path: string): Promise<T> {
  const response = await originFetch(env, path);
  if (!response.ok) {
    throw new Error(`origin returned ${response.status} for ${path}`);
  }
  return response.json() as Promise<T>;
}

async function originBytes(env: Env, path: string): Promise<Uint8Array> {
  const response = await originFetch(env, path);
  if (!response.ok) {
    throw new Error(`origin returned ${response.status} for ${path}`);
  }
  return new Uint8Array(await response.arrayBuffer());
}

function originFetch(env: Env, path: string): Promise<Response> {
  const base = env.SATREPO_ORIGIN.replace(/\/+$/, "");
  return fetch(`${base}/${path.replace(/^\/+/, "")}`, {
    headers: { "User-Agent": "satrepo-cloudflare-shim" },
  });
}

async function manifestForDid(env: Env, did: string): Promise<Manifest> {
  const manifest = await readManifest(env);
  if (manifest.did !== did) {
    throw new XrpcError("RepoNotFound", `Could not find repo for DID: ${did}`);
  }
  return manifest;
}

async function manifestForRepo(env: Env, repo: string): Promise<Manifest> {
  const manifest = await readManifest(env);
  if (repo !== manifest.did && repo !== manifest.handle) {
    throw new XrpcError("RepoNotFound", `Could not find repo: ${repo}`);
  }
  return manifest;
}

function latestCommit(manifest: Manifest): { cid: string; rev: string } {
  if (!manifest.head) {
    throw new XrpcError("RepoNotFound", `Could not find root for DID: ${manifest.did}`);
  }
  return manifest.head;
}

async function listRepos(env: Env, params: URLSearchParams): Promise<unknown> {
  const manifest = await readManifest(env);
  if (params.get("cursor") && manifest.did <= params.get("cursor")!) {
    return { repos: [] };
  }
  const limit = boundedLimit(params.get("limit"), 500, 500);
  const head = latestCommit(manifest);
  return {
    repos: [
      {
        did: manifest.did,
        head: head.cid,
        rev: head.rev,
        active: true,
      },
    ].slice(0, limit),
  };
}

async function describeRepo(env: Env, repo: string): Promise<unknown> {
  const manifest = await manifestForRepo(env, repo);
  const loaded = await loadRepo(env);
  const contents = await loaded.repo.getContents();
  return {
    handle: manifest.handle,
    did: manifest.did,
    didDoc: await didDoc(env),
    collections: Object.keys(contents).sort(),
    handleIsCorrect: true,
  };
}

async function resolveHandle(env: Env, handle: string): Promise<unknown> {
  const manifest = await readManifest(env);
  if (handle !== manifest.handle) {
    throw new XrpcError("InvalidRequest", "Unable to resolve handle");
  }
  return { did: manifest.did };
}

async function describeServer(env: Env): Promise<unknown> {
  const manifest = await readManifest(env);
  const index = manifest.handle.indexOf(".");
  const domain = index >= 0 ? manifest.handle.slice(index) : `.${manifest.handle}`;
  return {
    did: env.SERVICE_DID ?? "did:web:localhost",
    availableUserDomains: [domain],
    inviteCodeRequired: true,
  };
}

async function loadRepo(env: Env): Promise<LoadedRepo> {
  const manifest = await readManifest(env);
  const snapshot = await originBytes(env, "repo/snapshot.car");
  const { roots, blocks } = await readCar(snapshot);
  if (roots.length < 1) {
    throw new XrpcError("InvalidRequest", "snapshot.car has no root");
  }

  const root = roots[0];
  const head = latestCommit(manifest);
  if (!root.equals(parseCid(head.cid))) {
    throw new XrpcError("InvalidRequest", "snapshot.car root does not match manifest head");
  }

  const storage = new MemoryBlockstore(blocks);
  const repo = await Repo.load(storage, root);
  return { manifest, repo, storage, root };
}

async function getRepoRecord(
  env: Env,
  repo: string,
  collection: string,
  rkey: string,
  cid: string | null,
): Promise<RepoRecord> {
  const manifest = await manifestForRepo(env, repo);
  const loaded = await loadRepo(env);
  const recordCid = await findRecordCid(loaded, collection, rkey);
  if (cid && recordCid.toString() !== cid) {
    throw new XrpcError("RecordNotFound", `Record not found: ${collection}/${rkey}`);
  }
  return recordJson(manifest.did, collection, rkey, recordCid, await loaded.storage.readRecord(recordCid));
}

async function listRecords(
  env: Env,
  repo: string,
  collection: string,
  limit: number,
  cursor: string | null,
  reverse: boolean,
): Promise<unknown> {
  const manifest = await manifestForRepo(env, repo);
  const loaded = await loadRepo(env);
  const prefix = `${collection}/`;
  let entries = await loaded.repo.data.listWithPrefix(prefix);

  if (cursor) {
    const cursorKey = `${collection}/${cursor}`;
    entries = entries.filter((entry) => (reverse ? entry.key < cursorKey : entry.key > cursorKey));
  }
  if (reverse) {
    entries = entries.reverse();
  }

  const page = entries.slice(0, limit + 1);
  const records = await Promise.all(
    page.slice(0, limit).map(async (entry) => {
      const [, rkey] = entry.key.split("/", 2);
      return recordJson(
        manifest.did,
        collection,
        rkey,
        entry.value,
        await loaded.storage.readRecord(entry.value),
      );
    }),
  );

  const result: { records: RepoRecord[]; cursor?: string } = { records };
  if (page.length > limit && records.length > 0) {
    result.cursor = page[limit - 1].key.split("/", 2)[1];
  }
  return result;
}

async function getRecordCar(
  env: Env,
  did: string,
  collection: string,
  rkey: string,
): Promise<Uint8Array> {
  await manifestForDid(env, did);
  const loaded = await loadRepo(env);
  const path = `${collection}/${rkey}`;
  const cid = await findRecordCid(loaded, collection, rkey);
  const recordBytes = await loaded.storage.getBytes(cid);
  if (!recordBytes) {
    throw new XrpcError("BlockNotFound", `Record block not found: ${cid.toString()}`);
  }

  const blocks = new BlockMap();
  blocks.set(cid, recordBytes);
  blocks.addMap(await loaded.repo.data.getCoveringProof(path));
  return blocksToCarFile(cid, blocks);
}

async function getBlocksCar(env: Env, did: string, cids: string[]): Promise<Uint8Array> {
  const manifest = await manifestForDid(env, did);
  const blocks = new BlockMap();
  for (const cid of cids) {
    const parsed = parseCid(cid);
    blocks.set(parsed, await readBlockBytes(env, parsed));
  }
  return blocksToCarFile(parseCid(latestCommit(manifest).cid), blocks);
}

async function findRecordCid(loaded: LoadedRepo, collection: string, rkey: string): Promise<Cid> {
  const path = `${collection}/${rkey}`;
  const cid = await loaded.repo.data.get(path);
  if (!cid) {
    throw new XrpcError("RecordNotFound", `Record not found: ${path}`);
  }
  return cid;
}

async function readBlockBytes(env: Env, cid: Cid): Promise<Uint8Array> {
  const tried = new Set<string>();
  for (const candidate of [cid.toString(), base58btcEncode(cid.bytes)]) {
    if (tried.has(candidate)) {
      continue;
    }
    tried.add(candidate);
    try {
      return await originBytes(env, `repo/blocks/${candidate}`);
    } catch {
      // Try the next common CID string encoding.
    }
  }
  throw new XrpcError("BlockNotFound", `No block found for CID ${cid.toString()}`);
}

async function eventsAfter(env: Env, cursor: number): Promise<StaticEvent[]> {
  const manifest = await readManifest(env);
  const events = [];
  for (const entry of manifest.events ?? []) {
    if (Number(entry.seq) > cursor) {
      events.push(await originJson<StaticEvent>(env, entry.path));
    }
  }
  return events.sort((a, b) => Number(a.seq) - Number(b.seq));
}

async function encodeMessageFrame(env: Env, event: StaticEvent): Promise<Uint8Array> {
  const header = { op: 1, t: event.type };
  return concat(encode(header), encode((await eventPayload(env, event)) as never));
}

function encodeErrorFrame(error: string, message?: string): Uint8Array {
  const body: Record<string, string> = { error };
  if (message) {
    body.message = message;
  }
  return concat(encode({ op: -1 }), encode(body));
}

async function eventPayload(env: Env, event: StaticEvent): Promise<Record<string, unknown>> {
  if (event.type === "#commit") {
    return commitPayload(env, event);
  }
  if (event.type === "#sync") {
    const payload = didEventPayload(event);
    payload.blocks = await originBytes(env, requiredEventString(event, "blocks"));
    return payload;
  }
  if (event.type === "#identity" || event.type === "#account") {
    return didEventPayload(event);
  }
  return withoutType(event);
}

async function commitPayload(env: Env, event: StaticEvent): Promise<Record<string, unknown>> {
  const payload = withoutType(event);
  payload.rebase ??= false;
  payload.tooBig ??= false;
  payload.blobs ??= [];
  payload.commit = parseCid(requiredEventString(event, "commit"));
  payload.blocks = await originBytes(env, requiredEventString(event, "blocks"));
  payload.since = await normalizeSince(env, event.since);
  payload.ops = (event.ops ?? []).map((op) => {
    const copy = { ...op };
    if (typeof copy.cid === "string") {
      copy.cid = parseCid(copy.cid);
    }
    if (typeof copy.prev === "string") {
      copy.prev = parseCid(copy.prev);
    }
    return copy;
  });
  payload.blobs = (event.blobs ?? []).map((cid) => parseCid(cid));
  if (event.prevData) {
    payload.prevData = parseCid(event.prevData);
  }
  return payload;
}

async function normalizeSince(env: Env, since: unknown): Promise<unknown> {
  if (typeof since !== "string") {
    return since;
  }
  try {
    const commit = decode(await readBlockBytes(env, parseCid(since)));
    if (isRecord(commit) && typeof commit.rev === "string") {
      return commit.rev;
    }
  } catch {
    return since;
  }
  return since;
}

function didEventPayload(event: StaticEvent): Record<string, unknown> {
  const payload = withoutType(event);
  if (typeof payload.repo === "string" && typeof payload.did !== "string") {
    payload.did = payload.repo;
    delete payload.repo;
  }
  return payload;
}

function withoutType(event: StaticEvent): Record<string, unknown> {
  const { type: _type, ...payload } = event;
  return payload;
}

function recordJson(
  did: string,
  collection: string,
  rkey: string,
  cid: Cid,
  value: unknown,
): RepoRecord {
  return {
    uri: `at://${did}/${collection}/${rkey}`,
    cid: cid.toString(),
    value,
  };
}

function required(params: URLSearchParams, name: string): string {
  const value = params.get(name);
  if (value === null) {
    throw new XrpcError("InvalidRequest", `missing required query parameter: ${name}`);
  }
  return value;
}

function requiredEventString(event: StaticEvent, name: string): string {
  const value = event[name];
  if (typeof value !== "string") {
    throw new Error(`event is missing string field: ${name}`);
  }
  return value;
}

function boundedLimit(value: string | null, fallback: number, max: number): number {
  if (value === null) {
    return fallback;
  }
  const limit = Number(value);
  if (!Number.isInteger(limit) || limit < 1 || limit > max) {
    throw new XrpcError("InvalidRequest", `limit must be between 1 and ${max}`);
  }
  return limit;
}

function queryBool(value: string | null): boolean {
  return value === "true" || value === "1" || value === "yes";
}

function subscriptionCursor(raw: string | null, lastSeq: number): number {
  if (raw === null) {
    return lastSeq;
  }
  const cursor = Number(raw);
  if (!Number.isInteger(cursor)) {
    throw new XrpcError("InvalidRequest", "cursor must be an integer");
  }
  if (cursor > lastSeq) {
    throw new XrpcError("FutureCursor", "Cursor in the future.");
  }
  return cursor;
}

function json(value: unknown, status = 200): Response {
  return withCors(
    new Response(JSON.stringify(value), {
      status,
      headers: { "Content-Type": JSON_TYPE },
    }),
  );
}

function bytes(value: Uint8Array, contentType: string): Response {
  const body = value.buffer.slice(value.byteOffset, value.byteOffset + value.byteLength);
  return withCors(new Response(body as ArrayBuffer, { headers: { "Content-Type": contentType } }));
}

function xrpcError(name: string, message: string, status: number): Response {
  return json({ error: name, message }, status);
}

function withCors(response: Response): Response {
  response.headers.set("Access-Control-Allow-Origin", "*");
  response.headers.set("Access-Control-Allow-Methods", "GET, OPTIONS");
  response.headers.set(
    "Access-Control-Allow-Headers",
    "Authorization, Content-Type, Atproto-Accept-Labelers",
  );
  response.headers.set("Access-Control-Expose-Headers", "Atproto-Repo-Rev");
  return response;
}

function withRepoRev(response: Response, manifest: Manifest): Response {
  if (manifest.head?.rev) {
    response.headers.set("Atproto-Repo-Rev", manifest.head.rev);
  }
  return response;
}

function concat(left: Uint8Array, right: Uint8Array): Uint8Array {
  const out = new Uint8Array(left.length + right.length);
  out.set(left, 0);
  out.set(right, left.length);
  return out;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function safeSend(ws: WebSocket, message: string | ArrayBuffer | Uint8Array): boolean {
  try {
    ws.send(message);
    return true;
  } catch {
    return false;
  }
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

const BASE58BTC_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";

function base58btcEncode(bytes: Uint8Array): string {
  if (bytes.length === 0) {
    return "z";
  }

  const digits = [0];
  for (const byte of bytes) {
    let carry = byte;
    for (let index = 0; index < digits.length; index += 1) {
      const value = digits[index] * 256 + carry;
      digits[index] = value % 58;
      carry = Math.floor(value / 58);
    }
    while (carry > 0) {
      digits.push(carry % 58);
      carry = Math.floor(carry / 58);
    }
  }

  let output = "z";
  for (const byte of bytes) {
    if (byte !== 0) {
      break;
    }
    output += BASE58BTC_ALPHABET[0];
  }
  for (let index = digits.length - 1; index >= 0; index -= 1) {
    output += BASE58BTC_ALPHABET[digits[index]];
  }
  return output;
}
