import pytest

from apps.policies.models import PolicyProfile


@pytest.mark.django_db
class TestPolicyProfileModel:
    def test_deny_by_default(self):
        """GIVEN a new PolicyProfile WHEN created with defaults THEN cloud models and RC are denied."""
        policy = PolicyProfile.objects.create(name="default-deny")
        assert policy.cloud_models_allowed is False
        assert policy.rc_via_anthropic_allowed is False
        assert policy.egress_allowed is False
        assert policy.block_destructive is True
        assert policy.require_worktree is True
        assert policy.runtime_modes_allowed == []
        assert policy.providers_allowed == []

    def test_confidential_profile(self):
        """GIVEN a confidential profile THEN it denies unknown jurisdictions."""
        policy = PolicyProfile.objects.create(
            name="confidential",
            sensitivity_max=PolicyProfile.SensitivityChoices.CONFIDENTIAL,
            provider_jurisdictions_allowed=["CH", "EU"],
            raw_retention_max_days=7,
        )
        assert policy.sensitivity_max == "confidential"
        assert policy.raw_retention_max_days == 7
        assert "US" not in policy.provider_jurisdictions_allowed

    def test_str(self):
        """GIVEN a policy WHEN str() is called THEN name is returned."""
        policy = PolicyProfile.objects.create(name="test-policy")
        assert str(policy) == "test-policy"
