"""Tests for supervisor.push — proactive-push formatting (S2).

All tests are pure-function, model-free, no DB required.

Coverage:
  - format_push: empty steps → empty string
  - format_push: correct ordering (highest severity first)
  - format_push: each StepKind has a human-readable representation
  - format_push: redaction applied to labels (Safety Contract #6)
  - coalesce: de-duplicates per thread_id (highest severity wins)
  - coalesce: caps at MAX_PUSH_PER_CYCLE
  - coalesce: ordering is descending by severity
"""

from __future__ import annotations

import pytest

from apps.supervisor.classifier import MAX_PUSH_PER_CYCLE, MajorStep, Severity, StepKind
from apps.supervisor.push import coalesce, format_push


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _step(
    *,
    thread_id: str = "tid-1",
    label: str = "test-session",
    kind: str = StepKind.STARTED,
    severity: int = Severity.STARTED,
) -> MajorStep:
    return MajorStep(
        thread_id=thread_id,
        label=label,
        kind=kind,
        severity=severity,
    )


# ---------------------------------------------------------------------------
# format_push — empty
# ---------------------------------------------------------------------------


def test_format_push_empty_returns_empty_string():
    """
    GIVEN an empty list of major steps
    WHEN format_push is called
    THEN an empty string is returned (no push needed).
    """
    assert format_push([]) == ""


# ---------------------------------------------------------------------------
# format_push — content
# ---------------------------------------------------------------------------


def test_format_push_contains_session_label():
    """
    GIVEN a major step for session 'my-session'
    WHEN format_push is called
    THEN 'my-session' appears in the output.
    """
    steps = [_step(label="my-session", kind=StepKind.STARTED, severity=Severity.STARTED)]
    result = format_push(steps)
    assert "my-session" in result


def test_format_push_contains_kind_label_needs_input():
    """
    GIVEN a NEEDS_INPUT major step
    WHEN format_push is called
    THEN "needs input" appears in the output.
    """
    steps = [_step(kind=StepKind.NEEDS_INPUT, severity=Severity.NEEDS_INPUT)]
    result = format_push(steps)
    assert "needs input" in result


def test_format_push_contains_kind_label_finished():
    """
    GIVEN a FINISHED major step
    WHEN format_push is called
    THEN "finished" appears in the output.
    """
    steps = [_step(kind=StepKind.FINISHED, severity=Severity.FINISHED)]
    result = format_push(steps)
    assert "finished" in result


def test_format_push_contains_kind_label_started():
    """
    GIVEN a STARTED major step
    WHEN format_push is called
    THEN "started" appears in the output.
    """
    steps = [_step(kind=StepKind.STARTED, severity=Severity.STARTED)]
    result = format_push(steps)
    assert "started" in result


def test_format_push_contains_kind_label_stalled():
    """
    GIVEN a STALLED major step
    WHEN format_push is called
    THEN "stalled" appears in the output.
    """
    steps = [_step(kind=StepKind.STALLED, severity=Severity.STALLED)]
    result = format_push(steps)
    assert "stalled" in result


# ---------------------------------------------------------------------------
# format_push — severity ordering
# ---------------------------------------------------------------------------


def test_format_push_orders_by_severity_descending():
    """
    GIVEN a STALLED step and a NEEDS_INPUT step
    WHEN format_push is called
    THEN the NEEDS_INPUT line appears before the STALLED line.
    """
    steps = [
        _step(thread_id="tid-1", kind=StepKind.STALLED, severity=Severity.STALLED, label="stalled-session"),
        _step(thread_id="tid-2", kind=StepKind.NEEDS_INPUT, severity=Severity.NEEDS_INPUT, label="urgent-session"),
    ]
    result = format_push(steps)
    pos_urgent = result.find("urgent-session")
    pos_stalled = result.find("stalled-session")
    assert pos_urgent < pos_stalled, (
        "Higher-severity step must appear earlier in the push output"
    )


# ---------------------------------------------------------------------------
# format_push — redaction (Safety Contract #6)
# ---------------------------------------------------------------------------


def test_format_push_redacts_openai_key_in_label():
    """
    GIVEN a step whose label contains an OpenAI API key
    WHEN format_push is called
    THEN the key is stripped from the output and [REDACTED] appears.
    """
    secret = "sk-" + "X" * 24
    steps = [_step(label=f"project-{secret}", kind=StepKind.STARTED, severity=Severity.STARTED)]
    result = format_push(steps)
    assert secret not in result
    assert "[REDACTED]" in result


