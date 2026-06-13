"""
Tests for cli.py and __main__.py entry points.
"""

from __future__ import annotations

import subprocess
import sys


def test_python_m_agent_host_cli_runs_main():
    """
    GIVEN the agent_host package on PYTHONPATH
    WHEN `python -m agent_host.cli daemon --help` is executed
    THEN it exits 0 and prints daemon usage (main() is called, not a no-op).
    """
    result = subprocess.run(
        [sys.executable, "-m", "agent_host.cli", "daemon", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Non-zero exit: {result.stderr}"
    assert "daemon" in result.stdout.lower() or "poll" in result.stdout.lower(), (
        f"Expected daemon help text in stdout, got: {result.stdout!r}"
    )


def test_python_m_agent_host_runs_main():
    """
    GIVEN the agent_host package on PYTHONPATH
    WHEN `python -m agent_host daemon --help` is executed
    THEN it exits 0 and prints daemon usage (__main__.py dispatches to main()).
    """
    result = subprocess.run(
        [sys.executable, "-m", "agent_host", "daemon", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Non-zero exit: {result.stderr}"
    assert "daemon" in result.stdout.lower() or "poll" in result.stdout.lower(), (
        f"Expected daemon help text in stdout, got: {result.stdout!r}"
    )


def test_python_m_agent_host_cli_no_args_exits_nonzero():
    """
    GIVEN the agent_host.cli module
    WHEN invoked with no arguments
    THEN it exits non-zero (argparse required subcommand error), not silently 0.
    """
    result = subprocess.run(
        [sys.executable, "-m", "agent_host.cli"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, (
        "Expected non-zero exit for missing subcommand, "
        f"got 0 (stdout={result.stdout!r})"
    )


def test_run_subcommand_dispatches_to_cmd_run(monkeypatch):
    """
    GIVEN `orc-host run echo hi` on argv
    WHEN main() parses and dispatches
    THEN run_cmd.cmd_run is called with args.command == ["echo", "hi"].

    Regression: the subparsers dest was "command", colliding with the `run`
    subcommand's own positional "command" — the positional overwrote the chosen
    subcommand, so `args.command` became the command list and NO dispatch branch
    matched. `orc-host run …` silently no-opped (exit 0, nothing launched).
    The unit tests for run only called cmd_run/run_pty directly, bypassing the
    argparse dispatch, so the collision was invisible until a live smoke test.
    """
    import agent_host.cli as cli
    import agent_host.run_cmd as run_cmd

    captured: dict = {}

    def _fake_cmd_run(args):
        captured["command"] = args.command
        captured["name"] = args.name
        captured["cwd"] = args.cwd

    monkeypatch.setattr(run_cmd, "cmd_run", _fake_cmd_run)
    monkeypatch.setattr(sys, "argv", ["orc-host", "run", "echo", "hi"])

    cli.main()

    assert captured.get("command") == ["echo", "hi"], (
        f"run did not dispatch to cmd_run with the command list; captured={captured!r}"
    )
