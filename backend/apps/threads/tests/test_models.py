import pytest

from apps.accounts.models import Account
from apps.hosts.models import Host
from apps.policies.models import PolicyProfile
from apps.projects.models import Project
from apps.threads.models import Message, Thread


@pytest.mark.django_db
class TestThreadModel:
    def test_create_thread(self):
        """GIVEN valid thread data WHEN created THEN it persists with pending status."""
        account = Account.objects.create(
            provider="anthropic",
            label="test",
            auth_type="oauth",
            credential_type="token",
            encrypted_credential=b"x",
            credential_key_id="k1",
            credential_recipient="r1",
        )
        thread = Thread.objects.create(
            name="test-thread",
            runtime="claude_code",
            runtime_mode=Thread.RuntimeModeChoices.PTY,
            account=account,
            status=Thread.StatusChoices.PENDING,
        )
        assert thread.status == "pending"
        assert thread.runtime_mode == "pty"

    def test_thread_with_project_and_host(self):
        """GIVEN a project and host WHEN a thread links both THEN FKs resolve."""
        policy = PolicyProfile.objects.create(name="default")
        project = Project.objects.create(
            slug="proj1",
            name="Project 1",
            policy=policy,
        )
        host = Host.objects.create(
            slug="host1",
            name="Host 1",
            os=Host.OsChoices.DARWIN,
            status=Host.StatusChoices.ONLINE,
        )
        account = Account.objects.create(
            provider="openai",
            label="test",
            auth_type="api_key",
            credential_type="api_key",
            encrypted_credential=b"y",
            credential_key_id="k2",
            credential_recipient="r2",
        )
        thread = Thread.objects.create(
            name="test-thread-2",
            runtime="codex",
            runtime_mode=Thread.RuntimeModeChoices.EXEC,
            account=account,
            project=project,
            host=host,
            status=Thread.StatusChoices.RUNNING,
        )
        assert thread.project == project
        assert thread.host == host

    def test_message_redaction_fields(self):
        """GIVEN a thread WHEN a message is created THEN redaction fields exist."""
        account = Account.objects.create(
            provider="ollama",
            label="local",
            auth_type="none",
            credential_type="none",
            encrypted_credential=b"",
            credential_key_id="k3",
            credential_recipient="r3",
        )
        thread = Thread.objects.create(
            name="msg-test",
            runtime="ollama",
            runtime_mode=Thread.RuntimeModeChoices.API,
            account=account,
        )
        msg = Message.objects.create(
            thread=thread,
            role=Message.RoleChoices.USER,
            content="redacted prompt here",
            sequence=1,
        )
        assert msg.content == "redacted prompt here"
        assert msg.raw_content_encrypted is None
        assert msg.raw_retention_expires_at is None
