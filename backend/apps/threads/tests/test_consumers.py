import pytest
from channels.testing import WebsocketCommunicator

from apps.accounts.models import Account
from apps.threads.models import Thread
from config.asgi import application


@pytest.fixture
def thread(db):
    account = Account.objects.create(provider="anthropic", label="c", auth_type="oauth", credential_type="token")
    return Thread.objects.create(name="ws-thread", runtime="claude_code", account=account)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
class TestThreadConsumer:
    async def test_connect_and_receive(self, thread):
        communicator = WebsocketCommunicator(
            application, f"/ws/threads/{thread.id}/", headers=[(b"origin", b"http://localhost")]
        )
        connected, subprotocol = await communicator.connect()
        assert connected is True
        await communicator.send_json_to({"type": "test", "message": "hello"})
        response = await communicator.receive_json_from()
        assert response["message"] == "hello"
        await communicator.disconnect()
