"""Tests for PTY liveness reconcile (session.pty_reconcile backend handler)."""

from __future__ import annotations

import pytest
from asgiref.sync import sync_to_async

from apps.hostlink.consumers import HostDaemonConsumer
from apps.hosts.models import Host
from apps.threads.models import Thread


def _make_consumer(host):
    c = HostDaemonConsumer()
    c.host = host
    c._file_sessions = {}
    c._pty_threads = {}
    return c


def _make_host(slug):
    return Host.objects.create(slug=slug, name=slug, os="linux")


def _make_pty_thread(host, session_name, *, status=Thread.StatusChoices.RUNNING):
    from apps.accounts.models import Account

    account, _ = Account.objects.get_or_create(
        provider="pty",
        label="orc-run",
        defaults={"auth_type": "none", "credential_type": "none"},
    )
    return Thread.objects.create(
        external_session_ref=session_name,
        name="orc-run: test",
        runtime="pty",
        runtime_mode=Thread.RuntimeModeChoices.PTY,
        host=host,
        account=account,
        status=status,
        metadata={"tmux_session_name": session_name},
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
class TestPtyReconcile:

    async def test_absent_session_marked_completed(self):
        """
        GIVEN a host with a RUNNING PTY thread whose tmux name is NOT in the reconcile list
        WHEN session.pty_reconcile is received with that session absent
        THEN the thread is marked COMPLETED.
        """
        host = await sync_to_async(_make_host)("reconcile-host-1")
        thread = await sync_to_async(_make_pty_thread)(host, "dead-session")
        consumer = _make_consumer(host)

        await consumer._handle_pty_reconcile({"session_names": ["other-session"]})

        refreshed = await sync_to_async(Thread.objects.get)(id=thread.id)
        assert refreshed.status == Thread.StatusChoices.COMPLETED

    async def test_present_session_stays_running(self):
        """
        GIVEN a host with a RUNNING PTY thread whose tmux name IS in the reconcile list
        WHEN session.pty_reconcile is received
        THEN the thread remains RUNNING.
        """
        host = await sync_to_async(_make_host)("reconcile-host-2")
        thread = await sync_to_async(_make_pty_thread)(host, "live-session")
        consumer = _make_consumer(host)

        await consumer._handle_pty_reconcile({"session_names": ["live-session"]})

        refreshed = await sync_to_async(Thread.objects.get)(id=thread.id)
        assert refreshed.status == Thread.StatusChoices.RUNNING

    async def test_other_host_thread_untouched(self):
        """
        GIVEN a RUNNING PTY thread on a DIFFERENT host whose session is absent from the list
        WHEN session.pty_reconcile is received for the first host
        THEN the other host's thread is NOT completed (scope is strictly self.host).
        """
        host_a = await sync_to_async(_make_host)("reconcile-host-a")
        host_b = await sync_to_async(_make_host)("reconcile-host-b")
        thread_b = await sync_to_async(_make_pty_thread)(host_b, "b-session")
        consumer = _make_consumer(host_a)

        # Reconcile for host_a with empty live list — must not affect host_b's threads.
        await consumer._handle_pty_reconcile({"session_names": []})

        refreshed = await sync_to_async(Thread.objects.get)(id=thread_b.id)
        assert refreshed.status == Thread.StatusChoices.RUNNING

    async def test_missing_session_names_key_is_noop(self):
        """
        GIVEN a session.pty_reconcile data dict with no 'session_names' key
        WHEN _handle_pty_reconcile is called
        THEN it is a no-op (fail-safe: only an explicit list reconciles).
        """
        host = await sync_to_async(_make_host)("reconcile-host-3")
        thread = await sync_to_async(_make_pty_thread)(host, "maybe-session")
        consumer = _make_consumer(host)

        await consumer._handle_pty_reconcile({})

        refreshed = await sync_to_async(Thread.objects.get)(id=thread.id)
        assert refreshed.status == Thread.StatusChoices.RUNNING

    async def test_absent_and_present_together(self):
        """
        GIVEN a host with one RUNNING PTY thread whose tmux name is NOT in the list
              and one whose name IS in the list
        WHEN session.pty_reconcile is received
        THEN only the absent one is marked COMPLETED; the present one stays RUNNING.
        """
        host = await sync_to_async(_make_host)("reconcile-host-4")
        dead = await sync_to_async(_make_pty_thread)(host, "dead-s")
        live = await sync_to_async(_make_pty_thread)(host, "live-s")
        consumer = _make_consumer(host)

        await consumer._handle_pty_reconcile({"session_names": ["live-s"]})

        dead_refreshed = await sync_to_async(Thread.objects.get)(id=dead.id)
        live_refreshed = await sync_to_async(Thread.objects.get)(id=live.id)
        assert dead_refreshed.status == Thread.StatusChoices.COMPLETED
        assert live_refreshed.status == Thread.StatusChoices.RUNNING
