"""Subcommand parser registration, split by concern.

Each module exposes `add_<command>(sub)` functions that attach one top-level
subcommand (and its nested subparsers) to the shared `sub` action. `cli.main`
calls them all in `_build_parser()`.
"""
