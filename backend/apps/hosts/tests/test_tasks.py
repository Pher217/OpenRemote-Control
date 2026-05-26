from datetime import timedelta

import pytest
from django.utils import timezone

from apps.hosts.models import Host
from apps.hosts.tasks import check_host_heartbeats


@pytest.mark.django_db
class TestCheckHostHeartbeats:
    def test_marks_stale_hosts_offline(self):
        host = Host.objects.create(
            slug="stale", name="Stale", os="linux", status="online",
            last_seen_at=timezone.now() - timedelta(minutes=10),
        )
        count = check_host_heartbeats(timeout_minutes=5)
        assert count == 1
        host.refresh_from_db()
        assert host.status == "offline"

    def test_leaves_recent_hosts_online(self):
        host = Host.objects.create(
            slug="fresh", name="Fresh", os="darwin", status="online",
            last_seen_at=timezone.now() - timedelta(minutes=2),
        )
        count = check_host_heartbeats(timeout_minutes=5)
        assert count == 0
        host.refresh_from_db()
        assert host.status == "online"
