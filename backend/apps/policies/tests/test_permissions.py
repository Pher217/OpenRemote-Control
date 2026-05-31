import pytest

from apps.accounts.models import Account
from apps.policies.models import PolicyProfile
from apps.policies.permissions import PolicyPermission
from apps.projects.models import Project
from apps.threads.models import Thread


@pytest.mark.django_db
class TestPolicyPermission:
    def test_allowed_when_no_project(self):
        account = Account.objects.create(provider="anthropic", label="t", auth_type="oauth", credential_type="token")
        thread = Thread.objects.create(name="no-proj", runtime="claude_code", account=account)
        perm = PolicyPermission()
        assert perm.has_object_permission(None, None, thread) is True

    def test_allowed_when_policy_has_no_restrictions(self):
        policy = PolicyProfile.objects.create(name="open", runtime_modes_allowed=[], providers_allowed=[])
        project = Project.objects.create(slug="open-proj", name="Open", policy=policy)
        account = Account.objects.create(provider="anthropic", label="t2", auth_type="oauth", credential_type="token")
        thread = Thread.objects.create(name="open-thread", runtime="claude_code", runtime_mode="pty", account=account, project=project)
        perm = PolicyPermission()
        assert perm.has_object_permission(None, None, thread) is True

    def test_blocked_when_runtime_mode_not_allowed(self):
        policy = PolicyProfile.objects.create(name="restrict", runtime_modes_allowed=["api"], providers_allowed=[])
        project = Project.objects.create(slug="restrict-proj", name="Restrict", policy=policy)
        account = Account.objects.create(provider="anthropic", label="t3", auth_type="oauth", credential_type="token")
        thread = Thread.objects.create(name="restrict-thread", runtime="claude_code", runtime_mode="pty", account=account, project=project)
        perm = PolicyPermission()
        assert perm.has_object_permission(None, None, thread) is False

    def test_blocked_when_provider_not_allowed(self):
        policy = PolicyProfile.objects.create(name="prov", runtime_modes_allowed=[], providers_allowed=["openai"])
        project = Project.objects.create(slug="prov-proj", name="Prov", policy=policy)
        account = Account.objects.create(provider="anthropic", label="t4", auth_type="oauth", credential_type="token")
        thread = Thread.objects.create(name="prov-thread", runtime="claude_code", runtime_mode="pty", account=account, project=project)
        perm = PolicyPermission()
        assert perm.has_object_permission(None, None, thread) is False

    def test_allowed_when_provider_matches(self):
        policy = PolicyProfile.objects.create(name="ok", runtime_modes_allowed=[], providers_allowed=["anthropic"])
        project = Project.objects.create(slug="ok-proj", name="OK", policy=policy)
        account = Account.objects.create(provider="anthropic", label="t5", auth_type="oauth", credential_type="token")
        thread = Thread.objects.create(name="ok-thread", runtime="claude_code", runtime_mode="pty", account=account, project=project)
        perm = PolicyPermission()
        assert perm.has_object_permission(None, None, thread) is True
