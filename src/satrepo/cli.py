"""Command line interface for satrepo."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import click
import typer

from .bsky import create_bsky_post
from .errors import SatRepoError
from .init_repo import init_repo
from .plc import plc_summary, submit_plc_operation, update_pds_url
from .porcelain import commit_log, worktree_status
from .publish import publish
from .standard_site import create_standard_document, create_standard_publication
from .verify import format_result, verify_repo

app = typer.Typer(help="Local static ATProto repo authoring tools.")
plc_app = typer.Typer(help="Manage local did:plc state.")
bsky_app = typer.Typer(help="Bluesky record helpers.")
standard_app = typer.Typer(help="Standard.site record helpers.")
app.add_typer(plc_app, name="plc")
app.add_typer(bsky_app, name="bsky")
app.add_typer(standard_app, name="standard")


RootOption = Annotated[Path, typer.Option(help="Checkout root.")]


@app.command("init")
def init_command(
    handle: Annotated[str, typer.Argument(help="ATProto handle for the repo.")],
    pds_url: Annotated[str, typer.Option("--pds-url", help="Future shim/PDS service URL.")],
    root: RootOption = Path("."),
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite existing config and keys.")
    ] = False,
) -> None:
    config = init_repo(root, handle=handle, pds_url=pds_url, force=force)
    typer.echo(f"initialized satrepo at {root.resolve()}")
    typer.echo(f"did: {config.did}")
    typer.echo(f"handle: {config.handle}")
    typer.echo(f"keys: {config.key_dir}")


@app.command("status")
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


@app.command("log")
def log_command(
    root: RootOption = Path("."),
    limit: Annotated[
        int | None, typer.Option("--limit", "-n", min=1, help="Maximum commits to show.")
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


@app.command("commit")
def commit_command(root: RootOption = Path(".")) -> None:
    _run_publish(root, verb="committed")


@app.command("verify")
def verify_command(root: RootOption = Path(".")) -> None:
    result = verify_repo(root)
    typer.echo(format_result(result))
    if not result.ok:
        raise typer.Exit(1)


@bsky_app.command("post")
def bsky_post(
    text: Annotated[str, typer.Argument(help="Text for a new app.bsky.feed.post record.")],
    root: RootOption = Path("."),
    created_at: Annotated[
        str | None,
        typer.Option("--created-at", help="Override record createdAt datetime."),
    ] = None,
) -> None:
    post = create_bsky_post(root, text=text, created_at=created_at)
    typer.echo(f"created {post.repo_path}")
    typer.echo(f"file: {post.path}")


@standard_app.command("publication")
def standard_publication(
    name: Annotated[str, typer.Argument(help="Publication name.")],
    url: Annotated[str, typer.Option("--url", help="Canonical publication URL.")],
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


@standard_app.command("document")
def standard_document(
    title: Annotated[str, typer.Argument(help="Document title.")],
    markdown: Annotated[str, typer.Argument(help="Markdown document body.")],
    path: Annotated[str, typer.Option("--path", help="Canonical document path.")],
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
        typer.Option("--published-at", help="Override record publishedAt datetime."),
    ] = None,
    publication_rkey: Annotated[
        str | None,
        typer.Option("--publication-rkey", help="Publication rkey to link this document to."),
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


@plc_app.command("show")
def plc_show(root: RootOption = Path(".")) -> None:
    summary = plc_summary(root)
    typer.echo(f"did: {summary['did']}")
    typer.echo(f"handle: {summary['handle']}")
    typer.echo(f"pds_url: {summary['pdsUrl']}")
    typer.echo(f"service_endpoint: {summary['serviceEndpoint'] or '(none)'}")
    typer.echo(f"keys: {summary['keyDir']}")
    typer.echo(f"registered: {'yes' if summary['plcRegistered'] else 'no'}")


@plc_app.command("update")
def plc_update(
    pds_url: Annotated[str, typer.Option("--pds-url", help="Shim/PDS service URL.")],
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


@plc_app.command("submit")
def plc_submit(
    root: RootOption = Path("."),
    directory: Annotated[
        str,
        typer.Option("--directory", help="PLC directory base URL."),
    ] = "https://plc.directory",
) -> None:
    result = submit_plc_operation(root, directory=directory)
    if result.submitted:
        typer.echo("submitted local did:plc genesis operation")
    elif result.already_registered:
        typer.echo("did:plc already registered")
    typer.echo(f"did: {result.did}")
    typer.echo(f"directory: {result.directory}")


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


def _run_publish(root: Path, *, verb: str) -> None:
    result = publish(root)
    typer.echo(f"{verb} {result.did}")
    typer.echo(f"head: {result.head or '(none)'}")
    typer.echo(f"rev: {result.rev or '(none)'}")
    typer.echo(f"last_seq: {result.last_seq}")
    typer.echo(f"writes: {result.writes}")
    typer.echo(f"events: {result.events}")


if __name__ == "__main__":
    sys.exit(main())
