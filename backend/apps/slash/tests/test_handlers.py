import pytest

from apps.accounts.models import Account
from apps.slash.handlers import account, get_handler, model, remote_control, stop
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
def test_remote_control_creates_new_named_thread(thread):
    """
    GIVEN the current chat thread
    WHEN /openremote-control runs with a name argument
    THEN a new thread is created carrying that name and its id is returned
    """
    result = remote_control.handle(thread, ["Deploy", "review"])

    new_thread = Thread.objects.get(id=result["new_thread_id"])
    assert new_thread.id != thread.id
    assert new_thread.name == "Deploy review"
    assert new_thread.account_id == thread.account_id
    assert new_thread.runtime == thread.runtime
    assert result["ok"] is True
    assert result["thread_name"] == "Deploy review"


@pytest.mark.django_db
def test_remote_control_auto_names_when_no_args(thread):
    """
    GIVEN no name argument
    WHEN /openremote-control runs
    THEN a new thread is created with an auto-generated 'Session ...' name
    """
    result = remote_control.handle(thread, [])

    new_thread = Thread.objects.get(id=result["new_thread_id"])
    assert new_thread.id != thread.id
    assert new_thread.name.startswith("Session ")


@pytest.mark.django_db
def test_openremote_control_aliases_registered():
    """
    GIVEN the handler registry
    WHEN looking up the universal command and its short alias
    THEN both resolve to the remote_control handler
    """
    assert get_handler("openremote-control") is remote_control.handle
    assert get_handler("orc") is remote_control.handle


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
