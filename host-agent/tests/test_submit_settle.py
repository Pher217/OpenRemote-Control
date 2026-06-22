"""Tests for the TUI submit-settle delay.

A full-screen TUI (claude) ingests pasted text asynchronously; the submit Enter
must wait for the text to land. _submit_settle_seconds scales that wait with text
length, bounded by a max.
"""

from agent_host.pty_session import (
    _SUBMIT_SETTLE_BASE,
    _SUBMIT_SETTLE_MAX,
    _submit_settle_seconds,
)


def test_short_text_uses_base_settle():
    """GIVEN a short prompt WHEN computing settle THEN it is at least the base wait."""
    assert _submit_settle_seconds("hi") >= _SUBMIT_SETTLE_BASE


def test_settle_scales_with_length():
    """GIVEN a longer prompt WHEN computing settle THEN it waits longer than a short one."""
    assert _submit_settle_seconds("x" * 300) > _submit_settle_seconds("x")


def test_settle_is_capped():
    """GIVEN a very long prompt WHEN computing settle THEN it never exceeds the max."""
    assert _submit_settle_seconds("x" * 100_000) == _SUBMIT_SETTLE_MAX
