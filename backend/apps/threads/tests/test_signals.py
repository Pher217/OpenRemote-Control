from unittest.mock import patch

import pytest
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer

from apps.accounts.models import Account
from apps.threads.models import Thread


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
class TestThreadBroadcastSignal:
    async def test_broadcasts_on_status_change(self):
        channel_layer = get_channel_layer()
        account = await sync_to_async(Account.objects.create)(
            provider="anthropic", label="s", auth_type="oauth", credential_type="token"
        )
        thread = await sync_to_async(Thread.objects.create)(
            name="signal-thread", runtime="claude_code", account=account
        )
        group_name = f"thread_{thread.id}"
        await channel_layer.group_add(group_name, "test-channel")
        thread.status = Thread.StatusChoices.RUNNING
        await sync_to_async(thread.save)()
        message = await channel_layer.receive("test-channel")
        assert message["type"] == "thread_update"
        assert message["data"]["status"] == "running"


class _BrokenChannelLayer:
    def group_send(self, *args, **kwargs):
        raise ConnectionError("broker is down")


@pytest.mark.django_db
def test_broadcast_failure_does_not_propagate():
    account = Account.objects.create(
        provider="anthropic", label="s", auth_type="oauth", credential_type="token"
    )
    thread = Thread.objects.create(name="resilient-thread", runtime="claude_code", account=account)
    with patch(
        "apps.threads.signals.get_channel_layer",
        return_value=_BrokenChannelLayer(),
    ):
        thread.status = Thread.StatusChoices.RUNNING
        thread.save()
    thread.refresh_from_db()
    assert thread.status == Thread.StatusChoices.RUNNING
