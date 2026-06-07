from django.contrib import admin
from django.urls import include, path

from config.health import health

urlpatterns = [
    path("health/", health, name="health"),
    path("admin/", admin.site.urls),
    path("api/accounts/", include("apps.accounts.urls", namespace="accounts")),
    path("api/hosts/", include("apps.hosts.urls", namespace="hosts")),
    path("api/projects/", include("apps.projects.urls", namespace="projects")),
    path("api/policies/", include("apps.policies.urls", namespace="policies")),
    path("api/threads/", include("apps.threads.urls", namespace="threads")),
    path("api/approvals/", include("apps.approvals.urls", namespace="approvals")),
    path("api/audit/", include("apps.audit.urls", namespace="audit")),
    path("api/skills/", include("apps.skills.urls", namespace="skills")),
    path("api/connectors/", include("apps.connectors.urls", namespace="connectors")),
    path("api/gateway/", include("apps.gateway.urls", namespace="gateway")),
    path("api/hostlink/", include("apps.hostlink.urls", namespace="hostlink")),
]
