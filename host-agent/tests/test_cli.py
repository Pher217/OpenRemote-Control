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
