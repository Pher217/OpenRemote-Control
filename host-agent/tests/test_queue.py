"""
Tests for queue.py — OfflineQueue durable event buffer.
"""

from __future__ import annotations

from agent_host.queue import OfflineQueue


class TestEnqueue:
    def test_enqueue_creates_file(self, tmp_path):
        """
        GIVEN an empty queue
        WHEN enqueue() is called
        THEN the backing file is created.
        """
        q = OfflineQueue(tmp_path / "queue.jsonl")
        q.enqueue({"type": "session.line", "data": {"raw": "hello"}})
        assert (tmp_path / "queue.jsonl").exists()

    def test_enqueue_multiple_events(self, tmp_path):
        """
        GIVEN multiple enqueue() calls
        WHEN the file is read back
        THEN all events are present in order.
        """
        q = OfflineQueue(tmp_path / "queue.jsonl")
        q.enqueue({"n": 1})
        q.enqueue({"n": 2})
        q.enqueue({"n": 3})

        assert len(q) == 3

    def test_len_empty_queue(self, tmp_path):
        q = OfflineQueue(tmp_path / "queue.jsonl")
        assert len(q) == 0


class TestDrain:
    def test_drain_with_successful_send_removes_all(self, tmp_path):
        """
        GIVEN events in the queue
        WHEN drain() is called with a send callback that always returns True
        THEN all events are removed and drain returns the count.
        """
        q = OfflineQueue(tmp_path / "queue.jsonl")
        q.enqueue({"n": 1})
        q.enqueue({"n": 2})

        sent = []
        count = q.drain(lambda ev: (sent.append(ev), True)[1])

        assert count == 2
        assert len(q) == 0
        assert [e["n"] for e in sent] == [1, 2]

    def test_drain_with_failing_send_keeps_events(self, tmp_path):
        """
        GIVEN three events in the queue
        WHEN drain() is called and the second send fails
        THEN only the first event is removed; events 2 and 3 remain.
        """
        q = OfflineQueue(tmp_path / "queue.jsonl")
        q.enqueue({"n": 1})
        q.enqueue({"n": 2})
        q.enqueue({"n": 3})

        call_count = [0]

        def send(ev: dict) -> bool:
            call_count[0] += 1
            return call_count[0] == 1  # only first call succeeds

        count = q.drain(send)

        assert count == 1
        assert len(q) == 2

        # Remaining events are 2 and 3.
        remaining = q._read_all()
        assert [e["n"] for e in remaining] == [2, 3]

    def test_drain_first_send_fails_keeps_all(self, tmp_path):
        """
        GIVEN events in the queue
        WHEN the very first send fails
        THEN all events are kept and drain returns 0.
        """
        q = OfflineQueue(tmp_path / "queue.jsonl")
        q.enqueue({"n": 1})
        q.enqueue({"n": 2})

        count = q.drain(lambda ev: False)

        assert count == 0
        assert len(q) == 2

    def test_drain_empty_queue_returns_zero(self, tmp_path):
        q = OfflineQueue(tmp_path / "queue.jsonl")
        count = q.drain(lambda ev: True)
        assert count == 0

    def test_drain_then_enqueue_then_drain_again(self, tmp_path):
        """
        GIVEN a drained queue
        WHEN new events are enqueued and drained again
        THEN the second drain works correctly.
        """
        q = OfflineQueue(tmp_path / "queue.jsonl")
        q.enqueue({"n": 1})
        q.drain(lambda ev: True)
        assert len(q) == 0

        q.enqueue({"n": 2})
        sent = []
        q.drain(lambda ev: (sent.append(ev), True)[1])
        assert [e["n"] for e in sent] == [2]

    def test_events_sent_in_order(self, tmp_path):
        """Events are drained in FIFO order."""
        q = OfflineQueue(tmp_path / "queue.jsonl")
        for i in range(5):
            q.enqueue({"seq": i})

        received = []
        q.drain(lambda ev: (received.append(ev["seq"]), True)[1])
        assert received == list(range(5))
