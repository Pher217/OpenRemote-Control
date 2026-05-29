import pytest

from apps.accounts.models import Account
from apps.slash.handlers import account, model, stop
from apps.threads.models import Thread


@pytest.fixture
def thread():
    account_obj = Account.objects.create(
        provider="ollama",
        label="l",
        auth_type="none",
        credential_type="none",
    )
    return Thread.objects.create(
        name="t",
        runtime="ollama",
        account=account_obj,
    )


@pytest.mark.django_db
def test_stop_handle_sets_status(thread):
    result = stop.handle(thread, [])
    thread.refresh_from_db()
    assert thread.status == Thread.StatusChoices.STOPPED
    assert result == {"ok": True, "message": "Thread stopped."}


@pytest.mark.django_db
def test_model_handle_sets_metadata(thread):
    result = model.handle(thread, ["gpt-4"])
    thread.refresh_from_db()
    assert thread.metadata["model"] == "gpt-4"
    assert result == {"ok": True, "message": "Model set to gpt-4."}


@pytest.mark.django_db
def test_account_handle_sets_metadata(thread):
    result = account.handle(thread, ["work"])
    thread.refresh_from_db()
    assert thread.metadata["account"] == "work"
    assert result == {"ok": True, "message": "Account set to work."}


@pytest.mark.django_db
def test_model_handle_no_args_returns_error(thread):
    result = model.handle(thread, [])
    assert result == {"ok": False, "message": "Usage: /model <name>"}


@pytest.mark.django_db
def test_account_handle_no_args_returns_error(thread):
    result = account.handle(thread, [])
    assert result == {"ok": False, "message": "Usage: /account <label>"}


@pytest.mark.django_db
def test_handler_messages_contain_no_secrets(thread):
    results = [
        stop.handle(thread, []),
        model.handle(thread, []),
        model.handle(thread, ["gpt-4"]),
        account.handle(thread, []),
        account.handle(thread, ["work"]),
    ]
    for r in results:
        assert "secret" not in r["message"].lower()
        assert "password" not in r["message"].lower()
        assert "token" not in r["message"].lower()
        assert "credential" not in r["message"].lower()
        assert "key" not in r["message"].lower()
