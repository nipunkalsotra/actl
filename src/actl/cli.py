"""actl demo | verify-chain | seed | explain | replay | policy-check (§25).

At P0 only the entrypoint exists; each subcommand lands with the phase that
owns the logic it calls into (P1 policy-check, P3 verify-chain, P4 seed, P9 demo).
"""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="actl")
    parser.add_argument("--version", action="version", version="actl 0.1.0")
    return parser


def main() -> None:
    parser = build_parser()
    parser.parse_args()
    parser.print_help()


if __name__ == "__main__":
    main()
