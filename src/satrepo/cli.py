"""Command line interface for satrepo."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .config import read_config
from .errors import SatRepoError
from .init_repo import init_repo
from .paths import discover_root
from .publish import publish
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


if __name__ == "__main__":
    sys.exit(main())
