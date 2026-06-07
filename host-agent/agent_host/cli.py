"""
cli.py — Command-line interface for the host-agent daemon.

Commands:
  enroll   Enroll this host with the backend and save credentials.
  daemon   Load saved credentials and start the observation daemon.
"""

from __future__ import annotations

import argparse
import sys


def _cmd_enroll(args: argparse.Namespace) -> None:
    from agent_host.enroll import enroll

    cfg = enroll(
        backend_url=args.backend,
        enroll_secret=args.secret,
        hostname=args.hostname or None,
    )
    print("Enrolled successfully.")
    print(f"  host_id : {cfg.host_id}")
    print(f"  backend : {cfg.backend_url}")
    print("Config saved.")


def _cmd_daemon(args: argparse.Namespace) -> None:
    from agent_host.config import load
    from agent_host.daemon import run

    cfg = load()
    if cfg is None:
        print(
            "Error: no config found. Run 'orc-host enroll' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    runtimes = [r.strip() for r in args.runtimes.split(",")] if args.runtimes else None
    run(cfg, runtimes=runtimes, poll_interval=args.poll_interval)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="orc-host",
        description="OpenRemote Control host-agent daemon",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- enroll ---
    enroll_p = sub.add_parser("enroll", help="Enroll this host with the backend")
    enroll_p.add_argument("--backend", required=True, help="Backend base URL")
    enroll_p.add_argument("--secret", required=True, help="Enroll secret")
    enroll_p.add_argument("--hostname", default="", help="Override hostname (optional)")

    # --- daemon ---
    daemon_p = sub.add_parser("daemon", help="Start the observation daemon")
    daemon_p.add_argument(
        "--runtimes",
        default="",
        help="Comma-separated list of runtimes to observe (e.g. claude_code,codex)",
    )
    daemon_p.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Seconds between file-discovery polls (default: 2.0)",
    )

    args = parser.parse_args()

    if args.command == "enroll":
        _cmd_enroll(args)
    elif args.command == "daemon":
        _cmd_daemon(args)
