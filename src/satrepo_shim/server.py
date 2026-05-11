"""Placeholder for the dynamic sync shim CLI."""

from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="satrepo-shim")
    parser.add_argument("--origin", help="static site origin URL")
    parser.parse_args(argv)
    parser.exit(2, "satrepo-shim: Phase B is not implemented yet\n")
