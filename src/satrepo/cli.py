"""Command line interface for satrepo."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .config import read_config
from .errors import SatRepoError
from .init_repo import init_repo
from .paths import discover_root
from .plc import plc_summary, update_pds_url
from .publish import publish
from .verify import format_result, verify_repo
from .worktree import scan_records


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="satrepo")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="initialize a local static ATProto repo")
    init.add_argument("handle", help="ATProto handle for the repo")
    init.add_argument("--pds-url", required=True, help="future shim/PDS service URL")
    init.add_argument("--root", type=Path, default=Path("."), help="checkout root")
    init.add_argument("--force", action="store_true", help="overwrite existing config and keys")
    init.set_defaults(func=_cmd_init)

    status = subparsers.add_parser("status", help="show local repo status")
    status.add_argument("--root", type=Path, default=Path("."), help="checkout root")
    status.set_defaults(func=_cmd_status)

    publish_parser = subparsers.add_parser("publish", help="publish worktree records to site/")
    publish_parser.add_argument("--root", type=Path, default=Path("."), help="checkout root")
    publish_parser.set_defaults(func=_cmd_publish)

    verify_parser = subparsers.add_parser("verify", help="verify generated repo artifacts")
    verify_parser.add_argument("--root", type=Path, default=Path("."), help="checkout root")
    verify_parser.set_defaults(func=_cmd_verify)

    plc = subparsers.add_parser("plc", help="manage local did:plc state")
    plc_subparsers = plc.add_subparsers(dest="plc_command", required=True)

    plc_show = plc_subparsers.add_parser("show", help="show local did:plc state")
    plc_show.add_argument("--root", type=Path, default=Path("."), help="checkout root")
    plc_show.set_defaults(func=_cmd_plc_show)

    plc_update = plc_subparsers.add_parser("update", help="update local did:plc service data")
    plc_update.add_argument("--root", type=Path, default=Path("."), help="checkout root")
    plc_update.add_argument("--pds-url", required=True, help="shim/PDS service URL")
    plc_update.add_argument(
        "--no-publish",
        action="store_true",
        help="only update DID metadata, leaving repo artifacts unpublished",
    )
    plc_update.set_defaults(func=_cmd_plc_update)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        return args.func(args)
    except SatRepoError as exc:
        parser.exit(2, f"satrepo: error: {exc}\n")
    except ValueError as exc:
        parser.exit(2, f"satrepo: error: {exc}\n")


def _cmd_init(args: argparse.Namespace) -> int:
    config = init_repo(args.root, handle=args.handle, pds_url=args.pds_url, force=args.force)
    print(f"initialized satrepo at {args.root.resolve()}")
    print(f"did: {config.did}")
    print(f"handle: {config.handle}")
    print(f"keys: {config.key_dir}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    paths = discover_root(args.root)
    config = read_config(paths.config)
    records = scan_records(paths.root)
    print(f"root: {paths.root}")
    print(f"did: {config.did}")
    print(f"handle: {config.handle}")
    print(f"pds_url: {config.pds_url}")
    print(f"records: {len(records)}")
    return 0


def _cmd_publish(args: argparse.Namespace) -> int:
    result = publish(args.root)
    print(f"published {result.did}")
    print(f"head: {result.head or '(none)'}")
    print(f"rev: {result.rev or '(none)'}")
    print(f"last_seq: {result.last_seq}")
    print(f"writes: {result.writes}")
    print(f"events: {result.events}")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    result = verify_repo(args.root)
    print(format_result(result))
    return 0 if result.ok else 1


def _cmd_plc_show(args: argparse.Namespace) -> int:
    summary = plc_summary(args.root)
    print(f"did: {summary['did']}")
    print(f"handle: {summary['handle']}")
    print(f"pds_url: {summary['pdsUrl']}")
    print(f"service_endpoint: {summary['serviceEndpoint'] or '(none)'}")
    print(f"keys: {summary['keyDir']}")
    print(f"registered: {'yes' if summary['plcRegistered'] else 'no'}")
    return 0


def _cmd_plc_update(args: argparse.Namespace) -> int:
    result = update_pds_url(args.root, pds_url=args.pds_url, publish_after=not args.no_publish)
    print("updated local did:plc state")
    if result.did_changed:
        print(f"old_did: {result.old_did}")
        print(f"new_did: {result.new_did}")
    else:
        print(f"did: {result.new_did}")
    print(f"pds_url: {result.pds_url}")
    print(f"keys: {result.key_dir}")
    print(f"published: {'yes' if result.published else 'no'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
