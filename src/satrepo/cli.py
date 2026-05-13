"""Command line interface for satrepo."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import click
import typer

from .bsky import create_bsky_post
from .config import read_config
from .errors import SatRepoError
from .init_repo import init_repo
from .paths import discover_root
from .plc import plc_summary, submit_plc_operation, update_pds_url
from .porcelain import commit_log, worktree_status
from .publish import publish
from .remote_signer import run_signer_server
from .standard_site import create_standard_document, create_standard_publication
from .verify import format_result, verify_repo

app = typer.Typer(
    help=(
        "Author an ATProto repo from local JSON files, create signed commits, and "
        "regenerate a static site that can be served by satrepo-shim."
    ),
    epilog=(
        "Typical flow: satrepo init alice.example --pds-url https://satrepo.example; "
        "satrepo bsky post 'hello'; satrepo status; satrepo commit; satrepo log."
    ),
    no_args_is_help=True,
    add_completion=False,
)
plc_app = typer.Typer(
    help=(
        "Inspect or submit local did:plc genesis state. These commands are the only "
        "ones that talk to a PLC directory."
    ),
    no_args_is_help=True,
    add_completion=False,
)
bsky_app = typer.Typer(
    help="Create Bluesky records with valid record keys and basic schema shape.",
    no_args_is_help=True,
    add_completion=False,
)
standard_app = typer.Typer(
    help="Create Standard.site publication and document records.",
    no_args_is_help=True,
    add_completion=False,
)
signer_app = typer.Typer(
    help=(
        "Experimental remote signing provider commands. These let repo commits be "
        "signed outside the process writing repo artifacts."
    ),
    no_args_is_help=True,
    add_completion=False,
)
app.add_typer(plc_app, name="plc")
app.add_typer(bsky_app, name="bsky")
app.add_typer(standard_app, name="standard")
app.add_typer(signer_app, name="signer")


RootOption = Annotated[
    Path,
    typer.Option(
        "--root",
        help="satrepo checkout root. Defaults to the current directory.",
    ),
]


@app.command(
    "init",
    help=(
        "Create a local satrepo checkout, keys, did:plc genesis operation, and empty "
        "static repo artifacts. This does not publish to plc.directory."
    ),
    short_help="Initialize a new local satrepo checkout.",
)
def init_command(
    handle: Annotated[str, typer.Argument(help="ATProto handle for the repo.")],
    pds_url: Annotated[
        str,
        typer.Option(
            "--pds-url",
            help="HTTPS URL that the DID document should advertise as this repo's PDS.",
        ),
    ],
    root: RootOption = Path("."),
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Overwrite existing satrepo config and key files for this root.",
        ),
    ] = False,
) -> None:
    config = init_repo(root, handle=handle, pds_url=pds_url, force=force)
    typer.echo(f"initialized satrepo at {root.resolve()}")
    typer.echo(f"did: {config.did}")
    typer.echo(f"handle: {config.handle}")
    typer.echo(f"keys: {config.key_dir}")


@app.command(
    "status",
    help=(
        "Compare worktree records with the last signed commit and show create, update, "
        "and delete operations that commit would write."
    ),
    short_help="Show uncommitted worktree changes.",
)
def status_command(root: RootOption = Path(".")) -> None:
    status = worktree_status(root)
    typer.echo(f"root: {status.root}")
    typer.echo(f"did: {status.did}")
    typer.echo(f"handle: {status.handle}")
    typer.echo(f"pds_url: {status.pds_url}")
    typer.echo(f"head: {status.head or '(none)'}")
    typer.echo(f"rev: {status.rev or '(none)'}")
    typer.echo(f"records: {status.records}")

    if status.clean:
        typer.echo("working tree clean")
        return

    typer.echo("changes not committed:")
    for change in status.changes:
        typer.echo(f"  {change.action:<6} {change.path}")


@app.command(
    "log",
    help="Show signed commit events from newest to oldest, including repo ops.",
    short_help="Show signed commit history.",
)
def log_command(
    root: RootOption = Path("."),
    limit: Annotated[
        int | None,
        typer.Option(
            "--limit",
            "-n",
            min=1,
            help="Maximum number of commits to show.",
        ),
    ] = None,
) -> None:
    entries = commit_log(root, limit=limit)
    if not entries:
        typer.echo("no commits")
        return

    for index, entry in enumerate(entries):
        if index:
            typer.echo("")
        typer.echo(f"commit {entry.commit}")
        typer.echo(f"seq: {entry.seq}")
        typer.echo(f"rev: {entry.rev}")
        if entry.time:
            typer.echo(f"time: {entry.time}")
        typer.echo(f"since: {entry.since or '(none)'}")
        typer.echo(f"ops: {len(entry.ops)}")
        for op in entry.ops:
            typer.echo(f"  {op.action:<6} {op.path}")


@app.command(
    "commit",
    help=(
        "Sign the current worktree into the local ATProto repo and regenerate site/. "
        "No network publishing happens here."
    ),
    short_help="Create a signed repo commit from the worktree.",
)
def commit_command(
    root: RootOption = Path("."),
    signer_url: Annotated[
        str | None,
        typer.Option(
            "--signer-url",
            help=(
                "Experimental remote signer base URL. When set, commit signing is "
                "delegated to /satrepo-signer/v0/sign."
            ),
        ),
    ] = None,
    signer_token: Annotated[
        str | None,
        typer.Option(
            "--signer-token",
            envvar="SATREPO_SIGNER_TOKEN",
            help="Bearer token for --signer-url. Can also be set as SATREPO_SIGNER_TOKEN.",
        ),
    ] = None,
) -> None:
    _run_publish(root, verb="committed", signer_url=signer_url, signer_token=signer_token)


@app.command(
    "verify",
    help=(
        "Check generated repo artifacts, commit signatures, manifest consistency, "
        "snapshot CAR loading, and known collection rkey rules."
    ),
    short_help="Verify generated repo artifacts.",
)
def verify_command(root: RootOption = Path(".")) -> None:
    result = verify_repo(root)
    typer.echo(format_result(result))
    if not result.ok:
        raise typer.Exit(1)


@bsky_app.command(
    "post",
    help=(
        "Write a new app.bsky.feed.post JSON file under worktree/ with a valid TID rkey. "
        "Run satrepo commit afterward to sign it into the repo."
    ),
    short_help="Create a Bluesky post record.",
)
def bsky_post(
    text: Annotated[str, typer.Argument(help="Text for a new app.bsky.feed.post record.")],
    root: RootOption = Path("."),
    created_at: Annotated[
        str | None,
        typer.Option(
            "--created-at",
            help="Override the record createdAt timestamp. Defaults to the current UTC time.",
        ),
    ] = None,
) -> None:
    post = create_bsky_post(root, text=text, created_at=created_at)
    typer.echo(f"created {post.repo_path}")
    typer.echo(f"file: {post.path}")


@standard_app.command(
    "publication",
    help=(
        "Write a site.standard.publication record. This describes the canonical HTTP "
        "site that Standard.site indexers should verify."
    ),
    short_help="Create a Standard.site publication record.",
)
def standard_publication(
    name: Annotated[str, typer.Argument(help="Publication name.")],
    url: Annotated[
        str,
        typer.Option(
            "--url",
            help="Canonical HTTPS base URL for the publication, for example https://alice.example.",
        ),
    ],
    root: RootOption = Path("."),
    description: Annotated[
        str | None,
        typer.Option("--description", help="Brief publication description."),
    ] = None,
) -> None:
    publication = create_standard_publication(
        root,
        name=name,
        url=url,
        description=description,
    )
    typer.echo(f"created {publication.repo_path}")
    typer.echo(f"file: {publication.path}")


@standard_app.command(
    "document",
    help=(
        "Write a site.standard.document record with Markdown content. On commit, "
        "satrepo also renders a matching static HTML page under site/."
    ),
    short_help="Create a Standard.site document record.",
)
def standard_document(
    title: Annotated[str, typer.Argument(help="Document title.")],
    markdown: Annotated[str, typer.Argument(help="Markdown document body.")],
    path: Annotated[
        str,
        typer.Option(
            "--path",
            help="Canonical URL path within the publication, for example /first-note.",
        ),
    ],
    root: RootOption = Path("."),
    description: Annotated[
        str | None,
        typer.Option("--description", help="Brief document description."),
    ] = None,
    tag: Annotated[
        list[str] | None,
        typer.Option("--tag", help="Document tag. May be passed multiple times."),
    ] = None,
    published_at: Annotated[
        str | None,
        typer.Option(
            "--published-at",
            help="Override the record publishedAt timestamp. Defaults to current UTC time.",
        ),
    ] = None,
    publication_rkey: Annotated[
        str | None,
        typer.Option(
            "--publication-rkey",
            help="Publication rkey to link this document to. Defaults to the first publication.",
        ),
    ] = None,
) -> None:
    document = create_standard_document(
        root,
        title=title,
        markdown=markdown,
        path=path,
        description=description,
        tags=tag,
        published_at=published_at,
        publication_rkey=publication_rkey,
    )
    typer.echo(f"created {document.repo_path}")
    typer.echo(f"file: {document.path}")


@plc_app.command(
    "show",
    help="Show local did:plc metadata, key directory, PDS URL, and registration status.",
    short_help="Show local PLC state.",
)
def plc_show(root: RootOption = Path(".")) -> None:
    summary = plc_summary(root)
    typer.echo(f"did: {summary['did']}")
    typer.echo(f"handle: {summary['handle']}")
    typer.echo(f"pds_url: {summary['pdsUrl']}")
    typer.echo(f"service_endpoint: {summary['serviceEndpoint'] or '(none)'}")
    typer.echo(f"keys: {summary['keyDir']}")
    typer.echo(f"registered: {'yes' if summary['plcRegistered'] else 'no'}")


@plc_app.command(
    "update",
    help=(
        "Rewrite the local did:plc genesis operation with a new PDS service URL. "
        "Only works before the DID is registered."
    ),
    short_help="Update the local PLC PDS URL.",
)
def plc_update(
    pds_url: Annotated[
        str,
        typer.Option(
            "--pds-url",
            help="HTTPS URL the DID document should advertise as the repo's PDS.",
        ),
    ],
    root: RootOption = Path("."),
    no_publish: Annotated[
        bool,
        typer.Option(
            "--no-publish", help="Only update DID metadata, leaving repo artifacts unpublished."
        ),
    ] = False,
) -> None:
    result = update_pds_url(root, pds_url=pds_url, publish_after=not no_publish)
    typer.echo("updated local did:plc state")
    if result.did_changed:
        typer.echo(f"old_did: {result.old_did}")
        typer.echo(f"new_did: {result.new_did}")
    else:
        typer.echo(f"did: {result.new_did}")
    typer.echo(f"pds_url: {result.pds_url}")
    typer.echo(f"keys: {result.key_dir}")
    typer.echo(f"published: {'yes' if result.published else 'no'}")


@plc_app.command(
    "submit",
    help=(
        "Submit the local did:plc genesis operation to a PLC directory. This makes the "
        "DID public and is intentionally separate from init and commit."
    ),
    short_help="Submit the local PLC operation.",
)
def plc_submit(
    root: RootOption = Path("."),
    directory: Annotated[
        str,
        typer.Option("--directory", help="PLC directory base URL to submit to."),
    ] = "https://plc.directory",
) -> None:
    result = submit_plc_operation(root, directory=directory)
    if result.submitted:
        typer.echo("submitted local did:plc genesis operation")
    elif result.already_registered:
        typer.echo("did:plc already registered")
    typer.echo(f"did: {result.did}")
    typer.echo(f"directory: {result.directory}")


@signer_app.command(
    "serve",
    help=(
        "Serve the experimental signing-provider HTTP API for one repo signing key. "
        "By default this loads the current repo's private signing.key, but the writer "
        "process can then use only --signer-url."
    ),
    short_help="Serve a remote signing provider.",
)
def signer_serve(
    root: RootOption = Path("."),
    key_path: Annotated[
        Path | None,
        typer.Option(
            "--key-path",
            help="Private signing key path. Defaults to the repo's configured signing.key.",
        ),
    ] = None,
    host: Annotated[
        str,
        typer.Option("--host", help="Host/interface to bind."),
    ] = "127.0.0.1",
    port: Annotated[
        int,
        typer.Option("--port", min=1, max=65535, help="TCP port to bind."),
    ] = 8790,
    token: Annotated[
        str | None,
        typer.Option(
            "--token",
            envvar="SATREPO_SIGNER_TOKEN",
            help="Optional bearer token required for signing requests.",
        ),
    ] = None,
) -> None:
    paths = discover_root(root)
    config = read_config(paths.config)
    signing_key_path = key_path or (config.key_dir / "signing.key")
    typer.echo(f"serving signer for {config.did}")
    typer.echo(f"key: {signing_key_path}")
    typer.echo(f"url: http://{host}:{port}/satrepo-signer/v0/health")
    run_signer_server(
        signing_key_path=signing_key_path,
        host=host,
        port=port,
        token=token,
        allowed_did=config.did,
    )


def main(argv: list[str] | None = None) -> int:
    command = typer.main.get_command(app)
    try:
        result = command.main(
            args=sys.argv[1:] if argv is None else argv,
            prog_name="satrepo",
            standalone_mode=False,
        )
        return result or 0
    except (SatRepoError, ValueError) as exc:
        typer.echo(f"satrepo: error: {exc}", err=True)
        raise SystemExit(2) from exc
    except click.ClickException as exc:
        exc.show()
        raise SystemExit(exc.exit_code) from exc
    except click.exceptions.Exit as exc:
        return exc.exit_code


def _run_publish(
    root: Path,
    *,
    verb: str,
    signer_url: str | None = None,
    signer_token: str | None = None,
) -> None:
    result = publish(root, signer_url=signer_url, signer_token=signer_token)
    typer.echo(f"{verb} {result.did}")
    typer.echo(f"head: {result.head or '(none)'}")
    typer.echo(f"rev: {result.rev or '(none)'}")
    typer.echo(f"last_seq: {result.last_seq}")
    typer.echo(f"writes: {result.writes}")
    typer.echo(f"events: {result.events}")


if __name__ == "__main__":
    sys.exit(main())
