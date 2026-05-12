"""Dynamic sync shim server for a satrepo static origin."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Callable
from typing import Annotated

import click
import typer
from aiohttp import web

from .firehose import encode_error_frame, encode_message_frame
from .origin import OriginError
from .repo_view import CAR_MIME_TYPE, RepoViewError, StaticRepoView

Handler = Callable[[StaticRepoView, web.Request], web.StreamResponse]
REPO_VIEW_KEY = web.AppKey("repo_view", StaticRepoView)
POLL_INTERVAL_KEY = web.AppKey("poll_interval", float)
LOGGER = logging.getLogger(__name__)
SHIM_HELP = (
    "Serve one generated satrepo site as a read-only ATProto PDS-shaped shim. "
    "The shim reads static repo artifacts from --origin and exposes sync XRPCs."
)
SHIM_EPILOG = (
    "Example: satrepo-shim --origin https://satrepo.example --port 8781 "
    "--service-did did:web:satrepo.example"
)


def create_app(
    *,
    origin: str,
    service_did: str = "did:web:localhost",
    poll_interval: float = 2.0,
) -> web.Application:
    app = web.Application(middlewares=[cors_middleware])
    app[REPO_VIEW_KEY] = StaticRepoView.from_origin(origin, service_did=service_did)
    app[POLL_INTERVAL_KEY] = poll_interval
    app.router.add_route("OPTIONS", "/xrpc/_health", options)
    app.router.add_route("OPTIONS", "/xrpc/{method}", options)
    app.router.add_get("/xrpc/_health", health)
    app.router.add_get("/xrpc/com.atproto.sync.subscribeRepos", subscribe_repos)
    app.router.add_get("/xrpc/{method}", xrpc)
    return app


@web.middleware
async def cors_middleware(request: web.Request, handler) -> web.StreamResponse:
    response = await handler(request)
    _add_cors_headers(response)
    return response


async def options(_request: web.Request) -> web.Response:
    return web.Response(status=204)


async def health(request: web.Request) -> web.Response:
    view = _view(request)
    response = web.json_response(view.health())
    _add_repo_rev_header(response, view)
    return response


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
        response = handler(view, request)
        if response.status < 400:
            _add_repo_rev_header(response, view)
        LOGGER.info(
            "xrpc method=%s status=%s remote=%s",
            method,
            response.status,
            request.remote,
        )
        return response
    except RepoViewError as exc:
        LOGGER.info(
            "xrpc method=%s status=%s error=%s remote=%s",
            method,
            exc.status,
            exc.name,
            request.remote,
        )
        return _xrpc_error(exc.name, str(exc), status=exc.status)
    except OriginError as exc:
        LOGGER.warning("xrpc method=%s origin_error=%s remote=%s", method, exc, request.remote)
        return _xrpc_error("OriginUnavailable", str(exc), status=502)


async def subscribe_repos(request: web.Request) -> web.WebSocketResponse:
    view = _view(request)
    poll_interval = request.app[POLL_INTERVAL_KEY]
    ws = web.WebSocketResponse(autoping=True)
    await ws.prepare(request)

    try:
        cursor = _subscription_cursor(request, view.last_seq())
        LOGGER.info("subscribeRepos start cursor=%s remote=%s", cursor, request.remote)
        while not ws.closed:
            cursor, sent = await _send_events_after(ws, view, cursor)
            if sent:
                LOGGER.info(
                    "subscribeRepos sent=%s cursor=%s remote=%s",
                    sent,
                    cursor,
                    request.remote,
                )
            if not await _wait_for_poll_or_close(ws, poll_interval):
                break
    except RepoViewError as exc:
        LOGGER.info("subscribeRepos error=%s remote=%s", exc.name, request.remote)
        await ws.send_bytes(encode_error_frame(exc.name, str(exc)))
    except OriginError as exc:
        LOGGER.warning("subscribeRepos origin_error=%s remote=%s", exc, request.remote)
        await ws.send_bytes(encode_error_frame("OriginUnavailable", str(exc)))
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        await ws.close()
        LOGGER.info("subscribeRepos end remote=%s", request.remote)
    return ws


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


def repo_get_record(view: StaticRepoView, request: web.Request) -> web.Response:
    return web.json_response(
        view.record(
            _required_query(request, "repo"),
            _required_query(request, "collection"),
            _required_query(request, "rkey"),
            request.query.get("cid"),
        )
    )


def repo_list_records(view: StaticRepoView, request: web.Request) -> web.Response:
    return web.json_response(
        view.list_records(
            _required_query(request, "repo"),
            _required_query(request, "collection"),
            limit=_bounded_limit(request.query.get("limit"), default=50, maximum=100),
            cursor=request.query.get("cursor"),
            reverse=_query_bool(request.query.get("reverse")),
        )
    )


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
    "com.atproto.repo.getRecord": repo_get_record,
    "com.atproto.repo.listRecords": repo_list_records,
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


def _bounded_limit(value: str | None, *, default: int, maximum: int) -> int:
    if value is None:
        return default
    try:
        limit = int(value)
    except ValueError as exc:
        raise RepoViewError("limit must be an integer") from exc
    if limit < 1 or limit > maximum:
        raise RepoViewError(f"limit must be between 1 and {maximum}")
    return limit


def _query_bool(value: str | None) -> bool:
    return value in {"true", "1", "yes"}


def _subscription_cursor(request: web.Request, last_seq: int) -> int:
    raw = request.query.get("cursor")
    if raw is None:
        return last_seq

    try:
        cursor = int(raw)
    except ValueError as exc:
        raise RepoViewError("cursor must be an integer") from exc

    if cursor > last_seq:
        raise RepoViewError(
            "Cursor in the future.",
            name="FutureCursor",
        )
    return cursor


async def _send_events_after(
    ws: web.WebSocketResponse,
    view: StaticRepoView,
    cursor: int,
) -> tuple[int, int]:
    sent = 0
    for event in view.events_after(cursor):
        await ws.send_bytes(encode_message_frame(event, view))
        cursor = max(cursor, int(event["seq"]))
        sent += 1
    return cursor, sent


async def _wait_for_poll_or_close(ws: web.WebSocketResponse, poll_interval: float) -> bool:
    try:
        message = await asyncio.wait_for(ws.receive(), timeout=poll_interval)
    except TimeoutError:
        return True

    return message.type not in {
        web.WSMsgType.CLOSE,
        web.WSMsgType.CLOSING,
        web.WSMsgType.CLOSED,
        web.WSMsgType.ERROR,
    }


def _xrpc_error(name: str, message: str, *, status: int) -> web.Response:
    return web.json_response({"error": name, "message": message}, status=status)


def _add_cors_headers(response: web.StreamResponse) -> None:
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = (
        "Authorization, Content-Type, Atproto-Accept-Labelers"
    )
    response.headers["Access-Control-Expose-Headers"] = "Atproto-Repo-Rev"


def _add_repo_rev_header(response: web.StreamResponse, view: StaticRepoView) -> None:
    if rev := view.current_rev():
        response.headers["Atproto-Repo-Rev"] = rev


def run_shim(
    origin: Annotated[
        str,
        typer.Option(
            "--origin",
            help="Static site origin URL or local site path containing repo/manifest.json.",
        ),
    ],
    host: Annotated[str, typer.Option("--host", help="Host interface to bind.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="TCP port to bind.")] = 8080,
    service_did: Annotated[
        str,
        typer.Option(
            "--service-did",
            help="Service DID reported by com.atproto.server.describeServer.",
        ),
    ] = "did:web:localhost",
    poll_interval: Annotated[
        float,
        typer.Option(
            "--poll-interval",
            help="Seconds between static-origin manifest polls for subscribeRepos.",
        ),
    ] = 2.0,
) -> None:
    """Run the shim until interrupted."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    web.run_app(
        create_app(
            origin=origin,
            service_did=service_did,
            poll_interval=poll_interval,
        ),
        host=host,
        port=port,
    )


def main(argv: list[str] | None = None) -> int:
    command_info = typer.main.CommandInfo(
        callback=run_shim,
        help=SHIM_HELP,
        epilog=SHIM_EPILOG,
        no_args_is_help=True,
    )
    command = typer.main.get_command_from_info(
        command_info,
        pretty_exceptions_short=True,
        rich_markup_mode="rich",
    )
    try:
        result = command.main(
            args=sys.argv[1:] if argv is None else argv,
            prog_name="satrepo-shim",
            standalone_mode=False,
        )
        return result or 0
    except click.ClickException as exc:
        exc.show()
        raise SystemExit(exc.exit_code) from exc
    except click.exceptions.Exit as exc:
        return exc.exit_code


if __name__ == "__main__":
    sys.exit(main())