def test_format_push_redacts_anthropic_key_in_label():
    """
    GIVEN a step whose label contains an Anthropic API key
    WHEN format_push is called
    THEN the key is stripped from the output.
    """
    secret = "sk-ant-api03-" + "Y" * 30
    steps = [_step(label=f"worker-{secret}", kind=StepKind.STARTED, severity=Severity.STARTED)]
    result = format_push(steps)
    assert secret not in result
    assert "[REDACTED]" in result


def test_format_push_redacts_github_pat_in_label():
    """
    GIVEN a step whose label contains a GitHub PAT
    WHEN format_push is called
    THEN the PAT is stripped from the output.
    """
    secret = "ghp_" + "G" * 24
    steps = [_step(label=f"ci-{secret}", kind=StepKind.FINISHED, severity=Severity.FINISHED)]
    result = format_push(steps)
    assert secret not in result
    assert "[REDACTED]" in result


# ---------------------------------------------------------------------------
# coalesce — de-duplication
# ---------------------------------------------------------------------------


def test_coalesce_deduplicates_by_thread_id_keeps_highest_severity():
    """
    GIVEN two steps for the same thread_id (STALLED and NEEDS_INPUT)
    WHEN coalesce is called
    THEN only the highest-severity step is kept.
    """
    steps = [
        _step(thread_id="tid-1", kind=StepKind.STALLED, severity=Severity.STALLED),
        _step(thread_id="tid-1", kind=StepKind.NEEDS_INPUT, severity=Severity.NEEDS_INPUT),
    ]
    result = coalesce(steps)
    assert len(result) == 1
    assert result[0]["kind"] == StepKind.NEEDS_INPUT
    assert result[0]["severity"] == Severity.NEEDS_INPUT


def test_coalesce_preserves_distinct_threads():
    """
    GIVEN two steps for different thread_ids
    WHEN coalesce is called
    THEN both steps are preserved.
    """
    steps = [
        _step(thread_id="tid-1", kind=StepKind.STARTED, severity=Severity.STARTED),
        _step(thread_id="tid-2", kind=StepKind.NEEDS_INPUT, severity=Severity.NEEDS_INPUT),
    ]
    result = coalesce(steps)
    assert len(result) == 2


def test_coalesce_sorts_descending_by_severity():
    """
    GIVEN steps with mixed severities for different threads
    WHEN coalesce is called
    THEN the output is sorted by descending severity.
    """
    steps = [
        _step(thread_id="tid-1", kind=StepKind.STALLED, severity=Severity.STALLED),
        _step(thread_id="tid-2", kind=StepKind.NEEDS_INPUT, severity=Severity.NEEDS_INPUT),
        _step(thread_id="tid-3", kind=StepKind.STARTED, severity=Severity.STARTED),
    ]
    result = coalesce(steps)
    severities = [s["severity"] for s in result]
    assert severities == sorted(severities, reverse=True)


def test_coalesce_caps_at_max_push_per_cycle():
    """
    GIVEN more steps than MAX_PUSH_PER_CYCLE (each for a different thread)
    WHEN coalesce is called
    THEN at most MAX_PUSH_PER_CYCLE steps are returned.
    """
    steps = [
        _step(
            thread_id=f"tid-{i}",
            label=f"session-{i}",
            kind=StepKind.STARTED,
            severity=Severity.STARTED,
        )
        for i in range(MAX_PUSH_PER_CYCLE + 3)
    ]
    result = coalesce(steps)
    assert len(result) <= MAX_PUSH_PER_CYCLE


def test_coalesce_empty_input_returns_empty():
    """
    GIVEN an empty list
    WHEN coalesce is called
    THEN an empty list is returned.
    """
    assert coalesce([]) == []


def test_coalesce_does_not_mutate_input():
    """
    GIVEN a list of steps
    WHEN coalesce is called
    THEN the original list is not mutated.
    """
    steps = [
        _step(thread_id="tid-1", kind=StepKind.STARTED, severity=Severity.STARTED),
    ]
    original_id = id(steps)
    _ = coalesce(steps)
    assert id(steps) == original_id
    assert len(steps) == 1  # list not truncated
