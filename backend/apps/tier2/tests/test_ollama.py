import os

import pytest

from apps.accounts.models import Account
from apps.threads.models import Thread
from apps.tier2.ollama import OllamaAdapter

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_OLLAMA_LIVE") != "1",
    reason="live Ollama test; set RUN_OLLAMA_LIVE=1 to run",
)


@pytest.fixture
def ollama_thread(db):
    account = Account.objects.create(
        provider="ollama",
        label="local",
        auth_type="none",
        credential_type="none",
    )
    thread = Thread.objects.create(
        name="ol",
        runtime="ollama",
        account=account,
        runtime_mode=Thread.RuntimeModeChoices.API,
    )
    thread.metadata = {"model": "gemma4:31b"}
    thread.save(update_fields=["metadata"])
    return thread


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_ollama_stream(ollama_thread):
    adapter = OllamaAdapter()
    history = [{"role": "user", "content": "Say hello in one short word."}]
    deltas = []
    complete = None
    async for ev in adapter.stream(ollama_thread, history):
        if ev.kind == "message_delta":
            deltas.append(ev)
        elif ev.kind == "message_complete":
            complete = ev
            break
        elif ev.kind == "error":
            pytest.fail(f"Adapter returned error: {ev.payload.get('message')}")
    assert deltas
    assert all(d.payload.get("text") for d in deltas)
    assert complete is not None
    assert complete.payload.get("text")
