import uuid

import pytest

from apps.accounts.models import Account
from apps.hosts.models import Host


@pytest.mark.django_db
class TestAccountModel:
    def test_create_account(self):
        """GIVEN valid account data WHEN created THEN it persists with UUID pk."""
        account = Account.objects.create(
            provider="anthropic",
            label="personal",
            auth_type="oauth",
            credential_type="api_key",
            encrypted_credential=b"encrypted-data",
            credential_key_id="key-001",
            credential_recipient="age1abc123",
            credential_scheme_version=1,
        )
        assert isinstance(account.id, uuid.UUID)
        assert account.provider == "anthropic"
        assert account.label == "personal"
        assert account.credential_scheme_version == 1

    def test_account_host_binding(self):
        """GIVEN a host WHEN an account binds to it THEN host_binding is set."""
        host = Host.objects.create(
            slug="dev1",
            name="Development Host 1",
            os=Host.OsChoices.LINUX,
            status=Host.StatusChoices.ONLINE,
        )
        account = Account.objects.create(
            provider="ollama",
            label="local",
            auth_type="none",
            credential_type="none",
            encrypted_credential=b"",
            credential_key_id="key-002",
            credential_recipient="age1local",
            host_binding=host,
        )
        assert account.host_binding == host
        assert host.bound_accounts.count() == 1

    def test_account_str(self):
        """GIVEN an account WHEN str() is called THEN format is provider:label."""
        account = Account.objects.create(
            provider="openai",
            label="work",
            auth_type="api_key",
            credential_type="api_key",
            encrypted_credential=b"secret",
            credential_key_id="key-003",
            credential_recipient="age1work",
        )
        assert str(account) == "openai:work"

    def test_account_credential_rotation_fields(self):
        """GIVEN an account WHEN rotated THEN rotated_at is set and revoked_at is null."""
        from django.utils import timezone

        account = Account.objects.create(
            provider="anthropic",
            label="rotated",
            auth_type="oauth",
            credential_type="token",
            encrypted_credential=b"new-data",
            credential_key_id="key-004",
            credential_recipient="age1new",
            credential_rotated_at=timezone.now(),
        )
        assert account.credential_rotated_at is not None
        assert account.credential_revoked_at is None
