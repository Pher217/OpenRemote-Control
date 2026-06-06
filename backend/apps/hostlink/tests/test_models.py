import pytest

from apps.hostlink.models import HostToken
from apps.hosts.models import Host


@pytest.fixture
def host(db):
    return Host.objects.create(
        slug="test-host",
        name="Test Host",
        os=Host.OsChoices.LINUX,
    )


@pytest.mark.django_db
class TestHostTokenIssue:
    def test_issue_returns_token_and_raw(self, host):
        """
        GIVEN a host
        WHEN HostToken.issue() is called
        THEN it returns a (HostToken, raw_token) tuple
        """
        token_obj, raw = HostToken.issue(host)
        assert isinstance(token_obj, HostToken)
        assert len(raw) > 10

    def test_raw_token_not_stored(self, host):
        """
        GIVEN a freshly issued token
        WHEN the token_hash field is inspected
        THEN it does not equal the raw token (hash is stored, not plaintext)
        """
        token_obj, raw = HostToken.issue(host)
        assert token_obj.token_hash != raw

    def test_verify_correct_token(self, host):
        """
        GIVEN a freshly issued raw token
        WHEN HostToken.verify() is called with the correct token
        THEN it returns True
        """
        _, raw = HostToken.issue(host)
        assert HostToken.verify(host, raw) is True

    def test_verify_wrong_token(self, host):
        """
        GIVEN a freshly issued token
        WHEN HostToken.verify() is called with a different string
        THEN it returns False
        """
        HostToken.issue(host)
        assert HostToken.verify(host, "wrong-token") is False

    def test_revoked_token_inactive(self, host):
        """
        GIVEN an issued token that is then revoked by issuing a new one
        WHEN HostToken.verify() is called with the old raw token
        THEN it returns False (the old token is revoked)
        """
        _, old_raw = HostToken.issue(host)
        HostToken.issue(host)  # revokes old token
        assert HostToken.verify(host, old_raw) is False

    def test_new_token_after_reissue(self, host):
        """
        GIVEN a second issue call
        WHEN HostToken.verify() is called with the new raw token
        THEN it returns True
        """
        HostToken.issue(host)
        _, new_raw = HostToken.issue(host)
        assert HostToken.verify(host, new_raw) is True

    def test_no_token_returns_false(self, host):
        """
        GIVEN a host with no tokens
        WHEN HostToken.verify() is called
        THEN it returns False
        """
        assert HostToken.verify(host, "anything") is False
