"""Dynamic sync shim server for a satrepo static origin."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from aiohttp import web

from .origin import OriginError
from .repo_view import CAR_MIME_TYPE, RepoViewError, StaticRepoView

Handler = Callable[[StaticRepoView, web.Request], web.StreamResponse]
REPO_VIEW_KEY = web.AppKey("repo_view", StaticRepoView)


def create_app(*, origin: str, service_did: str = "did:web:localhost") -> web.Application:
    app = web.Application()
    app[REPO_VIEW_KEY] = StaticRepoView.from_origin(origin, service_did=service_did)
    app.router.add_get("/xrpc/_health", health)
    app.router.add_get("/xrpc/{method}", xrpc)
    return app


async def health(request: web.Request) -> web.Response:
    view = _view(request)
    return web.json_response(view.health())


async def xrpc(request: web.Request) -> web.StreamResponse:
    method = request.match_info["method"]
    handler = XRPC_HANDLERS.get(method)
    if not handler:
        raise web.HTTPNotFound(
            text=f"Unknown XRPC method: {method}",
            content_type="text/plain",
        )

    view = _view(request)
    try:
        return handler(view, request)
    except RepoViewError as exc:
        return _xrpc_error(exc.name, str(exc), status=exc.status)
    except OriginError as exc:
        return _xrpc_error("OriginUnavailable", str(exc), status=502)


def get_latest_commit(view: StaticRepoView, request: web.Request) -> web.Response:
    return web.json_response(view.latest_commit(_required_query(request, "did")))


def get_repo(view: StaticRepoView, request: web.Request) -> web.Response:
    body = view.snapshot_car(_required_query(request, "did"))
    return web.Response(body=body, content_type=CAR_MIME_TYPE)


def get_repo_status(view: StaticRepoView, request: web.Request) -> web.Response:
    return web.json_response(view.repo_status(_required_query(request, "did")))


def list_repos(view: StaticRepoView, request: web.Request) -> web.Response:
    limit = int(request.query.get("limit", "500"))
    cursor = request.query.get("cursor")
    return web.json_response(view.list_repos(limit=limit, cursor=cursor))


def get_record(view: StaticRepoView, request: web.Request) -> web.Response:
    body = view.get_record_car(
        _required_query(request, "did"),
        _required_query(request, "collection"),
        _required_query(request, "rkey"),
    )
    return web.Response(body=body, content_type=CAR_MIME_TYPE)


def get_blocks(view: StaticRepoView, request: web.Request) -> web.Response:
    body = view.get_blocks_car(
        _required_query(request, "did"),
        list(request.query.getall("cids", [])),
    )
    return web.Response(body=body, content_type=CAR_MIME_TYPE)


def list_blobs(view: StaticRepoView, request: web.Request) -> web.Response:
    return web.json_response(view.list_blobs(_required_query(request, "did")))


def get_blob(_view: StaticRepoView, _request: web.Request) -> web.Response:
    return _xrpc_error("BlobNotFound", "Blob not found", status=404)


def describe_repo(view: StaticRepoView, request: web.Request) -> web.Response:
    return web.json_response(view.describe_repo(_required_query(request, "repo")))


def resolve_handle(view: StaticRepoView, request: web.Request) -> web.Response:
    return web.json_response(view.resolve_handle(_required_query(request, "handle")))


def describe_server(view: StaticRepoView, _request: web.Request) -> web.Response:
    return web.json_response(view.describe_server())


XRPC_HANDLERS: dict[str, Handler] = {
    "com.atproto.sync.getLatestCommit": get_latest_commit,
    "com.atproto.sync.getRepo": get_repo,
    "com.atproto.sync.getRepoStatus": get_repo_status,
    "com.atproto.sync.listRepos": list_repos,
    "com.atproto.sync.getRecord": get_record,
    "com.atproto.sync.getBlocks": get_blocks,
    "com.atproto.sync.listBlobs": list_blobs,
    "com.atproto.sync.getBlob": get_blob,
    "com.atproto.repo.describeRepo": describe_repo,
    "com.atproto.identity.resolveHandle": resolve_handle,
    "com.atproto.server.describeServer": describe_server,
}


def _view(request: web.Request) -> StaticRepoView:
    return request.app[REPO_VIEW_KEY]


def _required_query(request: web.Request, name: str) -> str:
    value = request.query.get(name)
    if value is None:
        raise RepoViewError(f"missing required query parameter: {name}")
    return value


def _xrpc_error(name: str, message: str, *, status: int) -> web.Response:
    return web.json_response({"error": name, "message": message}, status=status)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="satrepo-shim")
    parser.add_argument("--origin", required=True, help="static site origin URL or local site path")
    parser.add_argument("--host", default="127.0.0.1", help="host to bind")
    parser.add_argument("--port", type=int, default=8080, help="port to bind")
    parser.add_argument(
        "--service-did",
        default="did:web:localhost",
        help="service DID reported by com.atproto.server.describeServer",
    )
    args = parser.parse_args(argv)

    web.run_app(
        create_app(origin=args.origin, service_did=args.service_did),
        host=args.host,
        port=args.port,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
