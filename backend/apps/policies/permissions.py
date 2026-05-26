from rest_framework import permissions


class PolicyPermission(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        if request is not None and request.method in permissions.SAFE_METHODS:
            return True
        project = getattr(obj, "project", None)
        if project is None:
            return True
        policy = project.policy
        if policy.runtime_modes_allowed and obj.runtime_mode not in policy.runtime_modes_allowed:
            return False
        return not (policy.providers_allowed and obj.account.provider not in policy.providers_allowed)
